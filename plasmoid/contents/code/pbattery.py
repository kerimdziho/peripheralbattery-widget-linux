#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Peripheral Battery - backend.

Finds the battery level of wireless peripherals (mice, keyboards, headphones,
gamepads, ...) and sends configurable low-battery / charged notifications.

Pure standard library, Python >= 3.8, no root needed. Data sources:

* UPower      (``upower --dump``)  - the main source on most distros; it already
                                    includes BlueZ and HID++ devices
* BlueZ       (``busctl`` D-Bus, ``bluetoothctl`` fallback)
* sysfs       (``/sys/class/power_supply``) - fallback when UPower is missing
* headsetcontrol (optional)         - USB / 2.4 GHz gaming headsets

Run ``pbattery.py --help`` for the command line interface.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

__version__ = "1.0.0"
APP_NAME = "Peripheral Battery"
APP_ID = "peripheral-battery"

Runner = Callable[..., Tuple[int, str, str]]

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_ID, "config.json")


def state_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, APP_ID, "state.json")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

DEFAULT_SETTINGS: Dict[str, Any] = {
    "poll_seconds": 60,
    # Notify once when the battery drops to / below each of these (percent).
    "thresholds": [75, 50, 25, 10, 5],
    # At or below this level the notification is "critical" (0 = never).
    "critical_below": 10,
    # Notify when charging reaches this level (0 = never).
    "charged_threshold": 100,
    # A threshold re-arms once the level is this many percent above it.
    "hysteresis": 3,
    "include_system_battery": False,
    # Fake devices, to try the widget without hardware (and for screenshots).
    "demo": False,
    "providers": {"upower": True, "bluez": True, "headsetcontrol": True, "openrazer": True, "hid": True},
    "muted": [],   # device ids that never notify
    "hidden": [],  # device ids that are not shown at all
    "notify": {
        "enabled": True,
        "desktop": True,
        "persistent": False,
        "critical_sticky": True,
        "sound": False,
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token": ""},
    },
}


def _deep_merge(base: Dict[str, Any], over: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\n]", value)]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def parse_thresholds(value: Any) -> List[int]:
    """Accept ``[75, 50]`` or ``"75, 50"``; returns unique ints 1..99, descending."""
    if isinstance(value, str):
        items: List[Any] = [p for p in re.split(r"[,\s;]+", value.strip()) if p]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    found = set()
    for item in items:
        try:
            number = int(float(str(item).strip().rstrip("%")))
        except ValueError:
            continue
        if 1 <= number <= 99:
            found.add(number)
    return sorted(found, reverse=True)


def normalize_settings(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = _deep_merge(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
    providers = merged.get("providers") if isinstance(merged.get("providers"), dict) else {}
    notify = merged.get("notify") if isinstance(merged.get("notify"), dict) else {}
    ntfy = notify.get("ntfy") if isinstance(notify.get("ntfy"), dict) else {}
    return {
        "poll_seconds": _clamp_int(merged.get("poll_seconds"), 60, 10, 3600),
        "thresholds": parse_thresholds(merged.get("thresholds")),
        "critical_below": _clamp_int(merged.get("critical_below"), 10, 0, 99),
        "charged_threshold": _clamp_int(merged.get("charged_threshold"), 100, 0, 100),
        "hysteresis": _clamp_int(merged.get("hysteresis"), 3, 0, 20),
        "include_system_battery": _as_bool(merged.get("include_system_battery")),
        "demo": _as_bool(merged.get("demo")),
        "providers": {k: _as_bool(providers.get(k), True) for k in DEFAULT_SETTINGS["providers"]},
        "muted": _str_list(merged.get("muted")),
        "hidden": _str_list(merged.get("hidden")),
        "notify": {
            "enabled": _as_bool(notify.get("enabled"), True),
            "desktop": _as_bool(notify.get("desktop"), True),
            "persistent": _as_bool(notify.get("persistent")),
            "critical_sticky": _as_bool(notify.get("critical_sticky"), True),
            "sound": _as_bool(notify.get("sound")),
            "ntfy": {
                "enabled": _as_bool(ntfy.get("enabled")),
                "server": str(ntfy.get("server") or "https://ntfy.sh").strip().rstrip("/"),
                "topic": str(ntfy.get("topic") or "").strip(),
                "token": str(ntfy.get("token") or "").strip(),
            },
        },
    }


def read_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return data


def load_effective_settings(
    use_file: bool = True,
    override_json: Optional[str] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    if use_file:
        raw = read_json_file(path or config_path())
    if override_json:
        try:
            override = json.loads(override_json)
        except ValueError as exc:
            raise ValueError("invalid --settings-json: %s" % exc)
        if not isinstance(override, dict):
            raise ValueError("--settings-json must be a JSON object")
        raw = _deep_merge(raw, override)
    return normalize_settings(raw)


# --------------------------------------------------------------------------
# Process helper
# --------------------------------------------------------------------------


def run_cmd(cmd: List[str], timeout: float = 6.0) -> Tuple[int, str, str]:
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", "%s: command not found" % cmd[0]
    except PermissionError:
        return 126, "", "%s: permission denied" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out" % cmd[0]
    except OSError as exc:
        return 125, "", "%s: %s" % (cmd[0], exc)
    return proc.returncode, proc.stdout, proc.stderr


# --------------------------------------------------------------------------
# Device model
# --------------------------------------------------------------------------

SOURCE_PRIORITY = {"upower": 0, "bluez": 1, "headsetcontrol": 2, "openrazer": 2, "hid": 3, "sysfs": 4, "demo": 5}

MAC_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{2}(?:[:_-][0-9A-Fa-f]{2}){5})(?![0-9A-Fa-f])")


@dataclass
class Device:
    id: str
    name: str
    kind: str = "other"
    percent: Optional[int] = None
    state: str = "unknown"  # charging | discharging | full | unknown
    transport: str = "unknown"  # bluetooth | wireless | usb | unknown
    source: str = ""
    approx: bool = False
    address: Optional[str] = None

    @property
    def charging(self) -> bool:
        return self.state == "charging"

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "percent": self.percent,
            "state": self.state,
            "charging": self.charging,
            "transport": self.transport,
            "source": self.source,
            "approx": self.approx,
            "address": self.address,
        }


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def names_match(a: str, b: str) -> bool:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 5 and shorter in longer


def extract_mac(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        match = MAC_RE.search(text or "")
        if match:
            return re.sub(r"[_-]", ":", match.group(1)).upper()
    return None


def make_id(mac: Optional[str], name: str, kind: str) -> str:
    if mac:
        return "bt:" + mac.lower()
    return "name:%s:%s" % (norm_name(name) or "unknown", kind)


_KIND_KEYWORDS: List[Tuple[str, str]] = [
    ("mouse", "mouse"), ("mice", "mouse"), ("trackball", "mouse"),
    ("keyboard", "keyboard"), ("keypad", "keyboard"), ("keys", "keyboard"),
    ("touchpad", "touchpad"), ("trackpad", "touchpad"),
    ("headset", "headset"), ("headphone", "headphones"), ("earbud", "headphones"),
    ("buds", "headphones"), ("airpods", "headphones"), ("earphone", "headphones"),
    ("gaming", "gamepad"), ("gamepad", "gamepad"), ("controller", "gamepad"),
    ("joystick", "gamepad"), ("joycon", "gamepad"), ("joy-con", "gamepad"),
    ("speaker", "speaker"), ("audio-card", "speaker"), ("soundbar", "speaker"),
    ("phone", "phone"), ("tablet", "tablet"), ("pen", "pen"), ("stylus", "pen"),
]


def kind_from_text(text: str, default: str = "other") -> str:
    lowered = (text or "").lower()
    for needle, kind in _KIND_KEYWORDS:
        if needle in lowered:
            return kind
    return default


# --------------------------------------------------------------------------
# Provider: UPower
# --------------------------------------------------------------------------

UPOWER_KIND_MAP = {
    "mouse": "mouse", "keyboard": "keyboard", "headset": "headset", "headphones": "headphones",
    "gaming-input": "gamepad", "phone": "phone", "pda": "phone", "tablet": "tablet",
    "touchpad": "touchpad", "pen": "pen", "speakers": "speaker", "other-audio": "speaker",
    "media-player": "media", "remote-control": "remote", "camera": "camera",
    "wearable": "wearable", "toy": "other", "bluetooth-generic": "other",
    "battery": "other", "unknown": "other",
}
UPOWER_EXCLUDED_KINDS = {"line-power", "ups", "monitor"}
UPOWER_LEVELS = {"full": 100, "high": 80, "normal": 55, "low": 25, "critical": 5}
UPOWER_STATES = {
    "charging": "charging", "fully-charged": "full", "discharging": "discharging",
    "pending-discharge": "discharging", "empty": "discharging",
}
SYSTEM_BATTERY_RE = re.compile(r"^(BAT|CMB)\d", re.IGNORECASE)


def _upower_blocks(text: str) -> Iterator[Tuple[str, List[str]]]:
    header: Optional[str] = None
    lines: List[str] = []
    for line in text.splitlines():
        if line.startswith("Device:") or line.startswith("Daemon:"):
            if header is not None:
                yield header, lines
            header, lines = line, []
        elif header is not None:
            lines.append(line)
    if header is not None:
        yield header, lines


def _transport_from_path(native: str, serial: str = "") -> str:
    lowered = native.lower()
    if "bluez" in lowered or MAC_RE.search(native) or MAC_RE.search(serial or ""):
        return "bluetooth"
    if "hidpp" in lowered:
        return "wireless"
    if "usb" in lowered:
        return "usb"
    return "unknown"


def parse_upower_dump(text: str, include_system: bool = False) -> List[Device]:
    devices: List[Device] = []
    for header, lines in _upower_blocks(text):
        if not header.startswith("Device:"):
            continue
        obj_path = header.split(":", 1)[1].strip()
        if obj_path.endswith("/DisplayDevice"):
            continue
        fields: Dict[str, str] = {}
        kind: Optional[str] = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip(" "))
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                fields.setdefault(key.strip(), value.strip())
            elif indent == 2 and re.fullmatch(r"[a-z][a-z\-]*", stripped):
                kind = stripped
        if kind is None or kind in UPOWER_EXCLUDED_KINDS:
            continue
        native = fields.get("native-path", "")
        power_supply = fields.get("power supply", "no").lower() == "yes"
        is_system = power_supply or (kind == "battery" and bool(SYSTEM_BATTERY_RE.match(native)))
        if is_system and not include_system:
            continue
        if fields.get("present", "yes").lower() == "no":
            continue

        percent: Optional[int] = None
        approx = False
        match = re.match(r"([\d.]+)\s*%", fields.get("percentage", ""))
        if match:
            percent = int(round(float(match.group(1))))
        elif fields.get("battery-level", "").lower() in UPOWER_LEVELS:
            percent = UPOWER_LEVELS[fields["battery-level"].lower()]
            approx = True
        if percent is None:
            continue

        vendor = fields.get("vendor", "")
        model = fields.get("model", "")
        if is_system:
            name, dev_kind = "System battery", "laptop"
        else:
            dev_kind = UPOWER_KIND_MAP.get(kind, "other")
            if model:
                name = model if (not vendor or vendor.lower() in model.lower()) else "%s %s" % (vendor, model)
            else:
                name = vendor or native or "Unknown device"
            if dev_kind == "other":
                dev_kind = kind_from_text(name)

        mac = extract_mac(native, fields.get("serial", ""), obj_path)
        devices.append(Device(
            id="system:battery" if is_system else make_id(mac, name, dev_kind),
            name=name,
            kind=dev_kind,
            percent=max(0, min(100, percent)),
            state=UPOWER_STATES.get(fields.get("state", ""), "unknown"),
            transport="unknown" if is_system else _transport_from_path(native, fields.get("serial", "")),
            source="upower",
            approx=approx,
            address=mac,
        ))
    return devices


def provider_upower(settings: Dict[str, Any], runner: Runner, which: Callable[[str], Optional[str]]) -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "upower", "important": True, "available": True, "ok": True, "count": 0, "error": ""}
    if not which("upower"):
        info.update(available=False, ok=False, error="UPower is not installed (package: upower)")
        return info, []
    rc, out, err = runner(["upower", "--dump"])
    if rc != 0:
        info.update(ok=False, error=(err.strip() or "upower exited with code %d" % rc))
        return info, []
    devices = parse_upower_dump(out, settings.get("include_system_battery", False))
    info["count"] = len(devices)
    return info, devices


# --------------------------------------------------------------------------
# Provider: BlueZ
# --------------------------------------------------------------------------

def _unwrap(value: Any) -> Any:
    """busctl --json wraps variants as {"type": "s", "data": ...}."""
    while isinstance(value, dict) and set(value.keys()) == {"type", "data"}:
        value = value["data"]
    return value


def _bluez_device(props: Dict[str, Any], percent: int) -> Device:
    name = str(props.get("Alias") or props.get("Name") or props.get("Address") or "Bluetooth device")
    address = str(props.get("Address") or "").upper() or None
    kind = kind_from_text(str(props.get("Icon") or ""), default="other")
    if kind == "other":
        kind = kind_from_text(name)
    return Device(
        id=make_id(address, name, kind), name=name, kind=kind, percent=max(0, min(100, percent)),
        state="unknown", transport="bluetooth", source="bluez", address=address,
    )


def parse_bluez_managed_objects(obj: Any) -> List[Device]:
    data = obj.get("data") if isinstance(obj, dict) and "data" in obj else obj
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return []
    devices: List[Device] = []
    for ifaces in data.values():
        if not isinstance(ifaces, dict):
            continue
        dev, bat = ifaces.get("org.bluez.Device1"), ifaces.get("org.bluez.Battery1")
        if not isinstance(dev, dict) or not isinstance(bat, dict):
            continue
        props = {k: _unwrap(v) for k, v in dev.items()}
        bprops = {k: _unwrap(v) for k, v in bat.items()}
        if not props.get("Connected"):
            continue
        try:
            percent = int(bprops["Percentage"])
        except (KeyError, TypeError, ValueError):
            continue
        devices.append(_bluez_device(props, percent))
    return devices


def parse_bluetoothctl_info(text: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    match = re.match(r"\s*Device\s+([0-9A-Fa-f:]{17})", text)
    if match:
        info["address"] = match.group(1).upper()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Alias:"):
            info["alias"] = line.split(":", 1)[1].strip()
        elif line.startswith("Icon:"):
            info["icon"] = line.split(":", 1)[1].strip()
        elif line.startswith("Connected:"):
            info["connected"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Battery Percentage:"):
            found = re.search(r"\((\d+)\)", line)
            if found:
                info["percent"] = int(found.group(1))
            else:
                found = re.search(r"0x([0-9a-fA-F]+)", line)
                if found:
                    info["percent"] = int(found.group(1), 16)
    return info


def _bluez_via_bluetoothctl(runner: Runner) -> Tuple[int, str, List[Device]]:
    rc, out, err = runner(["bluetoothctl", "devices", "Connected"], 3.0)
    if rc != 0 or "Device" not in out:
        # Older BlueZ has no "Connected" filter: list everything and check each one.
        rc, out, err = runner(["bluetoothctl", "devices"], 3.0)
        if rc != 0:
            return rc, err.strip(), []
    devices: List[Device] = []
    for line in out.splitlines():
        match = re.match(r"\s*Device\s+([0-9A-Fa-f:]{17})", line)
        if not match:
            continue
        mac = match.group(1).upper()
        rc, info_out, _ = runner(["bluetoothctl", "info", mac], 3.0)
        if rc != 0:
            continue
        info = parse_bluetoothctl_info(info_out)
        if not info.get("connected") or "percent" not in info:
            continue
        info.setdefault("address", mac)
        devices.append(_bluez_device(
            {"Alias": info.get("alias") or info.get("name"), "Address": info["address"], "Icon": info.get("icon")},
            info["percent"],
        ))
    return 0, "", devices


def provider_bluez(settings: Dict[str, Any], runner: Runner, which: Callable[[str], Optional[str]]) -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "bluez", "important": True, "available": True, "ok": True, "count": 0, "error": ""}
    if not which("busctl") and not which("bluetoothctl"):
        info.update(available=False, ok=False, error="Neither busctl nor bluetoothctl found")
        return info, []
    devices: List[Device] = []
    error = ""
    parsed = False
    if which("busctl"):
        rc, out, err = runner([
            "busctl", "--system", "--json=short", "call", "org.bluez", "/",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
        ])
        if rc == 0:
            try:
                devices = parse_bluez_managed_objects(json.loads(out))
                parsed = True
            except ValueError:
                error = "could not parse busctl output"
        elif "org.bluez" in err:
            # bluetoothd is not on the bus: no adapter, or Bluetooth is off.
            # That is normal on many desktops - nothing to report, nothing to retry.
            info["note"] = "Bluetooth is not running or no adapter found"
            return info, []
        else:
            error = err.strip() or "busctl exited with code %d" % rc
    if not parsed and which("bluetoothctl"):
        rc, msg, devices = _bluez_via_bluetoothctl(runner)
        if rc == 0:
            parsed, error = True, ""
        else:
            error = msg or error
    if not parsed:
        info.update(ok=False, error=error or "BlueZ is not available (is Bluetooth running?)")
        return info, []
    info["count"] = len(devices)
    return info, devices


# --------------------------------------------------------------------------
# Provider: headsetcontrol (optional)
# --------------------------------------------------------------------------

def parse_headsetcontrol_json(text: str) -> List[Device]:
    data = json.loads(text)
    entries = data.get("devices") if isinstance(data, dict) else None
    devices: List[Device] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        battery = entry.get("battery") or {}
        status = str(battery.get("status") or "")
        level = battery.get("level")
        if status == "BATTERY_UNAVAILABLE" or not isinstance(level, int) or level < 0:
            continue
        name = str(entry.get("product") or entry.get("device") or "Wireless headset")
        devices.append(Device(
            id=make_id(None, name, "headset"), name=name, kind="headset",
            percent=max(0, min(100, level)),
            state="charging" if status == "BATTERY_CHARGING" else "unknown",
            transport="wireless", source="headsetcontrol",
        ))
    return devices


def provider_headsetcontrol(settings: Dict[str, Any], runner: Runner, which: Callable[[str], Optional[str]]) -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "headsetcontrol", "important": False, "available": True, "ok": True, "count": 0, "error": ""}
    if not which("headsetcontrol"):
        info.update(available=False, ok=False, error="headsetcontrol not installed (optional)")
        return info, []
    devices: List[Device] = []
    rc, out, _ = runner(["headsetcontrol", "-b", "-o", "json"])
    if out.strip():
        try:
            devices = parse_headsetcontrol_json(out)
        except ValueError:
            devices = []
    if not devices:
        # Older versions (< 3.0) have no JSON output; "-c" prints just the number.
        rc, out, _ = runner(["headsetcontrol", "-b", "-c"])
        match = re.fullmatch(r"\s*(\d+)\s*", out or "")
        if rc == 0 and match:
            devices = [Device(
                id=make_id(None, "Wireless headset", "headset"), name="Wireless headset", kind="headset",
                percent=min(100, int(match.group(1))), transport="wireless", source="headsetcontrol",
            )]
    info["count"] = len(devices)
    return info, devices


# --------------------------------------------------------------------------
# Provider: sysfs (fallback)
# --------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def provider_sysfs(settings: Dict[str, Any], root: str = "/sys/class/power_supply") -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "sysfs", "important": False, "available": os.path.isdir(root), "ok": True, "count": 0, "error": ""}
    devices: List[Device] = []
    if not info["available"]:
        info.update(ok=False, error="%s not found" % root)
        return info, devices
    states = {"charging": "charging", "discharging": "discharging", "full": "full"}
    for entry in sorted(os.listdir(root)):
        base = os.path.join(root, entry)
        if _read(os.path.join(base, "type")).lower() != "battery":
            continue
        is_peripheral = _read(os.path.join(base, "scope")).lower() == "device"
        if not is_peripheral and not settings.get("include_system_battery"):
            continue
        capacity = _read(os.path.join(base, "capacity"))
        if not capacity.isdigit():
            continue
        model = _read(os.path.join(base, "model_name")) or entry
        maker = _read(os.path.join(base, "manufacturer"))
        name = model if (not maker or maker.lower() in model.lower()) else "%s %s" % (maker, model)
        mac = extract_mac(entry, _read(os.path.join(base, "serial_number")))
        kind = "laptop" if not is_peripheral else kind_from_text(name)
        devices.append(Device(
            id="system:battery" if not is_peripheral else make_id(mac, name, kind),
            name="System battery" if not is_peripheral else name,
            kind=kind, percent=max(0, min(100, int(capacity))),
            state=states.get(_read(os.path.join(base, "status")).lower(), "unknown"),
            transport=_transport_from_path(entry) if is_peripheral else "unknown",
            source="sysfs", address=mac,
        ))
    info["count"] = len(devices)
    return info, devices


# --------------------------------------------------------------------------
# Provider: HID (vendor-specific 2.4 GHz dongles, driven by small JSON profiles)
# --------------------------------------------------------------------------
#
# Cheap wireless mice/keyboards report their battery through a private HID
# message. Nobody can know that message without capturing it from the device,
# so instead of guessing, a *profile* describes it: which device, what to send,
# what comes back and which byte holds the percentage. See docs/PROFILES.md.

HIDRAW_SYSFS = "/sys/class/hidraw"


class HidError(Exception):
    pass


def _ioc(direction: int, nr: int, size: int) -> int:
    """Linux _IOC() for the 'H' (hidraw) ioctl family (x86/arm/riscv layout)."""
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def hidiocsfeature(length: int) -> int:
    return _ioc(3, 0x06, length)


def hidiocgfeature(length: int) -> int:
    return _ioc(3, 0x07, length)


def parse_hex(value: Any) -> bytes:
    """'08 04 0x1f', '0804' or b'..' -> bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    cleaned = re.sub(r"0x", " ", str(value), flags=re.IGNORECASE).replace(",", " ")
    if not re.fullmatch(r"[\s0-9a-fA-F]*", cleaned):
        raise ValueError("not a hex string: %r" % (value,))
    return bytes(int(tok, 16) for tok in re.findall(r"[0-9a-fA-F]{1,2}", cleaned))


def hex_int(value: Any) -> int:
    """JSON numbers are decimal, JSON strings are hex ('7f', '0x7f', '3554')."""
    if isinstance(value, bool):
        raise ValueError("expected a number")
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 16)


def format_hex(data: bytes) -> str:
    return " ".join("%02x" % b for b in data)


def parse_report_descriptor(data: bytes) -> Dict[str, Any]:
    """Minimal HID report descriptor reader: top-level collections + report sizes."""
    collections: List[Dict[str, int]] = []
    reports: Dict[str, Dict[int, int]] = {"input": {}, "output": {}, "feature": {}}
    usage_page = size = count = report_id = depth = 0
    usage: Optional[int] = None
    i = 0
    while i < len(data):
        prefix = data[i]
        if prefix == 0xFE:  # long item - never used in practice
            if i + 1 >= len(data):
                break
            i += 3 + data[i + 1]
            continue
        item_size = prefix & 3
        item_size = 4 if item_size == 3 else item_size
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        value = int.from_bytes(data[i + 1:i + 1 + item_size], "little") if item_size else 0
        i += 1 + item_size
        if kind == 1:  # global
            if tag == 0:
                usage_page = value
            elif tag == 7:
                size = value
            elif tag == 8:
                report_id = value
            elif tag == 9:
                count = value
        elif kind == 2:  # local
            if tag == 0 and usage is None:
                if item_size == 4 and value > 0xFFFF:
                    usage_page, usage = value >> 16, value & 0xFFFF
                else:
                    usage = value
        elif kind == 0:  # main
            if tag == 0xA:
                if depth == 0:
                    collections.append({"usage_page": usage_page, "usage": usage if usage is not None else 0})
                depth += 1
            elif tag == 0xC:
                depth = max(0, depth - 1)
            elif tag in (0x8, 0x9, 0xB):
                name = {0x8: "input", 0x9: "output", 0xB: "feature"}[tag]
                reports[name][report_id] = reports[name].get(report_id, 0) + size * count
            usage = None
    return {
        "collections": collections,
        "reports": {k: {rid: (bits + 7) // 8 for rid, bits in v.items()} for k, v in reports.items()},
    }


@dataclass
class HidrawNode:
    path: str
    vendor: int
    product: int
    name: str = ""
    uniq: str = ""
    interface: Optional[int] = None
    collections: Optional[List[Dict[str, int]]] = None
    reports: Optional[Dict[str, Dict[int, int]]] = None


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


def list_hidraw(sys_root: str = HIDRAW_SYSFS, dev_root: str = "/dev") -> List[HidrawNode]:
    try:
        entries = sorted(os.listdir(sys_root), key=lambda e: (len(e), e))
    except OSError:
        return []
    nodes: List[HidrawNode] = []
    for entry in entries:
        dev_dir = os.path.join(sys_root, entry, "device")
        uevent: Dict[str, str] = {}
        for line in _read(os.path.join(dev_dir, "uevent")).splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                uevent[key] = value
        match = re.match(r"([0-9A-Fa-f]+):([0-9A-Fa-f]+):([0-9A-Fa-f]+)", uevent.get("HID_ID", ""))
        if not match:
            continue
        interface = None
        text = _read(os.path.join(os.path.dirname(os.path.realpath(dev_dir)), "bInterfaceNumber"))
        if text:
            try:
                interface = int(text, 16)
            except ValueError:
                pass
        parsed = parse_report_descriptor(_read_bytes(os.path.join(dev_dir, "report_descriptor")))
        nodes.append(HidrawNode(
            path=os.path.join(dev_root, entry), vendor=int(match.group(2), 16), product=int(match.group(3), 16),
            name=uevent.get("HID_NAME", ""), uniq=uevent.get("HID_UNIQ", ""), interface=interface,
            collections=parsed["collections"], reports=parsed["reports"],
        ))
    return nodes


class HidrawTransport:
    """Thin wrapper over /dev/hidrawN. Replaceable in tests."""

    def __init__(self, path: str) -> None:
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def read(self, timeout: float) -> Optional[bytes]:
        try:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
            if not ready:
                return None
            return os.read(self.fd, 512)
        except BlockingIOError:
            return None
        except OSError as exc:
            raise HidError(str(exc))

    def drain(self) -> None:
        while self.read(0.0) is not None:
            pass

    def write(self, data: bytes) -> None:
        try:
            os.write(self.fd, data)
        except OSError as exc:
            raise HidError("write failed: %s" % exc)

    def set_feature(self, data: bytes) -> None:
        try:
            fcntl.ioctl(self.fd, hidiocsfeature(len(data)), bytearray(data), True)
        except OSError as exc:
            raise HidError("set feature failed: %s" % exc)

    def get_feature(self, report_id: int, length: int) -> bytes:
        buf = bytearray(length)
        buf[0] = report_id
        try:
            got = fcntl.ioctl(self.fd, hidiocgfeature(length), buf, True)
        except OSError as exc:
            raise HidError("get feature failed: %s" % exc)
        return bytes(buf[:got] if isinstance(got, int) and 0 < got <= length else buf)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


# ---- profiles -------------------------------------------------------------

def validate_profile(profile: Any) -> List[str]:
    """Return a list of problems (empty = valid)."""
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    errors: List[str] = []

    def check(label: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            errors.append("%s: %s" % (label, exc if str(exc) else exc.__class__.__name__))

    if not str(profile.get("name") or "").strip():
        errors.append("name: missing")
    match = profile.get("match")
    if not isinstance(match, dict):
        errors.append("match: missing")
    else:
        check("match.vendor", lambda: hex_int(match["vendor"]))
        check("match.product", lambda: hex_int(match["product"]))
        if "usage_page" in match:
            check("match.usage_page", lambda: hex_int(match["usage_page"]))
        if "interface" in match:
            check("match.interface", lambda: int(match["interface"]))
    resp = profile.get("response")
    if not isinstance(resp, dict) or resp.get("type") not in ("input", "feature"):
        errors.append("response.type must be 'input' or 'feature'")
    else:
        if resp["type"] == "feature":
            check("response.report_id", lambda: int(resp["report_id"]))
            check("response.length", lambda: int(resp["length"]))
        if resp.get("prefix"):
            check("response.prefix", lambda: parse_hex(resp["prefix"]))
        if "sum_equals" in resp:
            check("response.sum_equals", lambda: hex_int(resp["sum_equals"]))
    req = profile.get("request")
    if req is not None:
        if not isinstance(req, dict) or req.get("type") not in ("output", "feature"):
            errors.append("request.type must be 'output' or 'feature'")
        else:
            check("request.hex", lambda: parse_hex(req["hex"]) or (_ for _ in ()).throw(ValueError("empty")))
    pct = profile.get("percent")
    if not isinstance(pct, dict):
        errors.append("percent: missing")
    else:
        check("percent.byte", lambda: int(pct["byte"]) >= 0 or (_ for _ in ()).throw(ValueError("negative")))
        if "mask" in pct:
            check("percent.mask", lambda: hex_int(pct["mask"]))
        lo, hi = pct.get("min", 0), pct.get("max", 100)
        if not (isinstance(lo, int) and isinstance(hi, int) and hi > lo):
            errors.append("percent.min/max must be integers with max > min")
    charging = profile.get("charging")
    if charging is not None:
        if not isinstance(charging, dict):
            errors.append("charging must be an object")
        else:
            check("charging.byte", lambda: int(charging["byte"]))
            for key in ("mask", "equals"):
                if key in charging:
                    check("charging." + key, lambda k=key: hex_int(charging[k]))
    return errors


def profile_dirs() -> List[str]:
    return [os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles"),
            os.path.join(os.path.dirname(config_path()), "profiles")]


def load_profiles(dirs: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Returns (valid profiles, problems). Files starting with '_' are examples and skipped."""
    profiles: List[Dict[str, Any]] = []
    problems: List[str] = []
    for folder in dirs if dirs is not None else profile_dirs():
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for fname in names:
            if not fname.endswith(".json") or fname.startswith("_"):
                continue
            path = os.path.join(folder, fname)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError) as exc:
                problems.append("%s: %s" % (path, exc))
                continue
            for item in data if isinstance(data, list) else [data]:
                errors = validate_profile(item)
                if errors:
                    problems.append("%s: %s" % (path, "; ".join(errors)))
                else:
                    profiles.append(item)
    return profiles, problems


def profile_matches(profile: Dict[str, Any], node: HidrawNode) -> bool:
    match = profile["match"]
    if hex_int(match["vendor"]) != node.vendor or hex_int(match["product"]) != node.product:
        return False
    if "interface" in match and node.interface is not None and int(match["interface"]) != node.interface:
        return False
    if "usage_page" in match:
        wanted = hex_int(match["usage_page"])
        if not any(c["usage_page"] == wanted for c in node.collections or []):
            return False
    if match.get("name_contains") and str(match["name_contains"]).lower() not in node.name.lower():
        return False
    return True


def decode_profile_reply(profile: Dict[str, Any], data: bytes) -> Optional[Tuple[int, bool]]:
    """Extract (percent, charging) from one report, or None if it does not look right."""
    cfg = profile["percent"]
    index = int(cfg["byte"])
    if index >= len(data):
        return None
    raw = (data[index] & hex_int(cfg.get("mask", "ff"))) >> int(cfg.get("shift", 0))
    low, high = int(cfg.get("min", 0)), int(cfg.get("max", 100))
    if raw < low or raw > high:
        return None  # garbage / not a battery report
    percent = int(round((raw - low) * 100.0 / (high - low)))
    charging = False
    ch = profile.get("charging")
    if ch and int(ch["byte"]) < len(data):
        value = data[int(ch["byte"])] & hex_int(ch.get("mask", "ff"))
        charging = value == hex_int(ch["equals"]) if "equals" in ch else value != 0
    return percent, charging


def _reply_ok(response: Dict[str, Any], prefix: bytes, data: bytes) -> bool:
    """Prefix check plus optional whole-frame checksum (sum of all bytes mod 256)."""
    if not data.startswith(prefix):
        return False
    if "sum_equals" in response and (sum(data) & 0xFF) != (hex_int(response["sum_equals"]) & 0xFF):
        return False
    return True


def read_with_profile(transport: Any, profile: Dict[str, Any]) -> Optional[Tuple[int, bool]]:
    request, response = profile.get("request"), profile["response"]
    prefix = parse_hex(response["prefix"]) if response.get("prefix") else b""
    timeout = float(response.get("timeout_ms", 600)) / 1000.0

    def send() -> None:
        if request:
            payload = parse_hex(request["hex"])
            (transport.write if request["type"] == "output" else transport.set_feature)(payload)

    if response["type"] == "feature":
        send()
        if profile.get("delay_ms"):
            time.sleep(float(profile["delay_ms"]) / 1000.0)
        data = transport.get_feature(int(response["report_id"]), int(response["length"]))
        return decode_profile_reply(profile, data) if _reply_ok(response, prefix, data) else None

    transport.drain()
    send()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        data = transport.read(remaining)
        if data is None:
            return None
        if _reply_ok(response, prefix, data):
            decoded = decode_profile_reply(profile, data)
            if decoded is not None:
                return decoded


def provider_hid(
    settings: Dict[str, Any],
    profiles: Optional[List[Dict[str, Any]]] = None,
    nodes: Optional[List[HidrawNode]] = None,
    factory: Callable[[str], Any] = HidrawTransport,
) -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "hid", "important": False, "available": True, "ok": True, "count": 0, "error": ""}
    problems: List[str] = []
    if profiles is None:
        profiles, problems = load_profiles()
    if not profiles:
        info["note"] = "no device profiles installed"
        if problems:
            info.update(ok=False, error=problems[0])
        return info, []
    nodes = list_hidraw() if nodes is None else nodes
    devices: List[Device] = []
    seen = set()
    for node in nodes:
        for profile in profiles:
            if not profile_matches(profile, node):
                continue
            dev_id = "hid:%04x:%04x%s" % (node.vendor, node.product, (":" + node.uniq) if node.uniq else "")
            if dev_id in seen:
                break
            try:
                transport = factory(node.path)
            except PermissionError:
                problems.append("no permission for %s - run: %s hid-udev %04x:%04x" % (
                    node.path, APP_ID, node.vendor, node.product))
                break
            except OSError as exc:
                problems.append("%s: %s" % (node.path, exc))
                break
            try:
                reading = read_with_profile(transport, profile)
            except (HidError, OSError, ValueError, KeyError) as exc:
                problems.append("%s: %s" % (node.path, exc))
                reading = None
            finally:
                transport.close()
            if reading is not None:
                percent, charging = reading
                kind = str(profile.get("kind") or kind_from_text(profile["name"]))
                devices.append(Device(
                    id=dev_id, name=str(profile["name"]), kind=kind, percent=percent,
                    state="charging" if charging else "discharging", transport="wireless", source="hid"))
                seen.add(dev_id)
                break  # first profile that answers wins; otherwise try the next candidate
    info["count"] = len(devices)
    if problems and not devices:
        info.update(ok=False, error="; ".join(problems[:2]))
    return info, devices


def udev_rule(vendor: int, product: int) -> str:
    return 'KERNEL=="hidraw*", ATTRS{idVendor}=="%04x", ATTRS{idProduct}=="%04x", TAG+="uaccess"\n' % (vendor, product)


# --------------------------------------------------------------------------
# Provider: OpenRazer (Razer mice, keyboards, headsets) - optional
# --------------------------------------------------------------------------
#
# Uses the official OpenRazer Python client (python3-openrazer) which talks to
# the openrazer-daemon over D-Bus. Nothing happens unless OpenRazer is installed.

def _load_openrazer_manager() -> Any:
    from openrazer.client import DeviceManager  # type: ignore  # noqa: E402
    return DeviceManager()


def provider_openrazer(settings: Dict[str, Any], loader: Optional[Callable[[], Any]] = None) -> Tuple[Dict[str, Any], List[Device]]:
    info: Dict[str, Any] = {"name": "openrazer", "important": False, "available": True, "ok": True, "count": 0, "error": ""}
    try:
        manager = (loader or _load_openrazer_manager)()
    except ImportError:
        info.update(available=False, ok=False, error="OpenRazer is not installed (optional, for Razer devices)")
        return info, []
    except Exception as exc:  # DaemonNotFound, D-Bus errors ...
        info.update(ok=False, error="OpenRazer daemon not reachable: %s" % (str(exc) or exc.__class__.__name__))
        return info, []
    devices: List[Device] = []
    try:
        for dev in list(getattr(manager, "devices", [])):
            try:
                level = dev.battery_level
                charging = bool(dev.is_charging)
                name = str(dev.name)
            except Exception:
                continue  # device without a battery
            if level is None or isinstance(level, bool) or not isinstance(level, (int, float)):
                continue
            kind = {"mouse": "mouse", "keyboard": "keyboard", "headset": "headset", "keypad": "keyboard"}.get(
                str(getattr(dev, "type", "")).lower()) or kind_from_text(name)
            serial = str(getattr(dev, "serial", "") or norm_name(name))
            devices.append(Device(
                id="razer:" + serial.lower(), name=name, kind=kind, percent=max(0, min(100, int(round(level)))),
                state="charging" if charging else "discharging", transport="wireless", source="openrazer"))
    finally:
        closer = getattr(manager, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    info["count"] = len(devices)
    return info, devices


# --------------------------------------------------------------------------
# Provider: demo (fake devices)
# --------------------------------------------------------------------------

def provider_demo() -> Tuple[Dict[str, Any], List[Device]]:
    rows = [
        ("Demo mouse", "mouse", 82, "discharging", "wireless"),
        ("Demo keyboard", "keyboard", 47, "charging", "bluetooth"),
        ("Demo headphones", "headphones", 18, "discharging", "bluetooth"),
        ("Demo controller", "gamepad", 6, "discharging", "bluetooth"),
    ]
    devices = [Device(id="demo:" + kind, name=name, kind=kind, percent=pct, state=state,
                      transport=transport, source="demo") for name, kind, pct, state, transport in rows]
    info = {"name": "demo", "important": False, "available": True, "ok": True, "count": len(devices), "error": ""}
    return info, devices


# --------------------------------------------------------------------------
# Collect + merge
# --------------------------------------------------------------------------

def merge_devices(devices: List[Device]) -> List[Device]:
    """Merge the same physical device reported by several providers."""
    groups: List[List[Device]] = []
    for dev in sorted(devices, key=lambda d: SOURCE_PRIORITY.get(d.source, 99)):
        target = None
        for group in groups:
            for other in group:
                same_id = other.id == dev.id and other.source != dev.source
                same_name = other.source != dev.source and other.id != dev.id and names_match(other.name, dev.name)
                if same_id or same_name:
                    target = group
                    break
            if target is not None:
                break
        if target is not None:
            target.append(dev)
        else:
            groups.append([dev])

    merged: List[Device] = []
    for group in groups:
        primary = next((d for d in group if d.percent is not None), group[0])
        result = Device(**{**primary.__dict__})
        for other in group:
            if result.kind == "other" and other.kind != "other":
                result.kind = other.kind
            if result.state == "unknown" and other.state != "unknown":
                result.state = other.state
            if result.transport == "unknown" and other.transport != "unknown":
                result.transport = other.transport
            if not result.address and other.address:
                result.address = other.address
            if result.id.startswith("name:") and other.id.startswith("bt:"):
                result.id = other.id
        merged.append(result)

    # Two identical devices (same model) in one poll must not share an id.
    seen: Dict[str, int] = {}
    for dev in merged:
        count = seen.get(dev.id, 0) + 1
        seen[dev.id] = count
        if count > 1:
            dev.id = "%s#%d" % (dev.id, count)
    return merged


def sort_devices(devices: List[Device]) -> List[Device]:
    return sorted(devices, key=lambda d: (d.percent is None, d.percent if d.percent is not None else 0, d.name.lower()))


def collect(
    settings: Dict[str, Any],
    runner: Runner = run_cmd,
    which: Callable[[str], Optional[str]] = shutil.which,
    sysfs_root: str = "/sys/class/power_supply",
) -> Tuple[List[Device], List[Dict[str, Any]]]:
    enabled = settings["providers"]
    providers: List[Dict[str, Any]] = []
    found: List[Device] = []

    upower_ok = False
    if enabled.get("upower"):
        info, devs = provider_upower(settings, runner, which)
        providers.append(info)
        found += devs
        upower_ok = info["ok"]
    if enabled.get("bluez"):
        info, devs = provider_bluez(settings, runner, which)
        providers.append(info)
        found += devs
    if enabled.get("headsetcontrol"):
        info, devs = provider_headsetcontrol(settings, runner, which)
        providers.append(info)
        found += devs
    if enabled.get("openrazer"):
        info, devs = provider_openrazer(settings)
        providers.append(info)
        found += devs
    if enabled.get("hid"):
        info, devs = provider_hid(settings)
        providers.append(info)
        found += devs
    if not upower_ok:
        info, devs = provider_sysfs(settings, sysfs_root)
        providers.append(info)
        found += devs
    if settings.get("demo"):
        info, devs = provider_demo()
        providers.append(info)
        found += devs

    hidden = set(settings["hidden"])
    devices = [d for d in merge_devices(found) if d.id not in hidden]
    return sort_devices(devices), providers


# --------------------------------------------------------------------------
# Notification engine (pure logic)
# --------------------------------------------------------------------------

KIND_ICONS = {
    "mouse": "input-mouse", "keyboard": "input-keyboard", "headphones": "audio-headphones",
    "headset": "audio-headset", "gamepad": "input-gaming", "phone": "phone",
    "tablet": "input-tablet", "touchpad": "input-touchpad", "speaker": "audio-speakers",
    "laptop": "computer-laptop", "pen": "input-tablet",
}


def _event(dev: Device, etype: str, threshold: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    percent = dev.percent if dev.percent is not None else 0
    critical = etype == "low" and settings["critical_below"] > 0 and percent <= settings["critical_below"]
    if etype == "charged":
        title = "%s is charged" % dev.name
        body = "Battery reached %d%%. You can unplug it." % percent
    elif critical:
        title = "%s: battery critically low (%d%%)" % (dev.name, percent)
        body = "Charge it now."
    else:
        title = "%s: battery at %d%%" % (dev.name, percent)
        body = "It dropped below %d%%. Plan to charge it soon." % threshold
    return {
        "device_id": dev.id, "name": dev.name, "kind": dev.kind, "percent": percent,
        "type": etype, "threshold": threshold,
        "urgency": "critical" if critical else "normal",
        "title": title, "body": body,
        "icon": KIND_ICONS.get(dev.kind, "battery"),
    }


def evaluate(
    devices: List[Device],
    settings: Dict[str, Any],
    state: Dict[str, Any],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Decide which notifications are due and update ``state`` in place."""
    now = time.time() if now is None else now
    known = state.setdefault("devices", {})
    thresholds: List[int] = settings["thresholds"]
    critical_below = settings["critical_below"]
    charged_at = settings["charged_threshold"]
    hyst = settings["hysteresis"]
    muted = set(settings["muted"])
    events: List[Dict[str, Any]] = []

    for dev in devices:
        percent = dev.percent
        if percent is None:
            continue
        entry = known.get(dev.id)
        first = entry is None
        if first:
            entry = {"fired": [], "charged": False, "last_percent": None}
        prev = entry.get("last_percent")
        charging = dev.state in ("charging", "full") or (
            dev.state == "unknown" and prev is not None and percent > prev)
        fired = set(int(t) for t in entry.get("fired", []))
        charged_flag = bool(entry.get("charged"))
        due: List[Dict[str, Any]] = []

        # Re-arm thresholds once the battery has recovered above them.
        for t in list(fired):
            if percent >= t + hyst or (charging and percent > t):
                fired.discard(t)

        if first:
            # Do not flood on first sight: stay silent about stages that were
            # passed before we ever saw the device, unless it is nearly empty.
            crossed = [t for t in thresholds if percent <= t]
            fired.update(crossed)
            if crossed and critical_below > 0 and percent <= critical_below and not charging:
                due.append(_event(dev, "low", min(crossed), settings))
            charged_flag = charged_at > 0 and percent >= charged_at
        else:
            if not charging:
                newly = [t for t in thresholds if percent <= t and t not in fired]
                if newly:
                    fired.update(newly)
                    # Several stages passed between two polls -> one notification.
                    due.append(_event(dev, "low", min(newly), settings))
            if charged_at > 0:
                if charged_flag and (percent < charged_at - hyst or (dev.state == "discharging" and percent < charged_at)):
                    charged_flag = False
                if not charged_flag and percent >= charged_at and charging:
                    charged_flag = True
                    due.append(_event(dev, "charged", charged_at, settings))

        if dev.id not in muted:
            events += due
        entry.update(fired=sorted(fired, reverse=True), charged=charged_flag,
                     last_percent=percent, last_state=dev.state, last_seen=now)
        known[dev.id] = entry

    # Forget devices not seen for two weeks.
    for key in [k for k, v in known.items() if now - float(v.get("last_seen", now)) > 14 * 86400]:
        del known[key]
    return events


# --------------------------------------------------------------------------
# Notification delivery
# --------------------------------------------------------------------------

def _sound_for(event: Dict[str, Any]) -> str:
    if event["type"] == "charged":
        return "complete"
    return "battery-caution" if event["urgency"] == "critical" else "battery-low"


def send_desktop(event: Dict[str, Any], cfg: Dict[str, Any], runner: Runner, which: Callable[[str], Optional[str]]) -> Tuple[bool, str]:
    sticky = cfg["persistent"] or (event["urgency"] == "critical" and cfg["critical_sticky"])
    if which("notify-send"):
        cmd = ["notify-send", "-a", APP_NAME, "-i", event["icon"], "-u", event["urgency"], "-c", "device"]
        if sticky:
            cmd += ["-t", "0"]
        if cfg["sound"]:
            cmd += ["-h", "string:sound-name:" + _sound_for(event)]
        cmd += [event["title"], event["body"]]
        rc, _, err = runner(cmd)
        return rc == 0, err.strip()
    if which("busctl"):
        hints = [("urgency", "y", "2" if event["urgency"] == "critical" else "1")]
        if cfg["sound"]:
            hints.append(("sound-name", "s", _sound_for(event)))
        cmd = [
            "busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify", "susssasa{sv}i",
            APP_NAME, "0", event["icon"], event["title"], event["body"], "0", str(len(hints)),
        ]
        for key, sig, value in hints:
            cmd += [key, sig, value]
        cmd.append("0" if sticky else "-1")
        rc, _, err = runner(cmd)
        return rc == 0, err.strip()
    return False, "neither notify-send nor busctl found"


def send_ntfy(event: Dict[str, Any], cfg: Dict[str, Any], timeout: float = 6.0) -> Tuple[bool, str]:
    ntfy = cfg["ntfy"]
    if not ntfy["topic"]:
        return False, "ntfy topic is empty"
    if not re.match(r"^https?://", ntfy["server"]):
        return False, "ntfy server must start with http:// or https://"
    payload = {
        "topic": ntfy["topic"],
        "title": event["title"],
        "message": event["body"],
        "priority": 5 if event["urgency"] == "critical" else 3,
        "tags": ["battery" if event["type"] == "charged" else "low_battery"],
    }
    headers = {"Content-Type": "application/json", "User-Agent": "%s/%s" % (APP_ID, __version__)}
    if ntfy["token"]:
        headers["Authorization"] = "Bearer " + ntfy["token"]
    request = urllib.request.Request(
        ntfy["server"], data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)


def deliver(
    event: Dict[str, Any],
    settings: Dict[str, Any],
    runner: Runner = run_cmd,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, Any]:
    cfg = settings["notify"]
    result: Dict[str, Any] = {}
    if cfg["desktop"]:
        ok, err = send_desktop(event, cfg, runner, which)
        result["desktop"] = {"ok": ok, "error": err}
    if cfg["ntfy"]["enabled"]:
        ok, err = send_ntfy(event, cfg)
        result["ntfy"] = {"ok": ok, "error": err}
    return result


# --------------------------------------------------------------------------
# Persistent state (shared by every widget instance and the daemon)
# --------------------------------------------------------------------------

def load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("devices"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "devices": {}}


def save_state(path: str, state: Dict[str, Any]) -> None:
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked_state(path: str) -> Iterator[Dict[str, Any]]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = load_state(path)
            yield state
            save_state(path, state)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def notify_cycle(
    devices: List[Device],
    settings: Dict[str, Any],
    path: Optional[str] = None,
    runner: Runner = run_cmd,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> List[Dict[str, Any]]:
    """Evaluate + deliver under a file lock so parallel instances never double-notify."""
    sent: List[Dict[str, Any]] = []
    devices = [d for d in devices if d.source != "demo"]
    with locked_state(path or state_path()) as state:
        for event in evaluate(devices, settings, state):
            event["delivery"] = deliver(event, settings, runner, which)
            sent.append(event)
    return sent


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def format_table(devices: List[Device]) -> str:
    rows = [("NAME", "TYPE", "BATTERY", "STATE", "VIA")]
    for dev in devices:
        level = "n/a" if dev.percent is None else ("~" if dev.approx else "") + "%d%%" % dev.percent
        rows.append((dev.name, dev.kind, level, dev.state, "%s/%s" % (dev.transport, dev.source)))
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _settings_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return load_effective_settings(
        use_file=not getattr(args, "no_config", False),
        override_json=getattr(args, "settings_json", None),
        path=getattr(args, "config", None),
    )


def cmd_list(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    devices, providers = collect(settings)
    if args.json:
        print(json.dumps({"devices": [d.public() for d in devices], "providers": providers}, indent=2))
    elif devices:
        print(format_table(devices))
    else:
        print("No peripherals with battery information found. Try: %s doctor" % APP_ID)
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    try:
        settings = _settings_from_args(args)
    except ValueError as exc:
        print(json.dumps({"version": 1, "error": str(exc)}))
        return 2
    devices, providers = collect(settings)
    result: Dict[str, Any] = {
        "version": 1, "timestamp": int(time.time()),
        "devices": [d.public() for d in devices], "providers": providers,
    }
    if args.notify and settings["notify"]["enabled"]:
        try:
            result["notifications"] = notify_cycle(devices, settings)
        except OSError as exc:
            result["notify_error"] = str(exc)
    print(json.dumps(result))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    print("%s %s watching (Ctrl+C to stop)" % (APP_NAME, __version__), file=sys.stderr, flush=True)
    while True:
        interval = 60
        try:
            settings = _settings_from_args(args)  # re-read each round: edits apply live
            interval = args.interval or settings["poll_seconds"]
            devices, _ = collect(settings)
            if settings["notify"]["enabled"]:
                for event in notify_cycle(devices, settings):
                    print("notified: %s" % event["title"], file=sys.stderr, flush=True)
        except (ValueError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr, flush=True)
        try:
            time.sleep(max(10, interval))
        except KeyboardInterrupt:
            return 0


def _pango(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cmd_waybar(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    devices, _ = collect(settings)
    devices = [d for d in devices if d.percent is not None]
    if not devices:
        print(json.dumps({"text": "", "tooltip": "No peripherals found", "class": "empty"}))
        return 0
    lowest = devices[0]
    crit = settings["critical_below"]
    css = "critical" if crit and lowest.percent <= crit else ("low" if lowest.percent <= 25 else "normal")
    if lowest.charging:
        css = "charging"
    tooltip = "\n".join(
        "%s: %s%d%%%s" % (_pango(d.name), "~" if d.approx else "", d.percent, " (charging)" if d.charging else "")
        for d in devices)
    print(json.dumps({
        "text": "%d%%" % lowest.percent, "alt": lowest.kind, "class": css,
        "percentage": lowest.percent, "tooltip": tooltip,
    }))
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    dev = Device(id="test", name="Test device", kind=args.kind, percent=args.level, state="discharging", source="test")
    etype = "charged" if args.charged else "low"
    event = _event(dev, etype, args.level, settings)
    if args.critical:
        event.update(urgency="critical")
    result = deliver(event, settings)
    print(json.dumps(result, indent=2))
    if not result:
        print("No notification channel is enabled.", file=sys.stderr)
        return 1
    return 0 if all(r["ok"] for r in result.values()) else 1


def cmd_config(args: argparse.Namespace) -> int:
    path = args.config or config_path()
    if args.action == "path":
        print(path)
    elif args.action == "show":
        print(json.dumps(load_effective_settings(path=path), indent=2))
    elif args.action == "init":
        if os.path.exists(path) and not args.force:
            print("%s already exists (use --force to overwrite)" % path, file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(DEFAULT_SETTINGS, handle, indent=2)
            handle.write("\n")
        print("Wrote %s" % path)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    def row(label: str, ok: bool, detail: str = "") -> None:
        print("  [%s] %-16s %s" % ("ok" if ok else "--", label, detail))

    print("%s %s - diagnostics" % (APP_NAME, __version__))
    print("Python %s" % sys.version.split()[0])
    print("Desktop: %s" % (os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"))
    print("Tools:")
    for tool, note in [("upower", "main data source"), ("busctl", "BlueZ battery via D-Bus"),
                       ("bluetoothctl", "BlueZ fallback"), ("notify-send", "desktop notifications"),
                       ("headsetcontrol", "optional: USB / 2.4 GHz headsets")]:
        path = shutil.which(tool)
        row(tool, bool(path), path or "not found - %s" % note)
    print("Files:")
    row("config", os.path.exists(config_path()), config_path())
    row("state", os.path.exists(state_path()), state_path())
    try:
        settings = _settings_from_args(args)
    except ValueError as exc:
        print("Config error: %s" % exc)
        return 1
    devices, providers = collect(settings)
    print("Providers:")
    for p in providers:
        detail = "%d device(s)" % p["count"] if p["ok"] else p["error"]
        row(p["name"], p["ok"], detail)
    profiles, bad = load_profiles()
    row("hid profiles", True, "%d loaded%s" % (len(profiles), "" if not bad else ", %d invalid: %s" % (len(bad), bad[0])))
    print("Devices with battery info: %d" % len(devices))
    if devices:
        print(format_table(devices))
    else:
        print("\nNothing found. Checklist:\n"
              "  * Is the device connected right now (not only paired)?\n"
              "  * Is the upower service running?  systemctl status upower\n"
              "  * Is Bluetooth running?           systemctl status bluetooth\n"
              "  * Some headsets only report a battery with 'Experimental = true' in /etc/bluetooth/main.conf\n"
              "  * Logitech receivers: UPower needs a recent kernel (hid-logitech-hidpp) or Solaar.")
    return 0


def _listen(transport: Any, seconds: float) -> None:
    start = time.monotonic()
    previous: Dict[Tuple[int, int], bytes] = {}
    try:
        while time.monotonic() - start < seconds:
            data = transport.read(min(0.5, seconds - (time.monotonic() - start)))
            if not data:
                continue
            key = (data[0], len(data))
            note = ""
            if key in previous:
                changed = [i for i, (a, b) in enumerate(zip(previous[key], data)) if a != b]
                note = "   changed bytes: %s" % changed if changed else ""
            previous[key] = data
            print("%7.2fs  %s%s" % (time.monotonic() - start, format_hex(data), note), flush=True)
    except KeyboardInterrupt:
        pass


def cmd_hid_probe(args: argparse.Namespace) -> int:
    if not args.device:
        nodes = list_hidraw()
        if not nodes:
            print("No /dev/hidraw devices found.")
            return 0
        profiles, _ = load_profiles()
        for node in nodes:
            access = "ok" if os.access(node.path, os.R_OK | os.W_OK) else "NO PERMISSION (see: %s hid-udev %04x:%04x)" % (
                APP_ID, node.vendor, node.product)
            matched = [p["name"] for p in profiles if profile_matches(p, node)]
            print("%s  %04x:%04x  interface %s  %r  access: %s%s" % (
                node.path, node.vendor, node.product, "?" if node.interface is None else node.interface,
                node.name, access, "  PROFILE: " + ", ".join(matched) if matched else ""))
            for col in node.collections or []:
                print("    usage page 0x%04x, usage 0x%04x%s" % (
                    col["usage_page"], col["usage"], "   <- vendor-defined (often the battery/config channel)"
                    if col["usage_page"] >= 0xFF00 else ""))
            for kind, sizes in (node.reports or {}).items():
                if sizes:
                    print("    %-7s reports (id: bytes): %s" % (kind, ", ".join("%d: %d" % kv for kv in sorted(sizes.items()))))
        print("\nNext: %s hid-probe --device /dev/hidrawN --listen 30   (move the mouse, plug/unplug the charging cable)" % APP_ID)
        return 0

    try:
        transport = HidrawTransport(args.device)
    except OSError as exc:
        print("Cannot open %s: %s" % (args.device, exc), file=sys.stderr)
        print("Fix permissions with: %s hid-udev VENDOR:PRODUCT" % APP_ID, file=sys.stderr)
        return 1
    try:
        if args.send:
            if not args.yes:
                print("Sending unknown data to a device can change its settings. Only send bytes you captured from the\n"
                      "vendor's own tool for THIS device. Repeat with --yes to proceed.", file=sys.stderr)
                return 1
            payload = parse_hex(args.send)
            (transport.write if args.type == "output" else transport.set_feature)(payload)
            print("sent %s (%s report)" % (format_hex(payload), args.type))
            _listen(transport, args.listen or 2.0)
        elif args.feature_id is not None:
            if not args.length:
                print("--length is required with --feature-id", file=sys.stderr)
                return 2
            print(format_hex(transport.get_feature(args.feature_id, args.length)))
        else:
            _listen(transport, args.listen or 20.0)
    except (HidError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    finally:
        transport.close()
    return 0


def cmd_hid_udev(args: argparse.Namespace) -> int:
    match = re.fullmatch(r"\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*", args.ids)
    if not match:
        print("expected VENDOR:PRODUCT as shown by lsusb, e.g. 3554:f5f7", file=sys.stderr)
        return 2
    vendor, product = int(match.group(1), 16), int(match.group(2), 16)
    name = "70-%s-%04x-%04x.rules" % (APP_ID, vendor, product)
    print("# Lets your logged-in user talk to this one device (no root needed afterwards).")
    print("# Run these commands once:\n")
    print("sudo tee /etc/udev/rules.d/%s >/dev/null <<'EOF'\n%sEOF" % (name, udev_rule(vendor, product)))
    print("sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw")
    print("# then unplug and re-plug the dongle")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_ID, description="Battery levels of wireless peripherals, with alerts.")
    parser.add_argument("--version", action="version", version="%s %s" % (APP_ID, __version__))
    sub = parser.add_subparsers(dest="command", metavar="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="path to config.json (default: %s)" % config_path())
        p.add_argument("--no-config", action="store_true", help="ignore the config file")
        p.add_argument("--settings-json", help="JSON settings that override the config file")

    p = sub.add_parser("list", help="show devices")
    common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("poll", help="print JSON (used by the Plasma widget)")
    common(p)
    p.add_argument("--notify", action="store_true", help="also send due notifications")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("watch", help="run forever and send notifications (systemd service)")
    common(p)
    p.add_argument("--interval", type=int, help="seconds between polls (default: from config)")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("waybar", help="one JSON line for Waybar custom modules")
    common(p)
    p.set_defaults(func=cmd_waybar)

    p = sub.add_parser("test-notify", help="send a test notification with the current settings")
    common(p)
    p.add_argument("--level", type=int, default=10)
    p.add_argument("--kind", default="mouse")
    p.add_argument("--critical", action="store_true")
    p.add_argument("--charged", action="store_true", help="test the 'charged' message")
    p.set_defaults(func=cmd_test_notify)

    p = sub.add_parser("config", help="path | show | init the config file")
    p.add_argument("action", choices=["path", "show", "init"])
    p.add_argument("--config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("hid-probe", help="find / watch the battery report of a vendor-specific dongle")
    p.add_argument("--device", help="/dev/hidrawN to inspect (omit to list all)")
    p.add_argument("--listen", type=float, help="print incoming reports for this many seconds")
    p.add_argument("--feature-id", type=int, help="read this feature report id")
    p.add_argument("--length", type=int, help="feature report length in bytes (incl. report id)")
    p.add_argument("--send", help="hex bytes to send, e.g. '08 04 00' (advanced)")
    p.add_argument("--type", choices=["output", "feature"], default="output")
    p.add_argument("--yes", action="store_true", help="confirm --send")
    p.set_defaults(func=cmd_hid_probe)

    p = sub.add_parser("hid-udev", help="print the udev rule that gives you access to a dongle")
    p.add_argument("ids", help="VENDOR:PRODUCT from lsusb, e.g. 3554:f5f7")
    p.set_defaults(func=cmd_hid_udev)

    p = sub.add_parser("doctor", help="diagnose why a device does not show up")
    common(p)
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())

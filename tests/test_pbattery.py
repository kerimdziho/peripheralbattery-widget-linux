import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plasmoid", "contents", "code"))

import pbattery as pb  # noqa: E402


_TMP = None
_ORIG_RAZER = pb._load_openrazer_manager


def setUpModule():
    """Hermetic: never touch the real config/state/profile folders or a real OpenRazer daemon."""
    global _TMP
    _TMP = tempfile.TemporaryDirectory()
    os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP.name, "config")
    os.environ["XDG_STATE_HOME"] = os.path.join(_TMP.name, "state")

    class NoRazerDevices:
        devices = []

    pb._load_openrazer_manager = lambda: NoRazerDevices()


def tearDownModule():
    pb._load_openrazer_manager = _ORIG_RAZER
    _TMP.cleanup()


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as handle:
        return handle.read()


def dev(id="bt:aa", name="Mouse", percent=80, state="discharging", kind="mouse"):
    return pb.Device(id=id, name=name, kind=kind, percent=percent, state=state, source="upower")


def settings(**over):
    return pb.normalize_settings(over)


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = settings()
        self.assertEqual(s["thresholds"], [75, 50, 25, 10, 5])
        self.assertEqual(s["charged_threshold"], 100)
        self.assertTrue(s["notify"]["desktop"])

    def test_threshold_parsing(self):
        self.assertEqual(pb.parse_thresholds("75, 50;25 10%  10 0 150 x"), [75, 50, 25, 10])
        self.assertEqual(pb.parse_thresholds([100, 30, "20"]), [30, 20])  # 100 is the "charged" stage
        self.assertEqual(pb.parse_thresholds(None), [])

    def test_clamping_and_garbage(self):
        s = settings(poll_seconds=1, hysteresis=999, critical_below="abc", notify={"ntfy": {"server": "https://x.org/"}})
        self.assertEqual(s["poll_seconds"], 10)
        self.assertEqual(s["hysteresis"], 20)
        self.assertEqual(s["critical_below"], 10)
        self.assertEqual(s["notify"]["ntfy"]["server"], "https://x.org")

    def test_override_json(self):
        s = pb.load_effective_settings(use_file=False, override_json='{"thresholds": [40], "notify": {"sound": true}}')
        self.assertEqual(s["thresholds"], [40])
        self.assertTrue(s["notify"]["sound"])
        self.assertTrue(s["notify"]["desktop"])  # untouched default survives deep merge
        with self.assertRaises(ValueError):
            pb.load_effective_settings(use_file=False, override_json="{nope")


class ParserTests(unittest.TestCase):
    def test_upower(self):
        devices = {d.name: d for d in pb.parse_upower_dump(fixture("upower_dump.txt"))}
        self.assertEqual(set(devices), {
            "Logitech MX Master 3", "Keychron K2", "WH-1000XM4", "Wireless Controller", "Wireless Presenter"})
        mx = devices["Logitech MX Master 3"]
        self.assertEqual((mx.kind, mx.percent, mx.state, mx.transport), ("mouse", 80, "discharging", "wireless"))
        kb = devices["Keychron K2"]
        self.assertEqual((kb.kind, kb.percent, kb.state, kb.transport, kb.id), (
            "keyboard", 45, "charging", "bluetooth", "bt:aa:bb:cc:dd:ee:01"))
        self.assertEqual(devices["Wireless Controller"].kind, "gamepad")
        self.assertEqual(devices["Wireless Controller"].address, "A0:B1:C2:D3:E4:F5")
        presenter = devices["Wireless Presenter"]
        self.assertTrue(presenter.approx)
        self.assertEqual(presenter.percent, 25)

    def test_upower_system_battery_optional(self):
        names = [d.name for d in pb.parse_upower_dump(fixture("upower_dump.txt"), include_system=True)]
        self.assertIn("System battery", names)
        self.assertEqual(names.count("System battery"), 1)  # DisplayDevice is not duplicated
        self.assertNotIn("Back-UPS", names)

    def test_bluez_json(self):
        devices = pb.parse_bluez_managed_objects(json.loads(fixture("bluez_managed_objects.json")))
        self.assertEqual(sorted((d.name, d.percent, d.kind) for d in devices), [
            ("Keychron K2", 45, "keyboard"), ("WH-1000XM4", 20, "headset")])

    def test_bluetoothctl_info(self):
        info = pb.parse_bluetoothctl_info(fixture("bluetoothctl_info.txt"))
        self.assertEqual((info["address"], info["percent"], info["connected"], info["icon"]),
                         ("AA:BB:CC:DD:EE:01", 45, True, "input-keyboard"))

    def test_headsetcontrol(self):
        devices = pb.parse_headsetcontrol_json(fixture("headsetcontrol.json"))
        self.assertEqual(len(devices), 1)  # the unavailable one is skipped
        self.assertEqual((devices[0].name, devices[0].percent, devices[0].state), ("Arctis Nova 7", 64, "charging"))

    def test_headsetcontrol_charging_without_level_is_skipped(self):
        text = '{"devices":[{"device":"X","battery":{"status":"BATTERY_CHARGING","level":-1}}]}'
        self.assertEqual(pb.parse_headsetcontrol_json(text), [])

    def test_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            for name, files in {
                "hid-aa:bb:cc:dd:ee:ff-battery": {"type": "Battery", "scope": "Device", "capacity": "70",
                                                   "status": "Discharging", "model_name": "Cool Mouse"},
                "BAT0": {"type": "Battery", "capacity": "50", "status": "Charging"},
                "AC": {"type": "Mains"},
            }.items():
                os.makedirs(os.path.join(root, name))
                for key, value in files.items():
                    with open(os.path.join(root, name, key), "w") as handle:
                        handle.write(value + "\n")
            _, devs = pb.provider_sysfs({"include_system_battery": False}, root)
            self.assertEqual([(d.name, d.percent, d.kind) for d in devs], [("Cool Mouse", 70, "mouse")])
            _, devs = pb.provider_sysfs({"include_system_battery": True}, root)
            self.assertEqual(len(devs), 2)


class MergeTests(unittest.TestCase):
    def test_same_mac_from_two_sources(self):
        up = pb.parse_upower_dump(fixture("upower_dump.txt"))
        bz = pb.parse_bluez_managed_objects(json.loads(fixture("bluez_managed_objects.json")))
        merged = pb.merge_devices(up + bz)
        names = sorted(d.name for d in merged)
        self.assertEqual(names.count("Keychron K2"), 1)
        self.assertEqual(names.count("WH-1000XM4"), 1)
        headset = next(d for d in merged if d.name == "WH-1000XM4")
        self.assertEqual(headset.source, "upower")

    def test_similar_name_without_mac(self):
        a = pb.Device(id="name:a:headset", name="Arctis Nova 7", kind="headset", percent=60, source="upower")
        b = pb.Device(id="name:b:headset", name="SteelSeries Arctis Nova 7", kind="headset", percent=64,
                      state="charging", source="headsetcontrol")
        merged = pb.merge_devices([b, a])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].percent, 60)
        self.assertEqual(merged[0].source, "upower")

    def test_identical_devices_get_distinct_ids(self):
        a = pb.Device(id="name:m:mouse", name="M", percent=1, source="upower")
        b = pb.Device(id="name:m:mouse", name="M", percent=2, source="upower")
        self.assertEqual(len({d.id for d in pb.merge_devices([a, b])}), 2)


class CollectTests(unittest.TestCase):
    def test_collect_with_fake_runner(self):
        outputs = {
            "upower": (0, fixture("upower_dump.txt"), ""),
            "busctl": (0, fixture("bluez_managed_objects.json"), ""),
            "headsetcontrol": (0, fixture("headsetcontrol.json"), ""),
        }

        def runner(cmd, timeout=6.0):
            return outputs.get(cmd[0], (127, "", "not found"))

        devices, providers = pb.collect(settings(), runner=runner, which=lambda c: "/usr/bin/" + c)
        self.assertEqual([p["name"] for p in providers], ["upower", "bluez", "headsetcontrol", "openrazer", "hid"])
        self.assertTrue(all(p["ok"] for p in providers))
        self.assertEqual(len(devices), 6)
        self.assertEqual(devices[0].percent, 5)  # sorted lowest first

    def test_upower_missing_falls_back_to_sysfs(self):
        def runner(cmd, timeout=6.0):
            return 127, "", "not found"

        devices, providers = pb.collect(settings(), runner=runner, which=lambda c: None, sysfs_root="/nonexistent")
        self.assertEqual(devices, [])
        by_name = {p["name"]: p for p in providers}
        self.assertFalse(by_name["upower"]["available"])
        self.assertIn("sysfs", by_name)

    def test_hidden_devices_removed(self):
        def runner(cmd, timeout=6.0):
            return (0, fixture("upower_dump.txt"), "") if cmd[0] == "upower" else (1, "", "x")

        devices, _ = pb.collect(settings(hidden=["bt:aa:bb:cc:dd:ee:01"], providers={"bluez": False, "headsetcontrol": False}),
                                runner=runner, which=lambda c: "/x/" + c)
        self.assertNotIn("Keychron K2", [d.name for d in devices])

    def test_bluetoothctl_fallback(self):
        def runner(cmd, timeout=6.0):
            if cmd[:2] == ["bluetoothctl", "devices"]:
                return 0, "Device AA:BB:CC:DD:EE:01 Keychron K2\n", ""
            if cmd[:2] == ["bluetoothctl", "info"]:
                return 0, fixture("bluetoothctl_info.txt"), ""
            return 1, "", "boom"

        info, devs = pb.provider_bluez({}, runner, lambda c: "/x/bluetoothctl" if c == "bluetoothctl" else None)
        self.assertTrue(info["ok"])
        self.assertEqual([(d.name, d.percent) for d in devs], [("Keychron K2", 45)])


class NoBluetoothTests(unittest.TestCase):
    def test_no_adapter_is_not_an_error_and_skips_slow_fallback(self):
        calls = []

        def runner(cmd, timeout=6.0):
            calls.append(cmd[0])
            if cmd[0] == "busctl":
                return 1, "", "Call failed: The name org.bluez was not provided by any .service files"
            raise AssertionError("bluetoothctl must not be called when bluetoothd is absent")

        info, devs = pb.provider_bluez({}, runner, lambda c: "/usr/bin/" + c)
        self.assertTrue(info["ok"])
        self.assertEqual((devs, info["error"]), ([], ""))
        self.assertEqual(calls, ["busctl"])


class DemoTests(unittest.TestCase):
    def test_demo_devices_shown_but_never_notify(self):
        def runner(cmd, timeout=6.0):
            return 1, "", "x"

        devices, providers = pb.collect(settings(demo=True), runner=runner, which=lambda c: None, sysfs_root="/nonexistent")
        self.assertEqual(sorted(d.name for d in devices), ["Demo controller", "Demo headphones", "Demo keyboard", "Demo mouse"])
        self.assertIn("demo", [p["name"] for p in providers])
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            pb.notify_cycle(devices, settings(), os.path.join(tmp, "s.json"), lambda c, t=6.0: sent.append(c) or (0, "", ""), lambda c: "/x/" + c)
        self.assertEqual(sent, [])  # the 6% demo controller would otherwise raise a critical alert

    def test_demo_off_by_default(self):
        self.assertFalse(settings()["demo"])


MOUSE_DESC = bytes.fromhex("05010902a1010901a100050919012903150025019503750181029501750581030501093009311581257f750895028106c0c0")
VENDOR_DESC = bytes.fromhex("06 00 ff 09 01 a1 01 85 08 09 02 15 00 26 ff 00 75 08 95 20 81 02 09 03 91 02 09 04 b1 02 c0")

PROFILE = {
    "name": "Test Mouse", "kind": "mouse",
    "match": {"vendor": "3554", "product": "f5f7", "usage_page": "ff00"},
    "request": {"type": "output", "hex": "08 04 00"},
    "response": {"type": "input", "prefix": "08 04", "timeout_ms": 200},
    "percent": {"byte": 3, "mask": "7f", "min": 0, "max": 100},
    "charging": {"byte": 3, "mask": "80"},
}


class FakeTransport:
    def __init__(self, replies=(), feature=b""):
        self.replies, self.feature, self.sent, self.closed = list(replies), feature, [], False

    def drain(self):
        pass

    def write(self, data):
        self.sent.append(("output", data))

    def set_feature(self, data):
        self.sent.append(("feature", data))

    def read(self, timeout):
        return self.replies.pop(0) if self.replies else None

    def get_feature(self, report_id, length):
        return self.feature

    def close(self):
        self.closed = True


class HidTests(unittest.TestCase):
    def test_hex_helpers(self):
        self.assertEqual(pb.parse_hex("08 04 0x1f"), b"\x08\x04\x1f")
        self.assertEqual(pb.parse_hex("0804"), b"\x08\x04")
        with self.assertRaises(ValueError):
            pb.parse_hex("08 zz")
        self.assertEqual((pb.hex_int("7f"), pb.hex_int("0x7f"), pb.hex_int(127)), (127, 127, 127))

    def test_descriptor_parser(self):
        mouse = pb.parse_report_descriptor(MOUSE_DESC)
        self.assertEqual(mouse["collections"], [{"usage_page": 1, "usage": 2}])
        self.assertEqual(mouse["reports"]["input"], {0: 3})
        vendor = pb.parse_report_descriptor(VENDOR_DESC)
        self.assertEqual(vendor["collections"], [{"usage_page": 0xFF00, "usage": 1}])
        self.assertEqual((vendor["reports"]["input"], vendor["reports"]["output"], vendor["reports"]["feature"]),
                         ({8: 32}, {8: 32}, {8: 32}))

    def test_profile_validation(self):
        self.assertEqual(pb.validate_profile(PROFILE), [])
        self.assertEqual(pb.validate_profile(json.loads(open(os.path.join(
            HERE, "..", "plasmoid", "contents", "code", "profiles", "_example.json")).read())), [])
        bad = dict(PROFILE, percent={"byte": 3, "min": 50, "max": 10}, response={"type": "weird"})
        problems = " | ".join(pb.validate_profile(bad))
        self.assertIn("response.type", problems)
        self.assertIn("percent.min/max", problems)
        self.assertTrue(pb.validate_profile("nope"))
        self.assertTrue(pb.validate_profile(dict(PROFILE, match={"vendor": "xyz", "product": "1"})))

    def test_decode(self):
        self.assertEqual(pb.decode_profile_reply(PROFILE, bytes([8, 4, 0, 0x55])), (85, False))
        self.assertEqual(pb.decode_profile_reply(PROFILE, bytes([8, 4, 0, 0x80 | 40])), (40, True))
        self.assertIsNone(pb.decode_profile_reply(PROFILE, bytes([8, 4])))  # too short
        scaled = dict(PROFILE, percent={"byte": 0, "min": 1, "max": 5})  # 5-step devices
        self.assertEqual(pb.decode_profile_reply(scaled, bytes([3])), (50, False))
        self.assertIsNone(pb.decode_profile_reply(scaled, bytes([9])))  # outside min..max = not a battery report

    def test_read_ignores_noise_and_filters_prefix(self):
        transport = FakeTransport([bytes([1, 2, 3, 4]), bytes([8, 9, 0, 70]), bytes([8, 4, 0, 62])])
        self.assertEqual(pb.read_with_profile(transport, PROFILE), (62, False))
        self.assertEqual(transport.sent, [("output", b"\x08\x04\x00")])

    def test_read_timeout_means_asleep(self):
        self.assertIsNone(pb.read_with_profile(FakeTransport([]), PROFILE))

    def test_feature_mode(self):
        profile = dict(PROFILE, request={"type": "feature", "hex": "05 01"},
                       response={"type": "feature", "report_id": 5, "length": 4, "prefix": "05"})
        transport = FakeTransport(feature=bytes([5, 0, 0, 0x80 | 12]))
        self.assertEqual(pb.read_with_profile(transport, profile), (12, True))
        self.assertEqual(transport.sent[0][0], "feature")

    def test_sysfs_discovery(self):
        with tempfile.TemporaryDirectory() as root:
            hid = os.path.join(root, "sys", "devices", "usb1", "1-3", "1-3:1.1", "0003:3554:F5F7.0005")
            os.makedirs(hid)
            with open(os.path.join(hid, "uevent"), "w") as handle:
                handle.write("HID_ID=0003:00003554:0000F5F7\nHID_NAME=Compx 2.4G Receiver\nHID_UNIQ=\n")
            with open(os.path.join(hid, "report_descriptor"), "wb") as handle:
                handle.write(VENDOR_DESC)
            with open(os.path.join(os.path.dirname(hid), "bInterfaceNumber"), "w") as handle:
                handle.write("01\n")
            cls = os.path.join(root, "class", "hidraw", "hidraw3")
            os.makedirs(cls)
            os.symlink(hid, os.path.join(cls, "device"))
            nodes = pb.list_hidraw(os.path.join(root, "class", "hidraw"), "/dev")
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual((node.path, node.vendor, node.product, node.interface, node.name),
                         ("/dev/hidraw3", 0x3554, 0xF5F7, 1, "Compx 2.4G Receiver"))
        self.assertTrue(pb.profile_matches(PROFILE, node))
        self.assertFalse(pb.profile_matches(dict(PROFILE, match={"vendor": "3554", "product": "f5f7", "usage_page": "0001"}), node))
        self.assertFalse(pb.profile_matches(dict(PROFILE, match={"vendor": "3554", "product": "0000"}), node))

    def _node(self, **kw):
        base = dict(path="/dev/hidraw3", vendor=0x3554, product=0xF5F7, name="x", interface=1,
                    collections=[{"usage_page": 0xFF00, "usage": 1}])
        base.update(kw)
        return pb.HidrawNode(**base)

    def test_provider_reads_device(self):
        fake = FakeTransport([bytes([8, 4, 0, 0x80 | 33])])
        info, devs = pb.provider_hid({}, profiles=[PROFILE], nodes=[self._node(), self._node(path="/dev/hidraw4")],
                                     factory=lambda path: fake)
        self.assertTrue(info["ok"])
        self.assertEqual([(d.name, d.percent, d.state, d.kind, d.source) for d in devs],
                         [("Test Mouse", 33, "charging", "mouse", "hid")])  # second node of same dongle is skipped
        self.assertTrue(fake.closed)

    def test_provider_permission_denied_has_actionable_hint(self):
        def deny(path):
            raise PermissionError()

        info, devs = pb.provider_hid({}, profiles=[PROFILE], nodes=[self._node()], factory=deny)
        self.assertEqual(devs, [])
        self.assertFalse(info["ok"])
        self.assertIn("hid-udev 3554:f5f7", info["error"])

    def test_provider_silent_without_profiles(self):
        info, devs = pb.provider_hid({}, profiles=[], nodes=[self._node()])
        self.assertTrue(info["ok"])
        self.assertEqual(devs, [])

    def test_asleep_device_is_not_listed(self):
        _, devs = pb.provider_hid({}, profiles=[PROFILE], nodes=[self._node()], factory=lambda p: FakeTransport([]))
        self.assertEqual(devs, [])

    def test_load_profiles_skips_examples_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, body in {"_ex.json": PROFILE, "good.json": PROFILE, "bad.json": {"name": "x"}}.items():
                with open(os.path.join(folder, name), "w") as handle:
                    json.dump(body, handle)
            with open(os.path.join(folder, "broken.json"), "w") as handle:
                handle.write("{")
            profiles, problems = pb.load_profiles([folder])
        self.assertEqual(len(profiles), 1)
        self.assertEqual(len(problems), 2)

    def test_udev_rule(self):
        self.assertEqual(pb.udev_rule(0x3554, 0xF5F7),
                         'KERNEL=="hidraw*", ATTRS{idVendor}=="3554", ATTRS{idProduct}=="f5f7", TAG+="uaccess"\n')

    def test_collect_includes_hid_device(self):
        def runner(cmd, timeout=6.0):
            return 1, "", "x"

        orig = pb.provider_hid
        pb.provider_hid = lambda s: orig(s, profiles=[PROFILE], nodes=[self._node()],
                                         factory=lambda p: FakeTransport([bytes([8, 4, 0, 75])]))
        try:
            devices, providers = pb.collect(settings(providers={"bluez": False, "headsetcontrol": False}),
                                            runner=runner, which=lambda c: None, sysfs_root="/nonexistent")
        finally:
            pb.provider_hid = orig
        self.assertEqual([(d.name, d.percent) for d in devices], [("Test Mouse", 75)])
        self.assertIn("hid", [p["name"] for p in providers])


class FakeRazerDevice:
    def __init__(self, name, level, charging=False, type="mouse", serial="PM1"):
        self.name, self._level, self.is_charging, self.type, self.serial = name, level, charging, type, serial

    @property
    def battery_level(self):
        if self._level is None:
            raise AttributeError("no battery")
        return self._level


class OpenRazerTests(unittest.TestCase):
    def manager(self, *devices):
        class Manager:
            closed = False

            def __init__(self):
                self.devices = list(devices)

            def close(self):
                Manager.closed = True

        return Manager

    def test_reads_battery_devices_only(self):
        M = self.manager(FakeRazerDevice("Razer Viper Ultimate", 87.0, True, "mouse", "PM123"),
                         FakeRazerDevice("Razer BlackWidow", None, False, "keyboard", "KB1"),
                         FakeRazerDevice("Razer Kraken", 40, False, "headset", "KR1"))
        info, devs = pb.provider_openrazer({}, loader=lambda: M())
        self.assertTrue(info["ok"])
        self.assertEqual([(d.name, d.percent, d.state, d.kind, d.id) for d in devs], [
            ("Razer Viper Ultimate", 87, "charging", "mouse", "razer:pm123"),
            ("Razer Kraken", 40, "discharging", "headset", "razer:kr1")])
        self.assertTrue(M.closed)

    def test_not_installed_is_quiet(self):
        def missing():
            raise ImportError("No module named openrazer")

        info, devs = pb.provider_openrazer({}, loader=missing)
        self.assertEqual((devs, info["available"], info["important"]), ([], False, False))

    def test_daemon_down_reports_reason(self):
        def down():
            raise RuntimeError("Could not connect to daemon")

        info, devs = pb.provider_openrazer({}, loader=down)
        self.assertFalse(info["ok"])
        self.assertIn("daemon", info["error"])


class CompxProfileTests(unittest.TestCase):
    """The 17-byte Compx framing: report 08, command 04 = GetPower, frame sums to 0x55."""

    REQUEST = "08 04 " + "00 " * 14 + "49"
    CHIMERA = {
        "name": "Compx mouse", "kind": "mouse",
        "match": {"vendor": "3554", "product": "f512", "usage_page": "ff02"},
        "request": {"type": "output", "hex": REQUEST},
        "response": {"type": "input", "prefix": "08 04", "timeout_ms": 200, "sum_equals": "55"},
        "percent": {"byte": 6, "min": 0, "max": 100}, "charging": {"byte": 7},
    }

    def test_request_checksum_is_valid(self):
        self.assertEqual(sum(pb.parse_hex(self.REQUEST)) & 0xFF, 0x55)
        self.assertEqual(pb.validate_profile(self.CHIMERA), [])

    def test_real_capture_decodes(self):
        # capture documented for a Compx receiver: mouse at 95 %, 4084 mV, not charging
        frame = pb.parse_hex("08 04 00 00 00 02 5f 00 0f f4 00 00 00 00 00 00 e5")
        self.assertEqual(sum(frame) & 0xFF, 0x55)
        self.assertEqual(pb.decode_profile_reply(self.CHIMERA, frame), (95, False))

    def test_bad_checksum_and_noise_are_ignored(self):
        good = pb.parse_hex("08 04 00 00 00 02 5f 00 0f f4 00 00 00 00 00 00 e5")
        corrupt = bytes(good[:-1]) + bytes([0x00])
        transport = FakeTransport([corrupt, good])
        self.assertEqual(pb.read_with_profile(transport, self.CHIMERA), (95, False))
        self.assertIsNone(pb.read_with_profile(FakeTransport([corrupt]), self.CHIMERA))

    def test_second_candidate_profile_is_tried(self):
        wrong = dict(self.CHIMERA, response={"type": "input", "prefix": "09 04", "timeout_ms": 50, "sum_equals": "55"})
        right = dict(self.CHIMERA, name="Compx mouse (variant 2)")
        node = pb.HidrawNode(path="/dev/hidraw3", vendor=0x3554, product=0xF512, name="x", interface=1,
                             collections=[{"usage_page": 0xFF02, "usage": 2}])
        good = pb.parse_hex("08 04 00 00 00 02 5f 00 0f f4 00 00 00 00 00 00 e5")
        info, devs = pb.provider_hid({}, profiles=[wrong, right], nodes=[node],
                                     factory=lambda p: FakeTransport([good]))
        self.assertEqual([(d.name, d.percent) for d in devs], [("Compx mouse (variant 2)", 95)])


class EvaluateTests(unittest.TestCase):
    def run_levels(self, levels, state="discharging", s=None, state_obj=None, device_id="bt:aa"):
        s = s or settings()
        st = state_obj if state_obj is not None else {"devices": {}}
        out = []
        for lvl in levels:
            out.append(pb.evaluate([dev(id=device_id, percent=lvl, state=state)], s, st))
        return out, st

    def test_thresholds_fire_once_going_down(self):
        out, _ = self.run_levels([100, 80, 74, 73, 60, 49, 30, 24, 23, 10, 9, 4])
        fired = [[e["threshold"] for e in events] for events in out]
        self.assertEqual(fired, [[], [], [75], [], [], [50], [], [25], [], [10], [], [5]])

    def test_first_sight_is_quiet_unless_nearly_empty(self):
        out, _ = self.run_levels([40])
        self.assertEqual(out[0], [])  # 75 and 50 are silently marked as passed
        out, _ = self.run_levels([8])
        self.assertEqual(len(out[0]), 1)
        self.assertEqual(out[0][0]["urgency"], "critical")
        self.assertEqual(out[0][0]["threshold"], 10)

    def test_multiple_stages_skipped_gives_one_notification(self):
        out, _ = self.run_levels([90, 20])
        self.assertEqual([e["threshold"] for e in out[1]], [25])

    def test_rearm_after_charging(self):
        st = {"devices": {}}
        self.run_levels([60, 24], state_obj=st)  # fires 25
        self.run_levels([30, 60], state="charging", state_obj=st)  # re-arms
        out, _ = self.run_levels([49, 24], state_obj=st)
        self.assertEqual([[e["threshold"] for e in ev] for ev in out], [[50], [25]])

    def test_no_low_alerts_while_charging(self):
        out, _ = self.run_levels([60, 20, 8], state="charging")
        self.assertEqual(out, [[], [], []])

    def test_hysteresis_prevents_flapping(self):
        out, _ = self.run_levels([60, 24, 26, 24, 25, 24])
        self.assertEqual(sum(len(e) for e in out), 1)

    def test_charged_notification(self):
        st = {"devices": {}}
        out, _ = self.run_levels([50, 90, 100, 100], state="charging", state_obj=st)
        types = [[e["type"] for e in ev] for ev in out]
        self.assertEqual(types, [[], [], ["charged"], []])
        # unplug, drain, charge again -> fires again
        self.run_levels([95], state="discharging", state_obj=st)
        out, _ = self.run_levels([100], state="charging", state_obj=st)
        self.assertEqual([e["type"] for e in out[0]], ["charged"])

    def test_custom_charge_limit(self):
        s = settings(charged_threshold=80)
        out, _ = self.run_levels([60, 79, 81], state="charging", s=s)
        self.assertEqual([len(e) for e in out], [0, 0, 1])
        self.assertEqual(out[2][0]["threshold"], 80)

    def test_charged_disabled(self):
        out, _ = self.run_levels([90, 100], state="charging", s=settings(charged_threshold=0))
        self.assertEqual(out, [[], []])

    def test_full_device_first_seen_is_silent(self):
        out, _ = self.run_levels([100], state="full")
        self.assertEqual(out, [[]])

    def test_unknown_state_rising_level_counts_as_charging(self):
        st = {"devices": {}}
        pb.evaluate([dev(percent=90, state="unknown")], settings(), st)
        events = pb.evaluate([dev(percent=100, state="unknown")], settings(), st)
        self.assertEqual([e["type"] for e in events], ["charged"])

    def test_muted_device_is_silent_but_tracked(self):
        s = settings(muted=["bt:aa"])
        out, st = self.run_levels([60, 20], s=s)
        self.assertEqual(out, [[], []])
        self.assertEqual(st["devices"]["bt:aa"]["last_percent"], 20)

    def test_critical_threshold_setting(self):
        s = settings(critical_below=30)
        out, _ = self.run_levels([60, 25], s=s)
        self.assertEqual(out[1][0]["urgency"], "critical")
        s = settings(critical_below=0)
        out, _ = self.run_levels([60, 5], s=s)
        self.assertEqual(out[1][0]["urgency"], "normal")

    def test_old_devices_are_pruned(self):
        st = {"devices": {"gone": {"fired": [], "last_seen": 0}}}
        pb.evaluate([], settings(), st, now=100 * 86400)
        self.assertEqual(st["devices"], {})


class DeliveryTests(unittest.TestCase):
    def event(self, urgency="normal", etype="low"):
        return {"title": "T", "body": "B", "icon": "input-mouse", "urgency": urgency, "type": etype}

    def test_notify_send_command(self):
        calls = []

        def runner(cmd, timeout=6.0):
            calls.append(cmd)
            return 0, "", ""

        cfg = settings(notify={"sound": True})["notify"]
        ok, _ = pb.send_desktop(self.event("critical"), cfg, runner, lambda c: "/x/" + c)
        self.assertTrue(ok)
        cmd = calls[0]
        self.assertEqual(cmd[0], "notify-send")
        self.assertIn("critical", cmd)
        self.assertIn("0", cmd[cmd.index("-t") + 1:cmd.index("-t") + 2])  # critical + sticky default
        self.assertIn("string:sound-name:battery-caution", cmd)
        self.assertEqual(cmd[-2:], ["T", "B"])

    def test_normal_not_sticky_unless_persistent(self):
        calls = []

        def runner(cmd, timeout=6.0):
            calls.append(cmd)
            return 0, "", ""

        pb.send_desktop(self.event(), settings()["notify"], runner, lambda c: "/x/" + c)
        self.assertNotIn("-t", calls[0])
        pb.send_desktop(self.event(), settings(notify={"persistent": True})["notify"], runner, lambda c: "/x/" + c)
        self.assertIn("-t", calls[1])

    def test_busctl_fallback(self):
        calls = []

        def runner(cmd, timeout=6.0):
            calls.append(cmd)
            return 0, "", ""

        which = lambda c: "/x/busctl" if c == "busctl" else None  # noqa: E731
        ok, _ = pb.send_desktop(self.event("critical"), settings()["notify"], runner, which)
        self.assertTrue(ok)
        cmd = calls[0]
        self.assertEqual(cmd[:4], ["busctl", "--user", "call", "org.freedesktop.Notifications"])
        self.assertEqual(cmd[cmd.index("susssasa{sv}i") + 7:], ["1", "urgency", "y", "2", "0"])

    def test_no_method_available(self):
        ok, err = pb.send_desktop(self.event(), settings()["notify"], lambda c, t=6.0: (0, "", ""), lambda c: None)
        self.assertFalse(ok)
        self.assertIn("notify-send", err)

    def test_ntfy_push(self):
        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                received["body"] = json.loads(self.rfile.read(length))
                received["auth"] = self.headers.get("Authorization")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = settings(notify={"ntfy": {"enabled": True, "server": "http://127.0.0.1:%d" % server.server_port,
                                            "topic": "my-battery", "token": "tk_123"}})["notify"]
            ok, err = pb.send_ntfy(dict(self.event("critical"), title="Mouse ünïcode"), cfg)
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(ok, err)
        self.assertEqual(received["body"]["topic"], "my-battery")
        self.assertEqual(received["body"]["priority"], 5)
        self.assertEqual(received["body"]["title"], "Mouse ünïcode")
        self.assertEqual(received["auth"], "Bearer tk_123")

    def test_ntfy_validation(self):
        cfg = settings(notify={"ntfy": {"enabled": True, "topic": ""}})["notify"]
        self.assertFalse(pb.send_ntfy(self.event(), cfg)[0])
        cfg = settings(notify={"ntfy": {"topic": "x", "server": "ftp://nope"}})["notify"]
        self.assertFalse(pb.send_ntfy(self.event(), cfg)[0])


class CycleTests(unittest.TestCase):
    def test_state_persisted_and_no_duplicates(self):
        sent = []

        def runner(cmd, timeout=6.0):
            sent.append(cmd)
            return 0, "", ""

        which = lambda c: "/x/" + c  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            s = settings()
            pb.notify_cycle([dev(percent=60)], s, path, runner, which)  # first sight
            first = pb.notify_cycle([dev(percent=24)], s, path, runner, which)
            again = pb.notify_cycle([dev(percent=24)], s, path, runner, which)  # a 2nd widget instance
            self.assertEqual(len(first), 1)
            self.assertEqual(again, [])
            self.assertEqual(len(sent), 1)
            with open(path) as handle:
                self.assertEqual(json.load(handle)["devices"]["bt:aa"]["fired"], [75, 50, 25])


class CliTests(unittest.TestCase):
    def test_waybar_and_table(self):
        devs = pb.parse_upower_dump(fixture("upower_dump.txt"))
        table = pb.format_table(pb.sort_devices(devs))
        self.assertIn("Keychron K2", table)
        self.assertIn("~25%", table)

    def test_main_version_and_help(self):
        with self.assertRaises(SystemExit) as ctx:
            pb.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(pb.main([]), 0)

    def test_config_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c", "config.json")
            self.assertEqual(pb.main(["config", "init", "--config", path]), 0)
            self.assertEqual(pb.main(["config", "init", "--config", path]), 1)
            self.assertEqual(pb.read_json_file(path)["thresholds"], [75, 50, 25, 10, 5])


if __name__ == "__main__":
    unittest.main()

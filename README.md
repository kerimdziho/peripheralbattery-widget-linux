# Peripheral Battery

[![CI](https://github.com/kerimdziho/peripheralbattery-widget-linux/actions/workflows/ci.yml/badge.svg)](https://github.com/kerimdziho/peripheralbattery-widget-linux/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Battery levels of your **wireless mouse, keyboard, headphones, gamepad** and other peripherals in one KDE Plasma 6 widget,
with alerts you control: pick the stages (75 %, 50 %, 25 %, 10 % …), get an urgent warning when it is nearly empty,
a "fully charged" message, and optional push notifications to your phone.

<!-- Add a screenshot here: ![Screenshot](docs/screenshot.png) -->

* **Panel or desktop** - the panel shows your weakest battery as plain text like **38%** (or icon, or both); click for the list. On the desktop it is a minimal rounded card. Follows your Plasma theme, or force light / dark.
* **Works with what you have** - Bluetooth devices, Logitech Unifying/Bolt (via UPower/HID++), PlayStation/Xbox controllers,
  USB and 2.4 GHz gaming headsets (optional `headsetcontrol`), and **any other 2.4 GHz dongle device through a small profile** - see [docs/PROFILES.md](docs/PROFILES.md).
* **Alerts that do not nag** - each stage fires once, skipped stages collapse into one alert, no repeats when the reading wobbles,
  nothing while charging.
* **Nothing to configure to start** - no accounts, no root, no extra dependencies besides what a Plasma desktop already has.

## Which brands work

| Device | How it is read | What you need |
| --- | --- | --- |
| **Any Bluetooth device** that reports a battery (Keychron, Apple Magic Mouse/Keyboard, AirPods-type earbuds, Sony / Bose / JBL headphones, most mice and keyboards) | BlueZ / UPower | nothing extra |
| **Logitech** (Unifying, Bolt, Lightspeed, Bluetooth) | UPower through the kernel's `hid-logitech-hidpp` driver | a recent kernel |
| **PlayStation, Nintendo Switch Pro, Xbox** controllers | kernel power-supply drivers → UPower | Xbox wireless needs the `xpadneo` or `xone` driver |
| **Razer** wireless mice, keyboards, headsets | [OpenRazer](https://openrazer.github.io) | OpenRazer daemon + its Python client |
| **Headsets**: SteelSeries Arctis, Corsair Virtuoso / Void, Logitech G, HyperX Cloud, Audeze, Sony INZONE, … | [HeadsetControl](https://github.com/Sapd/HeadsetControl) (optional) | `headsetcontrol` installed |
| **Other 2.4 GHz mice / keyboards** (Compx, Scyrox, Attack Shark, VGN, Corsair/SteelSeries/Glorious wireless mice, …) | a small **profile** for that model | [docs/PROFILES.md](docs/PROFILES.md) - none ship yet |

Run `peripheral-battery doctor` to see which of these your system has. Exact headset support is the
[HeadsetControl device list](https://github.com/Sapd/HeadsetControl#supported-devices); Razer support follows OpenRazer's.
The last row is honest: those brands expose no battery to Linux through any standard or open tool, so a profile is the only way.

## Install

**Option A - download (easiest).** Get `peripheral-battery-x.y.z.plasmoid` from the
[Releases](https://github.com/kerimdziho/peripheralbattery-widget-linux/releases) page, then either:

* right-click the desktop → *Add Widgets…* → *Get New…* → *Install Widget From Local File…*, or
* `kpackagetool6 -t Plasma/Applet -i peripheral-battery-*.plasmoid`

**Option B - from source.**

```bash
git clone https://github.com/kerimdziho/peripheralbattery-widget-linux.git
cd peripheralbattery-widget-linux
./scripts/install.sh
```

Then right-click a panel or the desktop → *Add Widgets…* → **Peripheral Battery**.
If it is not listed yet: `kquitapp6 plasmashell; kstart plasmashell`.

**Requirements:** Plasma 6, Python 3.8+, and `upower` (installed on practically every desktop distro; BlueZ is used for Bluetooth).
Plasma 5 is not supported.

## Settings

Right-click the widget → *Configure…*

| Setting | What it does |
| --- | --- |
| Appearance | Follow the system theme, or force Light / Dark. |
| In the panel show | Percentage only (default), icon and percentage, or icon only. |
| Notify me at | Tick the stages you want: 90, 80, 75, 70, 60, 50, 40, 30, 25, 20, 15, 10, 5, 3 %. |
| Urgent alert at or below | Alerts at/under this level are marked urgent (and can stay until dismissed). |
| Charged alert | Tell me when a device reaches 100 % - or a lower limit if you cap charging at, say, 80 %. |
| Desktop | Keep every alert on screen, play the sound. |
| Push (ntfy) | Also send alerts to your phone with [ntfy](https://ntfy.sh). |

**Right-click a device** in the list to *show it in the panel*, mute its alerts or hide it.

*Test low battery* / *Test charged* in the settings send a sample alert with what you have currently set.

### Push notifications to your phone

1. Install the free **ntfy** app (Android / iOS) and subscribe to a topic with a long random name.
2. Enter the same topic in *Notifications → Push notifications*. Use your own server if you self-host.

Device names and levels are sent to the ntfy server, and anyone who knows the topic can read them - choose a hard-to-guess topic
or use access tokens. The token is stored unencrypted in your Plasma config.

## Without Plasma (other desktops, Waybar, servers)

The widget is a thin layer over a dependency-free Python tool that works on any distro.

```bash
./scripts/install.sh --daemon     # CLI + systemd user service that sends the alerts
peripheral-battery list           # what is connected, and how full
peripheral-battery doctor         # why is my device missing?
peripheral-battery test-notify --level 8 --critical
peripheral-battery hid-probe      # dongles with a vendor-specific protocol, see docs/PROFILES.md
```

Alerts on non-Plasma desktops use `notify-send` (package `libnotify`) and work with GNOME, Xfce, Sway/mako, dunst, …

**Waybar:**

```jsonc
"custom/peripherals": {
  "exec": "peripheral-battery waybar",
  "return-type": "json",
  "interval": 60
}
```

CSS classes: `normal`, `low`, `critical`, `charging`, `empty`.

**Config file** (for the service/CLI; the widget uses its own settings): `peripheral-battery config init` writes
`~/.config/peripheral-battery/config.json`:

```json
{
  "thresholds": [75, 50, 25, 10, 5],
  "critical_below": 10,
  "charged_threshold": 100,
  "notify": { "desktop": true, "persistent": false, "sound": false,
              "ntfy": { "enabled": false, "server": "https://ntfy.sh", "topic": "" } },
  "muted": [], "hidden": []
}
```

The file is re-read on every poll, so edits apply without restarting.
Device ids for `muted` / `hidden` appear in `peripheral-battery list --json`.

## Troubleshooting

Run `peripheral-battery doctor` (or `python3 ~/.local/share/plasma/plasmoids/io.github.peripheralbattery/contents/code/pbattery.py doctor`).

* **Device missing** - it must be *connected* now, not only paired, and report a battery. Check `upower --dump`.
* **Bluetooth headset has no level** - some headsets only expose it when BlueZ runs with `Experimental = true`
  in `/etc/bluetooth/main.conf` (then `systemctl restart bluetooth`).
* **Logitech receiver (Unifying / Bolt)** - needs a recent kernel (`hid-logitech-hidpp`) or [Solaar](https://pwr-solaar.github.io/Solaar/) running.
* **2.4 GHz gaming headset** - install [`headsetcontrol`](https://github.com/Sapd/HeadsetControl).
* **No notification appears** - `peripheral-battery doctor` must list `notify-send` (package `libnotify` / `libnotify-bin`).

### My mouse / keyboard is not listed

The widget shows what Linux reports. Logitech receivers and anything connected over Bluetooth normally work. Many other
2.4 GHz gaming mice and keyboards use a **vendor-specific message** that Linux does not know, so `upower --dump` lists nothing.

No program can read every brand out of the box - each protocol has to be learned once. Peripheral Battery makes that
a small job: `peripheral-battery hid-probe` shows what the dongle sends, and a short JSON **profile** teaches the widget to read
it. Step-by-step guide: **[docs/PROFILES.md](docs/PROFILES.md)**. Profiles for new models are very welcome as pull requests.

To try the widget without any device, tick *Show demo devices* in *Configure → General* (demo devices never send alerts), or run
`peripheral-battery list --no-config --settings-json '{"demo": true}'`.

### About "Wi-Fi" peripherals

Practically all wireless peripherals use Bluetooth or a proprietary 2.4 GHz dongle; both are covered. Devices that join your
Wi-Fi network and report their battery over the LAN (smart speakers and the like) are not supported.

## Development

```bash
make test       # unit tests (Python standard library only)
make lint
make package    # dist/peripheral-battery-<version>.plasmoid
```

Layout: `plasmoid/` is the widget (QML in `contents/ui`, backend in `contents/code/pbattery.py`), `systemd/` the optional service,
`scripts/` installer and build script. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)

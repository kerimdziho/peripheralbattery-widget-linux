# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Profiles: `response.sum_equals` checksum check; every matching profile is tried until one answers.
- Documented Compx 17-byte framing and unverified candidate profiles in `docs/candidates/`.

## [1.0.0] - 2026-11-20

### Added
- Plasma 6 widget for panel and desktop: weakest device in the panel, full list in the popup.
- Battery levels from UPower, BlueZ (D-Bus, `bluetoothctl` fallback), sysfs and optional `headsetcontrol`;
  the same device from several sources is merged.
- Configurable alert stages (90 … 3 %), urgent level, "charged" alert with custom limit, re-arm margin,
  sticky notifications, sound.
- Optional push notifications to a phone through ntfy.
- Per-device mute and hide.
- Command line tool: `list`, `poll`, `watch`, `waybar`, `test-notify`, `config`, `doctor`.
- systemd user service for alerts without Plasma.
- Razer devices through OpenRazer's Python client (optional).
- Generic HID reader for vendor-specific 2.4 GHz dongles, driven by JSON profiles, with `hid-probe` and `hid-udev` helpers (docs/PROFILES.md).
- Minimal card design for desktop and popup; light / dark / follow-system appearance.
- Panel shows plain text such as `38%` by default (icon / icon + text optional); any device can be pinned to the panel.
- Per-device options moved to a right-click menu; settings simplified.
- Demo mode (fake devices) to try the widget without hardware; never sends alerts.
- Systems without Bluetooth are handled quietly (no slow `bluetoothctl` timeout).

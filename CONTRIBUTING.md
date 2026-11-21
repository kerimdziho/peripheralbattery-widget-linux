# Contributing

Thanks for helping! The most valuable contribution is **real output from your hardware**.

## Adding or fixing device support

1. Run `upower --dump`, `busctl --system --json=short call org.bluez / org.freedesktop.DBus.ObjectManager GetManagedObjects`
   and (if relevant) `headsetcontrol -b -o json`.
2. Remove anything private (serial numbers, MAC addresses) and save the relevant part in `tests/fixtures/`.
3. Add a test in `tests/test_pbattery.py` that parses it, then fix `pbattery.py` until it passes.

## Adding support for a dongle / brand

Write a profile following [docs/PROFILES.md](docs/PROFILES.md) and add it to `plasmoid/contents/code/profiles/`
(one JSON file per model; `_`-prefixed files are ignored). Include a test with the captured report bytes if you can.

## Setup

No dependencies: `make test` runs everything with the standard library. The backend must stay
**pure standard library, Python 3.8 compatible** (no `match`, no `X | Y` types at runtime).
`make lint` additionally needs `xmllint` and (optionally) `shellcheck`.

To try the widget while editing QML: `./scripts/install.sh`, then `plasmoidviewer -a io.github.peripheralbattery`
(package `plasma-sdk`).

## Guidelines

* QML user-visible strings go through `i18n()` / `i18nc()`; `lib.js` has no translation context, so keep text out of it.
* The widget talks to the backend only through `pbattery.py poll --settings-json …`; new settings need a key in
  `main.xml`, `lib.js:buildSettings` and `normalize_settings` in the backend.
* Notification rules live in `evaluate()` and must stay a pure function with a test per behaviour.
* Update `CHANGELOG.md`.

## Releasing

Bump the version in `plasmoid/metadata.json`, update the changelog, then `git tag vX.Y.Z && git push --tags`.
The release workflow builds and attaches the `.plasmoid`. Upload the same file to the KDE Store
(store.kde.org → *Plasma Widgets*) for the in-app *Get New…* dialog.

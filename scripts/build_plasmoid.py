#!/usr/bin/env python3
"""Build dist/peripheral-battery-<version>.plasmoid (a zip with metadata.json at its root)."""

import json
import os
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "plasmoid")
DIST = os.path.join(ROOT, "dist")
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".swp", "~")


def main() -> int:
    with open(os.path.join(SRC, "metadata.json"), encoding="utf-8") as handle:
        version = json.load(handle)["KPlugin"]["Version"]
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "peripheral-battery-%s.plasmoid" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(SRC):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith(SKIP_SUFFIXES):
                    continue
                full = os.path.join(folder, name)
                info = zipfile.ZipInfo.from_file(full, os.path.relpath(full, SRC))
                info.date_time = (2026, 1, 1, 0, 0, 0)  # reproducible builds
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as handle:
                    archive.writestr(info, handle.read())
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

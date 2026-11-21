#!/usr/bin/env bash
# Peripheral Battery installer (user-level, no root needed).
#
#   ./install.sh                install / upgrade the Plasma 6 widget
#   ./install.sh --daemon       + CLI and a systemd user service (alerts without Plasma)
#   ./install.sh --cli          CLI only (Waybar, i3blocks, other desktops)
#   ./install.sh --uninstall    remove everything this script installed
set -euo pipefail

PLUGIN_ID="io.github.peripheralbattery"
SERVICE="peripheral-battery.service"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
BIN_DIR="${HOME}/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PLASMOID_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/${PLUGIN_ID}"

want_widget=1 want_cli=0 want_daemon=0 uninstall=0
for arg in "$@"; do
  case "$arg" in
    --daemon) want_cli=1; want_daemon=1 ;;
    --cli) want_cli=1; want_widget=0 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

# Source of the widget: the repository checkout, or a folder next to this script in a release archive.
SRC="$ROOT/plasmoid"
[ -d "$SRC" ] || SRC="$HERE/plasmoid"

if [ "$uninstall" = 1 ]; then
  say "Removing Peripheral Battery"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
  fi
  rm -f "$UNIT_DIR/$SERVICE" "$BIN_DIR/peripheral-battery"
  if command -v kpackagetool6 >/dev/null 2>&1; then
    kpackagetool6 -t Plasma/Applet -r "$PLUGIN_ID" 2>/dev/null || true
  fi
  rm -rf "$PLASMOID_DIR"
  echo "Done. Settings and state are kept in ~/.config/peripheral-battery and ~/.local/state/peripheral-battery."
  exit 0
fi

if [ "$want_widget" = 1 ]; then
  [ -f "$SRC/metadata.json" ] || { echo "Cannot find the plasmoid sources at $SRC" >&2; exit 1; }
  if command -v kpackagetool6 >/dev/null 2>&1; then
    say "Installing the Plasma widget"
    kpackagetool6 -t Plasma/Applet -i "$SRC" 2>/dev/null || kpackagetool6 -t Plasma/Applet -u "$SRC"
  else
    say "kpackagetool6 not found - copying the widget into place"
    mkdir -p "$PLASMOID_DIR"
    cp -r "$SRC/." "$PLASMOID_DIR/"
  fi
  echo "Right-click the desktop or a panel -> Add Widgets -> \"Peripheral Battery\"."
  echo "If it is not listed yet, restart the shell:  kquitapp6 plasmashell; kstart plasmashell"
fi

if [ "$want_cli" = 1 ]; then
  say "Installing the command line tool to $BIN_DIR/peripheral-battery"
  mkdir -p "$BIN_DIR"
  install -m 0755 "$SRC/contents/code/pbattery.py" "$BIN_DIR/peripheral-battery"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Note: $BIN_DIR is not in your PATH." ;; esac
fi

if [ "$want_daemon" = 1 ]; then
  command -v systemctl >/dev/null 2>&1 || { echo "systemd not found: start '$BIN_DIR/peripheral-battery watch' from your session autostart instead." >&2; exit 0; }
  say "Installing the systemd user service"
  mkdir -p "$UNIT_DIR"
  install -m 0644 "$ROOT/systemd/$SERVICE" "$UNIT_DIR/$SERVICE"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE"
  echo "Service running. In the widget settings, untick 'Send battery notifications from this widget'"
  echo "(or keep it - alerts are never sent twice). Config file: $(python3 "$BIN_DIR/peripheral-battery" config path)"
fi

say "Done."

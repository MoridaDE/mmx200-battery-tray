#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/mmx200-battery-tray"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
AUTO_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

mkdir -p "$SHARE" "$BIN" "$UNIT_DIR" "$AUTO_DIR" "$APP_DIR"
install -m 0644 "$ROOT/src/backend.py" "$SHARE/backend.py"
install -m 0755 "$ROOT/src/tray.py" "$SHARE/tray.py"
install -m 0755 "$ROOT/src/status.py" "$SHARE/status.py"
install -m 0644 "$ROOT/src/combined_tray.py" "$SHARE/combined_tray.py"

PAD_SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/8bitdo-2c-battery-tray"
if [[ -f "$PAD_SHARE/backend.py" ]]; then
  # Unified tray is hosted by this unit; keep the pad service from racing.
  systemctl --user disable --now 8bitdo-2c-battery-tray.service 2>/dev/null || true
  echo "Note: 8BitDo tray also installed — one combined StatusNotifier icon (this service hosts it)."
fi

cat > "$BIN/mmx200-battery-tray" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$SHARE/tray.py" "\$@"
EOF
cat > "$BIN/mmx200-battery-status" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$SHARE/status.py" "\$@"
EOF
chmod 0755 "$BIN/mmx200-battery-tray" "$BIN/mmx200-battery-status"

install -m 0644 "$ROOT/packaging/mmx200-battery-tray.service" "$UNIT_DIR/"
install -m 0644 "$ROOT/packaging/mmx200-battery-tray.desktop" "$AUTO_DIR/"
install -m 0644 "$ROOT/packaging/mmx200-battery-tray.desktop" "$APP_DIR/"

echo "Optional udev rule (once, with sudo):"
echo "  sudo cp $ROOT/packaging/71-mmx200-hidraw.rules /etc/udev/rules.d/"
echo "  sudo udevadm control --reload && sudo udevadm trigger"

systemctl --user daemon-reload
systemctl --user enable --now mmx200-battery-tray.service
systemctl --user try-restart mmx200-battery-tray.service 2>/dev/null || true
echo "Installed. Check status with: mmx200-battery-status"

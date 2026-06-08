#!/usr/bin/env bash
# Merge fleet-standard USB serial layout into /etc/nina-link/navigation.env
# without removing unrelated lines. Requires sudo.
#
# Standard Nina Jetson wiring:
#   ESP8266 eye (CP2102)  → /dev/ttyUSB0 @ 115200
#   Dynamixel U2D2 (FTDI) → /dev/ttyUSB1 @ 222222
set -euo pipefail
ENV_FILE="${NINA_NAV_ENV_FILE:-/etc/nina-link/navigation.env}"
sudo mkdir -p "$(dirname "$ENV_FILE")"
if [[ ! -f "$ENV_FILE" ]]; then
  sudo touch "$ENV_FILE"
fi
tmp="$(mktemp)"
sudo cp "$ENV_FILE" "$tmp"
python3 - "$tmp" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text() if path.read_text() else ""
lines = text.splitlines()
# Fleet default: eye CP2102 on ttyUSB0, Dynamixel FTDI on ttyUSB1.
additions = {
    "NINA_EYE_UART_ENABLE": "1",
    "NINA_EYE_UART_PORT": "/dev/ttyUSB0",
    "NINA_EYE_UART_BAUD": "115200",
    "NINA_DXL_PORT": "/dev/ttyUSB1",
    "NINA_DXL_BAUD": "222222",
}
keys = {
    line.split("=", 1)[0].strip()
    for line in lines
    if "=" in line and not line.strip().startswith("#")
}
out = list(lines)
for key, val in additions.items():
    if key in keys:
        out = [
            f"{key}={val}" if ln.split("=", 1)[0].strip() == key else ln
            for ln in out
        ]
    else:
        out.append(f"{key}={val}")
path.write_text("\n".join(out).rstrip() + "\n")
PY
sudo cp "$tmp" "$ENV_FILE"
rm -f "$tmp"
echo "Updated $ENV_FILE (eye=ttyUSB0, Dynamixel=ttyUSB1)."
echo "Restart kiosk: systemctl --user restart nina-ui-kiosk.service"

#!/usr/bin/env bash
# Append or update Dynamixel port keys in /etc/nina-link/navigation.env
# without removing unrelated lines. Requires sudo.
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
lines = path.read_text().splitlines() if path.read_text() else []
additions = {
    "NINA_DXL_PORT": "/dev/ttyUSB1",
    "NINA_DXL_BAUD": "222222",
    "NINA_DXL_EXPECTED_IDS": "12,13",
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
echo "Updated $ENV_FILE (Dynamixel port + lean-only IDs)."
echo "Restart kiosk: systemctl --user restart nina-ui-kiosk.service"

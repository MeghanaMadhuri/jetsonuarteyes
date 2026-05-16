#!/bin/bash
# Enable I2C2 on Jetson 40-pin header pins 27 (SDA) and 28 (SCL) for ADS1115.
# Persists after reboot when you complete the save step below.
#
# IMU stays on pins 3+5 (/dev/i2c-7). Wire ADS1115 only to 27+28.
#
# Usage (on the Jetson):
#   cd /path/to/Nvidia-jetson-platform
#   sudo bash scripts/jetson-enable-battery-i2c2.sh

set -euo pipefail

JETSON_IO="/opt/nvidia/jetson-io/jetson-io.py"
CFG_FN="/opt/nvidia/jetson-io/config-by-function.py"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

if [[ ! -x "$CFG_FN" ]]; then
  echo "Missing $CFG_FN — install Jetson.GPIO / jetson-io package." >&2
  exit 1
fi

echo "=== Enable header I2C2 (physical pins 27 SDA, 28 SCL) ==="
"$CFG_FN" -o dtbo i2c2

echo ""
echo "=== Apply saved overlays and reboot (required once) ==="
echo "Option A (recommended):"
echo "  sudo $JETSON_IO"
echo "  → Save and reboot to reconfigure pins"
echo ""
echo "Option B: if overlays are already in extlinux from a prior save, reboot:"
echo "  sudo reboot"
echo ""
echo "After reboot, verify ADS1115 (ADDR→GND = 0x48):"
echo "  sudo i2cdetect -y 1"
echo "  export NINA_BATTERY_I2C_BUS=1"
echo "  python3 -m nina.app.ads1115_bench_test --samples 5"
echo ""
echo "Set in /etc/nina-link/navigation.env (persistent for Nina UI):"
echo "  NINA_BATTERY_I2C_BUS=1"
echo "  NINA_BATTERY_ADS1115_ENABLE=1"
echo "  NINA_BATTERY_DIVIDER_RATIO=7.606"

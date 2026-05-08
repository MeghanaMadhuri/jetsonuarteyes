# Sirena Nina — Jetson robotics platform

Nina is a wheeled robot built on an **NVIDIA Jetson Orin Nano**: one
SBC runs the GUI, vision, SLAM, autonomy, action playback **and**
direct GPIO/PWM to the two **JYQD_V7.3E2** BLDC wheel drivers.

**Legacy:** a **Raspberry Pi** running `pi_motor_bridge` can still
offload the JYQDs (`NINA_NAV_MODE=remote` + UART). New production wiring
connects JYQD logic lines to the **Jetson** 40-pin header per
`nina/controllers/navigation_manager.py` (`DEFAULT_PINS`) and
`pi_motor_bridge/PINMAP.md` (signal names).

## Documentation

**[REQUIREMENTS.md](REQUIREMENTS.md)** — Hardware BOM, OS, Python deps, bring-up (Jetson-first).

Deeper references:

- [`sirena_ui/docs/NINA_APP.md`](sirena_ui/docs/NINA_APP.md) — PyQt cockpit (screens, env vars, tunables).
- [`pi_motor_bridge/PINMAP.md`](pi_motor_bridge/PINMAP.md) — JYQD ↔ header table (BCM labels; match Jetson `DEFAULT_PINS`).
- [`pi_motor_bridge/README.md`](pi_motor_bridge/README.md) — **Legacy** Pi motor daemon (pigpio, UART, systemd).

## Quick layout

```
├── sirena_ui/         PyQt5 cockpit (Home, Drive, Vision, Map, Actions, Settings, Health)
├── nina/              Backend: navigation, sensors, SLAM, autonomy, action runner
├── pi_motor_bridge/   Optional: legacy Pi motor daemon + wiring tables for JYQDs
├── desktop/           Systemd user units + .desktop launcher templates
├── scripts/           Installers (kiosk autostart, FTDI udev, desktop icon)
├── tests/             Hardware-free pytest suite (mocks pigpio + serial)
└── requirements*.txt  Python deps — see REQUIREMENTS.md §4
```

## Quick start

For bring-up from boxes to **Drive** on the panel, follow
**[REQUIREMENTS.md](REQUIREMENTS.md)** §5 (Jetson GPIO motors).

If both boards are already set up and you just want to run the GUI:

```bash
cd ~/Nvidia-jetson-platform
PYTHONPATH=. python3 -m sirena_ui
```

Or, to install the kiosk autostart (panel boots straight into the GUI
on every reboot):

```bash
./scripts/install-nina-ui-kiosk.sh
```

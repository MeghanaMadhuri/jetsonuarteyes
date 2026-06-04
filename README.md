# Sirena Nina — Jetson + Raspberry Pi robotics platform

Nina is a wheeled robot built on a two-board split: an **NVIDIA Jetson
Orin Nano** runs the GUI, vision, SLAM, autonomy and action playback,
and a **Raspberry Pi 4** is the dedicated motor controller for the
two JYQD_V7.3E2 BLDC drivers. The boards talk over a 115 200 8N1
serial link (40-pin UART crossover, or CP2102 / FT232 USB-to-TTL
adapter).

## Documentation

**[docs/CUSTOMER_SETUP_MANUAL.md](docs/CUSTOMER_SETUP_MANUAL.md)** — Customer-facing setup guide: safety, power/battery, wiring checklist, daily operation, charging, and troubleshooting.

**[REQUIREMENTS.md](REQUIREMENTS.md)** — Single reference: hardware BOM, OS versions, Python deps, and the end-to-end bring-up checklist for fresh Jetson + Pi pair.

**[docs/COMPANION_CONTROLS.md](docs/COMPANION_CONTROLS.md)** — Nina **Android companion** + Jetson **`nina-link`**: which HTTP controls need which env flags (`NINA_LINK_ENABLE_*`), auth, and troubleshooting when the tablet UI looks idle.

**[docs/COMPANION_APP.md](docs/COMPANION_APP.md)** — Tablet app + Jetson gateway install paths, updates, and parity notes.

### Android companion + Jetson gateway (one shot)

On the **Jetson**, from the repo root (after clone), run either:

- `./scripts/jetson-tablet-setup.sh` — recommended wrapper, or  
- `./scripts/install-sirena-companion-jetson.sh` — same chain: **nina-link** (venv, systemd), **HTTP bridge drop-in**, optional UFW **8787/tcp**; add `--with-sirena-headless` for vision / SLAM–class pip deps in `.venv-link`.

Then build or sideload the app from **`android/`** (see [`android/README.md`](android/README.md)) and point **Setup** at the daemon URL (default hotspot-style `http://10.42.0.1:8787` when applicable). Recent companion and `/v1/robot/capabilities` behavior are summarized in **COMPANION_APP.md** § *Companion app + gateway updates*.

Deeper references for each subsystem:

- [`sirena_ui/docs/NINA_APP.md`](sirena_ui/docs/NINA_APP.md) — full feature reference for the PyQt5 cockpit (every screen, every env var, every tunable).
- [`pi_motor_bridge/README.md`](pi_motor_bridge/README.md) — Raspberry Pi bring-up walkthrough (Bookworm, pigpio, UART, every pothole).
- [`pi_motor_bridge/PINMAP.md`](pi_motor_bridge/PINMAP.md) — JYQD ↔ Pi GPIO wiring table.
- [`docs/NINA_EYE_UART.md`](docs/NINA_EYE_UART.md) — ESP8266 eye display UART (Jetson USB `/dev/ttyUSB0`).
- [`firmware/nina_eye_esp8266/README.md`](firmware/nina_eye_esp8266/README.md) — Eye firmware flash checklist.

## Repository structure
```
├── sirena_ui/         PyQt5 cockpit (Home, Drive, Vision, Map, Actions, Settings, Health)
├── nina/              Backend: navigation, sensors, SLAM, autonomy, action runner
├── pi_motor_bridge/   Pi-side serial daemon that owns the JYQDs
├── firmware/          ESP8266 eye display and related device firmware
├── desktop/           Systemd user units + .desktop launcher templates
├── scripts/           Installers (kiosk autostart, FTDI udev, desktop icon)
├── tests/             Hardware-free pytest suite (mocks pigpio + serial)
└── requirements*.txt  Python deps — see REQUIREMENTS.md §4
```

## Quick start

For an end-to-end bring-up on fresh Jetson + Pi pair, follow the
checklist in **[REQUIREMENTS.md](REQUIREMENTS.md)** §5.

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

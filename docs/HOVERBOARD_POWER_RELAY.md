# Hoverboard DC power relay (GPIO + existing brake toggle)

Cut and restore **hoverboard mainboard DC power** from the same **Brake**
toggle used in Sirena UI and the Android companion app. Optional: relay
coil is driven by a **Jetson 40-pin GPIO** through a **5 V Songle-style
module** (e.g. FL-3FF-S-Z with **IN / GND / VCC** — never drive the coil
directly from a GPIO pin).

## Software behaviour

When `NINA_HOVER_POWER_RELAY_BCM` is set, [`HoverboardAxisDrive`](../nina/controllers/hoverboard_axis_drive.py):

| Event | Servo / bus | Relay GPIO |
| --- | --- | --- |
| `initialize()` | Applies brake pose on AX-18 | **Power allowed** (pack energised) before touching the bus |
| `engage_brake()` (Brake **ON** in UI) | `stop()` → brake goals | **Power cut** |
| `release_brake()` (Brake **OFF**) | No servo change | **Power allowed** |
| `emergency_stop()` | `stop()` | **Power cut** |
| `emergency_stop(routine_shutdown=True)` (kiosk exit) | `stop()` | **Cut** by default; see shutdown env below |

The Android companion issues the same brake transitions over **`POST /v1/robot/drive/brake`** (`{ "on": true|false }`), which maps to **`DriveController.set_brake`** on the Jetson (same queue as the kiosk).

If `NINA_HOVER_POWER_RELAY_BCM` is **unset**, nothing changes from the
pre-relay build (relay code is inactive).

## Environment variables

| Variable | Values | Default |
| --- | --- | --- |
| `NINA_HOVER_POWER_RELAY_BCM` | Positive integer: **BCM** GPIO number (Jetson numbering) | unset = feature **off** |
| `NINA_HOVER_RELAY_POWER_ON_LEVEL` | `0` or `1` — logic level on **IN** when the hoverboard **must be energised** | `1` |
| `NINA_HOVER_RELAY_SHUTDOWN_ALLOWS_POWER` | `1` = on kiosk exit, return GPIO to *power on*; `0` = leave at *power cut* after shutdown | `0` |

Map `POWER_ON_LEVEL` to your module’s **active-high vs active-low IN**
behaviour and your **NO/NC** contact wiring (bench-test before flight).

## Default GPIO suggestion (Jetson Orin Nano, 40-pin)

| Role | BCM | Physical pin | Notes |
| --- | --- | --- | --- |
| Relay **IN** (logic) | **26** | **37** | Avoids stock JYQD pins (see [`navigation_manager.py`](../nina/controllers/navigation_manager.py)). **Conflict:** default `rear_right` HC-SR04 **echo** is BCM 26 — if that sensor is wired to pin 37, pick another free BCM and set `NINA_HOVER_POWER_RELAY_BCM` accordingly. |

Other often-free candidates **only if nothing is wired there**: BCM 9
(pin 21), BCM 4 (pin 7), BCM 8 (pin 24) — cross-check
[`hcsr04.py`](../nina/sensors/hcsr04.py) defaults and your harness.

## Module wiring (IN / GND / VCC)

| Module pin | Connect to |
| --- | --- |
| **GND** | Jetson GND (e.g. pins 6, 9, 14, 20, 25, 30, 34, 39) |
| **VCC** | Jetson **5 V** (pins 2 or 4) **only if** the 5 V rail can supply **~70–100 mA** for coil + LED + driver; otherwise use a **separate 5 V** supply with **common GND** |
| **IN** | Selected Jetson GPIO (3.3 V logic) |

**Bench-test IN polarity** before flight:

1. Connect only VCC + GND.
2. Jumper **IN** to GND vs to 3V3 (pin 1) and listen for the relay click.
3. Decide which level means **coil energised** vs **released**, then set
   `NINA_HOVER_RELAY_POWER_ON_LEVEL` so that **brake OFF** matches **pack
   energised**.

## High-voltage / high-current side (your responsibility)

- Use relay contacts **rated** for your hoverboard **pack voltage** and
  **inrush current**; add an appropriate **fuse** on the switched leg.
- This Songle family is for **low-voltage DC** branch switching — **not**
  mains AC unless the relay and installation are explicitly mains-rated.
- Prefer switching the **positive** feed to the hoverboard controller
  (after fuse, before controller), with a single continuous **GND** reference.
- Add a **hardware pull** on IN so **GPIO high-impedance at boot** does not
  silently energise the wrong relay state.

## Fail-safe at boot

Decide explicitly:

- When Jetson is **off**, should the hoverboard be powered or not?
- When the kiosk process has **not** yet opened GPIO, what should the pack do?

That drives NO vs NC contact choice, pull resistor direction, and
`POWER_ON_LEVEL`.

## systemd (kiosk) drop-in

Copy the example under `~/.config/systemd/user/nina-ui-kiosk.service.d/`,
adjust BCM / levels, then reload:

```bash
mkdir -p ~/.config/systemd/user/nina-ui-kiosk.service.d
cp desktop/nina-ui-kiosk.service.d/hoverboard-power-relay.conf.example \
   ~/.config/systemd/user/nina-ui-kiosk.service.d/hoverboard-power-relay.conf
# edit BCM if needed
systemctl --user daemon-reload
systemctl --user restart nina-ui-kiosk.service
```

## Verification checklist (Jetson + hardware)

1. With relay env **unset**, confirm driving behaviour unchanged.
2. Set BCM, **brake OFF** → measure pack: **energised**.
3. **Brake ON** → pack **cuts** within relay operate time; servos may still
   move if Dynamixel bus is on a **separate** supply.
4. **Brake OFF** → pack **restores**.
5. Android / PyQt **Emergency stop** → same as brake ON (cut).
6. Reboot Jetson → confirm default pack state matches your fail-safe design.
7. `journalctl --user -u nina-ui-kiosk -f` — look for
   `Hoverboard power relay: BCM …` at first `HoverboardAxisDrive.initialize()`.

## Related code

- [`nina/controllers/hoverboard_power_relay.py`](../nina/controllers/hoverboard_power_relay.py) — lazy `Jetson.GPIO` output
- [`nina/config/settings.py`](../nina/config/settings.py) — `HoverboardAxisSettings` fields + `load_settings` env wiring
- [`sirena_ui/workers/drive_controller.py`](../sirena_ui/workers/drive_controller.py) — brake toggle → `engage_brake` / `release_brake`

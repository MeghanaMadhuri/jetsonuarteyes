# Hoverboard DC power relay (Jetson **header pin 37** + brake toggle)

Cut and restore **hoverboard mainboard DC power** from the same **Brake**
toggle used in Sirena UI and the Android companion app. Wire the relay
module **IN** to the Jetson **40-pin header pin 37** (already used as GPIO in
this stack). Coil is driven through a **5 V Songle-style module** (e.g.
FL-3FF-S-Z with **IN / GND / VCC** — never drive the coil directly from a GPIO
pin).

## Software behaviour

When the relay is configured (see env vars below),
[`HoverboardAxisDrive`](../nina/controllers/hoverboard_axis_drive.py):

| Event | Servo / bus | Pack relay (pin **37**) |
| --- | --- | --- |
| Kiosk process start (`NinaService`) | (none yet) | **Power cut** — GPIO is configured as soon as the relay module is first used |
| `initialize()` | Applies brake pose on AX-18 | **Power cut** (parked = pack off) |
| `engage_brake()` (Brake **ON** in UI) | `stop()` → brake goals | **Power cut** |
| `release_brake()` (Brake **OFF**) | No servo change | **Power allowed** |
| `emergency_stop()` | `stop()` | **Power cut** |
| `emergency_stop(routine_shutdown=True)` (kiosk exit) | `stop()` | **Cut** by default; see shutdown env below |

The Android companion issues the same brake transitions over **`POST /v1/robot/drive/brake`** (`{ "on": true|false }`), which maps to **`DriveController.set_brake`** on the Jetson (same queue as the kiosk). Those calls **always** energise the pack when releasing brake (`energize_pack=True`). Autonomy / goto pilots use a **software-only** brake release so they do **not** apply pack power until you use the **Brake** control (or the HTTP brake API) to release with pack energisation.

The shipped ``desktop/nina-ui-kiosk.service`` sets **`NINA_HOVER_RELAY_HEADER_PIN=37`** so a **pull + reinstall** enables the relay on **header pin 37** without a separate drop-in. Override in ``/etc/nina-link/navigation.env`` if your harness differs.

If neither header pin nor BCM relay env is set, nothing changes from the
pre-relay build (relay code is inactive).

## Verifying the relay (quiet coil / no wheel load)

Many **5 V Songle-style** boards make only a **faint** click; **opto-isolated**
or **solid-state** modules may be **silent** even when the IN pin toggles
correctly — absence of sound does **not** prove GPIO is idle.

**Confirm in software (kiosk running):** each time the GPIO level actually
changes, the logger emits an **INFO** line from `nina.hoverboard_power_relay`
(`GPIO=… pack energised` vs `pack cut`), labeled as **header pin 37**. Watch
the user journal while toggling **Brake OFF / ON** on Drive:

```bash
journalctl --user -u nina-ui-kiosk.service -f | grep -i 'power relay'
```

(or your usual `launch.log` path if the GUI logs there).

**Confirm on the bench (kiosk stopped):** with only **VCC + GND** on the relay
module, jumper **IN** between **3V3 (pin 1)** and **GND** and listen — that
tells you how loud *your* module is.

For a raw GPIO line exercise, `nav-test-pin` still takes the **Jetson BCM
index** used under the hood for header pin **37** (Orin Nano: **`--pin 26`**).
Stop the kiosk first so nothing else owns the line, then from the repo root:

```bash
cd /path/to/Nvidia-jetson-platform-nina-arm
PYTHONPATH=. python3 -m nina.app.main nav-test-pin --pin 26 --mode high --hold 2
PYTHONPATH=. python3 -m nina.app.main nav-test-pin --pin 26 --mode low --hold 2
```

Use a **multimeter** DC on **pin 37 vs GND** while toggling Brake — you should
see a clear **low ↔ high** swing (~0 V vs ~3.3 V). If `nav-test-pin` moves the
voltage but the brake path never logs, check that **`NINA_HOVER_RELAY_HEADER_PIN`**
is set in the **same** environment as the kiosk process and scan logs for
`relay disabled` / `setup … failed`.

## Environment variables

| Variable | Values | Default |
| --- | --- | --- |
| `NINA_HOVER_RELAY_HEADER_PIN` | **`37`** = relay IN on Jetson **40-pin header pin 37** (recommended) | unset |
| `NINA_HOVER_POWER_RELAY_BCM` | Advanced: Jetson **BCM** index if the relay IN is **not** on pin 37 | unset |
| `NINA_HOVER_RELAY_POWER_ON_LEVEL` | `0` or `1` — logic level on **IN** when the hoverboard **must be energised** | `1` |
| `NINA_HOVER_RELAY_SHUTDOWN_ALLOWS_POWER` | `1` = on kiosk exit, return GPIO to *power on*; `0` = leave at *power cut* after shutdown | `0` |

If **`NINA_HOVER_RELAY_HEADER_PIN`** is set, it **wins** over `NINA_HOVER_POWER_RELAY_BCM`.
Use **`NINA_HOVER_POWER_RELAY_BCM`** only when the relay IN is soldered to a
different header pin (look up BCM in
[`navigation_manager.py`](../nina/controllers/navigation_manager.py) `JETSON_ORIN_NANO_BOARD_BY_BCM`).

Map `POWER_ON_LEVEL` to your module’s **active-high vs active-low IN**
behaviour and your **NO/NC** contact wiring (bench-test before flight).

## Header pin 37 vs other uses

Stock Nina wiring puts the relay **IN** on **header pin 37**. If you instead
need that physical pin for something else (e.g. default `rear_right` HC-SR04
echo in [`hcsr04.py`](../nina/sensors/hcsr04.py)), move the relay wire to
another free header pin and set **`NINA_HOVER_POWER_RELAY_BCM`** to the BCM for
that pin instead of using `NINA_HOVER_RELAY_HEADER_PIN`.

## Module wiring (IN / GND / VCC)

| Module pin | Connect to |
| --- | --- |
| **GND** | Jetson GND (e.g. pins 6, 9, 14, 20, 25, 30, 34, 39) |
| **VCC** | Jetson **5 V** (pins 2 or 4) **only if** the 5 V rail can supply **~70–100 mA** for coil + LED + driver; otherwise use a **separate 5 V** supply with **common GND** |
| **IN** | Jetson **header pin 37** (this doc’s default) |

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
adjust pin / levels, then reload:

```bash
mkdir -p ~/.config/systemd/user/nina-ui-kiosk.service.d
cp desktop/nina-ui-kiosk.service.d/hoverboard-power-relay.conf.example \
   ~/.config/systemd/user/nina-ui-kiosk.service.d/hoverboard-power-relay.conf
# edit if needed
systemctl --user daemon-reload
systemctl --user restart nina-ui-kiosk.service
```

## Verification checklist (Jetson + hardware)

1. With relay env **unset**, confirm driving behaviour unchanged.
2. Set **`NINA_HOVER_RELAY_HEADER_PIN=37`**, **brake OFF** → measure pack: **energised**.
3. **Brake ON** → pack **cuts** within relay operate time; servos may still
   move if Dynamixel bus is on a **separate** supply.
4. **Brake OFF** → pack **restores**.
5. Android / PyQt **Emergency stop** → same as brake ON (cut).
6. Reboot Jetson → confirm default pack state matches your fail-safe design.
7. `journalctl --user -u nina-ui-kiosk -f` — look for
   `Hoverboard power relay: Jetson 40-pin header pin 37` at first setup.

## Related code

- [`nina/controllers/hoverboard_power_relay.py`](../nina/controllers/hoverboard_power_relay.py) — lazy `Jetson.GPIO` output
- [`nina/config/settings.py`](../nina/config/settings.py) — `HoverboardAxisSettings` fields + `load_settings` env wiring
- [`sirena_ui/workers/drive_controller.py`](../sirena_ui/workers/drive_controller.py) — brake toggle → `engage_brake` / `release_brake`

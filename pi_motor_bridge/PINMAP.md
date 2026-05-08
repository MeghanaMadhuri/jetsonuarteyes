# JYQD ↔ Jetson Orin Nano (Nina production wiring)

This document is the **canonical harness** for driving **2× JYQD V7.3E2**
controllers from a **Jetson Orin Nano** 40-pin header. BCM numbers and
physical pins match `nina.controllers.navigation_manager.DEFAULT_PINS` and
`Jetson.GPIO`’s **Orin Nano** table (same carrier mapping as Orin NX in
[NVIDIA/jetson-gpio](https://github.com/NVIDIA/jetson-gpio)).

**One-time setup:** enable hardware PWM on the header —  
`sudo /opt/nvidia/jetson-io/jetson-io.py` → Configure 40-pin Header → manual →
enable **`pwm0`** (pin 32) and **`pwm2`** (pin 33) → save → reboot.

Leave each JYQD **Signal** screw **unconnected**.

---

## Production pin table (Jetson → JYQD)

| Role | BCM | Physical (40-pin) | JYQD terminal | Env override |
|------|-----|-------------------|---------------|--------------|
| Left EL | 24 | 18 | EL | `NINA_NAV_L_EN` |
| Left Z/F | 6 | 31 | Z/F | `NINA_NAV_L_DIR` or `NINA_NAV_L_ZF` |
| Left VR (PWM) | **12** | **32** | VR | `NINA_NAV_L_PWM` |
| Right EL | 10 | 19 | EL | `NINA_NAV_R_EN` |
| Right Z/F | 23 | 16 | Z/F | `NINA_NAV_R_DIR` or `NINA_NAV_R_ZF` |
| Right VR (PWM) | **13** | **33** | VR | `NINA_NAV_R_PWM` |
| GND | — | e.g. **39** / **34** | GND | — |
| Logic 5 V | — | **2** or **4** | 5 V | — |

**VR rule:** Speed inputs **must** use **BCM 12 & 13** (pins 32 & 33) on this
stack — that is where `Jetson.GPIO` exposes stable hardware PWM after
`jetson-io`. Wiring VR to other BCMs and only changing `NINA_NAV_*_PWM`
typically **fails BLDC init** in the GUI.

Optional signals implemented in the same module:

| Role | BCM | Physical | Notes |
|------|-----|----------|-------|
| Status RED | 21 | 40 | Active-low LED helper |
| Status GREEN | 20 | 38 | Active-low LED helper |
| Status BLUE | 16 | 36 | Active-low LED helper |
| E-stop 1 | 17 | 11 | Input only (read when wired) |
| E-stop 2 | 5 | 29 | Input only |

Motor **high voltage** is **not** taken from the 40-pin header — connect the
JYQD **VCC** terminals to your **24 V** battery bus per your mechanical design.

---

## Raspberry Pi reference column (legacy bridge only)

If a **Raspberry Pi** runs `pi_motor_bridge/motor_bridge.py` and the Jetson uses
`NINA_NAV_MODE=remote`, the Pi firmware may still use the **original** BCM map
(BCM 18 / 25 / 22 for some EL/DIR lines). That map is **not** identical to the
Jetson `DEFAULT_PINS` above — see `pi_motor_bridge/navigation_bldc.py`.

> **Pi-only:** that module also sets **`L_SIG` / `R_SIG` (BCM 24 / 27)** as
> **JYQD feedback inputs**. Do **not** conflate those with Jetson **BCM 24**
> (left EL) or **BCM 27** (default **rear_right** HC-SR04 TRIG) — different boards,
> different roles.

| JYQD-L screw | Function | Pi BCM | Pi physical |
|--------------|----------|--------|-------------|
| EL | enable | 18 | 12 |
| Z/F | direction | 25 | 22 |
| VR | PWM speed | 12 | 32 |

| JYQD-R screw | Function | Pi BCM | Pi physical |
|--------------|----------|--------|-------------|
| EL | enable | 10 | 19 |
| Z/F | direction | 22 | 15 |
| VR | PWM speed | 13 | 33 |

## Direction polarity (software convention)

- Left forward → **L_DIR HIGH**
- Right forward → **R_DIR LOW** (mirrored vs left)

If a wheel runs backward from expectation: **`NINA_NAV_INVERT_LEFT=1`** or
**`NINA_NAV_INVERT_RIGHT=1`** on the Jetson.

## Ultrasonic ring collision

Production **L-DIR** is **BCM 6**. The HC-SR04 driver defaults **rear_right TRIG**
to **BCM 27** so it does not share that pin (`nina/sensors/hcsr04.py`).

## Troubleshooting: init OK but wheels never move

1. **Motor supply:** JYQD **VCC / battery** (e.g. 24 V) must be present; 5 V from
   the 40-pin header is **logic only** — without pack voltage the hubs will not
   turn even if GPIO looks fine.
2. **Minimal GPIO path:**  
   `PYTHONPATH=. python3 -m nina.app.main nav-diag-forward --speed 60 --hold 4`  
   This skips `stop()` + straight-line nudge. If hubs still do not spin, the
   problem is almost certainly **wiring or carrier pin routing**, not the full
   `nav-forward` sequence.
3. **PWM at the screw:**  
   `python3 -m nina.app.main nav-test-pin --pin 12 --mode pwm --duty 50 --hold 4`  
   (use your configured left-PWM BCM if not 12). Scope or meter **VR** at the
   JYQD while this runs.
4. **Alternate F/R:**  
   `python3 -m nina.app.main nav-test-direction --side both --speed 80`  
   If direction phases do nothing, **ZF / EL** may be on the wrong header pins.
5. **Custom carrier:** Jetson.GPIO’s BCM numbers assume the **dev-kit** strap.
   A third-party board may route the **40-pin plug** differently — confirm nets
   with the **carrier schematic**, not only BCM labels.
6. **Optional:** `NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC=0` disables the straight-line
   backlash nudge when testing `nav-forward` (unlikely to be the root cause if
   `nav-diag-forward` also fails).

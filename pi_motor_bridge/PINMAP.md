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

If **both** JYQDs were wired for the **same** direction sense (e.g. both
expect **HIGH** for forward), set **`NINA_NAV_INVERT_RIGHT=1`** so logical
forward drives **R_DIR HIGH** to match the left side.

If a wheel runs backward from expectation: **`NINA_NAV_INVERT_LEFT=1`** or
**`NINA_NAV_INVERT_RIGHT=1`** on the Jetson.

Some JYQD harnesses treat **EL as active-low** (GPIO low = armed). For
direct Jetson drive use **`NINA_NAV_EL_ACTIVE_LOW=1`** (local mode only).
That flag only changes **EL** behavior — it does **not** fix wrong **Z/F
(direction)** levels. If motion only appears when **Z/F (DR) is pulled to
GND**, treat it as a **direction polarity or wiring** issue: confirm the
**Z/F** screw is on the intended BCM, then try **`NINA_NAV_INVERT_LEFT=1`**
and/or **`NINA_NAV_INVERT_RIGHT=1`** (see above).

## When a wire **short** makes motion but normal software does not

If hubs move **only** while you bridge **Z/F (or VR) to GND** during a drive
command, treat that as an **electrical** signature — not a missing `nav-*`
flag. Software only asks the SoC to drive a pad; the **JYQD screw** must
see a **valid logic level** with a **solid return path**.

What a **short** actually does (why it is such a strong clue):

1. **Hard LOW** — A jumper to GND is near **0 Ω**. The JYQD input is pulled
   firmly below **VIL** (input low threshold). A GPIO pin *commanded* LOW still goes
   through **package + header + harness + connector**; input leakage,
   **optos**, or **internal / external pull-ups** on the driver can leave the
   net **above VIL** or **slow / ambiguous** if sink current is
   marginal. Your multimeter may read “0 V” at the screw with software and
   still miss µs-scale edges a scope would catch — but the pattern “short =
   good LOW, no short = dead” is classic **weak or reference-starved LOW**.

2. **Reference / GND** — The clip is almost always on **JYQD GND** (or pack
   return). If **Jetson header GND** and **JYQD logic GND** are not a **single
   low-impedance star**, GPIO has no clean **0 V reference** at the driver.
   A short **re-declares** “this is GND here.” Fix: **short, fat** GND runs from
   header pins **34/39** (and nearby GNDs) to **each** JYQD GND screw, same
   star as **24 V return** per your harness design.

3. **“Bad” header pads (still!)** — `navigation_manager.py` Notes A–C document
   Orin Nano pads that sit at **~1.5 V or ~2.4 V** “stuck” when the device tree
   claims them. If **any** `NINA_NAV_*` override or an old harness still uses
   **BCM 18 / 22 / 25** for EL or DIR, software and `nav-print-config` can
   look healthy while the **wire never toggles cleanly**. **Confirm** printed
   BCMs match **physically** routed nets (not RPi column of this doc). Bench:
   `python3 -m nina.app.pin_probe --pin <BCM>` while you meter **at the JYQD
   screw** (or scope).

4. **3.3 V vs JYQD threshold** — Some input stages want closer to **5 V**
   for a confident **HIGH**. Jetson is **3.3 V CMOS**. If forward needs a
   strong **HIGH** and the input barely recognizes it, a short to GND is not
   the fix for that case — but it rules in marginal **LOW** / GND issues when
   your working trick ties Z/F **low**.

**Software cannot increase GPIO sink/source current or fix a broken ground
strap.** Next steps: **scope** EL / Z/F / VR **at the driver screw** during
`nav-diag-forward`; verify `nav-print-config` BCMs vs wiring; add **buffers /
level shifters** (5 V–tolerant outputs) if the datasheet wants 5 V logic; never
rely on shorts as a workaround with **24 V** present.

## Ultrasonic ring collision

Production **L-DIR** is **BCM 6**. The HC-SR04 driver defaults **rear_right TRIG**
to **BCM 27** so it does not share that pin (`nina/sensors/hcsr04.py`).

## Troubleshooting: init OK but wheels never move

0. **Confirm this process sees your env:**  
   `python3 -m nina.app.main nav-print-config`  
   Run it in the **same** shell after `export NINA_NAV_INVERT_*=1`. If the
   printed `invert_*` stays `False`, the CLI never received the variable
   (systemd unit, typo, or different user session).
1. **Motor supply:** JYQD **VCC / battery** (e.g. 24 V) must be present; 5 V from
   the 40-pin header is **logic only** — without pack voltage the hubs will not
   turn even if GPIO looks fine.
2. **Minimal straight crawl (matches `forward()` start, not the full `nav-forward` stop/nudge tail):**  
   `PYTHONPATH=. python3 -m nina.app.main nav-diag-forward --speed 60 --hold 4`  
   Uses `_start_both_wheels` (kick + DIR settle). If hubs still do not spin, the
   problem is almost certainly **wiring, EL/VR, 24 V, or carrier pin routing**.  
   To drop the optional opposite-direction preload pulse:  
   `NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC=0` for that run.
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
6. **EL / Z(F) steady states:**  
   `python3 -m nina.app.main nav-probe-eldir --hold 15`  
   Arms both sides **forward** with **PWM 0** so you can meter **EL** and **Z/F**
   without motion. Expect left Z/F high and right Z/F low for “forward” (see
   **Direction polarity** above) when not using `NINA_NAV_INVERT_*`.
7. **Accidental “short Z/F or VR to GND” while commanding motion:**  
   **Do not do this on purpose** with motor pack present — it can destroy the
   Jetson, JYQD, or wiring. If you already saw behavior: tying **Z/F (DIR)** to
   GND forces **LOW** on that input. Default software uses **left Z/F HIGH**
   and **right Z/F LOW** for logical forward. If **forward** only makes sense
   when Z/F is dragged **LOW**, that often means the **left** channel should
   use the **same** sense as the right for “forward” — set
   **`NINA_NAV_INVERT_LEFT=1`** so logical forward drives **left Z/F LOW** (no
   short). If **both** drivers were built for **HIGH** on Z/F for forward, use
   **`NINA_NAV_INVERT_RIGHT=1`** instead or as well — use
   **`nav-print-config`** after each `export` to confirm flags.  
   **Shorting VR to GND** is **not** a direction test: it wrecks the speed
   reference (near 0 % duty). Any spin or “reverse” versus the Z/F-short case is
   **undefined** (faulty PWM path, coupling, or one side dominating) — fix VR
   wiring and use **`nav-test-pin`** on the **PWM BCM** with `--mode pwm`.
8. **Optional:** `NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC=0` disables the straight-line
   backlash nudge when testing `nav-forward` (unlikely to be the root cause if
   `nav-diag-forward` also fails).

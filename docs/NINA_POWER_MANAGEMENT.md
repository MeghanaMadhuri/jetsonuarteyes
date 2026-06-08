# Nina Power Management — Design & Implementation Guide

How to add a **power-saving mode** to the Nina Jetson kiosk (Sirena UI): idle sensor stand-down, display sleep, and wake from **touch**, **double-tap**, **tablet**, or **voice**. This document is the product and engineering spec. **Phase 1–2** (``PowerManager``, L1 stand-down, L2 overlay/DPMS/backlight, HTTP wake/status, Settings → Display) are implemented in software; wake-word (phase 4) and fleet metrics (phase 5) are still outstanding.

**Related docs:** [sirena_ui/docs/NINA_APP.md](../sirena_ui/docs/NINA_APP.md), [docs/NINA_Client_User_Manual.txt](NINA_Client_User_Manual.txt), [docs/NINA_VOICE_EDGE.md](NINA_VOICE_EDGE.md), [REQUIREMENTS.md](../REQUIREMENTS.md).

---

## 1. Executive summary

| Today | Planned / status |
|-------|------------------|
| Camera and heavy detectors stop when leaving Vision / Drive / Perception screens | **Done:** global **idle timer** (`PowerManager`) forces stand-down |
| No display blanking; kiosk runs fullscreen while `nina-ui-kiosk` is active | **Done (L2):** black overlay + DPMS + sysfs backlight off |
| No published power figures | **§2.1** — approx. **30–50%** L0 heavy→L1, **45–65%** L0 heavy→L2 (measure on fleet) |
| Settings → Display / Privacy / Voice show “Coming soon” placeholders | **Display wired;** Privacy / Voice still placeholders |
| Voice is **push-to-hold** on the Voice screen; no wake word on edge stack | Optional **wake-word listener** only in L2 sleep (not implemented) |
| Touch (AT42QT2120) triggers **safety** (stop, park), not UI wake | **Done in sleep:** **double-tap** = wake; in active: existing safety |
| Tablet HTTP gateway stays up on port 8787 | **Done:** `GET /v1/system/power-state`, `POST /v1/system/wake` |

**Do not** stop `nina-ui-kiosk` or close the Dynamixel bus for “sleep” — that would drop the tablet gateway and require a full bus re-init on wake.

---

## 2. Power states (three tiers)

Use three explicit tiers so safety and UX stay clear:

| Tier | Name | User-visible behavior | Typical trigger |
|------|------|----------------------|-----------------|
| **L0** | Active | Normal kiosk; sensors per open screen | Any user or tablet activity |
| **L1** | Idle | UI on (usually Home); camera off; background polls reduced | No activity for `NINA_POWER_IDLE_SEC` (e.g. 300 s) |
| **L2** | Deep sleep | Black screen; backlight off; minimal daemons | No activity for `NINA_POWER_SLEEP_SEC` (e.g. 900 s) |

```text
  ACTIVE ──(idle timeout)──► IDLE ──(sleep timeout)──► SLEEP
     ▲                        │                        │
     └──── wake (any source) ──┴────────────────────────┘
```

**L1** reuses existing screen `on_enter` / `on_leave` patterns. **L2** adds display power savings and optional always-on wake listeners.

### 2.1 Approximate power savings

All figures below are **order-of-magnitude estimates** for a typical Nina fleet build (Jetson Orin Nano 8 GB, 10.1″ HDMI panel, USB RGB camera, optional RealSense / RPLidar, 13× Dynamixel holding torque, Wi‑Fi, hoverboard electronics idle on 24 V). **Measure on your chassis** before quoting runtimes to customers.

**How to measure on the robot**

| Method | Command / tool |
|--------|----------------|
| Pack current (best) | Inline meter on 24 V feed, or ADS1115 pack monitor trend over 10+ minutes per state |
| Jetson only | `sudo tegrastats` or `jtop` — SoC power column while holding each state |
| Display only | Compare `brightness` sysfs 100% vs `0` with panel on Home (no camera) |

Log the state (`active` / `idle` / `sleep`) in `journalctl` when testing so samples line up with `PowerManager` transitions.

#### Reference baselines (whole-robot, 24 V system)

| Profile | What the operator is doing | Approx. system power |
|---------|----------------------------|----------------------|
| **L0 heavy** | Drive or Vision open: live camera, optional face/YOLO, Qt UI full brightness | **28–45 W** |
| **L0 light** | Home (or Settings), no camera, no SLAM/voice | **14–22 W** |
| **L1 idle** | Home visible, camera & heavy threads off, monitors stopped | **12–18 W** |
| **L2 sleep** | Black screen, backlight off, gateway + bus + optional touch/wake mic | **8–14 W** |

Hoverboard hub motors **not driving**; arms at neutral/brake. Driving, SLAM navigation, or voice conversation add **+5–20 W** on top of L0 heavy.

#### Savings by tier (vs previous tier)

| Transition | Approx. power saved | Approx. % of prior state | Main contributors |
|------------|--------------------:|-------------------------:|-------------------|
| **L0 heavy → L1** | **8–18 W** | **~30–50%** | USB camera off; Vision/YOLO/TRT stopped; SLAM/lidar off; IMU/IR polls stopped; voice edge stopped (if configured) |
| **L0 light → L1** | **1–4 W** | **~10–20%** | Same software stand-down; smaller delta if GPU was already idle |
| **L1 → L2** | **4–8 W** | **~25–40%** | Panel backlight + DPMS (~3–6 W); slightly lower Jetson load (static black UI) |
| **L0 heavy → L2** | **14–28 W** | **~45–65%** | Sum of sensor/vision savings + display off |

#### Per-subsystem savings when stand-down runs

Incremental savings when each item is turned off (typical; overlapping loads mean sums are not exact):

| Subsystem / action | Approx. savings | Notes |
|--------------------|----------------:|-------|
| USB RGB camera + capture thread | **1.5–3 W** | Sensor + USB + small CPU; Jetson ISP path idle |
| Face (YuNet) + object (YOLO/TRT) on Jetson GPU | **3–10 W** | Dominates when both enabled; **0 W** if already off on Home |
| RealSense depth stream (Perception) | **2–4 W** | USB + depth ASIC |
| RPLidar A1 scan (SLAM / Map) | **3–5 W** | Motor + USB |
| Voice edge (ASR + LLM + TTS idle) | **3–8 W** | Stop in L1/L2 unless wake-word sidecar needed |
| Wake-word listener only (L2) | **−0.3–1 W** | *Costs* power vs fully off voice; still far below full ASR |
| 10.1″ backlight → off (sysfs + DPMS) | **3–6 W** | Largest single win in L2; panel-dependent |
| Backlight dim 70% → 30% (optional L1) | **1–2 W** | If Settings brightness wired |
| IMU / battery / IR / ESP32 poll threads | **< 0.5 W** | Minor; worth doing for CPU wakeups, not battery alone |
| Qt UI animations / Home health poll | **< 0.5 W** | Stopping Home timers is small but reduces jitter |
| Dynamixel bus (13 servos holding) | **0 W in spec** | **Not powered down** — holding torque unchanged (~3–10 W always) |
| Wi‑Fi + HTTP gateway :8787 | **0 W in spec** | Left on for tablet wake (~1–2 W) |
| Hoverboard driver boards (idle) | **0 W in spec** | Factory electronics; lean at brake |

#### Example battery runtime (illustrative)

Assume a **24 V × 12 Ah** pack (~288 Wh usable before low-voltage cutout; real usable less due to Peukert and cutout):

| State | Assumed avg. power | Theoretical runtime |
|-------|-------------------:|--------------------:|
| L0 heavy | 35 W | ~8 h |
| L1 idle | 15 W | ~19 h |
| L2 sleep | 11 W | ~26 h |

**Event / overnight:** 8 h in L2 instead of L0 light saves roughly **(18−11)×8 ≈ 56 Wh** (~**20%** of pack capacity). Use measured W for contract numbers.

#### What power saving does *not* do

| Item | Approx. power | Comment |
|------|---------------:|---------|
| Full Jetson shutdown (Settings → Power) | **~0.5–2 W** | Only hoverboard BMS/LED leakage if mains removed from Jetson |
| Stopping `nina-ui-kiosk` only | Small | Bus drop breaks product; not recommended |
| `nvpmodel` / `jetson_clocks` downgrade (future) | **2–5 W** extra possible | Not in v1 spec; optional fleet tuning |

#### Summary for stakeholders

| Goal | Target state | Expect |
|------|--------------|--------|
| Less heat & fan noise during a paused demo | **L1** after 5 min idle | **~30–50%** vs Drive/Vision active |
| Overnight on battery in venue | **L2** after 15 min | **~45–65%** vs active demo; **measure** pack before promising “all night” |
| Wake without touching HDMI | **L2** + tablet / double-tap / voice | Small **extra** draw vs L2 with display-only wake |

---

## 3. Architecture — single owner: `PowerManager`

### 3.1 New module (recommended)

| Item | Detail |
|------|--------|
| Path | `sirena_ui/workers/power_manager.py` (or `sirena_ui/power_manager.py`) |
| Owner | One instance on `MainWindow`, same lifetime as `NinaService` |
| API | `note_activity(source)`, `enter_idle()`, `enter_sleep()`, `wake(reason)`, `state`, `can_sleep() -> (bool, reason)` |
| Signals | `state_changed(str)` for UI overlay and HTTP status |

### 3.2 Integration points (existing files)

| File | Change |
|------|--------|
| `sirena_ui/main_window.py` | Create `PowerManager`; global `QApplication` event filter (mouse/touch/key → `note_activity`); call `note_activity` on `navigate()` |
| `sirena_ui/workers/nina_service.py` | `stand_down_background(reason)` / `restore_background(reason)` — start/stop monitors, vision shutdown |
| `sirena_ui/android_gateway/fastapi_app.py` | `GET /v1/system/power-state`, `POST /v1/system/wake` |
| `sirena_ui/screens/settings_screen.py` | Replace placeholder panes for Display / Privacy / Voice |
| `android/.../SirenaHomeScreen.kt`, `SirenaSettingsScreen.kt`, `LinkClient.kt` | **Done:** power-state poll, **Wake robot** (`POST /v1/system/wake`) on Home + Settings Display/Power |
| `desktop/nina-ui-kiosk.service` | Document `NINA_POWER_*` in `navigation.env` (via `EnvironmentFile`) |
| `docs/NINA_Client_User_Manual.txt` | Operator section: sleep + wake methods |

### 3.3 What stays running in all tiers

| Component | Why keep alive |
|-----------|----------------|
| `nina-ui-kiosk` process | Tablet control + in-process wake |
| Dynamixel bus + brake/neutral on lean axes | Arms must not drop; avoid re-home every wake |
| HTTP gateway `:8787` | Tablet wake and status |
| Wi‑Fi / `nina-link` | Remote wake |
| Touch monitor (L2 only, if double-tap wake enabled) | Capacitive wake without full safety script |

---

## 4. Subsystem stand-down and restore

### 4.1 Master table — what stops and how it wakes back

| Subsystem | L0 Active | L1 Idle | L2 Sleep | How stopped | Wake trigger | How restored |
|-----------|-----------|---------|----------|-------------|--------------|--------------|
| **Display — Qt overlay** | Off | Optional dim | **On (black)** | Show fullscreen child widget | Single tap on overlay | Hide overlay |
| **Display — DPMS** | On | On | **Blank** (`xset dpms force off`) | Subprocess on L2 enter | Any wake | `xset dpms force on` |
| **Display — backlight (sysfs)** | User brightness | Optional dim | **0 or min** | Save → write `/sys/class/backlight/*/brightness` | Any wake | Restore saved value |
| **UI screen** | Any | Prefer **Home** | Home + overlay | `navigate("home")` | Touch / voice / tablet | Overlay off; user navigates for features |
| **Home timers** | Running | **Stopped** | Stopped | `home_screen.on_leave()` | Wake → L0 | `home_screen.on_enter()` |
| **VisionWorker / camera** | If screen holds refcount | **Off** | Off | `vision.release()` until 0; optional `shutdown()` | Open Drive/Vision/Perception | Screen `on_enter` → `acquire()` (lazy) |
| **Face / YOLO** | If enabled | **Forced off** | Off | `set_face_enabled(False)`, etc. | User toggles on Vision/Drive | `on_enter` + toggles |
| **Perception depth** | If Perception open | Released | Released | `autonomy.release_depth()` | Open Perception | Perception `on_enter` |
| **SLAM / lidar** | If Map/Perception | **Pause/stop** | Stop | `slam.pause()` or shutdown subset | Open Map/Perception | Screen `on_enter` |
| **Autonomy pilot** | If enabled | **Disabled** | Disabled | `autonomy._disable()` | Operator re-enables | Perception/Drive UI |
| **MPU9250 IMU monitor** | On Drive | **Stopped** | Stopped | New `stop_mpu9250_imu_monitor()` | Open Drive | `start_mpu9250_imu_monitor()` |
| **Battery ADS1115** | App / Drive / Home | **Stopped** | Stopped | `battery_monitor.stop()` | Wake L0 | `main_window` boot sequence |
| **IR obstacle stop** | Often on at boot | Optional off in L1 | **Stopped** | `ir_obstacle_monitor.stop()` | Wake L0 | `start_ir_obstacle_stop_monitor()` |
| **ESP32 trigger** | If enabled | Optional keep | **Stopped** | `esp32_trigger_monitor.stop()` | Wake L0 | `start_esp32_trigger_monitor()` |
| **AT42QT2120 touch** | Safety reactions | Normal | **Wake-only mode** | Do not stop if double-tap wake on | **Double-tap** | Restore safety mode |
| **Touch safety (brake/park/TTS)** | On touch | Active | **Suppressed until wake** | Branch in `run_touch_reaction` | Double-tap or other wake | Normal after wake |
| **Voice edge servers** | If enabled | Optional keep | **Stopped** | `stop_voice_assistant()` | Wake word or Voice screen | `start_voice_assistant()` if env on |
| **Wake-word listener** | Off | Off | **On (L2 only)** | Sidecar or thread | Phrase match | Stop on wake |
| **On-screen keyboard** | On demand | Hidden | Hidden | OSK hide | Text focus | Show on demand |
| **Drive / BLDC / lean** | Brake when idle | **Keep brake** | Keep | Do **not** `drive.shutdown()` / `dxl.close()` | — | Unchanged until user drives |

**Light stand-down** should mirror monitor stops already in `NinaService.shutdown()` (IMU, battery, touch, ESP32, IR, vision, autonomy, slam) but **exclude** `drive.shutdown()` and `dxl.close()` unless a separate “storage mode” is defined.

### 4.2 Restore sequence (single pipeline)

Every wake path should call the same function:

```text
wake(source):
  1. If blocked: return (optional: log reason)
  2. state := ACTIVE; reset idle timers
  3. Display: hide overlay → xset dpms on → restore sysfs backlight
  4. Touch monitor: mode := normal (safety enabled)
  5. Stop wake-word listener (if running)
  6. restore_background():
       - start_battery_ads1115_monitor()
       - start_ir_obstacle_stop_monitor()
       - start_esp32_trigger_monitor()  (if configured)
       - start_touch_at42qt2120_monitor()  (if not running)
       - start_voice_assistant()  (if NINA_VOICE_EDGE_ENABLE)
  7. UI: refresh header battery; home.refresh_battery_pill()
  8. Optional: short earcon or status pill "Awake"
  9. Do NOT auto-open camera — wait for Drive/Vision/Perception on_enter
 10. Log source: display_touch | double_tap | tablet | voice | keyboard
```

**Lazy restore:** camera, SLAM, depth, IMU follow existing per-screen `on_enter` hooks after wake.

---

## 5. Display sleep (L2)

Implement in order:

| Step | Method | Pros | Cons |
|------|--------|------|------|
| 1 | **Qt black overlay** on `MainWindow` | No X11 assumptions; first tap wakes | Backlight may still draw power |
| 2 | **DPMS** via `xset` (same user/DISPLAY as kiosk) | Real panel blanking | Must match `QT_QPA_PLATFORM=xcb` session |
| 3 | **sysfs backlight** | Lowest panel power | Panel-dependent path |

### 5.1 Qt overlay

- Full-window `QWidget`, black (+ optional clock/logo).
- `setAttribute(Qt.WA_TransparentForMouseEvents, False)` so the first tap calls `wake("display_touch")`.
- On L2 enter: show overlay; optionally hide or dim sidebar/stack behind it.

### 5.2 DPMS

```bash
# Example — sync timeouts with NINA_POWER_SLEEP_SEC in PowerManager
xset dpms force off    # enter_sleep
xset dpms force on     # wake
```

Run from Python with `subprocess` (same pattern as `sirena_ui/host_power.py`). Use the kiosk user’s `DISPLAY` / `XAUTHORITY`.

### 5.3 sysfs backlight

1. Discover path: `/sys/class/backlight/*/brightness` and `max_brightness`.
2. On L2 enter: save current brightness → write `0` (or minimum).
3. On wake: restore saved value.
4. Bind Settings → Display “Brightness” slider to this path when implemented.

Env: `NINA_BACKLIGHT_SYSFS` = full path to `brightness` file if auto-discovery fails.

---

## 6. Wake sources

### 6.1 Summary

| Method | When | Mechanism |
|--------|------|-----------|
| **Single tap (display)** | L2 only | Qt global event filter: `TouchBegin`, `MouseButtonPress`, `KeyPress` |
| **Double-tap (cap touch)** | L2 only | AT42QT2120: two press edges within `NINA_WAKE_DOUBLE_TAP_MS` (e.g. 600 ms) |
| **Tablet** | L1 or L2 | `POST /v1/system/wake` (authenticated) |
| **Tablet implicit** | Optional | Recent authenticated HTTP or “connected” → `note_activity("tablet")` |
| **Voice phrase** | L2 only | Porcupine / openWakeWord / light keyword engine (not full Whisper loop) |
| **Keyboard** | Optional | Any key wakes (bench / dev) |

### 6.2 Single tap (HDMI / USB touch)

Install on `QApplication` or `MainWindow`:

- If `state == SLEEP` → `wake("display_touch")`.
- Else → `note_activity("display_touch")`.

### 6.3 Double-tap gesture (capacitive skin / bezel)

Today `TouchAt42qt2120Monitor` calls `NinaService.run_touch_reaction()` (stop drive, park, TTS) — **not** UI wake.

**Sleep behavior:**

| Mode | Touch behavior |
|------|----------------|
| ACTIVE / IDLE | Keep existing safety |
| SLEEP | Count press edges; two within window → `wake("double_tap")` **without** full safety script |

Implementation sketch:

- `PowerManager.is_sleeping()` checked at start of `run_touch_reaction` (or in monitor callback).
- `NINA_POWER_WAKE_DOUBLE_TAP=1` to enable.
- Require release between taps; respect existing `NINA_TOUCH_DEBOUNCE`, `NINA_TOUCH_STUCK_SEC`.

If double-tap wake is disabled, touch monitor may be stopped in L2 (wake = display tap or tablet only).

### 6.4 Tablet / companion

**New HTTP (in `fastapi_app.py`):**

```http
GET  /v1/system/power-state
POST /v1/system/wake
     Body: { "source": "tablet" }   # optional
```

- Use existing `auth_mutate` / pair token like other mutating routes.
- Handler must run `PowerManager.wake()` on the **Qt main thread** (`QTimer.singleShot(0, ...)`).

**Android:**

- “Wake robot” on Home or Settings.
- Optional: auto-wake on successful connect.
- Show `power-state` in UI (Asleep / Idle / Active).

### 6.5 Voice wake

Current stack ([NINA_VOICE_EDGE.md](NINA_VOICE_EDGE.md)): **no wake word**; Voice screen uses **press-and-hold** mic.

For L2 sleep only:

```text
USB mic → wake listener (low CPU) → wake("voice")
              │
              └── only when state == SLEEP
```

| Option | Notes |
|--------|------|
| Thread inside kiosk | `WakeWordWorker` in `NinaService`; mic contention with ASR |
| **Sidecar** `nina-wake-word.service` | Unix socket / D-Bus → kiosk; cleaner when voice servers are stopped in L2 |

After wake: optional navigate to Voice; optional one-shot PTT without holding button (phase 2).

Env: `NINA_POWER_WAKE_VOICE`, `NINA_WAKE_WORD`, model path, sensitivity.

---

## 7. Safety guards — when sleep is forbidden

`PowerManager.can_sleep()` must return `(False, reason)` when:

| Condition | Source |
|-----------|--------|
| Wheels moving / not idle | `drive_controller` / hoverboard state |
| Movements or Actions playback active | `NinaService` / action runner |
| Lean cal screen open | `lean_cal_screen` |
| Autonomy enabled and navigating | `AutonomyController` |
| Recent touch safety cooldown | `NINA_TOUCH_COOLDOWN_SEC` |

While blocked: reset or freeze idle timer; log `sleep_blocked` at debug level.

---

## 8. Activity tracking

Reset idle timer on:

| Source | Examples |
|--------|----------|
| UI | Mouse, touch, key, navigation |
| Robot screen | `on_enter` on any screen (optional) |
| Tablet | Authenticated HTTP within last N seconds (e.g. 30) |
| Voice | PTT press (when Voice screen active) |

Do **not** count passive MJPEG viewers as activity unless product wants “streaming prevents sleep.”

---

## 9. Environment variables

Add to `/etc/nina-link/navigation.env` (or `systemctl --user edit nina-ui-kiosk`):

| Variable | Default (suggested) | Purpose |
|----------|---------------------|---------|
| `NINA_POWER_ENABLE` | `1` | Master enable |
| `NINA_POWER_IDLE_SEC` | `300` | L1 threshold |
| `NINA_POWER_SLEEP_SEC` | `900` | L2 threshold |
| `NINA_BACKLIGHT_SYSFS` | (auto) | Path to `brightness` file |
| `NINA_POWER_WAKE_DISPLAY_TAP` | `1` | Single tap on overlay |
| `NINA_POWER_WAKE_DOUBLE_TAP` | `1` | Double-tap on cap touch |
| `NINA_WAKE_DOUBLE_TAP_MS` | `600` | Max gap between taps |
| `NINA_POWER_WAKE_TABLET` | `1` | HTTP wake |
| `NINA_POWER_WAKE_VOICE` | `0` | Wake word in L2 (off until model shipped) |
| `NINA_WAKE_WORD` | `hey_nina` | Phrase / model id |

---

## 10. HTTP API (spec)

### 10.1 GET `/v1/system/power-state`

Example response:

```json
{
  "state": "sleep",
  "idle_sec_remaining": 0,
  "sleep_sec_remaining": 0,
  "wake_sources_enabled": {
    "display_touch": true,
    "double_tap": true,
    "tablet": true,
    "voice": false
  }
}
```

States: `active` | `idle` | `sleep`.

### 10.2 POST `/v1/system/wake`

Requires auth (same as `/v1/system/poweroff`). Schedules `PowerManager.wake("tablet")` on Qt thread.

---

## 11. Settings UI (replace placeholders)

Today `sirena_ui/screens/settings_screen.py` uses `_build_placeholder_pane()` with “Coming soon” for Display, Privacy, and Voice.

| Control | Maps to |
|---------|---------|
| Screen sleep (idle / deep) | `NINA_POWER_IDLE_SEC`, `NINA_POWER_SLEEP_SEC` |
| Brightness slider | sysfs backlight + optional gamma |
| Camera / mic when idle | Flags into `PowerManager` |
| Wake word | Enable + phrase → wake engine |

Android `SirenaSettingsScreen.kt`: mirror via HTTP or local prefs that POST to robot.

---

## 12. Current behavior (baseline — no PowerManager)

Without the feature, power is saved **per screen** only:

| Mechanism | File |
|-----------|------|
| `on_leave` releases camera, stops timers | `vision_screen.py`, `drive_screen.py`, `perception_screen.py`, `voice_screen.py`, `home_screen.py` |
| `VisionWorker` refcount | `vision_worker.py` — `acquire()` / `release()` |
| IR sensor closed when not driving | `NINA_IR_OBSTACLE_MOTION_GATED` |
| Full host off | `host_power.py` — Settings → Power shutdown/reboot |

Operators today: leave **Home** or **Actions** when idle; use **Power → Shutdown** at end of day. Camera stops when not on Drive/Vision/Perception.

---

## 13. Implementation phases

| Phase | Scope | Outcome |
|-------|--------|---------|
| **1** | `PowerManager`, L1 stand-down, activity filter, HTTP wake + status | Idle saves CPU; tablet can wake |
| **2** | L2 overlay + DPMS + sysfs backlight | Real display power down |
| **3** | Double-tap wake, touch sleep mode | Wake without HDMI tap |
| **4** | Wake-word sidecar + Settings/Android wiring | Hands-free wake from sleep |
| **5** | Fleet tuning, manual §, power metrics in logs | Production readiness |

Suggested first PR: **Phase 1 + Phase 2** (no voice wake).

---

## 14. Testing checklist

| Test | Pass criteria |
|------|----------------|
| Idle → L1 after `NINA_POWER_IDLE_SEC` | Camera off; navigated to Home; monitors stopped per table |
| L1 → L2 after sleep timeout | Black screen; backlight 0; gateway still answers ping |
| Tap display in L2 | Overlay gone; backlight restored; Home visible |
| Double-tap cap in L2 | Wake without safety TTS (when configured) |
| Single tap cap in L2 (safety off) | No brake/park unless product wants it |
| `POST /v1/system/wake` from tablet | Same as tap wake |
| Sleep blocked while driving | No L1/L2 until stop |
| Open Drive after wake | Camera acquires; preview works |
| `journalctl` | `wake(source=tablet)` (or equivalent) logged |

---

## 15. Anti-patterns

| Do not | Why |
|--------|-----|
| `systemctl stop nina-ui-kiosk` for sleep | Kills gateway, bus state, and pairing UX |
| Full Whisper ASR as wake word | Too heavy; use dedicated keyword engine |
| Disable touch safety globally | Branch on `PowerManager.state` |
| `NinaService.shutdown()` on sleep | Closes DXL bus and drive — use light `stand_down_background` only |
| Sleep while wheels commanded | Use `can_sleep()` guards |

---

## 16. Operator quick reference (for client manual)

When implemented, document for field staff:

1. **Asleep:** screen is black; robot still on Wi‑Fi.
2. **Wake:** tap the display once **or** double-tap the capacitive skin **or** open the tablet app → **Wake robot** **or** say the wake phrase (if enabled).
3. **After wake:** Home screen; open **Drive** or **Vision** to turn the camera on again.
4. **End of day:** Settings → **Power** → Shutdown (full Jetson off), not sleep.

---

## 17. File checklist (implementation)

| Area | Path |
|------|------|
| Core | `sirena_ui/workers/power_manager.py` (new) |
| Window | `sirena_ui/main_window.py` |
| Service | `sirena_ui/workers/nina_service.py` |
| HTTP | `sirena_ui/android_gateway/fastapi_app.py` |
| Settings | `sirena_ui/screens/settings_screen.py` |
| Touch | `nina/sensors/touch_at42qt2120_monitor.py`, `nina_service.run_touch_reaction` |
| Host display | `sirena_ui/host_power.py` (reference for subprocess pattern) |
| Deploy | `desktop/nina-ui-kiosk.service`, `/etc/nina-link/navigation.env` |
| Voice wake | `nina/voice/` or new `nina/wake_word/` |
| Android | `android/.../SirenaSettingsScreen.kt`, API client |
| Docs | `docs/NINA_Client_User_Manual.txt` |

---

*Document version: 1.1 — spec only; power figures are estimates; software implementation tracked separately.*

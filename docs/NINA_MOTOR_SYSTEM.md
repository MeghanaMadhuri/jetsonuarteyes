      # Nina Motor System — Technical Reference

      How Nina’s **13 Dynamixel servos** are identified, wired, and controlled on the Jetson: **11** for arms and neck, **2** for hoverboard lean (drive). Locomotion works by tilting the hoverboard driver modules with IDs **12** and **13**; the hoverboard’s onboard electronics then run the hub motors (brake, forward, reverse, pivot).

      **Related docs:** [REQUIREMENTS.md](../REQUIREMENTS.md), [sirena_ui/docs/NINA_APP.md](../sirena_ui/docs/NINA_APP.md).

      ---

      ## 1. Executive summary

      | Subsystem | Hardware | Count | Control path |
      |-----------|----------|-------|----------------|
      | **Upper body** | Robotis Dynamixel (arms + neck) | **11** (IDs **1–11**) | UART → `DynamixelManager` / `ActionRunner` |
      | **Hoverboard lean + drive** | Robotis Dynamixel MX-28 | **2** (IDs **12**, **13**) | Same UART → `HoverboardAxisDrive` |
      | **Hub wheels** | Hoverboard motors + factory driver boards | **2** | Driven by hoverboard electronics when lean axes tilt |

      All **13** servos share one USB–UART bus. Software commands only the Dynamixels; wheel power comes from the hoverboard after hall/magnet state changes from tilt.

      ```python
      # nina/config/motor_ids.py
      ARM_MOTOR_IDS = list(range(1, 12))      # 1..11
      HOVERBOARD_LEAN_IDS = [12, 13]
      EXPECTED_DYNAMIXEL_IDS = ARM_MOTOR_IDS + HOVERBOARD_LEAN_IDS  # 13
      ```

      ---

      ## 2. Architecture

      ```
                          ┌─────────────────────────────────────────┐
                          │         Jetson Orin Nano                 │
                          │  Sirena UI / companion → DriveController │
                          └────────────────────┬────────────────────────┘
                                              │ USB-UART  /dev/ttyUSB*
                                              ▼
                          ┌─────────────────────────────────────────┐
                          │     Dynamixel bus (Protocol 1.0 @ 222222) │     
                          └────────────────────┬────────────────────────┘
                ┌─────────────────────────────┼─────────────────────────────┐
                ▼                             ▼                             ▼
            IDs 1–11                      ID 12 (L lean)               ID 13 (R lean)
            Arms + neck                   MX-28                        MX-28
                │                             └──────────┬──────────┘
                │                                        ▼
                │                          Hoverboard driver modules (tilt)
                │                          → hall: brake / FWD / REV per side
                │                                        ▼
                │                          Hoverboard hub motors (24 V)
                ▼
            5 DOF × 2 arms + 1 neck
      ```

      | Layer | Module | Responsibility |
      |-------|--------|----------------|
      | Bus I/O | `nina/controllers/dynamixel_manager.py` | Ping, registers, SyncWrite, health, playback |
      | Arm actions | `nina/controllers/action_runner.py` | Named poses (`neutral`, `namaste`, …) for IDs 1–11 |
      | Locomotion | `nina/controllers/hoverboard_axis_drive.py` | Brake, forward/back lean, pivots, pulse series, optional IMU correction |
      | UI | `sirena_ui/workers/nina_service.py` | `DynamixelManager`, `bus_lock`, `HoverboardAxisDrive` → `DriveController` |
      | Drive UI | `sirena_ui/workers/drive_controller.py` | Teleop: D-pad, timed turns, straight pulses |

      ---

      ## 3. Dynamixel bus

      ### 3.1 Connection

      | Parameter | Default | Variable |
      |-----------|---------|----------|
      | Device | `/dev/ttyUSB0` | `NINA_DXL_PORT` |
      | Baud | **222222** | `NINA_DXL_BAUD` |
      | Protocol | Dynamixel **1.0** | — |
      | IDs | **1–13** | — |

      Use **FTDI or CP2102** USB–TTL on the Jetson; user in group **`dialout`**.

      ### 3.2 Joint mode

      Production servos use **joint mode** (position 0–4095 → 0°–300°). On boot, `ensure_joint_mode_for_ids([12, 13])` fixes lean axes if angle limits were 0/0 (wheel mode).

      ### 3.3 Registers (Protocol 1.0)

      | Register | Addr | Size | Use |
      |----------|------|------|-----|
      | Torque Enable | 24 | 1 | Hold / release |
      | CW / CCW Angle Limit | 6, 8 | 2 each | Joint mode |
      | Goal Position | 30 | 2 | Command pose |
      | Moving Speed | 32 | 2 | 0 = fastest (Protocol 1) |
      | Present Position | 36 | 2 | Feedback / record |

      Resolution ≈ **0.0732°/count**. Bus timing: 0.25 s settle, 4 ms between packets; torque via **SyncWrite** to all IDs.

      ---

      ## 4. Upper body — IDs 1–11

      Ten arm servos (five per side, bottom → top) plus one neck.

      #### Right arm

      | ID | Label | Model | Joint |
      |----|-------|-------|-------|
      | **1** | R-Pitch1 | MX-28 | Pitch |
      | **3** | R-Pitch2 | MX-28 | Pitch |
      | **5** | R-Roll1 | MX-28 | Roll |
      | **7** | R-Pitch3 | MX-106 | Pitch |
      | **9** | R-Roll2 | MX-106 | Roll |

      #### Left arm

      | ID | Label | Model | Joint |
      |----|-------|-------|-------|
      | **2** | L-Pitch1 | MX-28 | Pitch |
      | **4** | L-Pitch2 | MX-28 | Pitch |
      | **6** | L-Roll1 | MX-28 | Roll |
      | **8** | L-Pitch3 | MX-106 | Pitch |
      | **10** | L-Roll2 | MX-106 | Roll |

      #### Neck

      | ID | Label | Model |
      |----|-------|-------|
      | **11** | Neck | MX-28 |

      **MX-28:** 1, 2, 3, 4, 5, 6, 11 — **MX-106:** 7, 8, 9, 10.

      **Operations:** Neutral boot pose via action `neutral` (`NINA_NEUTRAL_ACTION`) at goal **2048** per arm/neck. Record/playback in Sirena UI or `carbot_main/nina_record.py`; recordings use IDs **1–11** only. `ActionRunner.play_smooth()` at 50 Hz by default.

      ---

      ## 5. Hoverboard lean — IDs 12 and 13

      Two **MX-28** servos tilt the left/right hoverboard driver modules. Hall sensing selects brake, forward, or reverse; the hoverboard ESC drives each hub motor.

      | ID | Side |
      |----|------|
      | **12** | Left lean |
      | **13** | Right lean |

      Configure with `NINA_HOVER_ID_LEFT` / `NINA_HOVER_ID_RIGHT` if rewired.

      ### 5.1 Brake (idle)

      | Servo | Default (ticks) | Variable |
      |-------|-----------------|----------|
      | Left (12) | **2048** | `NINA_HOVER_BRAKE_POS_LEFT` |
      | Right (13) | **2048** | `NINA_HOVER_BRAKE_POS_RIGHT` |

      `apply_hoverboard_brake_positions()` at bus init, after safety stops, and end of pulse sequences.

      ### 5.2 Straight lean (defaults)

      | Direction | Left (12) | Right (13) | Variables |
      |-----------|-----------|------------|-----------|
      | UI forward | 2022 | 2080 | `NINA_HOVER_FWD_POS_LEFT`, `NINA_HOVER_FWD_POS_RIGHT` |
      | UI backward | 2100 | 2000 | `NINA_HOVER_REV_POS_LEFT`, `NINA_HOVER_REV_POS_RIGHT` |

      Code defaults match the current fleet bench tune (`NINA_HOVER_SWAP_FWD_REV_ENV_VALUES=0`). Straight forward also applies per-side trim: `NINA_HOVER_STRAIGHT_FWD_LEFT_TICKS_OFFSET` (default **−10**, motor 12) and `NINA_HOVER_STRAIGHT_FWD_RIGHT_TICKS_OFFSET` (default **+10**, motor 13) on top of `NINA_HOVER_FWD_POS_*`. Optional straight prime: `NINA_HOVER_STRAIGHT_PRIME_POS` (default 2048).

      ### 5.3 Turns

      **Turn left:** left toward forward goals, right toward backward (mirror for right). Overrides: `NINA_HOVER_TURN_LEFT_POS_*`, `NINA_HOVER_TURN_RIGHT_POS_*`. `NINA_HOVER_SWAP_TURN_LR` defaults off after the hall swap; set `1` if GUI yaw is still reversed.

      ### 5.4 Slew rate

      `NINA_HOVER_MOVING_SPEED` (default **0** = fastest lean on Protocol 1).

      ### 5.5 Drive behavior

      `HoverboardAxisDrive` (via `DriveController`) sets **Goal Position** on 12 and 13:

      1. Tilt → hoverboard hall state (brake / FWD / REV).
      2. Hoverboard motor controller powers the wheel.

      `set_wheels(left_dir, left_speed, right_dir, right_speed)` maps direction and speed to **lean goals and hold times** (pulses, pivots, blends).

      | Command | Effect |
      |---------|--------|
      | Straight FWD/BACK | Lean to `NINA_HOVER_FWD_*` / `REV_*`, then brake |
      | Pivot | Opposing FWD/REV leans on 12 vs 13 |
      | Stop | Both to brake; `engage_brake()` / `park_hoverboard_brake()` |

      Optional **IMU** straight-line correction via `Mpu9250DriftMonitor` when enabled. GUI speed percent mainly affects pivot duty and pulse timing; forward/back strength comes from lean tick goals.

      ---

      ## 6. Hub wheels

      | Item | Detail |
      |------|--------|
      | Motors | 2× hoverboard hub motors, 24 V |
      | Electronics | Stock hoverboard driver board per wheel |
      | Software | Only through lean positions on IDs 12/13 |

      Commissioning: tune `NINA_HOVER_*` in `/etc/nina-link/navigation.env` (`nina/config/navigation_env.py`).

      ---

      ## 7. Boot and runtime

      ### 7.1 Startup

      1. Open UART (`NINA_DXL_PORT`, `NINA_DXL_BAUD`).
      2. Health check — ping IDs 1–13.
      3. `ensure_joint_mode_for_ids([12, 13])`.
      4. `set_torque_all(True)`.
      5. Run `neutral` action (arms/neck).
      6. `apply_hoverboard_brake_positions()`.

      ### 7.2 Bus lock

      `NinaService.bus_lock` serializes playback, recording, lean commands, and health on the same port.

      ### 7.3 Safety (park IDs 1–13 at goal, default 2048)

      | Trigger | Behavior |
      |---------|----------|
      | Touch (`AT42QT2120`) | Stop drive, park all servos, TTS |
      | Low battery (`ADS1115`) | Neutral arms, lean goal, speech |
      | IR obstacle | `HoverboardAxisDrive.stop`, brake lean, neutral arms, TTS |

      ---

      ## 8. Actions (arms + neck)

      **Manifest:** `nina/actions/manifest.json`

      Gestures: `neutral`, `namaste`, `wave`, `salute_left`, `salute_right`, `point_left`, `point_right`, `left_handshake`, `right_handshake`, `fold_arms`, `clap`, `bye`.

      **Frame example:**

      ```json
      {
        "delay": 0.2,
        "duration": 0.8,
        "speed": 200,
        "servos": {
          "1": { "type": "absolute", "value": 2048 }
        }
      }
      ```

      **CLI:** `carbot_main/nina_record.py --port /dev/ttyUSB0`

      ---

      ## 9. Environment variables

      ### Dynamixel

      | Variable | Default | Description |
      |----------|---------|-------------|
      | `NINA_DXL_PORT` | `/dev/ttyUSB0` | UART device |
      | `NINA_DXL_BAUD` | `222222` | Baud rate |
      | `NINA_NEUTRAL_ACTION` | `neutral` | Boot/safety pose |

      ### Hoverboard lean

      | Variable | Default | Description |
      |----------|---------|-------------|
      | `NINA_HOVER_ID_LEFT` | `12` | Left lean ID |
      | `NINA_HOVER_ID_RIGHT` | `13` | Right lean ID |
      | `NINA_HOVER_BRAKE_POS_LEFT` | `2048` | Brake goal |
      | `NINA_HOVER_BRAKE_POS_RIGHT` | `2048` | Brake goal |
      | `NINA_HOVER_FWD_POS_LEFT` | `2022` | Forward lean |
      | `NINA_HOVER_FWD_POS_RIGHT` | `2080` | Forward lean |
      | `NINA_HOVER_REV_POS_LEFT` | `2100` | Backward lean |
      | `NINA_HOVER_REV_POS_RIGHT` | `2000` | Backward lean |
      | `NINA_HOVER_SWAP_FWD_REV_ENV_VALUES` | `0` | Set `1` to exchange FWD/REV rows at load (legacy env only) |
      | `NINA_HOVER_MOVING_SPEED` | `0` | Lean slew |
      | `NINA_HOVER_SWAP_TURN_LR` | `0` | Extra GUI turn L/R swap (off after hall swap) |
      | `NINA_HOVER_PULSE_FORWARD` | `1` | Straight pulse series |

      Also used when needed: `NINA_HOVER_SIGN_LEFT`, `NINA_HOVER_SIGN_RIGHT`, turn position overrides, IMU correction `NINA_HOVER_IMU_CORR_*`.

      ---

      ## 10. Health

      Sirena **Health** screen (`health_collector.py`): expects **13** Dynamixels, per-ID ping, USB-serial adapter check.

      ---

      ## 11. Troubleshooting

      | Symptom | Action |
      |---------|--------|
      | No motors respond | Check `NINA_DXL_PORT`, baud `222222`, `dialout`, adapter |
      | Missing 1–11 or 12–13 only | Daisy-chain, programmed IDs |
      | Lean dead, arms OK | Reboot (joint mode on 12/13) |
      | Weak forward | Tune `NINA_HOVER_FWD_*`, brake positions |
      | Wrong wheel direction | `NINA_HOVER_FWD_*`, `REV_*`, `SIGN_*`, `SWAP_TURN_LR` |
      | GUI turn reversed | `NINA_HOVER_SWAP_TURN_LR` |
      | Arm won’t pose in record | Disable torque |

      **Tools:** `python -m nina.app.motor_control`, `carbot_main/nina_record.py`, `carbot_main/test_ping.py`

      ---

      ## 12. Source files

      | Topic | Path |
      |-------|------|
      | Motor IDs | `nina/config/motor_ids.py` |
      | Bus | `nina/controllers/dynamixel_manager.py` |
      | Drive | `nina/controllers/hoverboard_axis_drive.py` |
      | Settings | `nina/config/settings.py` |
      | Service | `sirena_ui/workers/nina_service.py` |
      | Drive UI | `sirena_ui/workers/drive_controller.py` |
      | Boot | `nina/services/startup_service.py` |
      | Actions | `nina/controllers/action_runner.py`, `nina/actions/` |
      | Record CLI | `carbot_main/nina_record.py` |

      ---

      ## 13. Quick reference

      ```
      ARMS + NECK (11)          HOVERBOARD LEAN (2)
      Right: 1,3,5,7,9         ID 12 left,  ID 13 right  (MX-28)
      Left:  2,4,6,8,10         Brake @ 2048
      Neck:  11

      MX-28: 1-6,11   MX-106: 7-10
      Bus: Protocol 1.0 @ 222222  (NINA_DXL_PORT)
      Total: 13 servos
      Drive: HoverboardAxisDrive + teleop (Sirena / companion)
      ```

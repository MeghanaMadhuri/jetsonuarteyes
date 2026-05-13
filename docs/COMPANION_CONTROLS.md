# Companion tablet: when controls work vs not

The **Android Nina companion** talks to the Jetson over **`nina-link`** (FastAPI under `sirena_ui/android_gateway/`). Many robot controls are **gated off by default** so the HTTP gateway never fights the PyQt5 Sirena UI or hardware without an explicit operator choice.

## 1. Network and auth (always check first)

| Symptom | Cause | Fix |
|--------|--------|-----|
| Most tabs show offline / HTTP errors | Wrong URL, Wi‑Fi, or Jetson down | **Find robot** or **Network**; confirm `http://<jetson>:8787/health` |
| `401 Unauthorized` on POST | Missing or wrong token | **Pair** with PIN on **Network**, or set fleet token (`NINA_ANDROID_HTTP_TOKEN` / `NINA_LINK_TOKEN` on Jetson) |
| Tablet shows “capabilities not loaded” | Status refresh did not finish | **Network → Refresh status** (reloads `GET /v1/robot/capabilities`) |

Mutating HTTP routes require **`Authorization: Bearer …`** from non-loopback clients when the Jetson has a token configured.

## 2. Jetson feature flags (bridges)

These are read from **`GET /v1/robot/capabilities`** and mirrored in the app (callout banners on **Drive**, **Vision**, **Map**, **Actions**).

| Capability JSON key | Control surface | Env var (Jetson) | Default |
|---------------------|-----------------|------------------|---------|
| `robot_bridge_enabled` | Drive momentary / invert / e-stop | `NINA_LINK_ENABLE_ROBOT_BRIDGE` | **off** |
| `record_bridge_enabled` | Actions → Record start/stop | `NINA_LINK_ENABLE_RECORD_BRIDGE` | **off** |
| `actions_static_enabled` | Actions → Audio file / offset APIs; `GET /v1/media/file` | `NINA_LINK_ENABLE_ACTIONS_STATIC` | **off** |
| `vision_bridge_enabled` | Vision MJPEG + vision HTTP | `NINA_LINK_ENABLE_VISION_BRIDGE` | **off** |
| `slam_bridge_enabled` | Map / SLAM HTTP | `NINA_LINK_ENABLE_SLAM_BRIDGE` | **off** |
| `depth_bridge_enabled` | Depth stream | `NINA_LINK_ENABLE_DEPTH_BRIDGE` | **off** |
| `autonomy_bridge_enabled` | Autonomy / goto from tablet | `NINA_LINK_ENABLE_AUTONOMY_BRIDGE` | **off** |

Set the variable to **`1`** / **`true`**, then **restart** `nina-link` (systemd). Example fragment is in [`docs/nina-link-bridge.env.example`](nina-link-bridge.env.example).

**Safety:** turning on **drive** and **autonomy** together with desktop **Drive** can conflict — only one pilot should command the base at a time.

## 3. Playback vs bridges

`POST /v1/actions/play` is **not** blocked by `enable_action_bridge` in the HTTP layer; it queues Qt `PlaybackWorker`. It can still **fail** if the Dynamixel bus is busy, the action is missing, or Sirena UI holds the hardware.

## 4. Android UI limits (not Jetson bugs)

- **Settings** categories other than General / Network are **scaffold** copy — full tuning remains on the Jetson Sirena UI until those HTTP routes exist.
- See [`ANDROID_SIRENA_GAPS.md`](ANDROID_SIRENA_GAPS.md) for the full parity matrix.

## 5. Install / verify on the Jetson

- One-shot: `scripts/jetson-tablet-setup.sh` (see [`COMPANION_APP.md`](COMPANION_APP.md)).
- After edits: `scripts/update-nina-link-jetson.sh --install-dropin --restart --verify`.
- Verbose HTTP timing: `NINA_LINK_HTTP_TRACE=1` + logger level DEBUG for `sirena_ui.android_gateway.fastapi_app`.

## 6. Tablet logs

```bash
adb logcat -s NinaCompanion:D NinaCompanion:I NinaCompanion:W NinaCompanion:E
```

Companion file log (if installed): `run-as com.sirena.nina.companion cat files/logs/nina_companion.log` (see `NinaFileLogger` in the app).

## 7. Polling / “infinite loops” (companion app)

Drive, Vision, Map, Perception, and Actions use **`LaunchedEffect` + `while (isActive)`** loops with a **`delay(...)`** on every iteration so they:

- **Exit** when you leave the screen (Compose cancels the effect; `isActive` becomes false and `delay` cooperates).
- **Do not spin the CPU** on tight empty loops.
- **Rethrow `CancellationException`** in `catch` blocks so navigation teardown is not accidentally swallowed.

When Jetson **HTTP bridges are off**, Vision and Map pollers use a **longer sleep** (fewer requests) so a misconfigured robot does not get hammered with failing calls. After you enable the bridge and **Refresh status**, polling returns to the normal interval.

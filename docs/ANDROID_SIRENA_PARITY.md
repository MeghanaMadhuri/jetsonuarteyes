# Android companion vs desktop `sirena_ui`

This document tracks **functional and navigation parity** between the Jetpack Compose companion (`android/app`) and the PyQt desktop app (`sirena_ui`). The companion shell (sidebar + header + footer) follows the same layout language as desktop `sirena_ui`; behavior targets the same Jetson tablet gateway HTTP API where it exists. For **known missing desktop features** and log capture tips, see [`ANDROID_SIRENA_GAPS.md`](ANDROID_SIRENA_GAPS.md).

## Hook: what to change when `sirena_ui` changes

1. **Feature truth** — [`sirena_ui/docs/NINA_APP.md`](../sirena_ui/docs/NINA_APP.md) (every screen, env var, and control).
2. **Compose design system** — [`SirenaTokens.kt`](../android/app/src/main/java/com/sirena/nina/companion/ui/sirena/SirenaTokens.kt) + [`SirenaComponents.kt`](../android/app/src/main/java/com/sirena/nina/companion/ui/sirena/SirenaComponents.kt) mirror [`sirena_ui/styles.py`](../sirena_ui/styles.py) and [`sirena_ui/widgets/common.py`](../sirena_ui/widgets/common.py) (Card / Pill / SectionLabel / primary & secondary buttons / D-pad / subtabs). Prefer these composables over raw Material3 `Card`/`Button` on robot screens so Android stays component-aligned with Qt.

3. **Constant-level mirrors (copy/paste source)** — update Kotlin in the same change as Python when any of these drift:
   - `sirena_ui/widgets/sidebar.py` → `NAV_ITEMS` → [`SirenaDefinitions.kt`](../android/app/src/main/java/com/sirena/nina/companion/ui/sirena/SirenaDefinitions.kt) `SIRENA_NAV_ITEMS` (glyphs per row) + [`SirenaShellChrome.kt`](../android/app/src/main/java/com/sirena/nina/companion/ui/sirena/SirenaShellChrome.kt) `SirenaSidebar` / `SirenaShellHeader`. Tablet-only rows: `SIRENA_COMPANION_SIDEBAR_ITEMS`.
   - `sirena_ui/screens/home_screen.py` → `QUICK_ACTIONS` → `SIRENA_QUICK_ACTIONS`.
   - `sirena_ui/screens/settings_screen.py` → `SETTINGS_CATEGORIES` → `SIRENA_SETTINGS_CATEGORIES`.
   - `sirena_ui/workers/health_collector.py` → `collect()` row order/labels → `SIRENA_HEALTH_SUBSYSTEM_LABELS` (reference for future full-table parity).
4. **Machine-readable index** — [`android_sirena_parity.manifest.json`](android_sirena_parity.manifest.json) lists each screen, its desktop doc section, Android file(s), and typical HTTP paths. Bump `updated` (and `manifest_version` if the contract changes) when you add routes or screens.
5. **HTTP** — Jetson behaviour goes through [`sirena_ui/android_gateway/fastapi_app.py`](../sirena_ui/android_gateway/fastapi_app.py) (run `python -m sirena_ui`); extend [`LinkClient.kt`](../android/app/src/main/java/com/sirena/nina/companion/data/LinkClient.kt) and the relevant Composable / [`CompanionViewModel.kt`](../android/app/src/main/java/com/sirena/nina/companion/CompanionViewModel.kt).
5. **Kiosk handoff** — with `NINA_LINK_SESSION_SCRIPT` on the Jetson, the app calls **`POST /v1/session/claim`** when the full **Nina** console opens and **`/v1/session/release`** when it closes (and on ViewModel clear), so `nina-link` can use USB/GPIO while the on-robot PyQt kiosk is stopped. See [`docs/COMPANION_APP.md`](COMPANION_APP.md) (Kiosk vs tablet) and [`scripts/nina-link-session-helper.sh`](../scripts/nina-link-session-helper.sh).
7. **On-robot checks** — run [`scripts/verify-nina-link-companion.sh`](../scripts/verify-nina-link-companion.sh) on the Jetson for capabilities + USB nodes + logs.

## Shared HTTP surface

The companion talks to the **tablet gateway** embedded in Sirena UI (same route surface as legacy nina-link). Endpoints include Wi‑Fi, pairing, drive, actions, vision (stream, options, detections, enroll, announce), session claim/release, and static media. When the matching **`NINA_LINK_ENABLE_*`** flags or **`NINA_ANDROID_GATEWAY_ALL=1`** are set on the Jetson (see [`scripts/install-nina-link-jetson.sh`](../scripts/install-nina-link-jetson.sh)), Android uses **`/v1/slam/*`**, **`/v1/depth/stream`**, and **`/v1/autonomy/*`** — matching desktop `sirena_ui` subject to hardware availability.

## Screen parity matrix

| Desktop (`sirena_ui`) | Android (Nina console) | Notes |
|----------------------|-------------------------|--------|
| Home | `SirenaHomeScreen` | Quick actions, live-ish overview from `/v1/status` + capabilities. |
| Drive | `SirenaDriveScreen` | Momentary drive + E-stop + MJPEG preview via `/v1/vision/stream`. **Autonomous** toggle calls `POST /v1/autonomy/enabled` and polls `/v1/autonomy/status` (same as Map/desktop). HTTP drive is refused while autonomy holds the wheels. |
| Vision | `SirenaVisionScreen` | Stream, face/object toggles, confidence + **Apply**, enrollment, announce, **person follow** (`/v1/vision/follow/*` + enrolled-face picker), **JPEG snapshot** (`GET /v1/vision/snapshot`); polling `/v1/vision/detections` for a short detection list. Toggle chips now mirror backend `face_enabled`/`object_enabled` state (not optimistic local state). |
| Perception | `SirenaPerceptionScreen` | Three-pane layout: SLAM occupancy (`/v1/slam/occupancy`), RGB MJPEG, depth MJPEG when bridges are enabled on the Jetson. Depth pill maps deterministic states (`stream` / `sim` / `err` / `off`) from backend status codes. |
| Map | `SirenaMapScreen` | Occupancy bitmap + pose from `/v1/slam/snapshot` / occupancy bytes; autonomy toggle calls `/v1/autonomy/enabled`. **Tap-to-go** / **Cancel goto** as in shared HTTP surface. **Start mapping** / **Stop mapping** labels match desktop `map_screen.py`. |
| Actions | `SirenaActionsScreen` | Playback / Record / Audio aligned with link daemon. |
| Settings | `SirenaSettingsScreen` | Same categories as desktop; **Network** includes Jetson Wi‑Fi actions (parity with Setup tab). |
| Health | `SirenaHealthScreen` | Daemon `/health` + capabilities; full desktop hardware donut/table remains on-robot Sirena UI until bridged. |

## Launcher shell

- Single **desktop-style** window: **160dp charcoal sidebar** (same nav keys + Unicode glyphs as Qt) + **red header bar** + **cloud** content + **charcoal status footer**. **Networks** and **Tablet** (setup) sit below a divider after **Health**; robot **Home** is the default landing (no separate splash hub tab).

## Footer strip

Charcoal strip dots: **Bus** (link reachability), **Wi‑Fi** (daemon session ready), **Battery** (no tablet API yet — off), **Voice** (ON when `vision_bridge_enabled` in capabilities).

## Tests

- JVM unit test: `JsonCleanStringTest` (JSON helpers used across the HTTP client).

Run unit tests in Android Studio (**test** source set) or `./gradlew test` when the Gradle wrapper is present.

## Jetson: `.venv-link` + systemd (why SLAM / autonomy / drive fail)

The tablet only sends HTTP; **everything substantive runs on the Jetson** inside **`nina-link`**, using **`REPO_ROOT/.venv-link/bin/python`**.

| Symptom in the app | Typical Jetson cause |
|-------------------|----------------------|
| **`No module named 'rplidar'`**, SLAM shows simulation | **`requirements-link.txt` alone is not enough.** Install the headless Sirena stack into the same venv: `./scripts/update-nina-link-jetson.sh --sirena-headless --restart` or `./.venv-link/bin/pip install -r sirena_ui/requirements-headless.txt`, then **`sudo systemctl restart nina-link`**. BreezySLAM needs build deps: **`sudo apt install -y build-essential python3-dev`**. |
| **Autonomy request failed** | Autonomy imports **`rplidar`**, **`breezyslam`**, sensors, etc. Fix venv as above; enable **`NINA_LINK_ENABLE_AUTONOMY_BRIDGE`** (already in `install-nina-link-jetson.sh` / recommended **`bridges.conf`** drop-in). Check **`journalctl -u nina-link -e`**. |
| **BLDC not connected / Jetson.GPIO** | **Not a pip package:** navigation expects **GPIO access on the real Jetson** (not a dev PC). User/group **`dialout`**, **no desktop Drive UI** holding the bus, and valid **`NavigationManager`** wiring. Message comes from lazy NavigationManager init when hardware/sim cannot arm. |
| **Vision / depth missing** | **`opencv-python-headless`** etc. from **`--sirena-headless`**; RealSense on aarch64 often needs a **built** `pyrealsense2`, not only pip (see `requirements-headless` markers). |

**One-shot (robot + tablet provisioning):**  
[`scripts/install-sirena-companion-jetson.sh`](../scripts/install-sirena-companion-jetson.sh)  
Add **`--with-sirena-headless`** after `git pull` for SLAM/vision/sensor parity with desktop.

Repo docs that drive Jetson + Android behaviour together: **[`docs/COMPANION_APP.md`](COMPANION_APP.md)** (tablet URL, `_venv-link`, troubleshooting), **[`sirena_ui/requirements-headless.txt`](../sirena_ui/requirements-headless.txt)** (exact pip set), **[`requirements-link.txt`](../requirements-link.txt)** (minimal daemon).

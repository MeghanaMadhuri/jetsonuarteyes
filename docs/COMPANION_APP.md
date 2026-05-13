# Nina Companion (tablet) + Jetson tablet gateway

This document describes the **Android companion app** under [`android/`](../android/README.md) and the **tablet HTTP API** served from **Sirena UI** on the Jetson ([`sirena_ui/android_gateway/`](../sirena_ui/android_gateway/) + [`nina/jetson_net/`](../nina/jetson_net/) for Wi‑Fi state).

**Tablet controls idle or HTTP 503?** See **[COMPANION_CONTROLS.md](COMPANION_CONTROLS.md)** (`NINA_LINK_ENABLE_*` bridge matrix, auth, and env example [`nina-link-bridge.env.example`](nina-link-bridge.env.example)).

## Jetson: one-shot install + diagnosis (recommended)

**Single entry point (tablet + Sirena UI gateway + bridges):** run **`scripts/jetson-tablet-setup.sh`** from the repo root. It chains the venv/systemd install, enables HTTP bridge drop-ins, optional UFW, and the same flags as **`scripts/install-sirena-companion-jetson.sh`** (e.g. `--with-sirena-headless`).

**Equivalent installer (explicit name):** **`./scripts/install-sirena-companion-jetson.sh`** — documented in the script header: installs **nina-link**, applies **`update-nina-link-jetson.sh --install-dropin`**, restarts and verifies, optional UFW on **8787**. Use **`--with-sirena-headless`** when you need the same pip set as **`sirena_ui` headless** (vision stream, richer sensors) inside **`.venv-link`**.

From the repo root on the Jetson (after `git clone` / copy):

```bash
# Executable bit is set in git; if you still see "Permission denied":
chmod +x scripts/jetson-tablet-setup.sh scripts/install-sirena-companion-jetson.sh scripts/install-nina-link-jetson.sh scripts/uninstall-nina-link-jetson.sh

# Full Jetson setup (recommended one-liner): apt + PyQt5 (distro) + venv, pip, systemd, bridge drop-in, verify
./scripts/jetson-tablet-setup.sh

# Same as above (companion-oriented entry point):
# ./scripts/install-sirena-companion-jetson.sh

# Lower-level equivalent (venv + systemd only, no drop-in / UFW orchestration):
./scripts/install-nina-link-jetson.sh --all

# If sudo password was not entered during --all, finish systemd registration:
sudo ./scripts/install-nina-link-jetson.sh --systemd-only

# Remove service and optional venv/state:
./scripts/uninstall-nina-link-jetson.sh --purge
```

## Companion app + gateway updates (0.4 track)

Use this section after **`git pull`** to see what changed on the **tablet** and **Jetson HTTP surface** without diffing the whole tree.

| Area | Change |
|------|--------|
| **Jetson `/v1/robot/capabilities`** | Includes **`neutral_action_name`** (from Nina settings / `NINA_NEUTRAL_ACTION`, default `neutral`) so the tablet can match Qt’s protected neutral when deleting actions. |
| **Android Drive** | Hardware keyboard: **W A S D**, arrow keys, **Space** (stop), **Esc** (brake + E‑STOP) when the Drive screen has focus; copy describes D‑pad + keyboard + HTTP pulse limits. |
| **Android Actions** | Short **Playing '…'** hint after successful manifest play; delete protection uses **`neutral_action_name`** from capabilities when present. |
| **Android Settings** | **General** and **Network** panels plus an **else** branch for other categories pointing operators at on-robot Sirena UI and **`docs/COMPANION_CONTROLS.md`**. |
| **Android shell** | Footer horizontal padding aligned to **12 dp** (closer to Qt status bar margins). |
| **Android version** | **`versionName`** aligned with desktop **0.4** line (**`0.4.0`** in `android/app/build.gradle.kts`); bump **`versionCode`** when you ship a new APK. |
| **Find robot** | On wide layouts (≥520 dp), **Discovery diagnostics** and **Currently connected** sit **side by side**; each nearby daemon card uses a **row** (details + **Connect** on the right). |

Full desktop-vs-tablet matrix: **[ANDROID_SIRENA_PARITY.md](ANDROID_SIRENA_PARITY.md)** and **[ANDROID_SIRENA_GAPS.md](ANDROID_SIRENA_GAPS.md)**.

### Vision MJPEG (tablet + Jetson)

The companion decodes RGB and depth MJPEG in **Compose** (multipart JPEG scan + `BitmapFactory`), with a client-side long-edge clamp (**`SirenaMjpegAndroidMaxLongEdgeDefault`** in `SirenaMjpegImage.kt`, typically **1152**). Intermediate JPEGs in a backlog are dropped so the UI stays on the **latest** frame.

On the Jetson gateway, tune encode cost and Wi‑Fi bandwidth with:

| Env | Default | Role |
|-----|---------|------|
| **`NINA_MJPEG_MAX_WIDTH`** | `1280` | Max width before JPEG encode (height scales). Try **`960`** on busy Wi‑Fi or older tablets. |
| **`NINA_MJPEG_JPEG_QUALITY`** | `78` | JPEG quality (40–95). Slightly higher quality is cheaper when width is capped. |

Source: `sirena_ui/android_gateway/vision_mjpeg.py`.

**Depth colorization:** RealSense depth MJPEG uses OpenCV in the same Python environment as `sirena_ui` / `nina-link`. If the depth stream is empty while RGB works, install headless deps (e.g. `./scripts/update-nina-link-jetson.sh --sirena-headless` or `--vision`) so OpenCV is available in the venv that runs the gateway.

## Jetson: quick update after `git pull` (recommended)

From the repo root on the robot — refreshes `.venv-link` from `requirements-link.txt`, runs an import check, and optionally restarts systemd + curls `/health`:

```bash
chmod +x scripts/update-nina-link-jetson.sh
./scripts/update-nina-link-jetson.sh --pull --restart --verify
```

First time enabling HTTP bridges via systemd drop-in (editable after install):

```bash
./scripts/update-nina-link-jetson.sh --install-dropin --restart --verify
```

Optional OpenCV for vision streaming in the same venv:

```bash
./scripts/update-nina-link-jetson.sh --vision --restart
```

Full **Sirena-style** deps for **`nina-link`** (gTTS, OpenCV, Ultralytics, Pillow, optional lidar/SLAM packages) **without PyQt5** — avoids Jetson **`pip install PyQt5`** failures (`qmake` / sipbuild). Do **not** run **`pip install -r sirena_ui/requirements.txt`** into `.venv-link` on Jetson; use:

```bash
./scripts/update-nina-link-jetson.sh --sirena-headless --restart
# or manually:
# ./.venv-link/bin/pip install -r sirena_ui/requirements-headless.txt
```

The desktop Sirena UI still wants **`sudo apt install python3-pyqt5 python3-pyqt5.qtsvg`** for Qt.

Run `./scripts/update-nina-link-jetson.sh --help` for all flags.

Smaller installs (no systemd / no apt): `./scripts/install-nina-link-jetson.sh --smoke` or add `--with-systemd` / `--install-system-deps` as needed. **`--no-systemd`** skips the unit when combined with **`--all`** (e.g. dev laptop).

On stock Ubuntu/Jetson images you may need **`python3-venv`** once: either `sudo apt install python3-venv` or use **`--install-system-deps`** (runs `apt` for `python3.X-venv`, `python3-venv`, `pip`, `curl`).

This creates **`.venv-link`** with **`--system-site-packages`** (new venvs) so **`python3-pyqt5`** from apt is visible to **`python -m sirena_ui`**. It installs **`requirements-link.txt`**, verifies **`nina.jetson_net`** and **PyQt5** imports, checks **`nmcli`/NetworkManager**, and with **`--smoke`** prints a **`curl /health`** hint (no auto-spawn).

If you see **`pip missing inside venv`**, an old `.venv-link` was built before **`python3-venv`** existed. The install script now runs **`python -m ensurepip`** inside that venv or recreates it. To reset manually: **`rm -rf .venv-link`** and run the script again.

### “`.venv-link` has no `activate`” / `source: …/activate: No such file`

The env may be **incomplete** (interrupted `venv` create, wrong copy). **You do not need `source activate`**: **systemd** already uses **`$REPO_ROOT/.venv-link/bin/python`** in **`ExecStart`**. Use **`./.venv-link/bin/pip`** and **`./.venv-link/bin/python`** by full path.

**1. Inspect**

```bash
cd ~/Nvidia-jetson-platform
ls -la .venv-link/bin/
```

You want **`python`** and **`pip`**. An **`activate`** script is optional; missing **`activate`** with **`python`**/**`pip`** present is OK.

**2. Install or upgrade packages**

```bash
cd ~/Nvidia-jetson-platform
./.venv-link/bin/pip install -U pip setuptools wheel
./.venv-link/bin/pip install -r requirements-link.txt
sudo systemctl restart nina-link
```

**3. If `bin/python` or `bin/pip` is missing — recreate the venv**

```bash
cd ~/Nvidia-jetson-platform
rm -rf .venv-link
sudo apt install -y python3-venv   # if `python3 -m venv` fails
python3 -m venv .venv-link
./.venv-link/bin/pip install -r requirements-link.txt
sudo systemctl restart nina-link
```

Or run the full installer once: **`./scripts/install-nina-link-jetson.sh --all`** (fresh `.venv-link` + systemd).

If you run **`cd /path/to/...`** literally from docs, it will fail — use **`cd ~/Nvidia-jetson-platform`** (or your real clone path).

---

## Jetson: manual install and run

With activation (optional):

```bash
cd ~/Nvidia-jetson-platform
python3 -m venv .venv-link && source .venv-link/bin/activate
export PYTHONPATH=.
pip install -r requirements-link.txt
pip install -r sirena_ui/requirements.txt
export NINA_ANDROID_GATEWAY=1
python -m sirena_ui
```

Same steps **without** `activate` (paths relative to repo root):

```bash
cd ~/Nvidia-jetson-platform
python3 -m venv .venv-link
./.venv-link/bin/pip install -r requirements-link.txt
./.venv-link/bin/pip install -r sirena_ui/requirements.txt
export PYTHONPATH=.
export NINA_ANDROID_GATEWAY=1
./.venv-link/bin/python -m sirena_ui
```

Environment variables (see [`nina/jetson_net/config.py`](../nina/jetson_net/config.py)):

| Variable | Meaning |
|----------|---------|
| `NINA_ANDROID_GATEWAY` | If `0`, do not start embedded FastAPI (default `1` in `server.py` when unset behaves as on) |
| `NINA_ANDROID_HTTP_HOST` / `NINA_ANDROID_HTTP_PORT` | Optional overrides for bind (else `NINA_LINK_HOST` / `NINA_LINK_PORT`) |
| `NINA_ANDROID_GATEWAY_ALL` | If `1`, enable robot/vision/slam/depth/autonomy/record/static HTTP bridges without per-flag env |
| `NINA_LINK_HOST` | Bind address (default `0.0.0.0`) |
| `NINA_LINK_PORT` | HTTP port (default `8787`) |
| `NINA_LINK_AP_SSID` / `NINA_LINK_AP_PASSWORD` | Hotspot credentials when using `nmcli device wifi hotspot` |
| `NINA_LINK_AP_WAIT_SEC` | Boot “window” indicator for status JSON (default 30) |
| `NINA_LINK_BOOT_AP` | If `1` (default), bring up **Nina AP** on daemon start (STA home Wi‑Fi only via app **connect-home** / live actions, not automatically on reboot) |
| `NINA_LINK_DISABLE_WIFI_AUTOCONNECT` | If `1` (default), saved Wi‑Fi profiles get **autoconnect=no** at boot and new profiles from the app don’t auto-join |
| `NINA_LINK_TOKEN` | If set, remote clients must send `Authorization: Bearer <token>` for mutating calls (localhost always trusted) |
| `NINA_LINK_MOCK` | If `1`, simulate Wi-Fi (for laptops without NetworkManager) |
| `NINA_LINK_ENABLE_ROBOT_BRIDGE` | If `1`, `POST /v1/robot/drive` (do not use desktop Drive at the same time) |
| `NINA_LINK_ENABLE_ACTION_BRIDGE` | Legacy nina-link only; embedded Sirena always serves `POST /v1/actions/play` via `NinaService` |
| `NINA_LINK_ENABLE_RECORD_BRIDGE` | If `1`, `POST /v1/actions/record/start` (same serial bus as Sirena UI) |
| `NINA_LINK_ENABLE_VISION_BRIDGE` | If `1`, `GET /v1/vision/stream` (MJPEG; needs OpenCV + `sirena_ui` vision stack on `PYTHONPATH`) |
| `NINA_LINK_ENABLE_ACTIONS_STATIC` | If `1`, `GET /v1/media/file?relative=…` for `nina/actions/` files (e.g. audio MP3) |
| `NINA_LINK_SESSION_SCRIPT` | Optional executable: invoked as `script claim` / `script release` (Jetson UI takeover) |

Systemd example: [`nina/systemd/nina-link.service`](../nina/systemd/nina-link.service).

### BLDC / Jetson GPIO parity with Sirena UI

Stock **`nina-link.service`** and **`desktop/nina-ui-kiosk.service`** run
**Jetson GPIO** navigation (`NavigationManager`).

The **same `NINA_NAV_*` polarity vars** must be visible to **`nina-link`**
(for `/v1/robot/drive/status`, companion drive) and to the Qt kiosk, or
status will disagree. Optional **`/etc/nina-link/navigation.env`**: copy
[`nina/systemd/nina-link-navigation.env.example`](../nina/systemd/nina-link-navigation.env.example)
to **`/etc/nina-link/navigation.env`**, then **`sudo systemctl restart nina-link`**.

- **Stock unit** ([`nina/systemd/nina-link.service`](../nina/systemd/nina-link.service)) loads optional **`/etc/nina-link/navigation.env`** (`EnvironmentFile=-/…`).
- Do **not** run Sirena UI Drive and HTTP robot drive at the same time — they contend for the same navigation backend.

### Systemd: enable drive / play / record / vision (drop-in)

Prefer a **drop-in** so future `install-nina-link-jetson.sh` runs do not overwrite your flags:

```bash
sudo mkdir -p /etc/systemd/system/nina-link.service.d
sudo tee /etc/systemd/system/nina-link.service.d/bridges.conf <<'EOF'
[Service]
# HTTP features (see table above). Adjust to your site.
Environment=NINA_LINK_ENABLE_ROBOT_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTION_BRIDGE=1
Environment=NINA_LINK_ENABLE_RECORD_BRIDGE=1
Environment=NINA_LINK_ENABLE_VISION_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTIONS_STATIC=1
# Optional: path to an executable helper — see scripts/nina-link-session-helper.example.sh
# Environment=NINA_LINK_SESSION_SCRIPT=/usr/local/bin/nina-link-session-helper
EOF

sudo systemctl daemon-reload
sudo systemctl restart nina-link
```

Verify from the Jetson:

```bash
curl -s http://127.0.0.1:8787/v1/robot/capabilities | head
curl -s http://127.0.0.1:8787/v1/vision/status | head
```

Companion toggles (Vision MJPEG, audio preview, recording) read **`capabilities`** from this endpoint.

Optional vision dependencies on top of `requirements-link.txt` (same repo the Jetson uses for Sirena UI):

```bash
pip install opencv-python-headless 'numpy>=1.19'
# If object detection is enabled: ultralytics / CUDA stack per sirena_ui/requirements.txt
```

### Systemd: service loops with `CHDIR` / `status=200/CHDIR`

If **`journalctl -u nina-link`** shows **`Changing to the requested working directory failed`** or **`Failed at step CHDIR`**, systemd never starts Python — **no AP, no `/health`**. Typical causes:

1. **`WorkingDirectory=` points at a path that does not exist** on this machine (for example **`/opt/nvidia-jetson-platform`** after copying the template while the repo actually lives under **`/home/...`**). **Fix:** set **`WorkingDirectory=/`** (repo location is defined only by **`PYTHONPATH`** and **`ExecStart`**).
2. **`ExecStart`** must be the **venv** interpreter when dependencies are in **`.venv-link`**, e.g. **`.../.venv-link/bin/python`**, not **`/usr/bin/python3`** unless everything is installed system-wide.

Re-register the unit from the real repo root so paths stay consistent:

```bash
cd ~/Nvidia-jetson-platform
sudo ./scripts/install-nina-link-jetson.sh --systemd-only
```

## Sirena UI on the robot

Settings → **Network** talks to `http://127.0.0.1:8787` by default (override with `NINA_LINK_URL`). Status shows **saved profiles** (including ones added on the Jetson) and any **active STA** connection. Home Wi‑Fi is joined only when you use **Connect jetson** / **force_sta** from the app or this screen—not automatically after reboot.

## Tablet: APK flow

The companion **MainActivity** is locked to **landscape** (`sensorLandscape` in the manifest). On cold start it plays **`res/raw/nina_splash.mp4`** full screen, then shows the app. **Jetson link status**, **daemon URL**, and **session log / diagnostics** live under the **Setup** tab (not Home).

1. Join the Jetson **access point** (SSID/password match daemon / Jetson screen).
2. Open **Sirena UI**. It **auto-tries** the Wi‑Fi **gateway** (Jetson) plus saved/fallback URLs — you usually do **not** need to type an IP. If you use **Setup** manually, use the **Router/Gateway** address from Wi‑Fi details — **never** the tablet’s own IP (e.g. not `10.42.0.153`). Common gateways: **`http://10.42.0.1:8787`** when the tablet has a **`10.42.x.x`** address, or **`http://192.168.4.1:8787`** when the tablet has **`192.168.4.x`**.
3. **Save home Wi‑Fi** credentials on the Jetson (sends them to NetworkManager via the daemon).
4. **Connect Jetson to home Wi‑Fi** (STA), then use **Open Android Wi‑Fi settings** to join the **same** SSID on the tablet. Android does not allow silent Wi‑Fi switching; this step is intentional.
5. Change the app **Setup** URL to the Jetson’s new LAN address (or mDNS later) and **Save & test connection**.

### Tablet cannot reach `http://192.168.4.1:8787` on the Nina AP

1. **Use the gateway shown on the tablet** — Android **Wi‑Fi → Nina‑Setup → details**: **Router / Gateway** is the Jetson on that subnet. On many Jetson/NM setups the hotspot uses **`10.42.0.1`** (your tablet will have an IP like **`10.42.0.153`**); **`192.168.4.1`** only works when the tablet’s IP is **`192.168.4.x`**. Set **`http://<gateway>:8787`** in **Setup** and **Save & test connection**.
2. **URL must include `http://`** — The app normalizes common mistakes; avoid a leading **`/`** before the IP (that breaks OkHttp and shows errors like `failed to connect to /192.168…`).
3. **On the Jetson** (SSH or console): `curl -s http://127.0.0.1:8787/health` should return JSON. If yes but the tablet still fails, check **`sudo ufw status`** and allow **`8787/tcp`**, or turn **Private DNS** off on the tablet for testing.

### Edge cases

- **Wrong Wi‑Fi password**: Jetson returns an error string; fix credentials and retry.
- **Tablet on AP, Jetson on home**: Status requests fail — switch the tablet Wi‑Fi first.
- **401 Unauthorized**: Set `NINA_LINK_TOKEN` on the Jetson and paste the same token under Setup, or **Pair** with the PIN (PIN is visible only on localhost status in Sirena Settings → Network on the Jetson).

### Troubleshooting companion HTTP errors

- **`{"detail":"Not Found"}` on `/v1/robot/...`**: Check spelling — the path is **`/v1/robot/capabilities`** (not `capabilites`).
- **`503`** on **`POST /v1/actions/play`**: Action bridge off in the running process — set `NINA_LINK_ENABLE_ACTION_BRIDGE=1` in systemd and **`sudo systemctl restart nina-link`**.
- **`500`** / **`ModuleNotFoundError: No module named 'serial'`** when playing/recording: the **`.venv-link`** used by systemd needs **PySerial**. From repo root: **`./.venv-link/bin/pip install -r requirements-link.txt`** (includes `pyserial`) or activate first, then **`pip install -r requirements-link.txt`** — then **`sudo systemctl restart nina-link`**.
- **`No module named 'rplidar'`**, **`breezyslam`**, or SLAM stuck in simulation on the **tablet Map** screen: **`requirements-link.txt` does not include lidar/SLAM.** Install the headless Sirena stack into the **same** venv the service uses: **`./scripts/update-nina-link-jetson.sh --sirena-headless --restart`** (or **`pip install -r sirena_ui/requirements-headless.txt`**). For BreezySLAM’s C extension: **`sudo apt install -y build-essential python3-dev`** first. One-shot for new robots: **`./scripts/install-sirena-companion-jetson.sh --with-sirena-headless`**.
- **Lidar health shows `/dev/ttyUSB0` missing**: recent builds auto-probe `/dev/ttyUSB*` + `/dev/ttyACM*` when `NINA_LIDAR_PORT` is not explicitly set. If your bot uses a fixed node, set it in systemd (`NINA_LIDAR_PORT=/dev/ttyUSB1`) and avoid sharing one port across `NINA_DXL_PORT`, `NINA_LIDAR_PORT`, and `NINA_NAV_REMOTE_PORT`.
- **Drive shows “BLDC not connected” / Jetson.GPIO**: hardware path — confirm you run **on the Jetson**, **`nina-link.service`** uses **`/.venv-link/bin/python`**, user can access GPIO/UART (**`dialout`** etc.), and **desktop Drive** is not holding the bus. Not fixed by pip alone.
- **Opaque “Internal Server Error” from curl**: Prefer **`curl -sS ...`** alone per request, or separate commands with **`echo`** between them — pasting capabilities + play on one line can merge JSON bodies in the terminal. After updating nina-link, **`POST /v1/actions/play`** errors return JSON **`{"detail":"..."}`** with the real cause (venv module, busy serial port, etc.).

## Jetson: verify bridges and USB (robot side)

From repo root on the Jetson (prints **`/health`**, **`/v1/robot/capabilities`**, USB serial nodes, and recent **`journalctl`**):

```bash
chmod +x scripts/verify-nina-link-companion.sh
./scripts/verify-nina-link-companion.sh
# optional: ./scripts/verify-nina-link-companion.sh 10.42.0.1 8787
```

If LiDAR errors mention the wrong device, set **`NINA_LIDAR_PORT`** (e.g. `/dev/ttyACM0`) in **`/etc/systemd/system/nina-link.service.d/bridges.conf`** and **`sudo systemctl daemon-reload && sudo systemctl restart nina-link`**.

### RealSense (`pyrealsense2`) on Jetson (aarch64)

Intel does not ship a universal aarch64 wheel; `pip install pyrealsense2` inside **`.venv-link`** often fails or mismatches the installed **`librealsense2`** version. Follow **`REQUIREMENTS.md` § 5.3.2** (build/install librealsense + Python bindings so `python3 -c "import pyrealsense2"` works **using the same interpreter** as **`nina-link`**, i.e. **`REPO_ROOT/.venv-link/bin/python`**). Until import succeeds in that venv, depth status reports `dependency missing` and Android surfaces **Depth: sim**.

### Kiosk vs tablet (exclusive hardware)

The PyQt kiosk and **`nina-link`** are **different processes**; both must not open the same USB camera, LiDAR, or RealSense at once. Configure on the Jetson:

1. Copy **[`scripts/nina-link-session-helper.sh`](../scripts/nina-link-session-helper.sh)** to **`/usr/local/bin/nina-link-session-helper`** and **`chmod +x`**.
2. In **`bridges.conf`**, set **`Environment=NINA_LINK_SESSION_SCRIPT=/usr/local/bin/nina-link-session-helper`** and **`Environment=NINA_SESSION_DESKTOP_USER=<login_that_runs_kiosk>`** (match **`systemctl --user status nina-ui-kiosk`**).
3. Ensure **`sudo`** allows **`nina-link`** (usually root) to run **`sudo -u`** that user without a password for **`systemctl --user stop/start`**, or run a narrower sudoers rule for this script.

The Android app **claims** on opening the full **Nina** console and **releases** when you leave it or when the process ends (best-effort), so the kiosk can restart. Manual **Session claim / release** under **Settings** still works for testing.

## Jetson: one-shot companion install (robot side)

From the **repo root on the Jetson** (single script — runs the full nina-link installer, then adds the **HTTP bridge** drop-in, restarts the service, optionally opens **UFW 8787**, and prints URL hints):

```bash
chmod +x scripts/install-sirena-companion-jetson.sh
./scripts/install-sirena-companion-jetson.sh
# Map / SLAM / autonomy / full vision (same pip set as Sirena UI without PyQt5):
./scripts/install-sirena-companion-jetson.sh --with-sirena-headless
```

Equivalent manual steps: `./scripts/install-nina-link-jetson.sh --all` then `./scripts/update-nina-link-jetson.sh --install-dropin --restart --verify` (same pieces as the recommended Jetson install section at the top of this doc). Append **`--sirena-headless`** on that update step (or use **`--with-sirena-headless`** above) so **`rplidar`**, **BreezySLAM**, vision, and sensors match desktop **`sirena_ui/requirements-headless.txt`** inside **`.venv-link`**.

## Android: build a shareable APK

1. Open the **`android/`** folder in **Android Studio** (JDK 17). Let Gradle create the wrapper on first sync if needed.
2. **Build → Build Bundle(s) / APK(s) → Build APK(s)** (choose **release** if prompted; release is configured to use the **debug keystore** for sideloading — fine for internal sharing, not for Play Store).
3. Android Studio shows the path to **`app-release.apk`** (typically `android/app/build/outputs/apk/release/app-release.apk`).

If the Gradle **`gradlew`** scripts exist in **`android/`**, from repo root:

- **Windows:** `powershell -File scripts/build-companion-apk.ps1`
- **Linux/macOS:** `chmod +x scripts/build-companion-apk.sh && ./scripts/build-companion-apk.sh`

Share the **`.apk`** file; on each device allow **Install unknown apps** for the browser / Files app used to open it.

## Tablet gateway verification (manual)

With Sirena UI running on the Jetson (`python -m sirena_ui`, `NINA_ANDROID_GATEWAY=1`):

1. `curl -s http://127.0.0.1:8787/health` — expect `"service":"sirena-tablet-gateway"` (or `"ok":true`).
2. `curl -s http://127.0.0.1:8787/v1/robot/capabilities` — confirm expected endpoints.
3. From the Android tablet (same LAN), set base URL to `http://<jetson-ip>:8787` and exercise Drive, Actions (play), Health, and Vision stream while the kiosk UI uses the same features — motion and bus access should serialize through one `NinaService` (no second serial opener).

Automated guard: `python -m unittest tests.test_jetson_net_imports`. Route/client parity: `python -m unittest tests.test_link_api_linkclient_parity`. Refresh OpenAPI for Kotlin: `python scripts/sync_android_api.py --url http://<jetson>:8787`.

## REST API (summary)

- `GET /health` — liveness.
- `GET /v1/status` — Wi‑Fi role, IPv4, saved profiles, errors, boot timer fields.
- `POST /v1/mode` — `{ "mode": "boot_default" | "force_ap" | "force_sta" }`.
- `POST /v1/wifi/home-credentials` — `{ "ssid", "password" }`.
- `POST /v1/wifi/connect-home` — optional `?ssid=`.
- `POST /v1/wifi/start-ap` — bring up hotspot.
- `DELETE /v1/wifi/saved/{id_or_uuid}` — remove saved NM profile.
- `POST /v1/pair` — `{ "pin" }` → `{ "token" }` for session bearer.
- `GET /v1/robot/capabilities` — which bridges are enabled and endpoint paths.
- `GET /v1/robot/health` — JSON `{ "rows": [ { "key", "label", "detail", "status" } ] }` from `health_collector` + Wi‑Fi (companion Health screen).
- `POST /v1/robot/drive` / `POST /v1/robot/emergency-stop` — when `NINA_LINK_ENABLE_ROBOT_BRIDGE=1`.
- `GET /v1/actions` / `POST /v1/actions/play` — embedded Sirena always exposes play via `NinaService` (legacy `NINA_LINK_ENABLE_ACTION_BRIDGE` ignored).
- `GET /v1/actions/recordings` — list `recordings/*.json` (no bus access).
- `GET /v1/actions/record/status` / `POST /v1/actions/record/start` — when `NINA_LINK_ENABLE_RECORD_BRIDGE=1`.
- `GET /v1/vision/status` / `GET /v1/vision/stream` (MJPEG) / `GET /v1/vision/snapshot` (single JPEG) / `GET /v1/vision/faces` (enrolled names) / `POST /v1/vision/options` / `POST /v1/vision/open` / `POST /v1/vision/stop` / `POST /v1/vision/follow/start` / `POST /v1/vision/follow/stop` / `GET /v1/vision/follow/status` — when `NINA_LINK_ENABLE_VISION_BRIDGE=1`. Person follow uses the same `FaceFollowController` + `NinaService.drive` path as the PyQt Vision screen (not HTTP momentary drive).
- `GET /v1/media/file?relative=audio/foo.mp3` — when `NINA_LINK_ENABLE_ACTIONS_STATIC=1` (path must stay under `nina/actions/`).
- `GET /v1/autonomy/status` / `POST /v1/autonomy/enabled` — when `NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1`. Status returns the merged blob `{ "enabled", "mode": "idle"|"wander"|"goto", "health", "pilot", "goto", "last_pilot" }`. Enabled toggles wander.
- `POST /v1/autonomy/goal` / `DELETE /v1/autonomy/goal` — same bridge flag. Body for POST is `{ "x_mm": <float>, "y_mm": <float> }` in the SLAM map frame (origin = map centre, +x right, +y forward). The Jetson plans an A* path on the live BreezySLAM grid (with footprint inflation), follows it with reactive obstacle avoidance, and stops on arrival. DELETE cancels the in-flight goto; if the goto turned autonomy on, autonomy also turns off.
- `POST /v1/session/claim` / `POST /v1/session/release` — if `NINA_LINK_SESSION_SCRIPT` is set.
- `POST /v1/slam/save` — body `{ "filename": "nina_map.pgm" }`; writes PGM under `nina/data/maps/` on the Jetson when `NINA_LINK_ENABLE_SLAM_BRIDGE=1` (same behaviour as desktop Save map).

## Android vs desktop Sirena UI

For a maintained parity checklist (screens, API coverage, and known gaps such as SLAM/autonomy HTTP), see [`docs/ANDROID_SIRENA_PARITY.md`](ANDROID_SIRENA_PARITY.md).

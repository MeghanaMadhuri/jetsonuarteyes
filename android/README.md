# Nina Companion (Android)

Jetpack Compose app for provisioning the Jetson over the **nina-link** HTTP API (Wi‑Fi AP / home network). Theme aligns with Sirena UI (red `#c8102e`, charcoal / cloud).

## Open in Android Studio

1. **File → Open** and select this folder: `nina-app/android` (the directory that contains `settings.gradle.kts`).
2. Wait for Gradle sync. If Android Studio asks to use the Gradle wrapper or download Gradle **8.7**, accept.
3. Enable **Developer options** and **USB debugging** on your tablet; connect USB (or use wireless debugging).
4. Choose your device in the toolbar and click **Run**.

If Gradle wrapper files (`gradlew`, `gradlew.bat`) are missing, Android Studio creates them on first sync, or install Gradle locally and run:

```bash
gradle wrapper --gradle-version 8.7
```

(from this directory).

## UI stack (Compose vs XML, parity with Sirena)

- **Layouts are Jetpack Compose** (Kotlin `@Composable` functions under `ui/sirena/`), **not** classic `res/layout/*.xml` screens. The app still uses **XML only where Android requires it** (e.g. `AndroidManifest.xml`, themes, `network_security_config`, drawables).
- **Pixel parity with the PyQt5 Sirena UI** is driven by docs and shared constants: [`docs/ANDROID_SIRENA_PARITY.md`](../docs/ANDROID_SIRENA_PARITY.md) and [`docs/android_sirena_parity.manifest.json`](../docs/android_sirena_parity.manifest.json). Treat the **desktop `sirena_ui` screens** as the layout reference.
- **Figma / wireframes:** this environment **cannot install or run Figma plugins** for you. If you want a Figma kit, export measurements from Qt Designer / screenshots, or mirror the parity doc in Figma manually; then adjust Compose `Modifier` chains (`padding`, `weight`, `aspectRatio`) to match.
- **Live camera (MJPEG):** RGB/depth streams are decoded in **`SirenaMjpegImage`** (OkHttp + JPEG scan + `BitmapFactory`); **`SirenaMjpegWebView`** remains for debugging. JSON “status” lines on Vision are **polled separately** (see `SirenaVisionScreen`). End‑to‑end latency includes Jetson encode, Wi‑Fi, and client decode — not only the poll interval. The client caps the multipart read buffer (~384KB) and uses a smaller preview decode long-edge (`SirenaMjpegPreviewMaxLongEdge`) in Vision / Perception / Drive so backlog and decode cost stay low. On the Jetson, tune **`NINA_MJPEG_MAX_WIDTH`** / **`NINA_MJPEG_JPEG_QUALITY`** (see `docs/COMPANION_APP.md`) so streams are not oversized for Wi‑Fi.

## Defaults

- **minSdk 26**, **compileSdk 34**
- Default Jetson URL: `http://10.42.0.1:8787` (NetworkManager hotspot on Jetson; use **Setup** if your gateway differs, e.g. `192.168.4.1` on some tether-style subnets).
- Cleartext HTTP is allowed for local robot communication (`network_security_config`).

## When Drive / Vision / Map / Record “do nothing”

The Jetson **`nina-link`** process disables hardware HTTP bridges **by default** (`503` from the API). The companion shows **callout cards** on the affected tabs when flags are off or capabilities have not been fetched yet.

**Read this first:** [`docs/COMPANION_CONTROLS.md`](../docs/COMPANION_CONTROLS.md) — env vars, auth, and a capability matrix. **Example env fragment:** [`docs/nina-link-bridge.env.example`](../docs/nina-link-bridge.env.example).

## Project layout

- `app/src/main/java/com/sirena/nina/companion/` — UI, `CompanionViewModel`, `LinkClient`
- `app/src/main/java/.../data/Prefs.kt` — DataStore for URL and bearer token

## Shareable APK (sideload)

After **Build → Build APK(s)** (release): outputs go to `app/build/outputs/apk/release/Sirena UI-<versionName>.apk` (for example `Sirena UI-0.4.1.apk`). Release builds use the **debug signing config** so you can install on any device without a Play Console key (internal use only). Scripts: [`scripts/build-companion-apk.ps1`](../scripts/build-companion-apk.ps1) / [`scripts/build-companion-apk.sh`](../scripts/build-companion-apk.sh) if `gradlew` exists.

See also [`docs/COMPANION_APP.md`](../docs/COMPANION_APP.md) and [`docs/ANDROID_SIRENA_PARITY.md`](../docs/ANDROID_SIRENA_PARITY.md) (desktop vs companion feature matrix). **What the tablet cannot mirror yet** (Health table, full Settings persist, map save, etc.): [`docs/ANDROID_SIRENA_GAPS.md`](../docs/ANDROID_SIRENA_GAPS.md). When `sirena_ui` changes labels, nav, or tiles, follow the **Hook** section in **ANDROID_SIRENA_PARITY** and [`docs/android_sirena_parity.manifest.json`](../docs/android_sirena_parity.manifest.json).

**Debug logs** (HTTP failures log at WARN from `LinkClient`):

`adb logcat -s NinaCompanion:I NinaCompanion:W NinaCompanion:E`

## Jetson dependencies (not built into the APK)

The APK only talks **HTTP** to **`nina-link`** on the Jetson. You must run a **Jetson-side install** before Drive / Actions / Vision are useful (bridges default off until the drop-in env is applied).

**Recommended one-shot on the robot** (repo root, after `git clone`):

```bash
chmod +x scripts/install-sirena-companion-jetson.sh scripts/update-nina-link-jetson.sh
./scripts/install-sirena-companion-jetson.sh
# Vision / SLAM-class pip deps in .venv-link (optional, larger):
# ./scripts/install-sirena-companion-jetson.sh --with-sirena-headless
```

Same behavior as **`./scripts/jetson-tablet-setup.sh`** (wrapper). After a normal install, headless extras only: **`./scripts/update-nina-link-jetson.sh --sirena-headless --restart`**.

On the robot, **`./scripts/verify-nina-link-companion.sh`** checks `/health`, capabilities, USB nodes, and logs.

**What changed recently (tablet + gateway):** see **[docs/COMPANION_APP.md](../docs/COMPANION_APP.md)** § *Companion app + gateway updates (0.4 track)* — capabilities **`neutral_action_name`**, Drive keyboard handling + honest copy, action playback hint, Settings category messaging, footer padding, **`versionName` `0.4.0`**.

Longer matrix: **COMPANION_APP.md**, **ANDROID_SIRENA_PARITY.md** § Jetson `.venv-link`.

#!/usr/bin/env bash
# Launcher for the Sirena Control Center, invoked by the .desktop entry.
#
# A double-clicked .desktop entry runs in a *very* sparse environment
# (no shell rc files sourced, PATH ~= /usr/local/bin:/usr/bin:/bin, no
# LD_LIBRARY_PATH for CUDA/cuDNN). This script bridges that gap so the
# GUI behaves identically whether you launch it from a terminal with
# ``python3 -m sirena_ui`` or by clicking the desktop icon:
#
#   1. Pick PYTHON_BIN first (repo .venv-link vs SIRENA_PYTHON vs system), then
#      source ~/.profile and ~/.bashrc so PATH / LD_LIBRARY_PATH / PYTHONPATH /
#      rc exports cannot hijack the interpreter choice.
#   2. Add Jetson's standard CUDA / cuDNN / TensorRT lib paths to
#      LD_LIBRARY_PATH so PyTorch + Ultralytics + TensorRT can find
#      their .so files. This is what /etc/profile.d/cuda.sh does
#      interactively but is missing for non-login shells.
#   3. Force ``QT_QPA_PLATFORM=xcb`` because PyQt5 on Jetson / older
#      Ubuntu builds doesn't always have a working Wayland plugin.
#   4. Append all stdout + stderr to ~/.cache/sirena/launch.log so
#      any error is captured even when the desktop launcher discards
#      the process output. The file is rotated to ~50 KB to stop it
#      growing forever on a long-running install.
#   5. If python exits non-zero, pop a zenity / notify-send / xmessage
#      dialog so the operator gets *something* visible instead of
#      "double-clicked the icon and nothing happened".
#
# You can override the Python interpreter:
#   SIRENA_PYTHON=/path/to/python3 ./scripts/launch-sirena.sh

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${HOME}/.cache/sirena"
LOG_FILE="${LOG_DIR}/launch.log"
mkdir -p "${LOG_DIR}"

# Choose Python before ~/.bashrc — rc files sometimes export SIRENA_PYTHON or
# PYTHONHOME and would skip ${REPO_ROOT}/.venv-link (uvicorn is in requirements-link.txt).
# Order: explicit SIRENA_PYTHON (systemd/interactive override), then repo venv, then system.
PYTHON_BIN=""
if [[ -n "${SIRENA_PYTHON:-}" ]]; then
    PYTHON_BIN="${SIRENA_PYTHON}"
elif [[ -x "${REPO_ROOT}/.venv-link/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv-link/bin/python"
else
    PYTHON_BIN="/usr/bin/python3"
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi

# Trim the log so it doesn't grow without bound between runs.
if [[ -f "${LOG_FILE}" ]]; then
    LOG_BYTES=$(stat -c %s "${LOG_FILE}" 2>/dev/null || echo 0)
    if [[ "${LOG_BYTES}" -gt 51200 ]]; then
        tail -c 32768 "${LOG_FILE}" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "${LOG_FILE}"
    fi
fi

# Bring across whatever the operator's interactive shell normally
# exposes (PATH, LD_LIBRARY_PATH for CUDA, PYTHONPATH if the user has
# any custom dirs). ``set +u`` first because both .profile and .bashrc
# routinely reference unset vars.
set +u
# shellcheck disable=SC1091
[[ -r "${HOME}/.profile" ]]      && . "${HOME}/.profile"
# shellcheck disable=SC1091
[[ -r "${HOME}/.bash_profile" ]] && . "${HOME}/.bash_profile"
# shellcheck disable=SC1091
[[ -r "${HOME}/.bashrc" ]]       && . "${HOME}/.bashrc"
set -u

# ``.bashrc`` sometimes exports PYTHONHOME / PYTHONUSERBASE for system Python;
# that breaks .venv-link (imports look fine in a bare ``python -c`` but fail
# under launch-sirena / the kiosk).
if [[ "${PYTHON_BIN}" == "${REPO_ROOT}/.venv-link/bin/python" ]]; then
    unset PYTHONHOME PYTHONUSERBASE
    # Ignore ~/.local torch/ultralytics/sympy — fleet packages live in .venv-link.
    export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
fi

# Belt-and-braces: explicitly add Jetson CUDA / cuDNN / TensorRT lib
# paths so PyTorch + Ultralytics can find libcudart, libcublas,
# libnvinfer etc. Harmless on hosts that don't have these dirs.
for _dir in \
    "/usr/local/cuda/lib64" \
    "/usr/local/cuda/targets/aarch64-linux/lib" \
    "/usr/lib/aarch64-linux-gnu/tegra" \
    "/usr/lib/aarch64-linux-gnu/nvidia" \
    "/usr/lib/aarch64-linux-gnu" \
    "${HOME}/.local/lib"
do
    if [[ -d "${_dir}" ]]; then
        case ":${LD_LIBRARY_PATH:-}:" in
            *":${_dir}:"*) : ;; # already on path
            *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${_dir}" ;;
        esac
    fi
done

# Always make sure ``~/.local/bin`` is on PATH so user-installed
# console scripts (e.g. mpg123 if pip-installed) are reachable.
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) : ;;
    *) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

# Ensure the repo is importable even if the user has nuked PYTHONPATH.
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
# If ``.venv-link`` was created without ``--system-site-packages``, apt's
# ``python3-pyqt5`` (under ``/usr/lib/python3/dist-packages``) is invisible and
# the GUI dies with ``ModuleNotFoundError: No module named 'PyQt5'``.
#
# PYTHONPATH entries are searched *before* the venv's site-packages, so we must
# prepend ``.venv-link/.../site-packages`` here (right after REPO_ROOT). Only
# then append dist-packages at the *end* for PyQt5 — otherwise Ubuntu's apt
# torch/ultralytics/sympy stubs shadow the Jetson wheels and object detection dies
# with "Ultralytics is required" / ``equal_valued`` ImportError even though a bare
# ``python -c "import ultralytics"`` works.
if [[ "${PYTHON_BIN}" == "${REPO_ROOT}/.venv-link/bin/python" ]]; then
    _venv_site="$("${PYTHON_BIN}" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
    if [[ -n "${_venv_site}" && -d "${_venv_site}" ]]; then
        case ":${PYTHONPATH}:" in
            *":${_venv_site}:"*) : ;;
            *) export PYTHONPATH="${REPO_ROOT}:${_venv_site}:${PYTHONPATH#${REPO_ROOT}:}" ;;
        esac
    fi
    _sys_py_d="/usr/lib/python3/dist-packages"
    if [[ -d "${_sys_py_d}/PyQt5" ]]; then
        case ":${PYTHONPATH}:" in
            *":${_sys_py_d}:"*) : ;;
            *) export PYTHONPATH="${PYTHONPATH}:${_sys_py_d}" ;;
        esac
    fi
fi

# ---------------------------------------------------------------------
# Defeat OpenCV's bundled-Qt vs system PyQt5 collision (exit 134).
# Must run AFTER PYTHONPATH is set so we resolve the same PyQt5 the app uses.
# ---------------------------------------------------------------------
_pin_qt_plugins_for_sirena() {
    unset QT_PLUGIN_PATH
    local _qt_plugins_pin=""
    if [[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]]; then
        _qt_plugins_pin="$("${PYTHON_BIN}" -c "
import os, sys
for p in os.environ.get('PYTHONPATH', '').split(':'):
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.append(p)
try:
    from PyQt5.QtCore import QLibraryInfo
    path = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    platforms = os.path.join(path, 'platforms') if path else ''
    if platforms and os.path.isfile(os.path.join(platforms, 'libqxcb.so')):
        print(platforms)
except Exception:
    pass
" 2>/dev/null || true)"
    fi
    if [[ -z "${_qt_plugins_pin}" ]]; then
        for _qt_plugins in \
            "/usr/lib/python3/dist-packages/PyQt5/Qt5/plugins" \
            "/usr/lib/python3/dist-packages/PyQt5/Qt/plugins" \
            "/usr/lib/aarch64-linux-gnu/qt5/plugins" \
            "/usr/lib/x86_64-linux-gnu/qt5/plugins" \
            "${REPO_ROOT}/.venv-link/lib/python3.10/site-packages/PyQt5/Qt5/plugins" \
            "${HOME}/.local/lib/python3.12/site-packages/PyQt5/Qt5/plugins" \
            "${HOME}/.local/lib/python3.11/site-packages/PyQt5/Qt5/plugins" \
            "${HOME}/.local/lib/python3.10/site-packages/PyQt5/Qt5/plugins"
        do
            if [[ -f "${_qt_plugins}/platforms/libqxcb.so" ]]; then
                _qt_plugins_pin="${_qt_plugins}/platforms"
                break
            fi
        done
    fi
    if [[ -n "${_qt_plugins_pin}" ]]; then
        export QT_QPA_PLATFORM_PLUGIN_PATH="${_qt_plugins_pin}"
        echo "[qt] pinned QT_QPA_PLATFORM_PLUGIN_PATH=${_qt_plugins_pin}"
    else
        echo "[qt] WARNING: no libqxcb.so found — GUI may abort if pip opencv-python is installed." >&2
        echo "[qt]   sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg" >&2
        echo "[qt]   ./.venv-link/bin/pip uninstall -y opencv-python && pip install opencv-python-headless" >&2
    fi
}
_pin_qt_plugins_for_sirena

# ---------------------------------------------------------------------
# Optional Jetson APE -> I2S route setup for MAX98357A.
#
# The Orin Nano APE mixer controls are runtime ALSA state, not persisted by
# the Python app. If the operator configured the MAX98357A via
# scripts/setup-max98357a-audio.sh, /etc/nina-link/navigation.env exports
# NINA_AUDIO_APE_ROUTE=1 plus the exact I2S5 parameters that were validated on
# the bench. Apply them here before the UI starts and before Nina opens the
# ALSA PCM. Best-effort: a route failure is logged but does not block the app.
# ---------------------------------------------------------------------
_apply_audio_ape_route_for_sirena() {
    case "${NINA_AUDIO_APE_ROUTE:-}" in
        1|true|TRUE|yes|YES|y|Y|on|ON) ;;
        *) return 0 ;;
    esac
    if ! command -v amixer >/dev/null 2>&1; then
        echo "[audio] WARNING: NINA_AUDIO_APE_ROUTE=1 but amixer is not installed" >&2
        return 0
    fi

    local card="${NINA_AUDIO_APE_CARD:-APE}"
    local i2s="${NINA_AUDIO_APE_I2S:-I2S5}"
    local mux="${NINA_AUDIO_APE_MUX:-ADMAIF1}"
    local rate="${NINA_AUDIO_OUTPUT_RATE:-48000}"
    local channels="${NINA_AUDIO_APE_CHANNELS:-2}"
    local bits="${NINA_AUDIO_APE_BITS:-16}"
    local frame="${NINA_AUDIO_APE_FRAME_MODE:-i2s}"
    local master="${NINA_AUDIO_APE_MASTER_MODE:-cbs-cfs}"
    local bclk="${NINA_AUDIO_APE_BCLK_RATIO:-16}"
    local fsync="${NINA_AUDIO_APE_FSYNC_WIDTH:-1}"

    echo "[audio] applying ${card} route: ${i2s}<=${mux} rate=${rate} ch=${channels} bits=${bits} frame=${frame} master=${master} bclk=${bclk} fsync=${fsync}"
    amixer -c "${card}" cset name="${i2s} Mux" "${mux}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Mux=${mux}" >&2
    amixer -c "${card}" cset name="${i2s} Sample Rate" "${rate}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Sample Rate=${rate}" >&2
    amixer -c "${card}" cset name="${i2s} Playback Audio Channels" "${channels}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Playback Audio Channels=${channels}" >&2
    amixer -c "${card}" cset name="${i2s} Playback Audio Bit Format" "${bits}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Playback Audio Bit Format=${bits}" >&2
    amixer -c "${card}" cset name="${i2s} Client Channels" "${channels}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Client Channels=${channels}" >&2
    amixer -c "${card}" cset name="${i2s} Client Bit Format" "${bits}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} Client Bit Format=${bits}" >&2
    amixer -c "${card}" cset name="${i2s} codec frame mode" "${frame}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} codec frame mode=${frame}" >&2
    amixer -c "${card}" cset name="${i2s} codec master mode" "${master}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} codec master mode=${master}" >&2
    amixer -c "${card}" cset name="${i2s} BCLK Ratio" "${bclk}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} BCLK Ratio=${bclk}" >&2
    amixer -c "${card}" cset name="${i2s} FSYNC Width" "${fsync}" >/dev/null 2>&1 || echo "[audio] WARN: failed ${i2s} FSYNC Width=${fsync}" >&2
}

# Older setup-hdmi-audio.sh wrote PERSISTENT_PIPE=0 or ALSA "default".
# Upgrade at runtime so git pull + restart fixes hiss without re-running setup.
_resolve_direct_hda_device_for_sirena() {
    command -v aplay >/dev/null 2>&1 || return 0
    local wav="/usr/share/sounds/alsa/Front_Center.wav"
    [[ -f "${wav}" ]] || return 0
    local d
    for d in \
        plughw:CARD=HDA,DEV=3 \
        plughw:CARD=HDA,DEV=0 \
        plughw:CARD=HDA,DEV=1 \
        plughw:CARD=HDA,DEV=2 \
        hw:CARD=HDA,DEV=3 \
        hw:CARD=HDA,DEV=0
    do
        if aplay -q -D "${d}" "${wav}" 2>/dev/null; then
            export NINA_GREET_APLAY_DEVICE="${d}"
            export NINA_AUDIO_MPG123_DEVICE="${d}"
            export NINA_AUDIO_MIXER_CARD=HDA
            echo "[audio] resolved direct HDMI PCM: ${d}"
            return 0
        fi
    done
    return 1
}

_upgrade_hdmi_audio_env_for_sirena() {
    if [[ "${NINA_AUDIO_APE_ROUTE:-}" == "1" ]]; then
        return 0
    fi
    case "${NINA_GREET_APLAY_DEVICE:-}${NINA_AUDIO_MPG123_DEVICE:-}" in
        *max98357*|*MAX98357*|*APE*|*ape*)
            return 0
            ;;
    esac

    if [[ "${NINA_AUDIO_PERSISTENT_PIPE:-}" == "0" ]]; then
        export NINA_AUDIO_PERSISTENT_PIPE=1
        echo "[audio] upgraded NINA_AUDIO_PERSISTENT_PIPE=0 -> 1 (anti-hiss)"
    fi
    if [[ -z "${NINA_AUDIO_IDLE_OUTPUT_MUTE:-}" ]]; then
        export NINA_AUDIO_IDLE_OUTPUT_MUTE=1
    fi

    local dev="${NINA_GREET_APLAY_DEVICE:-${NINA_AUDIO_MPG123_DEVICE:-default}}"
    case "${dev}" in
        default|sysdefault|sysdefault:*|"")
            systemctl --user stop pipewire pipewire-pulse wireplumber 2>/dev/null || true
            pulseaudio -k 2>/dev/null || true
            sleep 0.3
            if _resolve_direct_hda_device_for_sirena; then
                dev="${NINA_GREET_APLAY_DEVICE}"
            fi
            ;;
        *[Hh][Dd][Aa]*|*hdmi*|*HDMI*)
            if [[ -z "${NINA_AUDIO_MIXER_CARD:-}" ]]; then
                export NINA_AUDIO_MIXER_CARD=HDA
            fi
            ;;
    esac

    if [[ "${NINA_AUDIO_OUTPUT_RATE:-24000}" == "24000" ]]; then
        export NINA_AUDIO_OUTPUT_RATE=48000
        echo "[audio] HDMI: using NINA_AUDIO_OUTPUT_RATE=48000"
    fi

    echo "[audio] output device=${NINA_GREET_APLAY_DEVICE:-${NINA_AUDIO_MPG123_DEVICE:-unset}} "
    echo "  persistent_pipe=${NINA_AUDIO_PERSISTENT_PIPE:-1} "
    echo "  idle_mute=${NINA_AUDIO_IDLE_OUTPUT_MUTE:-1} rate=${NINA_AUDIO_OUTPUT_RATE:-48000}"
}

# Legacy Pi UART bridge vars — remove so GUI / children never inherit stale
# NINA_NAV_MODE=remote from ~/.bashrc or old navigation.env (Jetson is GPIO-only).
unset NINA_NAV_MODE NINA_NAV_REMOTE_PORT NINA_NAV_REMOTE_BAUD \
    NINA_NAV_REMOTE_TIMEOUT_SEC NINA_NAV_REMOTE_TURN_TICK_SEC \
    NINA_NAV_LEGACY_PI_BRIDGE 2>/dev/null || true

# Embedded Android gateway: if the operator did not set NINA_LINK_ENABLE_*,
# default tablet HTTP bridges on so the companion matches in-process Sirena UI
# (same defaults as desktop/nina-ui-kiosk.service). Explicit 0 is preserved.
case "${NINA_ANDROID_GATEWAY:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON)
        : "${NINA_LINK_ENABLE_ROBOT_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_ACTION_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_RECORD_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_VISION_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_ACTIONS_STATIC:=1}"
        : "${NINA_LINK_ENABLE_SLAM_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_DEPTH_BRIDGE:=1}"
        : "${NINA_LINK_ENABLE_AUTONOMY_BRIDGE:=1}"
        export NINA_LINK_ENABLE_ROBOT_BRIDGE NINA_LINK_ENABLE_ACTION_BRIDGE \
            NINA_LINK_ENABLE_RECORD_BRIDGE NINA_LINK_ENABLE_VISION_BRIDGE \
            NINA_LINK_ENABLE_ACTIONS_STATIC NINA_LINK_ENABLE_SLAM_BRIDGE \
            NINA_LINK_ENABLE_DEPTH_BRIDGE NINA_LINK_ENABLE_AUTONOMY_BRIDGE
        ;;
esac

# ---------------------------------------------------------------------
# Kiosk-mode panel: force 1024 x 600 on the connected display.
#
# Symptom this fixes: when launched by the systemd user unit on boot
# the GUI comes up huge / stretched, with parts running off the edges
# of the 10.1" panel. Same code launched manually from a terminal looks
# correct. Root cause is that the cheap HDMI panel reports a generic
# EDID (often 1920x1080) and X11 happily renders into that virtual
# surface; ``showFullScreen()`` then sizes our window to that surface,
# but every screen in sirena_ui/ is laid out for 1024 x 600 design
# pixels - hence the overflow.
#
# Fix: ask xrandr to put the panel into a real 1024 x 600 mode before
# we hand off to Qt. Only runs in kiosk mode (``NINA_UI_FULLSCREEN=1``)
# so dev workflows on a normal 1920x1080 / 4K monitor aren't downscaled
# behind the operator's back.
#
# The whole block is best-effort: missing xrandr / unsupported mode /
# locked panel all fall through silently with a log line; the GUI still
# launches, just at whatever resolution the panel was already in.
# ---------------------------------------------------------------------
_force_panel_resolution_1024x600() {
    case "${NINA_UI_FULLSCREEN:-}" in
        1|true|TRUE|yes|YES|y|Y|on|ON) ;;
        *) return 0 ;;
    esac
    if ! command -v xrandr >/dev/null 2>&1; then
        echo "[panel] xrandr not installed - skipping resolution forcing" >&2
        return 0
    fi
    if [[ -z "${DISPLAY:-}" ]]; then
        echo "[panel] DISPLAY unset - skipping resolution forcing" >&2
        return 0
    fi

    local output
    output="$(xrandr --query 2>/dev/null \
              | awk '/ connected/ {print $1; exit}')"
    if [[ -z "${output}" ]]; then
        echo "[panel] no connected output reported by xrandr" >&2
        return 0
    fi

    # Idempotency: if the panel is ALREADY at 1024x600, don't touch
    # xrandr at all. Reapplying the same mode (or re-adding the same
    # CVT modeline) on every launcher invocation appears to upset
    # gnome-session / mutter on JetPack 6 (Ubuntu 22.04 arm64) -
    # users see "gnome-session-binary crashed with SIGABRT in
    # g_assertion_..." apport pop-ups on most reboots. Treating the
    # whole xrandr block as a one-shot fixes that without giving up
    # the panel-resolution fix on first boot.
    local current_mode
    current_mode="$(xrandr --query 2>/dev/null \
                    | awk '/\*current/ {print $1; exit}')"
    if [[ "${current_mode}" == "1024x600" ]]; then
        echo "[panel] ${output} already at 1024x600 - no xrandr change needed"
        return 0
    fi

    # Try the existing mode first - if the panel's EDID already exposes
    # a 1024x600 mode, this is the one xrandr trusts most.
    if xrandr --output "${output}" --mode 1024x600 >/dev/null 2>&1; then
        echo "[panel] forced ${output} -> 1024x600 (existing mode)"
        return 0
    fi

    # Otherwise inject a CVT-derived 1024x600 modeline and retry. Values
    # come from ``cvt 1024 600 60`` and are stable across xrandr
    # versions; the ``|| true`` lets the call no-op if the mode is
    # already registered from a previous run.
    local mode_name="1024x600_60.00"
    local modeline="49.00 1024 1072 1168 1312 600 603 613 624 -hsync +vsync"
    xrandr --newmode "${mode_name}" ${modeline} 2>/dev/null || true
    xrandr --addmode "${output}" "${mode_name}" 2>/dev/null || true
    if xrandr --output "${output}" --mode "${mode_name}" >/dev/null 2>&1; then
        echo "[panel] forced ${output} -> ${mode_name} (custom CVT modeline)"
        return 0
    fi

    echo "[panel] WARNING: could not force ${output} to 1024x600 - GUI may overflow" >&2
}

EXIT=0
{
    echo
    echo "===== launching $(date '+%Y-%m-%d %H:%M:%S') ====="
    echo "REPO_ROOT=${REPO_ROOT}"
    echo "PYTHON=${PYTHON_BIN}"
    echo "DISPLAY=${DISPLAY:-<unset>}"
    echo "QT_QPA_PLATFORM=${QT_QPA_PLATFORM}"
    echo "QT_QPA_PLATFORM_PLUGIN_PATH=${QT_QPA_PLATFORM_PLUGIN_PATH:-<unset>}"
    echo "NINA_UI_FULLSCREEN=${NINA_UI_FULLSCREEN:-<unset>}"
    echo "NINA_REPO_ROOT=${NINA_REPO_ROOT:-<unset>}"
    echo "NINA_UI_SPLASH_VIDEO=${NINA_UI_SPLASH_VIDEO:-<unset>}"
    echo "NINA_UI_DEV_QUIT_PASSWORD=${NINA_UI_DEV_QUIT_PASSWORD:+<set>}"
    echo "PATH=${PATH}"
    echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
    echo "PYTHONPATH=${PYTHONPATH}"
    _force_panel_resolution_1024x600
    _upgrade_hdmi_audio_env_for_sirena
    _apply_audio_ape_route_for_sirena
    if [[ -z "${PYTHON_BIN}" ]]; then
        echo "FATAL: no python3 interpreter found on PATH" >&2
        exit 127
    fi
    cd "${REPO_ROOT}"
    export NINA_REPO_ROOT="${REPO_ROOT}"
    if [[ -f "${REPO_ROOT}/assets/nina_splash.mp4" ]]; then
        export NINA_UI_SPLASH_VIDEO="${REPO_ROOT}/assets/nina_splash.mp4"
    fi
    # Kiosk/systemd does not inherit shell exports — read dev-quit password from file.
    if [[ -z "${NINA_UI_DEV_QUIT_PASSWORD:-}" ]] \
        && [[ -f "${HOME}/.config/nina/dev_quit_password" ]]; then
        NINA_UI_DEV_QUIT_PASSWORD="$(tr -d '\r\n' < "${HOME}/.config/nina/dev_quit_password")"
        export NINA_UI_DEV_QUIT_PASSWORD
    fi
    # Tablet gateway MJPEG + camera/GPIO handles need more FDs than the default 1024.
    # Python also calls setrlimit() at import — log the shell ulimit for diagnostics.
    if command -v ulimit >/dev/null 2>&1; then
        ulimit -n 8192 2>/dev/null || ulimit -n 4096 2>/dev/null || true
        echo "ulimit -n: $(ulimit -n 2>/dev/null || echo '?')"
    fi
    "${PYTHON_BIN}" -m sirena_ui
    EXIT=$?
    echo "exit code: ${EXIT}"
} >> "${LOG_FILE}" 2>&1
EXIT=${EXIT:-$?}

# When launched from a terminal the operator sees the traceback. When
# launched from the desktop they see a dead icon, so try every dialog
# tool in turn until one of them sticks.
if [[ "${EXIT}" -ne 0 ]]; then
    MSG="Sirena Control Center failed to start (exit ${EXIT}).

See the last 50 lines of the launch log:
  tail -n 50 \"${LOG_FILE}\""
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title "Sirena" --text "${MSG}" --no-wrap >/dev/null 2>&1 || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical "Sirena failed to start" "${MSG}" >/dev/null 2>&1 || true
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center "${MSG}" >/dev/null 2>&1 || true
    fi
fi

exit "${EXIT}"

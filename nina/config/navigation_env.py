"""In-place updates for ``/etc/nina-link/navigation.env``.

The navigation env file is the single runtime source of truth for the
hoverboard lean tick values (``NINA_HOVER_FWD_POS_*`` forward,
``NINA_HOVER_REV_POS_*`` backward, ``NINA_HOVER_BRAKE_POS_*``) — see
``nina/systemd/nina-link-navigation.env.example``. The systemd unit
(``EnvironmentFile=-/etc/nina-link/navigation.env``) loads it BEFORE
the Python code starts, so anything in it overrides the in-tree
``settings.py`` defaults. The bench Lean Cal screen needs a way to
persist the operator's tuned L / R numbers into this file so the
calibrated lean survives a service restart.

Two constraints shape the implementation:

1. **Preserve everything else.** The env file holds polarity overrides,
   battery calibration, IMU enable flags, and a pile of operator
   commentary — we MUST NOT clobber lines we don't own. The update
   replaces only the two ``NINA_HOVER_REV_POS_LEFT`` /
   ``NINA_HOVER_REV_POS_RIGHT`` lines (matching by key, ignoring
   leading whitespace and ``export ``). Comments and unrelated keys
   pass through untouched.
2. **Root-owned destination.** ``/etc/nina-link/navigation.env`` is
   typically owned by root and the kiosk service runs as user
   ``nina``. We try a direct write first; if that hits
   ``PermissionError`` we fall back to ``pkexec`` (which pops a GUI
   polkit prompt for the operator's password). If neither path works
   the caller gets a descriptive :class:`NavigationEnvWriteError` and
   can fall back to manual copy/paste.

The "atomic write" is the standard write-to-tempfile-in-same-dir +
``os.replace`` dance — readers (systemd reading the file at next
restart) either see the old contents or the fully-written new
contents, never a half-written file.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_ENV_PATH = Path("/etc/nina-link/navigation.env")


def apply_env_file_to_process(
    env_path: Optional[Path] = None,
    *,
    only_if_unset: bool = True,
) -> int:
    """Load ``/etc/nina-link/navigation.env`` into ``os.environ``.

    Systemd ``EnvironmentFile`` does this for services; CLI and manual
    ``python3 -m sirena_ui`` need the same without shell ``export``.

    When *only_if_unset* is True (default), existing process env wins
    (operator ``export`` or systemd drop-ins are not overwritten).
    """
    path = env_path
    if path is None:
        custom = (os.environ.get("NINA_NAVIGATION_ENV_PATH") or "").strip()
        path = Path(custom) if custom else DEFAULT_ENV_PATH
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    applied = 0
    for line in text.splitlines():
        m = _KEY_LINE_RE.match(line)
        if m is None:
            continue
        key = m.group("key")
        value = m.group("value").strip().strip("\"'")
        if only_if_unset and key in os.environ:
            continue
        os.environ[key] = value
        applied += 1
    return applied


_KEY_LEFT = "NINA_HOVER_REV_POS_LEFT"
_KEY_RIGHT = "NINA_HOVER_REV_POS_RIGHT"
_LEGACY_KEY_LEFT = "NINA_HOVER_FWD_POS_LEFT"
_LEGACY_KEY_RIGHT = "NINA_HOVER_FWD_POS_RIGHT"


class NavigationEnvWriteError(RuntimeError):
    """Raised when neither the direct write nor the pkexec fallback
    succeeded. The :attr:`detail` describes which path failed and how
    so the UI layer can surface something actionable to the operator.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_backward_lean(
    left: int,
    right: int,
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    allow_pkexec: bool = True,
) -> Path:
    """Persist *left* / *right* MX-28 backward lean ticks into the
    navigation env file at *env_path*. Returns the resolved path that
    was written (same as *env_path* in the happy path).

    The two ``NINA_HOVER_REV_POS_*`` lines are replaced in-place if
    they already exist; otherwise they're appended at the end under a
    one-line comment marker so a later edit can find them again. All
    other lines pass through untouched.

    If the direct write hits ``PermissionError`` and *allow_pkexec* is
    True, the function falls back to invoking ``pkexec`` with a small
    shell command that copies a staged temp file into place as root.
    The operator sees the standard polkit auth dialog.

    Raises:
        NavigationEnvWriteError: when neither write path succeeded.
        ValueError: when *left* or *right* are outside the safe
            Dynamixel tick range ``[0, 4095]``.
    """
    left_i = _clamp_tick(left, "left")
    right_i = _clamp_tick(right, "right")

    new_content = _compute_updated_content(env_path, left_i, right_i)

    # Try direct write first — works when the operator runs the UI as
    # root or when the file's ACLs let the service user write.
    try:
        _atomic_write_text(env_path, new_content)
        return env_path
    except PermissionError as exc:
        direct_err = exc
    except FileNotFoundError as exc:
        # Parent dir missing — try to create it. If we can't, fall
        # through to pkexec (which can mkdir as root).
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(env_path, new_content)
            return env_path
        except PermissionError:
            direct_err = exc

    if not allow_pkexec:
        raise NavigationEnvWriteError(
            f"direct write to {env_path} failed and pkexec fallback is disabled",
            detail=str(direct_err),
        )

    return _pkexec_install(env_path, new_content, direct_err=str(direct_err))


def parse_existing_backward_lean(
    env_path: Path = DEFAULT_ENV_PATH,
) -> Tuple[Optional[int], Optional[int]]:
    """Return the (left, right) values currently set in *env_path* for
    the backward lean keys, or ``None`` for each side that isn't
    present / can't be parsed. Useful so the UI can pre-populate
    sliders from the persisted values rather than the import-time
    defaults.

    Missing file is treated as "no values set" (returns ``(None, None)``).
    """
    if not env_path.exists():
        return (None, None)
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (None, None)
    left = _last_value_for_key(text, _KEY_LEFT)
    right = _last_value_for_key(text, _KEY_RIGHT)
    if left is None:
        left = _last_value_for_key(text, _LEGACY_KEY_LEFT)
    if right is None:
        right = _last_value_for_key(text, _LEGACY_KEY_RIGHT)
    return (left, right)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_KEY_LINE_RE = re.compile(
    r"""
    ^\s*                  # leading whitespace
    (?:export\s+)?        # optional `export ` prefix (systemd EnvironmentFile
                          # ignores it but operators sometimes add it)
    (?P<key>[A-Z][A-Z0-9_]*)    # the variable name
    \s*=\s*
    (?P<value>[^\#\n]*?)  # the value (no inline comment)
    \s*(?:\#.*)?$         # optional trailing comment
    """,
    re.VERBOSE,
)


def _clamp_tick(value: int, side: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{side} tick must be an integer, got {value!r}") from exc
    if v < 0 or v > 4095:
        raise ValueError(
            f"{side} tick {v} out of Dynamixel range [0, 4095]"
        )
    return v


def _last_value_for_key(text: str, key: str) -> Optional[int]:
    """Return the last integer assigned to *key* in *text*, or None."""
    found: Optional[int] = None
    for line in text.splitlines():
        m = _KEY_LINE_RE.match(line)
        if m is None:
            continue
        if m.group("key") != key:
            continue
        raw = m.group("value").strip()
        # Strip surrounding quotes if any.
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        try:
            found = int(raw)
        except ValueError:
            # Don't lose the previously-parsed valid value to a malformed later line.
            continue
    return found


def _compute_updated_content(env_path: Path, left: int, right: int) -> str:
    """Read *env_path* (treating missing file as empty), replace the
    two backward-lean lines in place, and return the new content as a
    single string (with trailing newline)."""
    if env_path.exists():
        try:
            existing = env_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            existing = ""
    else:
        existing = ""

    desired = {
        _KEY_LEFT: f"{_KEY_LEFT}={left}",
        _KEY_RIGHT: f"{_KEY_RIGHT}={right}",
    }

    out_lines: List[str] = []
    replaced: dict[str, bool] = {_KEY_LEFT: False, _KEY_RIGHT: False}

    for line in _split_preserving_endings(existing):
        body, ending = _strip_line_ending(line)
        m = _KEY_LINE_RE.match(body)
        if m is not None and m.group("key") in desired:
            key = m.group("key")
            if not replaced[key]:
                out_lines.append(desired[key] + (ending or "\n"))
                replaced[key] = True
            # If a key appears multiple times in the source, keep only
            # the first (which we just rewrote) and drop the duplicates
            # — otherwise systemd reads the LAST occurrence and we'd be
            # surprised by a stale value beating our fresh write.
            continue
        out_lines.append(body + (ending or "\n"))

    # Strip a single trailing empty "line" so we don't accumulate
    # blank lines on repeated saves.
    if out_lines and out_lines[-1] == "\n":
        # but keep exactly one trailing newline at the end of the file
        pass

    missing = [k for k, v in replaced.items() if not v]
    if missing:
        # Append a header + the missing keys at the end. If the
        # existing content doesn't end in a newline, add one first so
        # our block sits on its own line.
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines[-1] = out_lines[-1] + "\n"
        out_lines.append("\n")
        out_lines.append("# Backward lean (MX-28) — written by Lean Cal screen\n")
        for key in missing:
            out_lines.append(desired[key] + "\n")

    result = "".join(out_lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def _split_preserving_endings(text: str) -> Iterable[str]:
    """Yield lines with their original endings preserved. Unlike
    ``str.splitlines()``, this keeps the trailing ``\\n`` / ``\\r\\n``
    so we can write the file back byte-for-byte identical aside from
    the lines we intentionally rewrote."""
    start = 0
    n = len(text)
    while start < n:
        nl = text.find("\n", start)
        if nl == -1:
            yield text[start:]
            return
        yield text[start : nl + 1]
        start = nl + 1


def _strip_line_ending(line: str) -> Tuple[str, str]:
    """Return ``(body, ending)`` where *ending* is ``"\\r\\n"``,
    ``"\\n"``, or ``""``."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically (write-temp-in-same-dir +
    ``os.replace``). The temp file is removed if any step fails."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        # Preserve mode from the existing file if any, otherwise 0644.
        try:
            existing_mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            existing_mode = 0o644
        os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _pkexec_install(path: Path, content: str, *, direct_err: str) -> Path:
    """Stage *content* in a world-readable temp file owned by the
    current user, then use ``pkexec`` to copy it into *path* as
    root. The polkit GUI prompt asks the operator for authentication
    (their password by default; on the Jetson this is typically the
    ``nina`` user's password).

    Raises :class:`NavigationEnvWriteError` if pkexec is missing or
    the elevated copy fails (including the operator dismissing the
    prompt).
    """
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        raise NavigationEnvWriteError(
            f"cannot write {path}: direct write failed and pkexec is not "
            f"installed on this host",
            detail=direct_err,
        )

    # Stage the content in /tmp (readable by root) — we own the temp
    # file so we can clean it up regardless of pkexec outcome.
    fd, staged_name = tempfile.mkstemp(
        prefix="navigation.env.staged.", suffix=".tmp", dir="/tmp"
    )
    staged_path = Path(staged_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(staged_path, 0o644)

        # pkexec runs the elevated command. We use install(1) so the
        # destination file's owner/mode end up at root:root 0644 —
        # matches what shipping the file via packaging would do.
        cmd = [
            pkexec,
            "/usr/bin/install",
            "-m",
            "0644",
            "-o",
            "root",
            "-g",
            "root",
            str(staged_path),
            str(path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except FileNotFoundError as exc:
            raise NavigationEnvWriteError(
                f"pkexec invocation failed: {exc}",
                detail=direct_err,
            ) from exc

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip()
            # pkexec exits 126 when the user dismisses the auth dialog
            # or auth fails — surface that distinctly so the UI can
            # message the operator cleanly.
            if result.returncode == 126:
                raise NavigationEnvWriteError(
                    "polkit authentication was cancelled or failed",
                    detail=tail or f"rc={result.returncode}",
                )
            raise NavigationEnvWriteError(
                f"pkexec install failed (rc={result.returncode})",
                detail=tail or "(no stderr)",
            )

        return path
    finally:
        try:
            staged_path.unlink()
        except FileNotFoundError:
            pass

"""Tests for :mod:`nina.config.navigation_env`.

We exercise the direct-write path end-to-end (atomic temp + replace,
in-place replacement of the two backward-lean keys, comment / unrelated
key preservation, append-if-missing). The ``pkexec`` fallback path is
NOT exercised here — it requires root + polkit + an interactive dialog,
neither of which belong in a unit test. The direct-write path is what
the operator hits 99% of the time (the kiosk service typically runs as
the ``nina`` user that owns the env file, or the ACLs let it write).
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path

from nina.config.navigation_env import (
    NavigationEnvWriteError,
    parse_existing_backward_lean,
    update_backward_lean,
)


def test_update_replaces_existing_lines_in_place(tmp_path: Path) -> None:
    """When both keys exist, update them in place — do NOT append
    duplicates. Other lines (comments, polarity, IMU flags, etc.)
    must pass through untouched."""
    env = tmp_path / "navigation.env"
    env.write_text(
        "# polarity\n"
        "NINA_NAV_INVERT_LEFT=1\n"
        "NINA_NAV_INVERT_RIGHT=0\n"
        "\n"
        "# Hoverboard lean (MX-28)\n"
        "NINA_HOVER_FWD_POS_LEFT=2022\n"
        "NINA_HOVER_FWD_POS_RIGHT=2080\n"
        "NINA_HOVER_REV_POS_LEFT=2068\n"
        "NINA_HOVER_REV_POS_RIGHT=2028\n"
        "NINA_HOVER_BRAKE_POS_LEFT=2048\n"
        "NINA_HOVER_BRAKE_POS_RIGHT=2048\n"
    )
    update_backward_lean(2100, 1950, env_path=env, allow_pkexec=False)

    out = env.read_text()

    # Backward keys replaced.
    assert "NINA_HOVER_REV_POS_LEFT=2100\n" in out
    assert "NINA_HOVER_REV_POS_RIGHT=1950\n" in out
    # Old values gone.
    assert "NINA_HOVER_REV_POS_LEFT=2068" not in out
    assert "NINA_HOVER_REV_POS_RIGHT=2028" not in out
    # Unrelated lines preserved.
    assert "NINA_NAV_INVERT_LEFT=1\n" in out
    assert "NINA_HOVER_FWD_POS_LEFT=2022\n" in out
    assert "NINA_HOVER_BRAKE_POS_LEFT=2048\n" in out
    # Comments preserved.
    assert "# polarity\n" in out
    assert "# Hoverboard lean (MX-28)\n" in out


def test_update_appends_keys_when_missing(tmp_path: Path) -> None:
    """If the env file exists but doesn't contain the backward-lean
    keys, append them under a header comment. Existing content
    must be preserved as-is."""
    env = tmp_path / "navigation.env"
    env.write_text(
        "# polarity overrides\n"
        "NINA_NAV_INVERT_LEFT=1\n"
        "NINA_HOVER_FWD_POS_LEFT=2022\n"
    )
    update_backward_lean(2110, 1985, env_path=env, allow_pkexec=False)
    out = env.read_text()
    # Original lines preserved.
    assert "# polarity overrides\n" in out
    assert "NINA_NAV_INVERT_LEFT=1\n" in out
    assert "NINA_HOVER_FWD_POS_LEFT=2022\n" in out
    # New lines appended with a header.
    assert "NINA_HOVER_REV_POS_LEFT=2110\n" in out
    assert "NINA_HOVER_REV_POS_RIGHT=1985\n" in out
    assert "# Backward lean (MX-28)" in out


def test_update_creates_file_when_missing(tmp_path: Path) -> None:
    """If the env file doesn't exist, create it (and parent dirs)
    with just the two backward-lean lines under a header comment."""
    env = tmp_path / "nested" / "missing-dir" / "navigation.env"
    assert not env.exists()
    update_backward_lean(2075, 2010, env_path=env, allow_pkexec=False)
    out = env.read_text()
    assert "NINA_HOVER_REV_POS_LEFT=2075\n" in out
    assert "NINA_HOVER_REV_POS_RIGHT=2010\n" in out
    assert env.exists()


def test_update_dedupes_repeated_keys(tmp_path: Path) -> None:
    """If a key shows up multiple times in the source (operator
    accidentally pasted twice), keep only the first occurrence and
    drop the duplicates — otherwise the LAST occurrence wins at
    systemd parse time and our fresh write gets shadowed by the
    stale one further down."""
    env = tmp_path / "navigation.env"
    env.write_text(
        "NINA_HOVER_REV_POS_LEFT=2068\n"
        "NINA_NAV_INVERT_LEFT=1\n"
        "NINA_HOVER_REV_POS_LEFT=9999\n"   # stale duplicate further down
        "NINA_HOVER_REV_POS_RIGHT=2028\n"
        "NINA_HOVER_REV_POS_RIGHT=8888\n"  # stale duplicate further down
    )
    update_backward_lean(2100, 1950, env_path=env, allow_pkexec=False)
    out = env.read_text()
    # Only one occurrence each, with the new value.
    assert out.count("NINA_HOVER_REV_POS_LEFT=") == 1
    assert out.count("NINA_HOVER_REV_POS_RIGHT=") == 1
    assert "NINA_HOVER_REV_POS_LEFT=2100\n" in out
    assert "NINA_HOVER_REV_POS_RIGHT=1950\n" in out
    # The stale duplicates and the old originals are gone.
    assert "9999" not in out
    assert "8888" not in out
    assert "NINA_HOVER_REV_POS_LEFT=2068" not in out
    # Unrelated line preserved.
    assert "NINA_NAV_INVERT_LEFT=1\n" in out


def test_update_handles_export_prefix_and_quoted_values(tmp_path: Path) -> None:
    """Operators sometimes write ``export NINA_HOVER_...="2068"`` —
    that's also a valid env-file form. The matcher must catch those
    so the values aren't left behind as stale duplicates."""
    env = tmp_path / "navigation.env"
    env.write_text(
        'export NINA_HOVER_REV_POS_LEFT="2068"\n'
        "export NINA_HOVER_REV_POS_RIGHT='2028'\n"
        "NINA_NAV_INVERT_LEFT=1\n"
    )
    update_backward_lean(2100, 1950, env_path=env, allow_pkexec=False)
    out = env.read_text()
    assert "NINA_HOVER_REV_POS_LEFT=2100\n" in out
    assert "NINA_HOVER_REV_POS_RIGHT=1950\n" in out
    assert "2068" not in out
    assert "2028" not in out


def test_update_rejects_out_of_range_ticks(tmp_path: Path) -> None:
    """Reject ticks outside the Dynamixel range — both raw write and
    pkexec fallback would silently clamp, but a programmer error here
    should be loud (so the UI doesn't think it persisted 4500 when
    the device will see 4095)."""
    env = tmp_path / "navigation.env"
    with pytest.raises(ValueError, match="out of Dynamixel range"):
        update_backward_lean(4500, 2000, env_path=env, allow_pkexec=False)
    with pytest.raises(ValueError, match="out of Dynamixel range"):
        update_backward_lean(2000, -1, env_path=env, allow_pkexec=False)
    with pytest.raises(ValueError, match="must be an integer"):
        update_backward_lean("abc", 2000, env_path=env, allow_pkexec=False)  # type: ignore[arg-type]
    assert not env.exists(), "no file should be created on validation failure"


def test_update_no_pkexec_when_disabled_raises_clear_error(tmp_path: Path) -> None:
    """If the direct write hits PermissionError and pkexec is
    explicitly disabled, surface a clear error rather than silently
    succeeding."""
    env = tmp_path / "navigation.env"
    env.write_text("NINA_HOVER_REV_POS_LEFT=2068\n")
    # Make the parent dir read-only so the rename inside _atomic_write_text
    # fails with PermissionError. (We can't chmod the file itself because
    # write goes via temp + rename — it's the rename into the directory
    # that needs to be denied.)
    os.chmod(tmp_path, 0o555)
    try:
        with pytest.raises(NavigationEnvWriteError) as exc_info:
            update_backward_lean(
                2100, 1950, env_path=env, allow_pkexec=False
            )
        assert "pkexec fallback is disabled" in str(exc_info.value)
    finally:
        os.chmod(tmp_path, 0o755)


def test_parse_existing_backward_lean_reads_current_values(tmp_path: Path) -> None:
    env = tmp_path / "navigation.env"
    env.write_text(
        "NINA_HOVER_FWD_POS_LEFT=2022\n"
        "NINA_HOVER_REV_POS_LEFT=2068\n"
        "NINA_HOVER_REV_POS_RIGHT=2028\n"
    )
    left, right = parse_existing_backward_lean(env_path=env)
    assert left == 2068
    assert right == 2028


def test_parse_existing_backward_lean_returns_none_for_missing(
    tmp_path: Path,
) -> None:
    # Missing file → both None
    assert parse_existing_backward_lean(env_path=tmp_path / "nope.env") == (None, None)
    # Present but no backward keys → both None
    env = tmp_path / "navigation.env"
    env.write_text("NINA_NAV_INVERT_LEFT=1\n")
    assert parse_existing_backward_lean(env_path=env) == (None, None)


def test_parse_existing_takes_last_occurrence(tmp_path: Path) -> None:
    """When duplicates exist (operator pasted twice), the LAST one
    wins at systemd parse time. parse_existing_backward_lean must
    return that same last-wins value so the UI shows what the device
    actually loads."""
    env = tmp_path / "navigation.env"
    env.write_text(
        "NINA_HOVER_REV_POS_LEFT=2068\n"
        "NINA_HOVER_REV_POS_LEFT=2150\n"
        "NINA_HOVER_REV_POS_RIGHT='2028'\n"
        'NINA_HOVER_REV_POS_RIGHT="2000"\n'
    )
    assert parse_existing_backward_lean(env_path=env) == (2150, 2000)


def test_update_preserves_trailing_newline(tmp_path: Path) -> None:
    """Output file always ends in exactly one newline — never a
    bare line that some editors would auto-add at next save (which
    would then show as a diff)."""
    env = tmp_path / "navigation.env"
    env.write_text("NINA_HOVER_REV_POS_LEFT=2068\nNINA_HOVER_REV_POS_RIGHT=2028\n")
    update_backward_lean(2100, 1950, env_path=env, allow_pkexec=False)
    out = env.read_text()
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_update_is_atomic_via_replace(tmp_path: Path, monkeypatch) -> None:
    """If the write phase blows up after the tempfile is created,
    the original file must remain intact AND no stray temp file
    should be left lying around in the parent dir."""
    env = tmp_path / "navigation.env"
    env.write_text("NINA_HOVER_REV_POS_LEFT=2068\nNINA_HOVER_REV_POS_RIGHT=2028\n")
    original = env.read_text()

    # Force os.replace to fail mid-write.
    import nina.config.navigation_env as mod
    boom = RuntimeError("simulated replace failure")
    monkeypatch.setattr(mod.os, "replace", lambda *_a, **_kw: (_ for _ in ()).throw(boom))

    with pytest.raises(RuntimeError, match="simulated replace failure"):
        update_backward_lean(2100, 1950, env_path=env, allow_pkexec=False)

    # Original file untouched.
    assert env.read_text() == original
    # No stray .navigation.env.*.tmp left behind.
    leftovers = [
        p.name
        for p in tmp_path.iterdir()
        if p.name.startswith(".navigation.env.") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], f"stray temp files: {leftovers}"

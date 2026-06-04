"""Tests for loading /etc/nina-link/navigation.env into the process."""

from __future__ import annotations

import os
from pathlib import Path

from nina.config.navigation_env import apply_env_file_to_process


def test_apply_env_file_sets_unset_keys(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / "navigation.env"
    env.write_text(
        "NINA_EYE_UART_PORT=/dev/ttyUSB0\nNINA_EYE_UART_BAUD=115200\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NINA_EYE_UART_PORT", raising=False)
    n = apply_env_file_to_process(env, only_if_unset=True)
    assert n >= 1
    assert os.environ["NINA_EYE_UART_PORT"] == "/dev/ttyUSB0"


def test_apply_env_file_does_not_override_existing(
    tmp_path: Path, monkeypatch
) -> None:
    env = tmp_path / "navigation.env"
    env.write_text("NINA_EYE_UART_PORT=/dev/ttyUSB0\n", encoding="utf-8")
    monkeypatch.setenv("NINA_EYE_UART_PORT", "/dev/ttyTHS1")
    apply_env_file_to_process(env, only_if_unset=True)
    assert os.environ["NINA_EYE_UART_PORT"] == "/dev/ttyTHS1"

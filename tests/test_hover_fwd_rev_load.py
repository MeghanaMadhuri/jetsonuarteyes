"""Hover FWD/REV env load defaults."""

from __future__ import annotations

import os

import pytest

from nina.config.settings import _load_hover_forward_backward_positions


@pytest.fixture(autouse=True)
def _clear_hover_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("NINA_HOVER_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults_match_fleet_hardcoded_tune() -> None:
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2022, 2080)
    assert (bwd_l, bwd_r) == (2100, 2000)


def test_direct_mapping_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINA_HOVER_FWD_POS_LEFT", "2022")
    monkeypatch.setenv("NINA_HOVER_FWD_POS_RIGHT", "2080")
    monkeypatch.setenv("NINA_HOVER_REV_POS_LEFT", "2100")
    monkeypatch.setenv("NINA_HOVER_REV_POS_RIGHT", "2000")
    monkeypatch.setenv("NINA_HOVER_SWAP_FWD_REV_ENV_VALUES", "0")
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2022, 2080)
    assert (bwd_l, bwd_r) == (2100, 2000)


def test_swap_env_values_exchanges_fwd_rev_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINA_HOVER_FWD_POS_LEFT", "2022")
    monkeypatch.setenv("NINA_HOVER_FWD_POS_RIGHT", "2080")
    monkeypatch.setenv("NINA_HOVER_REV_POS_LEFT", "2150")
    monkeypatch.setenv("NINA_HOVER_REV_POS_RIGHT", "2000")
    monkeypatch.setenv("NINA_HOVER_SWAP_FWD_REV_ENV_VALUES", "1")
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2150, 2000)
    assert (bwd_l, bwd_r) == (2022, 2080)

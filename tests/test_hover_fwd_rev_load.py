"""Hover FWD/REV env load after fleet hall F/B swap."""

from __future__ import annotations

import os

import pytest

from nina.config.settings import _load_hover_forward_backward_positions


@pytest.fixture(autouse=True)
def _clear_hover_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("NINA_HOVER_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults_match_post_swap_attachment_table() -> None:
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2150, 2000)
    assert (bwd_l, bwd_r) == (2022, 2080)


def test_legacy_env_file_values_are_swapped_at_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unmigrated navigation.env still lists pre-swap bench numbers."""
    monkeypatch.setenv("NINA_HOVER_FWD_POS_LEFT", "2022")
    monkeypatch.setenv("NINA_HOVER_FWD_POS_RIGHT", "2080")
    monkeypatch.setenv("NINA_HOVER_REV_POS_LEFT", "2150")
    monkeypatch.setenv("NINA_HOVER_REV_POS_RIGHT", "2000")
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2150, 2000)
    assert (bwd_l, bwd_r) == (2022, 2080)


def test_migrated_env_uses_direct_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINA_HOVER_FWD_POS_LEFT", "2150")
    monkeypatch.setenv("NINA_HOVER_FWD_POS_RIGHT", "2000")
    monkeypatch.setenv("NINA_HOVER_REV_POS_LEFT", "2022")
    monkeypatch.setenv("NINA_HOVER_REV_POS_RIGHT", "2080")
    monkeypatch.setenv("NINA_HOVER_SWAP_FWD_REV_ENV_VALUES", "0")
    fwd_l, fwd_r, bwd_l, bwd_r = _load_hover_forward_backward_positions()
    assert (fwd_l, fwd_r) == (2150, 2000)
    assert (bwd_l, bwd_r) == (2022, 2080)

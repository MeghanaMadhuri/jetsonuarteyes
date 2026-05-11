"""
Build a Jetson GPIO `NavigationManager` from `NavigationSettings`.

Used by `sirena_ui.workers.nina_service` and by CLI tools so construction
lives in one place. The factory returns an *un-initialised* manager —
the caller must still call `initialize()`.

Env var summary (read at settings-load time, see `nina.config.settings`):

    NINA_NAV_START_KICK_PCT    # default 14; 0 = no breakaway pulse (was 35 — dominated low cruise)
    NINA_NAV_START_KICK_SEC    # default 1.0 (max); clamped to 1.0; 0 = off
    NINA_NAV_DIR_SETTLE_SEC    # default 0.03; DIR+EL before PWM; 0 = off
    NINA_NAV_PWM_REASSERT_SEC  # default 0.02; 2nd PWM write from rest; 0 = off
    NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC # default 0.5; straight crawl only; 0 = off
    NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_PCT # default 20 (% of cmd speed for opposite jog)
    NINA_NAV_OPPOSITE_ZERO_SETTLE_SEC   # default 0.04; pause at PWM 0 after jog
    NINA_NAV_PIVOT_TURN_LEFT_EXTRA_PP # default 6; symmetric +% both wheels turn_left
    NINA_NAV_TURN_LEFT_PREP_BACK_SEC # default 0.12; 0=skip straight-back prime
    NINA_NAV_TURN_LEFT_PREP_FWD_SEC  # default 0.12; 0=skip straight-fwd prime
    NINA_NAV_EL_ACTIVE_LOW     # GPIO LOW arms EL, HIGH disables
    NINA_NAV_SETTLE_SEC        # default 0.1
    NINA_NAV_SPEED             # default 8
"""

from __future__ import annotations

from nina.config.settings import NavigationSettings
from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


def build_navigation_manager(settings: NavigationSettings) -> NavigationManager:
    """Return an un-initialised `NavigationManager`."""
    cfg = NavigationConfig(
        pins=DEFAULT_PINS,
        backend_name=settings.backend_name,
        pwm_frequency_hz=settings.pwm_frequency_hz,
        default_speed_percent=settings.default_speed_percent,
        turn_duration_sec=settings.turn_duration_sec,
        invert_left_dir=settings.invert_left_dir,
        invert_right_dir=settings.invert_right_dir,
        el_active_low=settings.el_active_low,
        start_kick_percent=settings.start_kick_percent,
        start_kick_sec=settings.start_kick_sec,
        dir_pwm_gap_sec=settings.dir_pwm_gap_sec,
        pwm_reassert_sec=settings.pwm_reassert_sec,
        straight_opposite_nudge_sec=settings.straight_opposite_nudge_sec,
        straight_opposite_nudge_pct=settings.straight_opposite_nudge_pct,
        opposite_zero_settle_sec=settings.opposite_zero_settle_sec,
        settle_delay_sec=settings.settle_delay_sec,
        pivot_turn_left_extra_pp=settings.pivot_turn_left_extra_pp,
        turn_left_prep_back_sec=settings.turn_left_prep_back_sec,
        turn_left_prep_fwd_sec=settings.turn_left_prep_fwd_sec,
    )
    return NavigationManager(cfg)

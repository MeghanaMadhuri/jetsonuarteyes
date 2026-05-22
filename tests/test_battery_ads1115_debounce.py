"""Unit tests for ADS1115 battery debounce helper + locked default thresholds."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from nina.config.settings import load_settings
from nina.sensors import ads1115 as ads1115_consts
from nina.sensors.battery_ads1115_monitor import (
    DEFAULT_REPEAT_STEP_V,
    battery_low_debounce_step,
    decide_low_battery_reminder,
    decide_low_battery_repeat,
    update_pack_min_v,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class BatteryLowDebounceTests(unittest.TestCase):
    def test_clear_resets(self) -> None:
        fire, n = battery_low_debounce_step(
            24.0, low_v=23.0, debounce_reads=2, consecutive_low=1
        )
        self.assertFalse(fire)
        self.assertEqual(n, 0)

    def test_fires_after_debounce(self) -> None:
        fire, n = battery_low_debounce_step(
            22.5, low_v=23.0, debounce_reads=2, consecutive_low=0
        )
        self.assertFalse(fire)
        self.assertEqual(n, 1)

        fire2, n2 = battery_low_debounce_step(
            22.0, low_v=23.0, debounce_reads=2, consecutive_low=n
        )
        self.assertTrue(fire2)
        self.assertEqual(n2, 0)


class BatteryThresholdDefaultsTests(unittest.TestCase):
    """The pack-low / clear defaults are safety-critical for the BLDC stack —
    pin them here so an accidental edit can't silently regress them.

    Last operator-validated values (2026-05-18): ``low=25.5 V``, ``clear=26.1 V``.
    Hysteresis is preserved at 0.6 V so a sagging pack does not ping-pong the
    spoken alert as it recovers.
    """

    def test_settings_defaults_match_locked_values(self) -> None:
        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "NINA_BATTERY_LOW_VOLTAGE_V",
                "NINA_BATTERY_CLEAR_VOLTAGE_V",
            )
        }
        with patch.dict(os.environ, env_clean, clear=True):
            settings = load_settings(REPO_ROOT)
        self.assertAlmostEqual(
            settings.battery_ads1115.low_voltage_v,
            25.5,
            places=3,
            msg="low_voltage_v default must stay at 25.5 V (operator-locked)",
        )
        self.assertAlmostEqual(
            settings.battery_ads1115.clear_voltage_v,
            26.1,
            places=3,
            msg="clear_voltage_v default must stay at 26.1 V (preserves 0.6 V hysteresis)",
        )
        self.assertGreater(
            settings.battery_ads1115.clear_voltage_v,
            settings.battery_ads1115.low_voltage_v,
            "clear_voltage_v must stay above low_voltage_v to avoid alert chatter",
        )

    def test_legacy_fallback_constants_match_settings_defaults(self) -> None:
        """The module-level fallbacks in ``nina.sensors.ads1115`` are only used
        when a caller forgets to pass an explicit threshold. They must stay in
        sync with the settings defaults so a stray legacy path can't draw from
        a stale lower threshold.
        """
        self.assertAlmostEqual(ads1115_consts.DEFAULT_LOW_BATTERY_V, 25.5, places=3)
        self.assertAlmostEqual(
            ads1115_consts.DEFAULT_CLEAR_BATTERY_V, 26.1, places=3
        )

    def test_low_battery_phrase_matches_operator_wording(self) -> None:
        """Operator chose the short ``I'm low on battery`` phrase. Pinning it
        here means a future copy-tweak can't silently drift the warning the
        bot speaks every 0.2 V on the way down.
        """
        self.assertEqual(ads1115_consts.LOW_BATTERY_TTS, "I'm low on battery")


class LowBatteryRepeatDecisionTests(unittest.TestCase):
    """``decide_low_battery_repeat`` is the pure rule behind the every-0.2 V
    repeat warning. The monitor thread is a thin wrapper around it; if these
    pass the wiring is just bookkeeping.
    """

    def test_default_repeat_step_is_0_2_v(self) -> None:
        """The operator-locked baseline step is 0.2 V."""
        self.assertAlmostEqual(DEFAULT_REPEAT_STEP_V, 0.2, places=3)

    def test_settings_repeat_step_from_env(self) -> None:
        with patch.dict(os.environ, {"NINA_BATTERY_REPEAT_STEP_V": "0.5"}, clear=False):
            settings = load_settings(REPO_ROOT)
        self.assertAlmostEqual(settings.battery_ads1115.repeat_step_v, 0.5, places=3)

    def test_first_call_anchors_without_speaking(self) -> None:
        """First call (``last_announced_v=None``) just sets the anchor.

        We don't speak on the very first poll because the initial latch
        reaction already announced — the anchor must catch the post-anchor
        sag, not the trigger itself.
        """
        should_speak, anchor = decide_low_battery_repeat(
            25.5, last_announced_v=None, step_v=0.2
        )
        self.assertFalse(should_speak)
        self.assertAlmostEqual(anchor, 25.5, places=3)

    def test_drop_of_exactly_step_fires_warning(self) -> None:
        """A drop equal to ``step_v`` must fire (uses ``<=`` so the boundary
        case is the trigger, not the gap)."""
        should_speak, anchor = decide_low_battery_repeat(
            25.3, last_announced_v=25.5, step_v=0.2
        )
        self.assertTrue(should_speak)
        self.assertAlmostEqual(anchor, 25.3, places=3)

    def test_drop_smaller_than_step_holds_anchor(self) -> None:
        """A sub-step sag (e.g. 25.4 with anchor 25.5) must NOT fire. This is
        the case that would cause speaker chatter under load if the rule
        used ``<`` against the raw anchor.
        """
        should_speak, anchor = decide_low_battery_repeat(
            25.4, last_announced_v=25.5, step_v=0.2
        )
        self.assertFalse(should_speak)
        self.assertAlmostEqual(anchor, 25.5, places=3)

    def test_rising_voltage_does_not_fire(self) -> None:
        """Voltage recovering above the anchor must NOT fire (the recovery
        path is owned by the latch-clear logic in ``_run``).
        """
        should_speak, anchor = decide_low_battery_repeat(
            25.7, last_announced_v=25.5, step_v=0.2
        )
        self.assertFalse(should_speak)
        self.assertAlmostEqual(anchor, 25.5, places=3)

    def test_steep_drop_re_anchors_at_current_pack_v(self) -> None:
        """If the pack collapses across multiple steps in one poll, fire
        once and re-anchor at the current pack_v. The next 0.2 V sag is
        measured from the new floor, not from the pre-collapse anchor.
        """
        should_speak, anchor = decide_low_battery_repeat(
            24.0, last_announced_v=25.5, step_v=0.2
        )
        self.assertTrue(should_speak)
        # Re-anchor at 24.0; next fire requires <= 23.8.
        self.assertAlmostEqual(anchor, 24.0, places=3)
        should_speak_2, anchor_2 = decide_low_battery_repeat(
            23.9, last_announced_v=anchor, step_v=0.2
        )
        self.assertFalse(should_speak_2)
        self.assertAlmostEqual(anchor_2, 24.0, places=3)
        should_speak_3, anchor_3 = decide_low_battery_repeat(
            23.8, last_announced_v=anchor, step_v=0.2
        )
        self.assertTrue(should_speak_3)
        self.assertAlmostEqual(anchor_3, 23.8, places=3)

    def test_pack_min_tracks_floor_for_slow_drain(self) -> None:
        """Repeat warnings use the minimum pack V since latch, not bounce peaks."""
        floor = update_pack_min_v(25.5, 25.6)
        self.assertAlmostEqual(floor, 25.5, places=3)
        floor2 = update_pack_min_v(floor, 25.29)
        self.assertAlmostEqual(floor2, 25.29, places=3)
        should_speak, anchor = decide_low_battery_repeat(
            floor2, last_announced_v=25.5, step_v=0.2
        )
        self.assertTrue(should_speak)
        self.assertAlmostEqual(anchor, 25.29, places=3)

    def test_reminder_fires_after_interval(self) -> None:
        self.assertFalse(
            decide_low_battery_reminder(
                now_mono=100.0,
                last_reminder_mono=50.0,
                reminder_interval_sec=90.0,
            )
        )
        self.assertTrue(
            decide_low_battery_reminder(
                now_mono=141.0,
                last_reminder_mono=50.0,
                reminder_interval_sec=90.0,
            )
        )

    def test_reminder_disabled_when_interval_zero(self) -> None:
        self.assertFalse(
            decide_low_battery_reminder(
                now_mono=1000.0,
                last_reminder_mono=0.0,
                reminder_interval_sec=0.0,
            )
        )

    def test_step_zero_disables_repeat_entirely(self) -> None:
        """``step_v=0`` is the operator opt-out: no repeats ever fire, anchor
        is left untouched (preserving caller bookkeeping).
        """
        should_speak, anchor = decide_low_battery_repeat(
            10.0, last_announced_v=25.5, step_v=0.0
        )
        self.assertFalse(should_speak)
        self.assertAlmostEqual(anchor, 25.5, places=3)


if __name__ == "__main__":
    unittest.main()

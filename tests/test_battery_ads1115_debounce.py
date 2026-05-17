"""Unit tests for ADS1115 battery debounce helper + locked default thresholds."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from nina.config.settings import load_settings
from nina.sensors import ads1115 as ads1115_consts
from nina.sensors.battery_ads1115_monitor import battery_low_debounce_step

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


if __name__ == "__main__":
    unittest.main()

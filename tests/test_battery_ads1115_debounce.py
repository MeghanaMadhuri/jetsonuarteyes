"""Unit tests for ADS1115 battery debounce helper."""

import unittest

from nina.sensors.battery_ads1115_monitor import battery_low_debounce_step


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


if __name__ == "__main__":
    unittest.main()

"""Unit tests for IR obstacle-stop debounce helper."""

import unittest

from nina.sensors.ir_obstacle_stop_monitor import obstacle_debounce_step


class ObstacleDebounceStepTests(unittest.TestCase):
    def test_resets_when_clear(self) -> None:
        fire, n = obstacle_debounce_step(
            2000, threshold_mm=1000, debounce_reads=2, consecutive_hits=1
        )
        self.assertFalse(fire)
        self.assertEqual(n, 0)

    def test_fires_after_debounce(self) -> None:
        fire, n = obstacle_debounce_step(
            800, threshold_mm=1000, debounce_reads=2, consecutive_hits=0
        )
        self.assertFalse(fire)
        self.assertEqual(n, 1)

        fire2, n2 = obstacle_debounce_step(
            900, threshold_mm=1000, debounce_reads=2, consecutive_hits=n
        )
        self.assertTrue(fire2)
        self.assertEqual(n2, 0)

    def test_none_distance_resets(self) -> None:
        fire, n = obstacle_debounce_step(
            None, threshold_mm=1000, debounce_reads=2, consecutive_hits=1
        )
        self.assertFalse(fire)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()

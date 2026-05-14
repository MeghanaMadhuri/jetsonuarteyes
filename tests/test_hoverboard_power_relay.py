"""Unit tests for hoverboard pack power relay helpers (no Jetson.GPIO)."""

import unittest

from nina.controllers.hoverboard_power_relay import HoverboardPowerRelay, build_power_relay


class TestHoverboardPowerRelay(unittest.TestCase):
    def test_build_power_relay_disabled_when_none(self) -> None:
        self.assertIsNone(build_power_relay(None, power_on_level=1))

    def test_build_power_relay_disabled_when_zero(self) -> None:
        self.assertIsNone(build_power_relay(0, power_on_level=1))

    def test_build_power_relay_returns_helper_and_singleton(self) -> None:
        a = build_power_relay(26, power_on_level=0)
        self.assertIsNotNone(a)
        self.assertIsInstance(a, HoverboardPowerRelay)
        b = build_power_relay(26, power_on_level=0)
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for hoverboard pack power relay helpers (no Jetson.GPIO)."""

import unittest

from nina.controllers.hoverboard_power_relay import (
    HoverboardPowerRelay,
    build_power_relay,
    reset_power_relay_singleton_for_tests,
)


class TestHoverboardPowerRelay(unittest.TestCase):
    def tearDown(self) -> None:
        reset_power_relay_singleton_for_tests()

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

    def test_status_led_bcm_part_of_singleton_key(self) -> None:
        a = build_power_relay(26, power_on_level=0, status_led_bcm=11)
        b = build_power_relay(26, power_on_level=0, status_led_bcm=11)
        self.assertIs(a, b)
        reset_power_relay_singleton_for_tests()
        c = build_power_relay(26, power_on_level=0, status_led_bcm=12)
        self.assertIsNotNone(c)
        self.assertIsNot(a, c)


if __name__ == "__main__":
    unittest.main()

"""Env parsing for hoverboard pack relay (header pin 37 vs BCM)."""

import os
import unittest
from unittest.mock import patch

from nina.config.settings import _parse_hoverboard_power_relay_bcm


class TestHoverboardRelayEnv(unittest.TestCase):
    def test_header_pin_37_maps_to_internal_gpio(self) -> None:
        with patch.dict(
            os.environ,
            {"NINA_HOVER_RELAY_HEADER_PIN": "37", "NINA_HOVER_POWER_RELAY_BCM": ""},
            clear=False,
        ):
            self.assertEqual(_parse_hoverboard_power_relay_bcm(), 26)

    def test_header_pin_wins_over_bcm(self) -> None:
        with patch.dict(
            os.environ,
            {
                "NINA_HOVER_RELAY_HEADER_PIN": "37",
                "NINA_HOVER_POWER_RELAY_BCM": "99",
            },
            clear=False,
        ):
            self.assertEqual(_parse_hoverboard_power_relay_bcm(), 26)

    def test_bcm_only_when_no_header_pin(self) -> None:
        with patch.dict(
            os.environ,
            {"NINA_HOVER_RELAY_HEADER_PIN": "", "NINA_HOVER_POWER_RELAY_BCM": "26"},
            clear=False,
        ):
            self.assertEqual(_parse_hoverboard_power_relay_bcm(), 26)


if __name__ == "__main__":
    unittest.main()

"""Pack voltage from ADS1115 AIN reading (divider + cal)."""

import unittest

from nina.sensors.ads1115 import (
    DEFAULT_BATTERY_CAL_SCALE,
    divider_ratio_from_resistors,
    pack_voltage_from_ain_volts,
)


class PackVoltageTests(unittest.TestCase):
    def test_divider_ratio_218k_33k(self) -> None:
        r = divider_ratio_from_resistors(218_000.0, 33_000.0)
        self.assertAlmostEqual(r, 251.0 / 33.0, places=4)

    def test_bench_trim_matches_dmm(self) -> None:
        v_ain = 3.197
        ratio = 251.0 / 33.0
        pack = pack_voltage_from_ain_volts(
            v_ain,
            divider_ratio=ratio,
            cal_scale=DEFAULT_BATTERY_CAL_SCALE,
        )
        self.assertAlmostEqual(pack, 26.3, delta=0.15)


if __name__ == "__main__":
    unittest.main()

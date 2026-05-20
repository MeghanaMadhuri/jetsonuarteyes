"""Tests for reading hover lean overrides from navigation.env."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nina.config.hover_calibration import read_hover_calibration_ints


class TestHoverCalibration(unittest.TestCase):
    def test_reads_hover_pos_keys(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as fh:
            fh.write(
                "NINA_HOVER_FWD_POS_LEFT=2022\n"
                "NINA_HOVER_REV_POS_RIGHT=1999\n"
                "NINA_IMU_MPU9250_ENABLE=1\n"
            )
            path = Path(fh.name)
        try:
            out = read_hover_calibration_ints(path)
            self.assertEqual(out["NINA_HOVER_FWD_POS_LEFT"], 2022)
            self.assertEqual(out["NINA_HOVER_REV_POS_RIGHT"], 1999)
            self.assertNotIn("NINA_IMU_MPU9250_ENABLE", out)
        finally:
            path.unlink(missing_ok=True)

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(read_hover_calibration_ints(Path("/nonexistent/navigation.env")), {})


if __name__ == "__main__":
    unittest.main()

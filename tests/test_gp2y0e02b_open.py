"""GP2Y0E02B must not leak I2C bus fds when open() fails after SMBus()."""

import sys
from unittest.mock import MagicMock, patch

from nina.sensors.gp2y0e02b import GP2Y0E02B


def test_open_closes_bus_when_register_read_fails():
    mock_bus = MagicMock()
    mock_bus.read_byte_data.side_effect = OSError(121, "Remote I/O error")
    smbus_mod = MagicMock()
    smbus_mod.SMBus.return_value = mock_bus

    with patch.dict(sys.modules, {"smbus2": smbus_mod}):
        sensor = GP2Y0E02B(bus=7, address=0x40)
        try:
            sensor.open()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass

    mock_bus.close.assert_called_once()
    assert sensor._bus is None

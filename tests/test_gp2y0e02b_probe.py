"""GP2Y0E02B availability must require a responding device, not only /dev/i2c-N."""

import sys
from unittest.mock import MagicMock, patch

from nina.sensors.gp2y0e02b import is_available, probe_gp2y0e02b_on_bus


def _with_smbus2():
    mock = MagicMock()
    return patch.dict(sys.modules, {"smbus2": mock}), mock


def test_is_available_requires_probe_when_bus_exists():
    with _with_smbus2()[0]:
        with patch("nina.sensors.gp2y0e02b.os.path.exists", return_value=True):
            with patch("nina.sensors.gp2y0e02b.probe_gp2y0e02b_on_bus", return_value=False):
                ok, msg = is_available(7, 0x40)
    assert not ok
    assert "not detected" in msg.lower()


def test_is_available_ok_when_probe_succeeds():
    with _with_smbus2()[0]:
        with patch("nina.sensors.gp2y0e02b.os.path.exists", return_value=True):
            with patch("nina.sensors.gp2y0e02b.probe_gp2y0e02b_on_bus", return_value=True):
                ok, msg = is_available(7, 0x40)
    assert ok
    assert msg == ""


def test_probe_returns_false_on_i2c_error():
    patcher, smbus_mod = _with_smbus2()
    with patcher:
        smbus_mod.SMBus.side_effect = OSError(121, "Remote I/O error")
        with patch("nina.sensors.gp2y0e02b.os.path.exists", return_value=True):
            assert probe_gp2y0e02b_on_bus(7, 0x40) is False

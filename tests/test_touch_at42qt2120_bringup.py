"""Tests for AT42QT2120 boot-time probe / monitor bring-up."""

from __future__ import annotations

from unittest.mock import patch

from nina.sensors import at42qt2120 as touch_hw
from nina.sensors.touch_at42qt2120_monitor import TouchAt42qt2120Monitor


def test_is_available_retries_probe(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_probe(bus: int, addr: int = touch_hw.DEFAULT_TOUCH_I2C_ADDR) -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    monkeypatch.setattr(touch_hw, "probe_at42qt2120_on_bus", fake_probe)
    monkeypatch.setattr(touch_hw.os.path, "exists", lambda _p: True)
    sleeps: list[float] = []
    monkeypatch.setattr(touch_hw.time, "sleep", lambda s: sleeps.append(float(s)))

    class _Smbus:
        pass

    monkeypatch.setitem(
        __import__("sys").modules,
        "smbus2",
        _Smbus(),
    )

    ok, msg = touch_hw.is_available(7, probe_attempts=5, probe_delay_sec=0.4)
    assert ok
    assert msg == ""
    assert calls["n"] == 3
    assert sleeps == [0.4, 0.4]


def test_monitor_is_running_false_before_start() -> None:
    from types import SimpleNamespace

    svc = SimpleNamespace(
        settings=SimpleNamespace(
            touch_at42qt2120=SimpleNamespace(
                i2c_bus=7,
                i2c_address=0x1C,
                use_key_mask=True,
                channel_mask=0xFFF,
                debounce_reads=3,
                release_reads=2,
                startup_probe_attempts=1,
                startup_probe_delay_sec=0.0,
                cooldown_sec=10.0,
                blind_after_reaction_sec=0.75,
                poll_interval_sec=0.1,
                baseline_clear_reads=10,
                stuck_high_sec=2.0,
                stuck_clear_reads=15,
            )
        )
    )
    mon = TouchAt42qt2120Monitor(svc)  # type: ignore[arg-type]
    assert not mon.is_running()

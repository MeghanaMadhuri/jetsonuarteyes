"""pulse_turn_90 must delegate to pulse_turn_degrees without changing the 90° default."""

from __future__ import annotations

from types import SimpleNamespace

from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive, _imu_turn_target_deg


def test_pulse_turn_90_delegates_to_pulse_turn_degrees() -> None:
    calls: list[tuple[str, float]] = []
    drv = SimpleNamespace()

    def fake_degrees(direction: str, degrees: float) -> bool:
        calls.append((direction, degrees))
        return True

    drv.pulse_turn_degrees = fake_degrees
    HoverboardAxisDrive.pulse_turn_90(drv, "left")  # type: ignore[arg-type]
    assert calls == [("left", _imu_turn_target_deg())]

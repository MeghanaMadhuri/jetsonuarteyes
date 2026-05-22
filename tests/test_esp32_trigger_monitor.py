"""Unit tests for ESP32 GPIO trigger debounce helpers."""

from nina.sensors.esp32_trigger_monitor import (
    esp32_release_rearm_step,
    esp32_rising_edge_step,
)


def test_rising_edge_requires_debounce_reads() -> None:
    fire, n = esp32_rising_edge_step(
        True, False, debounce_reads=3, consecutive_hits=0
    )
    assert not fire
    assert n == 1
    fire2, _ = esp32_rising_edge_step(
        True, True, debounce_reads=3, consecutive_hits=n
    )
    assert not fire2
    fire3, _ = esp32_rising_edge_step(
        True, True, debounce_reads=3, consecutive_hits=2
    )
    assert fire3


def test_low_resets_rising_edge_counter() -> None:
    _, n = esp32_rising_edge_step(True, False, debounce_reads=3, consecutive_hits=1)
    fire, n2 = esp32_rising_edge_step(False, True, debounce_reads=3, consecutive_hits=n)
    assert not fire
    assert n2 == 0


def test_rising_edge_fires_on_first_high_when_debounce_is_one() -> None:
    fire, n = esp32_rising_edge_step(
        True, False, debounce_reads=1, consecutive_hits=0
    )
    assert fire
    assert n == 0


def test_release_rearm_after_low_samples() -> None:
    armed, n = esp32_release_rearm_step(
        False, armed=False, release_reads=2, consecutive_low=0
    )
    assert not armed
    assert n == 1
    armed2, _ = esp32_release_rearm_step(
        False, armed=False, release_reads=2, consecutive_low=n
    )
    assert armed2

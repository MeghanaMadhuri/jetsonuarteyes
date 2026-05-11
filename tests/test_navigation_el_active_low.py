"""EL enable polarity (active-low JYQD) for local NavigationManager."""

from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


def test_el_gpio_active_high_default() -> None:
    nav = NavigationManager(
        NavigationConfig(pins=DEFAULT_PINS, el_active_low=False)
    )
    assert nav._el_gpio(armed=False) == 0
    assert nav._el_gpio(armed=True) == 1


def test_el_gpio_active_low() -> None:
    nav = NavigationManager(
        NavigationConfig(pins=DEFAULT_PINS, el_active_low=True)
    )
    assert nav._el_gpio(armed=False) == 1
    assert nav._el_gpio(armed=True) == 0

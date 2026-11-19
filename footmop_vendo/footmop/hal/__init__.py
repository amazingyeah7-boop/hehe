from __future__ import annotations

from ..config import Config
from .base import HAL


def create_hal(cfg: Config, mock: bool) -> HAL:
    if mock:
        from .mock_hal import MockHAL
        return MockHAL(cfg)
    from .lgpio_hal import LgpioHAL
    return LgpioHAL(cfg)

"""Hardware abstraction interface shared by the real (lgpio) and mock HALs."""
from __future__ import annotations

import threading
from typing import Callable

from ..core.state import Btn

ButtonCallback = Callable[[Btn, bool], None]     # (button, pressed)
EdgeCallback = Callable[[int, int], None]        # (level, timestamp_ns)


class HAL:
    """All methods are thread-safe unless noted."""

    def __init__(self) -> None:
        self._drop_event = threading.Event()
        self._drop_count = 0
        self._drop_lock = threading.Lock()

    # lifecycle ---------------------------------------------------------
    def start(self, on_button: ButtonCallback, on_coin_edge: EdgeCallback) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # outputs -----------------------------------------------------------
    def set_motor(self, on: bool) -> None:
        raise NotImplementedError

    def force_motor_off(self) -> None:
        """Must never raise. Called from watchdogs, excepthook and shutdown."""
        raise NotImplementedError

    def set_coin_enable(self, enabled: bool) -> None:
        pass  # optional hardware

    # drop sensor -------------------------------------------------------
    def drop_sensor_active(self) -> bool:
        raise NotImplementedError

    def arm_drop(self) -> None:
        with self._drop_lock:
            self._drop_count = 0
            self._drop_event.clear()

    def wait_drop(self, timeout: float) -> bool:
        return self._drop_event.wait(timeout)

    def drop_count(self) -> int:
        with self._drop_lock:
            return self._drop_count

    def _signal_drop(self) -> None:
        """Call from the sensor edge callback when the beam/reflection activates."""
        with self._drop_lock:
            self._drop_count += 1
        self._drop_event.set()

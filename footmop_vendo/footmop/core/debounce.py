"""Pure, time-injected debouncer for tactile buttons."""
from __future__ import annotations

from typing import Optional


class Debouncer:
    """Reports a press/release only after the raw level has been stable for
    ``debounce_s``. Bounces and short noise spikes shorter than that never
    produce an event.
    """

    def __init__(self, debounce_s: float, initial_level: int, active_low: bool = True):
        self.debounce_s = debounce_s
        self.active_low = active_low
        self._stable = initial_level
        self._candidate = initial_level
        self._since = 0.0

    def _is_pressed(self, level: int) -> bool:
        return (level == 0) if self.active_low else (level == 1)

    @property
    def pressed(self) -> bool:
        return self._is_pressed(self._stable)

    def update(self, level: int, now: float) -> Optional[bool]:
        """Feed a raw sample. Returns True (pressed) / False (released) when
        the debounced state changes, else None."""
        if level != self._candidate:
            self._candidate = level
            self._since = now
            return None
        if self._candidate != self._stable and (now - self._since) >= self.debounce_s:
            self._stable = self._candidate
            return self._is_pressed(self._stable)
        return None

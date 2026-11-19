"""Mock hardware for running the kiosk on a laptop/desktop.

Keyboard (forwarded from the Qt window):
  Left/Up/A  = GREEN button        Right/Down/D = BLUE button
  Enter/Space/S = WHITE button     C or 5    = insert a P5 coin
  N          = electrical noise burst on the coin line (should be rejected)
  J          = toggle "motor jam" (next dispense fails)
  B          = toggle "chute blocked" (sensor stuck active)
  Left,Right,Left,Right,Left (quick taps) = admin
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from ..config import Config
from ..core.state import Btn
from .base import HAL, ButtonCallback, EdgeCallback

log = logging.getLogger("footmop.hal.mock")


class MockHAL(HAL):
    DROP_DELAY_S = 0.6

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.motor_on = False
        self.coin_enabled = True
        self.jam = False
        self.blocked = False
        self._on_button: Optional[ButtonCallback] = None
        self._on_edge: Optional[EdgeCallback] = None
        self._lock = threading.Lock()
        self._coin_lock = threading.Lock()
        self.motor_history = []
        self.levels: Dict[str, int] = {"green": 1, "blue": 1, "white": 1, "coin": 1, "sensor": 1}

    def start(self, on_button: ButtonCallback, on_coin_edge: EdgeCallback) -> None:
        self._on_button = on_button
        self._on_edge = on_coin_edge
        log.info("MOCK hardware active. Keys: Left/Right/Enter buttons, C=coin, N=noise, "
                 "J=jam, B=chute blocked, taps Left Right Left Right Left = admin")

    def close(self) -> None:
        self.force_motor_off()

    # ------------------------------------------------------------- simulation
    def press(self, btn: Btn, pressed: bool) -> None:
        self.levels[btn.name.lower()] = 0 if pressed else 1
        if self._on_button:
            self._on_button(btn, pressed)

    def insert_coin(self) -> None:
        """Simulate one accepted coin as a real pulse train through the decoder."""
        if not self.coin_enabled:
            log.info("mock: coin slot inhibited, coin returned")
            return
        def run():
            with self._coin_lock:  # coins pass the sensor one at a time
                for _ in range(self.cfg.coin.pulses_per_coin):
                    self._edge(0)
                    time.sleep(0.040)
                    self._edge(1)
                    time.sleep(0.060)
        threading.Thread(target=run, daemon=True).start()

    def noise(self) -> None:
        def run():
            with self._coin_lock:
                for _ in range(5):
                    self._edge(0)
                    time.sleep(0.004)
                    self._edge(1)
                    time.sleep(0.003)
        threading.Thread(target=run, daemon=True).start()

    def _edge(self, level: int) -> None:
        self.levels["coin"] = level
        if self._on_edge:
            self._on_edge(level, time.monotonic_ns())

    def toggle_jam(self) -> bool:
        self.jam = not self.jam
        log.warning("mock: jam %s", "ON" if self.jam else "OFF")
        return self.jam

    def toggle_blocked(self) -> bool:
        self.blocked = not self.blocked
        self.levels["sensor"] = 0 if self.blocked else 1
        log.warning("mock: chute blocked %s", "ON" if self.blocked else "OFF")
        return self.blocked

    # ----------------------------------------------------------------- HAL
    def set_motor(self, on: bool) -> None:
        with self._lock:
            self.motor_on = on
            self.motor_history.append((time.monotonic(), on))
        log.info("mock: motor %s", "ON" if on else "OFF")
        if on and not self.jam:
            t = threading.Timer(self.DROP_DELAY_S, self._signal_drop)
            t.daemon = True
            t.start()

    def force_motor_off(self) -> None:
        with self._lock:
            if self.motor_on:
                self.motor_history.append((time.monotonic(), False))
            self.motor_on = False

    def set_coin_enable(self, enabled: bool) -> None:
        self.coin_enabled = enabled

    def drop_sensor_active(self) -> bool:
        return self.blocked

    def read_inputs(self) -> Dict[str, int]:
        return dict(self.levels)

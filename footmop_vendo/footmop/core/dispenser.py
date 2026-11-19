"""Dispense sequence, run on a worker thread one item at a time.

Per item:
  1. Pre-check: the drop sensor must be clear (else chute blocked/sensor fault).
  2. Arm the drop latch, relay ON, start an independent hard-cutoff timer.
  3. Run the motor for motor_on_ms (or until the drop if stop_on_drop).
  4. Relay OFF, then keep watching the sensor for drop_wait_after_ms.
  5. No drop seen -> fault (jam). Drop seen -> report item; before another
     item, wait for the sensor to clear while keeping the motor off.

The relay is switched off in a ``finally`` block AND by the hard-cutoff
timer, so neither an exception nor a stuck thread can leave the motor on.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from ..config import DispenseCfg

log = logging.getLogger("footmop.dispenser")


class Dispenser:
    def __init__(self, hal, cfg: DispenseCfg,
                 on_item: Callable[[int, int], None],
                 on_done: Callable[[int], None],
                 on_fault: Callable[[int, str, str], None],
                 on_waiting_for_clear: Optional[Callable[[int, bool], None]] = None):
        self.hal = hal
        self.cfg = cfg
        self.on_item = on_item
        self.on_done = on_done
        self.on_fault = on_fault
        self.on_waiting_for_clear = on_waiting_for_clear
        self._lock = threading.Lock()
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, qty: int, token: int) -> bool:
        with self._lock:
            if self._busy:
                log.error("dispense requested while busy - refused")
                self.on_fault(token, "dispenser_busy", "Dispenser was already running.")
                return False
            self._busy = True
        threading.Thread(target=self._run, args=(qty, token), name="dispenser",
                         daemon=True).start()
        return True

    # ------------------------------------------------------------------
    def _run(self, qty: int, token: int) -> None:
        try:
            for i in range(1, qty + 1):
                code, msg = self._one_item(i, qty)
                if code:
                    self.on_fault(token, code, msg)
                    return
                self.on_item(token, i)
                if i < qty:
                    self._wait_sensor_clear(token)
                    time.sleep(self.cfg.cooldown_ms / 1000)
            self.on_done(token)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("dispenser crashed")
            self.hal.force_motor_off()
            self.on_fault(token, "dispenser_exception", f"Dispenser error: {exc}")
        finally:
            self.hal.force_motor_off()
            with self._lock:
                self._busy = False

    def _one_item(self, i: int, qty: int):
        c = self.cfg
        if c.sensor_enabled and self.hal.drop_sensor_active():
            return ("chute_blocked",
                    "Drop sensor is blocked before dispensing (item stuck in chute or sensor fault).")

        self.hal.arm_drop()
        cutoff = threading.Timer((c.motor_on_ms + c.hard_cutoff_margin_ms) / 1000,
                                 self._hard_cutoff)
        cutoff.daemon = True
        log.info("item %d/%d: motor ON", i, qty)
        t0 = time.monotonic()
        self.hal.set_motor(True)
        cutoff.start()
        try:
            run_s = c.motor_on_ms / 1000
            if c.sensor_enabled and c.stop_on_drop:
                self.hal.wait_drop(run_s)
            else:
                time.sleep(run_s)
        finally:
            self.hal.set_motor(False)
            cutoff.cancel()
        log.info("item %d/%d: motor OFF after %.2fs", i, qty, time.monotonic() - t0)

        if not c.sensor_enabled:
            return (None, None)
        if not self.hal.wait_drop(c.drop_wait_after_ms / 1000):
            return ("dispense_failure",
                    f"No item detected by the drop sensor for item {i} of {qty} (possible jam or empty slot).")
        n = self.hal.drop_count()
        if n > 1:
            log.warning("item %d: sensor saw %d activations (possible double drop or sensor chatter)", i, n)
        return (None, None)

    def _wait_sensor_clear(self, token: int) -> None:
        if not self.cfg.sensor_enabled or not self.hal.drop_sensor_active():
            return
        log.info("drop sensor remains active; waiting for it to clear before next item")
        if self.on_waiting_for_clear:
            self.on_waiting_for_clear(token, True)
        try:
            while self.hal.drop_sensor_active():
                time.sleep(0.05)
        finally:
            if self.on_waiting_for_clear:
                self.on_waiting_for_clear(token, False)

    def _hard_cutoff(self) -> None:
        log.error("HARD CUTOFF: motor relay forced OFF by watchdog timer")
        self.hal.force_motor_off()

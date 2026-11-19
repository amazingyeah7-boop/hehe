"""Scripted end-to-end walkthrough in mock mode (developer/QA tool).

Drives the real app through the mock HAL (real coin pulse decoding, real
dispenser thread) and saves a screenshot of every screen. Run:

    python3 -m footmop --mock --windowed --seed-stock 3 --db /tmp/w.db --screenshots shots/
"""
from __future__ import annotations

import logging
import os
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from ..core.state import Btn, State

log = logging.getLogger("footmop.walkthrough")


class Walkthrough:
    def __init__(self, app, out_dir: str):
        self.app = app
        self.k = app.kiosk
        self.hal = app.hal
        self.out = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.offset = 0.0
        self.k.clock = lambda: time.monotonic() + self.offset   # lets us fast-forward timeouts
        self.steps = []
        self.n = 0
        self.failures = []
        self._build()

    # helpers -------------------------------------------------------------
    def shot(self, name):
        def f():
            self.n += 1
            path = os.path.join(self.out, f"{self.n:02d}_{name}.png")
            self.app.window.grab().save(path)
            log.info("screenshot %s (state=%s)", path, self.k.state.value)
        return f

    def expect(self, state):
        def f():
            if self.k.state != state:
                msg = f"step {len(self.steps)}: expected {state.value}, got {self.k.state.value}"
                self.failures.append(msg)
                log.error("WALKTHROUGH MISMATCH: %s", msg)
        return f

    def press(self, btn, hold=0.0):
        def f():
            self.hal.press(btn, True)
            if not hold:
                self.hal.press(btn, False)
        return f

    def add(self, delay_ms, fn):
        self.steps.append((delay_ms, fn))

    def coins(self, n):
        for _ in range(n):
            self.add(160, self.hal.insert_coin)

    def ff(self, seconds):
        def f():
            self.offset += seconds
            self.k.tick()
        return f

    def tap(self, btn, n=1, gap=120):
        for _ in range(n):
            self.add(gap, self.press(btn))

    def type_number(self, current, target, length):
        """Short presses to change a DigitEntry from `current` to `target`."""
        cur = str(current).zfill(length)[-length:]
        tgt = str(target).zfill(length)
        for c, t in zip(cur, tgt):
            diff = (int(t) - int(c)) % 11          # digits cycle 0-9 + X
            btn = Btn.BLUE if diff <= 5 else Btn.GREEN
            n = diff if diff <= 5 else 11 - diff
            for _ in range(n):
                self.hal.press(btn, True); self.hal.press(btn, False)
            self.hal.press(Btn.WHITE, True); self.hal.press(Btn.WHITE, False)

    # script ----------------------------------------------------------------
    def _build(self):
        a, G, B, W = self.add, Btn.GREEN, Btn.BLUE, Btn.WHITE
        a(600, self.expect(State.LANDING)); a(0, self.shot("landing"))

        # buy 3 with buttons, pay with coins (plus line noise that must be ignored)
        self.tap(W); self.tap(B, 2)
        a(300, self.expect(State.QUANTITY)); a(0, self.shot("quantity"))
        self.tap(W); a(200, self.expect(State.PAYMENT))
        self.coins(1)
        a(250, self.hal.noise)
        a(400, self.shot("payment_partial"))
        self.coins(2)
        a(500, self.expect(State.DISPENSING)); a(0, self.shot("dispensing"))
        a(8600, self.expect(State.SUCCESS)); a(0, self.shot("success"))
        self.tap(W); a(300, self.expect(State.LANDING))

        # coins are refused outside the payment screen
        self.coins(1)
        a(400, self.expect(State.LANDING)); a(0, lambda: None if self.k.credit == 0 else self.failures.append("coin credited on landing"))
        # buy 2 more
        self.tap(W); self.tap(B); self.tap(W)
        self.coins(2)
        a(400, self.expect(State.DISPENSING))
        a(5600, self.expect(State.SUCCESS))
        self.tap(W); a(300, self.expect(State.LANDING))

        # GREEN at quantity 1 = cancel
        self.tap(W); self.tap(G); a(300, self.expect(State.LANDING))

        # inactivity warning with credit, then forfeiture
        self.tap(W); self.tap(B); self.tap(W)
        self.coins(1)
        a(400, self.ff(61)); a(300, self.shot("timeout_warning"))
        a(100, self.ff(16)); a(300, self.expect(State.LANDING)); a(0, self.shot("timed_out_toast"))

        # jam -> fault lock
        a(4200, self.hal.toggle_jam)
        self.tap(W); self.tap(W)
        self.coins(1)
        a(6200, self.expect(State.FAULT)); a(0, self.shot("fault_locked"))
        a(100, self.hal.toggle_jam)
        self.tap(W); a(200, self.expect(State.FAULT))   # locked

        # admin: secret short-press sequence G B G B G, PIN digit by digit
        for ch in self.k.cfg.timing.admin_sequence:
            self.tap(G if ch == "G" else B, gap=200)
        a(300, self.expect(State.ADMIN_PIN))
        pin = str(self.k.cfg.admin.pin)
        for i, ch in enumerate(pin):
            self.tap(B, int(ch), gap=60)
            if i == 1:
                a(150, self.shot("admin_pin"))
            self.tap(W)
        a(300, self.expect(State.ADMIN)); a(0, self.shot("admin_menu"))
        self.tap(B, 2); self.tap(W)                      # -> Faults
        a(300, self.shot("admin_faults"))
        self.tap(W)                                      # Clear all faults
        self.tap(W)                                      # Back
        self.tap(W)                                      # Stock / Restock (index 0)
        a(300, self.shot("admin_stock_before"))
        a(100, lambda: self.type_number(self.k.stock, 24, 4))
        a(1500, self.shot("admin_stock_saved"))
        a(0, lambda: None if self.k.stock == 24 else self.failures.append(f"stock {self.k.stock} != 24"))
        self.tap(B, 4); self.tap(W)                      # -> Sales report
        a(300, self.shot("admin_sales"))
        self.tap(W)                                      # Download CSV
        a(400, self.shot("admin_sales_csv"))
        a(0, lambda: None if "orders" in self.k.admin_message else self.failures.append("csv export: " + self.k.admin_message))
        self.tap(B); self.tap(W)                         # Back
        self.tap(B, 3); self.tap(W)                      # -> Test hardware
        a(100, self.hal.insert_coin)
        a(700, self.shot("admin_hw_test"))
        self.tap(B); self.tap(W)                         # Back
        self.tap(B, 5); self.tap(W)                      # Exit admin (short press)
        a(400, self.expect(State.LANDING))
        # PIN screen can be cancelled with the X "digit"
        for ch in self.k.cfg.timing.admin_sequence:
            self.tap(G if ch == "G" else B, gap=200)
        a(300, self.expect(State.ADMIN_PIN))
        self.tap(G)                                      # 0 -> X
        a(200, self.shot("admin_pin_cancel"))
        self.tap(W)
        a(400, self.expect(State.LANDING))

        # sold out
        a(100, lambda: (self.k.store.set_stock(0), self.k._go_home()))
        a(300, self.expect(State.SOLD_OUT)); a(0, self.shot("sold_out"))
        a(100, self.finish)

    def start(self):
        self._next()

    def _next(self):
        if not self.steps:
            return
        delay, fn = self.steps.pop(0)

        def run():
            try:
                fn()
            except Exception:
                log.exception("walkthrough step failed")
                self.failures.append("exception")
            self._next()
        QTimer.singleShot(delay, run)

    def finish(self):
        path = os.path.join(self.out, "RESULT.txt")
        with open(path, "w") as fh:
            fh.write("PASS\n" if not self.failures else "FAIL\n" + "\n".join(self.failures) + "\n")
        log.info("walkthrough finished: %s", "PASS" if not self.failures else self.failures)
        QApplication.quit()

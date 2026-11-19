"""The kiosk state machine. Pure Python (no Qt), driven by:

  press()/release()  physical buttons (already debounced)
  action()           on-screen touch actions
  on_coin()          decoded coins
  on_item_dropped()/on_dispense_done()/on_dispense_fault()  dispenser results
  tick()             periodic timer for timeouts

Every method must be called from ONE thread (the GUI thread). Hardware
threads reach it only through queued Qt signals, so touch taps, button
presses and coin events are serialised and can never race each other.
"""
from __future__ import annotations

import enum
import logging
import time
from typing import Callable, List, Optional, Tuple

from ..config import Config
from . import store as st
from .store import Store

log = logging.getLogger("footmop.kiosk")


class State(str, enum.Enum):
    LANDING = "LANDING"
    SOLD_OUT = "SOLD_OUT"
    QUANTITY = "QUANTITY"
    PAYMENT = "PAYMENT"
    DISPENSING = "DISPENSING"
    SUCCESS = "SUCCESS"
    FAULT = "FAULT"
    ADMIN_PIN = "ADMIN_PIN"
    ADMIN = "ADMIN"


class Btn(enum.IntEnum):
    GREEN = 0   # left  / decrease
    BLUE = 1    # right / increase
    WHITE = 2   # confirm


class Effects:
    """Side effects the state machine asks the outside world to perform."""

    def start_dispense(self, qty: int, token: int) -> None: ...
    def set_coin_acceptor(self, enabled: bool) -> None: ...
    def force_motor_off(self) -> None: ...


# Coins are only accepted on the Payment screen (and in admin, for testing).
COIN_ACCEPTING_STATES = {State.PAYMENT}
TRANSACTION_STATES = {State.QUANTITY, State.PAYMENT}
ADMIN_ENTRY_STATES = {State.LANDING, State.SOLD_OUT, State.FAULT}


class Kiosk:
    def __init__(self, cfg: Config, store: Store, effects: Effects,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.store = store
        self.fx = effects
        self.clock = clock
        self.listeners: List[Callable[[], None]] = []
        # set by the admin UI: called with (button, pressed) while in ADMIN / ADMIN_PIN
        self.admin_nav: Optional[Callable[[Btn, bool], None]] = None

        self.state = State.LANDING
        self.boot_findings: List[str] = []
        self._reset_session()
        self.next_session_id: Optional[int] = None
        self.next_credit = 0

        self.toast = ""
        self._toast_until = 0.0
        self.warning = False
        self.warning_deadline = 0.0
        self._last_activity = clock()

        # Recent GREEN/BLUE presses on the entry screens, for the admin sequence.
        self._seq: List[Tuple[Btn, float]] = []
        self._last_warn_sec = -1

        self._dispense_token = 0
        self._dispense_started = 0.0
        self._dispense_budget = 0.0
        self.dispense_waiting_for_clear = False
        self.admin_testing = False
        self.admin_message = ""
        self.admin_live: List[str] = []       # recent hardware events for the test page
        self._pin_failures = 0
        self._pin_locked_until = 0.0
        self._success_until = 0.0

    # ================================================================ basics
    def _reset_session(self) -> None:
        self.session_id: Optional[int] = None
        self.qty = 1
        self.credit = 0
        self.items_done = 0
        self.overpaid = 0

    @property
    def cur(self) -> str:
        return self.cfg.machine.currency

    @property
    def price(self) -> int:
        return self.store.get_price()

    @property
    def stock(self) -> int:
        return self.store.get_stock()

    @property
    def max_qty(self) -> int:
        cap = self.cfg.machine.max_per_transaction
        return max(0, min(self.stock, cap) if cap > 0 else self.stock)

    @property
    def coins_paid(self) -> int:
        return self.credit // self.cfg.coin.value

    @property
    def min_qty(self) -> int:
        """Never let the customer pick fewer items than they already paid for."""
        paid_items = self.credit // self.price if self.price else 0
        return max(1, min(self.max_qty, paid_items))

    @property
    def coins_needed(self) -> int:
        c = self.cfg.coin.value
        return -(-self.remaining // c)

    @property
    def amount_due(self) -> int:
        return self.qty * self.price

    @property
    def remaining(self) -> int:
        return max(0, self.amount_due - self.credit)

    @property
    def warning_seconds_left(self) -> int:
        return max(0, int(round(self.warning_deadline - self.clock())))

    @property
    def success_seconds_left(self) -> int:
        return max(0, int(round(self._success_until - self.clock())))

    @property
    def admin_pin_seconds_left(self) -> int:
        """Seconds until an untouched PIN screen returns to the start screen."""
        return max(0, int(round(self.cfg.timing.admin_pin_idle_s - (self.clock() - self._last_activity))))

    @property
    def pin_locked_seconds(self) -> int:
        return max(0, int(round(self._pin_locked_until - self.clock())))

    def _changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                log.exception("listener failed")

    def _set_toast(self, text: str, seconds: float = 4.0) -> None:
        self.toast = text
        self._toast_until = self.clock() + seconds

    def _enter(self, state: State) -> None:
        if state != self.state:
            log.info("state %s -> %s", self.state.value, state.value)
        self.state = state
        self.warning = False
        self._last_warn_sec = -1
        self._last_activity = self.clock()
        self._seq = []
        # Only matters if a coin-inhibit relay is wired (pins.coin_enable).
        self.fx.set_coin_acceptor(state in COIN_ACCEPTING_STATES or state == State.ADMIN)
        self._changed()

    def boot(self) -> None:
        """Force outputs safe, close half-finished transactions, pick a start screen."""
        self.fx.force_motor_off()
        self.boot_findings = self.store.recover_on_boot()
        self._go_home()

    def _go_home(self) -> None:
        self._reset_session()
        if self.store.active_fault():
            self._enter(State.FAULT)
        elif self.stock <= 0:
            self._enter(State.SOLD_OUT)
        else:
            self._enter(State.LANDING)

    def _activity(self) -> bool:
        """Record user activity. Returns True if it was consumed to dismiss
        the inactivity warning (so the press should do nothing else)."""
        self._last_activity = self.clock()
        if self.warning:
            self.warning = False
            log.info("inactivity warning dismissed")
            self._changed()
            return True
        return False

    def note_activity(self) -> None:
        self._activity()

    # =============================================================== buttons
    def press(self, btn: Btn) -> None:
        """Every button acts on a short press - there are NO long-press actions."""
        now = self.clock()
        self.admin_live_event(f"{btn.name} pressed")
        if self._activity():
            return
        s = self.state
        if s in ADMIN_ENTRY_STATES and self._check_admin_sequence(btn, now):
            return
        if s in (State.ADMIN, State.ADMIN_PIN):
            if self.admin_nav:
                self.admin_nav(btn, True)
            return
        mapping = {
            State.LANDING: {Btn.WHITE: "start"},
            State.QUANTITY: {Btn.GREEN: "dec", Btn.BLUE: "inc", Btn.WHITE: "confirm"},
            State.PAYMENT: {Btn.GREEN: "back"},
            State.SUCCESS: {Btn.WHITE: "done"},
        }.get(s, {})
        act = mapping.get(btn)
        if act:
            self.action(act)

    def release(self, btn: Btn) -> None:
        # Releases carry no meaning (no long presses); only logged for the test page.
        self.admin_live_event(f"{btn.name} released")

    def _check_admin_sequence(self, btn: Btn, now: float) -> bool:
        """Secret short-press sequence (default G B G B G within 5 s) opens admin
        from the welcome / sold-out / fault screens, where GREEN and BLUE
        otherwise do nothing."""
        seq = self.cfg.timing.admin_sequence.upper()
        window = self.cfg.timing.admin_sequence_window_s
        if btn == Btn.WHITE:
            self._seq = []
            return False
        self._seq = [(b, t) for b, t in self._seq if now - t <= window] + [(btn, now)]
        self._seq = self._seq[-len(seq):]
        typed = "".join("G" if b == Btn.GREEN else "B" for b, _ in self._seq)
        if typed == seq:
            self._seq = []
            self.open_admin()
            return True
        return False

    # ================================================================ touch
    def action(self, name: str) -> None:
        """Single entry point for every customer action (touch or button)."""
        if self._activity() and name != "dismiss_warning":
            return
        s = self.state
        if name == "dismiss_warning":
            return
        if s == State.LANDING and name == "start":
            self.session_id = self.store.open_session(self.price)
            self.qty = 1
            self._enter(State.QUANTITY)
        elif s == State.QUANTITY:
            if name == "dec":
                if self.qty <= 1 and self.credit == 0:
                    # GREEN at quantity 1 with nothing paid = cancel (no touch screen).
                    self._end_session(st.CANCELLED, "cancelled by customer")
                    self._go_home()
                    return
                if self.qty <= self.min_qty:
                    self._set_toast(f"You already paid for {self.min_qty}", 2.0)
                self.qty = max(self.min_qty, self.qty - 1)
                self._changed()
            elif name == "inc":
                if self.qty >= self.max_qty:
                    self._set_toast(f"Only {self.max_qty} available", 2.0)
                self.qty = min(self.max_qty, self.qty + 1)
                self._changed()
            elif name == "confirm":
                self._confirm_quantity()
            elif name == "cancel" and self.credit == 0:
                self._end_session(st.CANCELLED, "cancelled by customer")
                self._go_home()
        elif s == State.PAYMENT:
            if name == "back":
                self._enter(State.QUANTITY)
            elif name == "cancel" and self.credit == 0:
                self._end_session(st.CANCELLED, "cancelled by customer")
                self._go_home()
        elif s == State.SUCCESS and name == "done":
            self._after_success()

    def _confirm_quantity(self) -> None:
        if self.max_qty <= 0:
            self._end_session(st.CANCELLED, "sold out")
            self._go_home()
            return
        self.qty = max(1, min(self.qty, self.max_qty))
        if self.credit >= self.amount_due:
            self._begin_dispense()
        else:
            if self.session_id is not None:
                self.store.set_order(self.session_id, self.qty, self.price, self.amount_due)
            self._enter(State.PAYMENT)

    # ================================================================= coins
    def on_coin(self, value: int, pulses: int) -> None:
        self._activity()
        self.admin_live_event(f"COIN +P{value} ({pulses} pulse)")
        s = self.state
        if s == State.PAYMENT:
            self._credit(value, pulses)
            if self.credit >= self.amount_due:
                self._begin_dispense()
            else:
                self._changed()
        elif s == State.ADMIN:
            self.store.add_coin(None, value, pulses, "admin_test")
            self._changed()
        else:
            self._unclaimed(value, pulses)

    def on_coin_rejected(self, reason: str) -> None:
        self.admin_live_event(f"coin pulse rejected: {reason}")
        if "stuck" in reason:
            log.error("coin acceptor line stuck active")

    def _credit(self, value: int, pulses: int) -> None:
        assert self.session_id is not None
        self.store.add_coin(self.session_id, value, pulses, "payment")
        self.credit += value
        log.info("credit +%d -> %d (session %s)", value, self.credit, self.session_id)

    def _unclaimed(self, value: int, pulses: int) -> None:
        self.store.add_coin(None, value, pulses, f"unclaimed_{self.state.value.lower()}")
        log.warning("coin P%d inserted in state %s - logged as unclaimed", value, self.state.value)
        if self.state == State.SOLD_OUT:
            self._set_toast("Sold out - coin logged. Please contact the owner.", 6)
        elif self.state in (State.LANDING, State.QUANTITY, State.DISPENSING, State.SUCCESS):
            self._set_toast("Coins are accepted only on the payment screen.", 5)
        else:
            self._set_toast("Machine unavailable - coin logged. Please contact the owner.", 6)
        self._changed()

    # ============================================================== dispense
    def _begin_dispense(self) -> None:
        assert self.session_id is not None
        due = self.amount_due
        self.overpaid = max(0, self.credit - due)
        self.store.begin_dispense(self.session_id, self.qty, self.price, due)
        self.items_done = 0
        self._dispense_token += 1
        self.dispense_waiting_for_clear = False
        self._dispense_started = self.clock()
        d = self.cfg.dispense
        per_item = (d.motor_on_ms + d.drop_wait_after_ms + d.cooldown_ms + d.hard_cutoff_margin_ms) / 1000
        self._dispense_budget = self.qty * per_item + 10.0
        log.info("dispense start: session=%s qty=%d due=%d credit=%d", self.session_id,
                 self.qty, due, self.credit)
        self._enter(State.DISPENSING)
        self.fx.start_dispense(self.qty, self._dispense_token)

    def on_item_dropped(self, token: int, count: int) -> None:
        if token != self._dispense_token:
            return
        if self.admin_testing:
            self.store.record_item_dispensed(None, "admin_test")
            self._changed()
            return
        if self.state != State.DISPENSING:
            return
        self.store.record_item_dispensed(self.session_id)
        self.items_done = count
        self._changed()

    def on_dispense_waiting_for_clear(self, token: int, waiting: bool) -> None:
        if token != self._dispense_token or not (
                self.state == State.DISPENSING or self.admin_testing):
            return
        self.dispense_waiting_for_clear = waiting
        if not waiting:
            self._dispense_started = self.clock()
        self._changed()

    def on_dispense_done(self, token: int) -> None:
        if token != self._dispense_token:
            return
        if self.admin_testing:
            self.admin_testing = False
            self.admin_message = "Test dispense OK"
            self._changed()
            return
        if self.state != State.DISPENSING:
            return
        note = f"overpaid P{self.overpaid}, no change given" if self.overpaid else None
        self._end_session(st.COMPLETED, note)
        self._success_until = self.clock() + self.cfg.timing.success_screen_s
        self._enter(State.SUCCESS)

    def on_dispense_fault(self, token: int, code: str, message: str) -> None:
        if token != self._dispense_token:
            return
        self._dispense_token += 1          # ignore anything else from that run
        self.fx.force_motor_off()
        if self.admin_testing:
            self.admin_testing = False
            self.store.add_fault(code, f"[admin test] {message}")
            self.admin_message = f"TEST FAILED: {message}"
            log.error("admin test dispense failed: %s %s", code, message)
            self._changed()
            return
        owed = max(0, self.qty - self.items_done)
        full = f"{message} Order #{self.session_id}: {self.items_done}/{self.qty} dispensed, {owed} owed."
        log.error("DISPENSE FAULT %s: %s", code, full)
        sid = self.session_id
        self._end_session(st.PARTIAL_FAULT, full)
        self.store.add_fault(code, full, sid, owed)
        self._drop_next_session("machine fault")
        self._go_home()

    # ================================================================ admin
    def admin_live_event(self, text: str) -> None:
        if self.state in (State.ADMIN, State.ADMIN_PIN):
            self.admin_live = (self.admin_live + [text])[-6:]

    def open_admin(self) -> None:
        if self.state not in ADMIN_ENTRY_STATES:
            return
        log.info("admin PIN requested")
        self._enter(State.ADMIN_PIN)

    def submit_pin(self, pin: str) -> bool:
        if self.state != State.ADMIN_PIN:
            return False
        if self.pin_locked_seconds > 0:
            return False
        if pin == str(self.cfg.admin.pin):
            self._pin_failures = 0
            self.admin_message = ""
            self.admin_live = []
            log.warning("admin login")
            self._enter(State.ADMIN)
            return True
        self._pin_failures += 1
        log.warning("wrong admin PIN (%d)", self._pin_failures)
        if self._pin_failures >= self.cfg.timing.admin_pin_max_attempts:
            self._pin_locked_until = self.clock() + self.cfg.timing.admin_pin_lockout_s
            self._pin_failures = 0
        self._changed()
        return False

    def exit_admin(self) -> None:
        if self.state not in (State.ADMIN, State.ADMIN_PIN) or self.admin_testing:
            return
        log.info("admin exit")
        self._go_home()

    def admin_set_stock(self, n: int) -> None:
        if self.state == State.ADMIN:
            new = self.store.set_stock(n, "admin_restock")
            self.admin_message = f"Stock saved: {new}"
            log.warning("admin set stock -> %d", new)
            self._changed()

    def admin_set_price(self, p: int) -> None:
        if self.state != State.ADMIN:
            return
        c = self.cfg.coin.value
        if p <= 0 or p % c:
            self.admin_message = f"Price must be a multiple of {self.cur}{c}"
        else:
            self.store.set_price(p)
            self.admin_message = f"Price saved: {self.cur}{p}"
            log.warning("admin set price -> %d", p)
        self._changed()

    def admin_export_csv(self) -> None:
        """Sales report -> CSV on a USB flash drive (or the local export folder)."""
        if self.state != State.ADMIN:
            return
        from .export import export_sales
        sc = self.cfg.storage
        try:
            path, n, on_usb = export_sales(self.store, sc.usb_globs, sc.export_dir)
        except OSError as exc:
            log.exception("CSV export failed")
            self.admin_message = f"Export failed: {exc.strerror or exc}"
        else:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            self.admin_message = (f"Saved {n} orders to USB: {name} - safe to remove" if on_usb
                                  else f"No USB drive - saved {n} orders to {path}")
        self._changed()

    def admin_clear_faults(self) -> None:
        if self.state == State.ADMIN:
            n = self.store.clear_faults()
            self.admin_message = f"Cleared {n} fault(s)" if n else "No active faults"
            log.warning("admin cleared %d fault(s)", n)
            self._changed()

    def admin_test_dispense(self) -> None:
        if self.state != State.ADMIN or self.admin_testing:
            return
        if self.stock <= 0:
            self.admin_message = "Stock is 0 - restock first"
            self._changed()
            return
        self.admin_testing = True
        self.admin_message = "Test dispensing 1 item..."
        self._dispense_token += 1
        self._dispense_started = self.clock()
        d = self.cfg.dispense
        self._dispense_budget = (d.motor_on_ms + d.drop_wait_after_ms + d.hard_cutoff_margin_ms) / 1000 + 10
        self._changed()
        self.fx.start_dispense(1, self._dispense_token)

    # ================================================================= tick
    def tick(self) -> None:
        now = self.clock()
        changed = False

        if self.toast and now >= self._toast_until:
            self.toast = ""
            changed = True

        s = self.state
        if s in TRANSACTION_STATES:
            idle = now - self._last_activity
            if not self.warning and idle >= self.cfg.timing.inactivity_timeout_s:
                self.warning = True
                self.warning_deadline = now + self.cfg.timing.warning_countdown_s
                log.info("inactivity warning shown (credit P%d)", self.credit)
                changed = True
            elif self.warning:
                if now >= self.warning_deadline:
                    self._timeout_cancel()
                    return
                # Repaint only when the visible seconds change (SPI panels are slow).
                sec = self.warning_seconds_left
                if sec != self._last_warn_sec:
                    self._last_warn_sec = sec
                    changed = True
        elif s == State.SUCCESS:
            if now >= self._success_until:
                self._after_success()
                return
            sec = self.success_seconds_left
            if sec != self._last_warn_sec:
                self._last_warn_sec = sec
                changed = True
        elif (s == State.DISPENSING or self.admin_testing) and \
                not self.dispense_waiting_for_clear and \
                now - self._dispense_started > self._dispense_budget:
            log.error("dispense watchdog expired")
            self.on_dispense_fault(self._dispense_token, "dispense_timeout",
                                   "Dispenser did not report back in time.")
            return
        elif s in (State.ADMIN, State.ADMIN_PIN) and not self.admin_testing:
            limit = (self.cfg.timing.admin_pin_idle_s if s == State.ADMIN_PIN
                     else self.cfg.timing.admin_idle_timeout_s)
            if now - self._last_activity >= limit:
                log.info("admin %s idle %ds - back to start screen",
                         "PIN screen" if s == State.ADMIN_PIN else "menu", int(limit))
                self.exit_admin()
                return
            if s == State.ADMIN_PIN:
                sec = self.admin_pin_seconds_left
                if sec != self._last_warn_sec:
                    self._last_warn_sec = sec
                    changed = True
        if changed:
            self._changed()

    # ============================================================== helpers
    def _end_session(self, status: str, note: Optional[str]) -> None:
        if self.session_id is not None:
            self.store.end_session(self.session_id, status, note)
            log.info("session %s -> %s %s", self.session_id, status, note or "")

    def _timeout_cancel(self) -> None:
        if self.credit > 0:
            log.warning("timeout: session %s abandoned with P%d credit (forfeited, not carried over)",
                        self.session_id, self.credit)
            self._end_session(st.ABANDONED, f"inactivity timeout, P{self.credit} forfeited")
        else:
            self._end_session(st.CANCELLED, "inactivity timeout")
        self._go_home()
        self._set_toast("Transaction timed out", 4)
        self._changed()

    def _drop_next_session(self, why: str) -> None:
        if self.next_session_id is not None:
            status = st.ABANDONED if self.next_credit else st.CANCELLED
            self.store.end_session(self.next_session_id, status, f"next-order credit dropped: {why}")
        self.next_session_id = None
        self.next_credit = 0

    def _after_success(self) -> None:
        if self.next_session_id is not None:
            if self.stock <= 0 or self.store.active_fault():
                self._drop_next_session("sold out" if self.stock <= 0 else "fault")
                self._go_home()
                return
            self._reset_session()
            self.session_id, self.credit = self.next_session_id, self.next_credit
            self.next_session_id, self.next_credit = None, 0
            self._enter(State.QUANTITY)
            return
        self._go_home()

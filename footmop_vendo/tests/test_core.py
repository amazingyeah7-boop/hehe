import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from footmop.config import Config, ConfigError, CoinCfg, DispenseCfg  # noqa: E402
from footmop.core import store as st  # noqa: E402
from footmop.core.coin import CoinDecoder  # noqa: E402
from footmop.core.debounce import Debouncer  # noqa: E402
from footmop.core.dispenser import Dispenser  # noqa: E402
from footmop.core.state import Btn, Kiosk, State  # noqa: E402
from footmop.core.store import Store  # noqa: E402

MS = 1_000_000  # ns per ms


# ----------------------------------------------------------------- debounce
def test_debounce_ignores_bounce_and_reports_once():
    d = Debouncer(0.05, initial_level=1, active_low=True)
    events = []
    t = 0.0
    # bouncy press: 1,0,1,0,0,0...
    for lvl in [0, 1, 0, 1, 0]:
        t += 0.002
        r = d.update(lvl, t)
        if r is not None:
            events.append(r)
    for _ in range(40):
        t += 0.005
        r = d.update(0, t)
        if r is not None:
            events.append(r)
    assert events == [True]
    for _ in range(40):
        t += 0.005
        r = d.update(1, t)
        if r is not None:
            events.append(r)
    assert events == [True, False]


def test_debounce_rejects_short_spike():
    d = Debouncer(0.05, 1)
    out = [d.update(0, 0.0), d.update(0, 0.02), d.update(1, 0.03), d.update(1, 0.2)]
    assert all(o is None for o in out)


# --------------------------------------------------------------------- coin
def _decoder(**kw):
    cfg = CoinCfg(**kw)
    coins, rejects = [], []
    dec = CoinDecoder(cfg, lambda v, p: coins.append((v, p)), rejects.append)
    return dec, coins, rejects


def _pulse(dec, start_ms, width_ms):
    dec.edge(0, int(start_ms * MS), start_ms / 1000)
    dec.edge(1, int((start_ms + width_ms) * MS), (start_ms + width_ms) / 1000)


def test_coin_valid_pulse_credits_five():
    dec, coins, rejects = _decoder()
    _pulse(dec, 1000, 40)
    assert coins == [(5, 1)] and rejects == []


def test_coin_noise_too_short_rejected():
    dec, coins, rejects = _decoder()
    for i in range(5):
        _pulse(dec, 1000 + i * 10, 3)
    assert coins == [] and len(rejects) == 5


def test_coin_too_long_rejected():
    dec, coins, rejects = _decoder()
    _pulse(dec, 1000, 400)
    assert coins == [] and "too long" in rejects[0]


def test_coin_bounce_gap_rejected_then_next_real_coin_ok():
    dec, coins, rejects = _decoder()
    _pulse(dec, 1000, 40)        # real coin, ends at 1040
    _pulse(dec, 1050, 20)        # 10 ms later -> bounce
    _pulse(dec, 1600, 40)        # next real coin
    assert coins == [(5, 1), (5, 1)]
    assert any("gap" in r for r in rejects)


def test_coin_multi_pulse_bursts():
    dec, coins, rejects = _decoder(pulses_per_coin=2, burst_settle_ms=200)
    _pulse(dec, 1000, 30)
    _pulse(dec, 1100, 30)
    dec.poll(1.2)                # not settled yet
    assert coins == []
    dec.poll(1.4)
    assert coins == [(5, 2)]
    _pulse(dec, 3000, 30)        # lone pulse = incomplete
    dec.poll(3.5)
    assert coins == [(5, 2)] and any("incomplete" in r for r in rejects)


def test_coin_stuck_line_reported():
    dec, coins, rejects = _decoder()
    dec.edge(0, 1000 * MS, 1.0)
    dec.poll(3.5)
    assert any("stuck" in r for r in rejects)
    dec.edge(1, 5000 * MS, 5.0)  # release after stuck -> not a coin
    assert coins == []


# -------------------------------------------------------------------- store
def test_store_persists_stock_and_recovers(tmp_path):
    path = str(tmp_path / "f.db")
    s = Store(path, 25)
    s.set_stock(10)
    sid = s.open_session(25)
    s.add_coin(sid, 5, 1, "payment")
    s.add_coin(sid, 5, 1, "payment")
    s.begin_dispense(sid, 2, 25, 50)
    s.record_item_dispensed(sid)
    s.close()                                  # "power cut"
    s2 = Store(path, 25)
    assert s2.get_stock() == 9
    findings = s2.recover_on_boot()
    assert findings and "1 item(s) owed" in findings[0]
    f = s2.active_fault()
    assert f["code"] == "power_interrupted" and f["owed_items"] == 1
    assert s2.get_session(sid)["status"] == st.INTERRUPTED


def test_store_recovery_open_session_with_credit(tmp_path):
    s = Store(str(tmp_path / "f.db"), 25)
    sid = s.open_session(25)
    s.add_coin(sid, 5, 1, "payment")
    s.recover_on_boot()
    assert s.get_session(sid)["status"] == st.ABANDONED
    assert s.active_fault() is None


def test_stock_never_negative(tmp_path):
    s = Store(str(tmp_path / "f.db"), 25)
    s.set_stock(0)
    s.record_item_dispensed(None)
    assert s.get_stock() == 0


# ----------------------------------------------------------- state machine
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeFx:
    def __init__(self):
        self.dispenses = []
        self.coin_enabled = None
        self.motor_off_calls = 0

    def start_dispense(self, qty, token):
        self.dispenses.append((qty, token))

    def set_coin_acceptor(self, enabled):
        self.coin_enabled = enabled

    def force_motor_off(self):
        self.motor_off_calls += 1


@pytest.fixture
def k(tmp_path):
    cfg = Config()
    cfg.machine.price_per_item = 25
    cfg.machine.max_per_transaction = 5
    store = Store(str(tmp_path / "k.db"), 25)
    store.set_stock(3)
    clock = FakeClock()
    kiosk = Kiosk(cfg, store, FakeFx(), clock)
    kiosk.boot()
    return kiosk


def pay(k, pesos):
    for _ in range(pesos // 5):
        k.on_coin(5, 1)


def test_happy_path(k):
    assert k.state == State.LANDING
    k.press(Btn.WHITE)
    assert k.state == State.QUANTITY and k.qty == 1
    k.press(Btn.BLUE)
    k.press(Btn.BLUE)
    k.press(Btn.BLUE)               # capped by stock 3
    assert k.qty == 3 and k.max_qty == 3
    k.press(Btn.GREEN)
    assert k.qty == 2
    k.press(Btn.WHITE)
    assert k.state == State.PAYMENT and k.amount_due == 50
    pay(k, 45)
    assert k.state == State.PAYMENT and k.remaining == 5
    k.on_coin(5, 1)
    assert k.state == State.DISPENSING
    qty, tok = k.fx.dispenses[-1]
    assert qty == 2
    k.on_item_dropped(tok, 1)
    k.on_item_dropped(tok, 2)
    k.on_dispense_done(tok)
    assert k.state == State.SUCCESS
    assert k.stock == 1
    assert k.store.get_session(k.session_id or 1)["status"] in (st.COMPLETED,)
    k.clock.t += 10
    k.tick()
    assert k.state == State.LANDING


def test_qty_min_is_one(k):
    k.action("start")
    k.action("dec")
    k.action("dec")
    assert k.qty == 1


def test_max_per_transaction_cap(k):
    k.store.set_stock(50)
    k.action("start")
    for _ in range(10):
        k.action("inc")
    assert k.qty == 5


def test_sold_out_blocks_purchase(k):
    k.store.set_stock(0)
    k.exit_admin()
    k._go_home()
    assert k.state == State.SOLD_OUT
    k.press(Btn.WHITE)
    assert k.state == State.SOLD_OUT
    k.on_coin(5, 1)                 # logged as unclaimed
    assert k.store.sales_summary()["unclaimed_coins"] == 5


def test_coin_outside_payment_is_not_credited(k):
    k.on_coin(5, 1)                     # landing
    assert k.state == State.LANDING and k.credit == 0
    k.action("start")
    k.on_coin(5, 1)                     # quantity screen
    assert k.state == State.QUANTITY and k.credit == 0
    assert k.store.sales_summary()["unclaimed_coins"] == 10
    assert "payment screen" in k.toast


def test_inactivity_warning_then_cancel_forfeits_credit(k):
    k.action("start")
    k.action("confirm")
    pay(k, 10)
    sid = k.session_id
    k.clock.t += 61
    k.tick()
    assert k.warning and k.state == State.PAYMENT
    k.clock.t += 16
    k.tick()
    assert k.state == State.LANDING and k.credit == 0
    s = k.store.get_session(sid)
    assert s["status"] == st.ABANDONED and s["credit"] == 10
    # not carried over to the next customer
    k.action("start")
    assert k.credit == 0


def test_warning_dismissed_by_press_without_side_effect(k):
    k.action("start")
    k.action("inc")
    k.clock.t += 61
    k.tick()
    assert k.warning
    k.press(Btn.BLUE)               # dismiss only, must not change qty
    assert not k.warning and k.qty == 2


def test_coin_during_warning_is_credited_and_dismisses(k):
    k.action("start")
    k.action("confirm")
    k.clock.t += 61
    k.tick()
    k.on_coin(5, 1)
    assert not k.warning and k.credit == 5


def test_no_overpayment_possible(k):
    k.action("start")
    k.action("confirm")
    pay(k, 25)
    assert k.state == State.DISPENSING and k.overpaid == 0
    k.on_coin(5, 1)                     # extra coin while dispensing: not credited
    assert k.credit == 25


def test_dispense_fault_partial_locks_machine(k):
    k.action("start")
    k.action("inc")
    k.action("confirm")
    pay(k, 50)
    _, tok = k.fx.dispenses[-1]
    k.on_item_dropped(tok, 1)
    k.on_dispense_fault(tok, "dispense_failure", "No item detected.")
    assert k.state == State.FAULT
    assert k.stock == 2              # only the confirmed item was deducted
    f = k.store.active_fault()
    assert f["owed_items"] == 1
    # locked: buttons and coins do nothing useful
    k.press(Btn.WHITE)
    assert k.state == State.FAULT
    # late messages from the dead run are ignored
    k.on_dispense_done(tok)
    assert k.state == State.FAULT


def test_dispense_watchdog(k):
    k.action("start")
    k.action("confirm")
    pay(k, 25)
    k.clock.t += 120
    k.tick()
    assert k.state == State.FAULT
    assert k.store.active_fault()["code"] == "dispense_timeout"


def tap_sequence(k, seq="GBGBG", gap=0.3):
    for ch in seq:
        k.press(Btn.GREEN if ch == "G" else Btn.BLUE)
        k.release(Btn.GREEN if ch == "G" else Btn.BLUE)
        k.clock.t += gap


def test_admin_sequence_pin_and_clear_fault(k):
    k.store.add_fault("x", "test")
    k._go_home()
    assert k.state == State.FAULT
    tap_sequence(k)                      # short presses only
    assert k.state == State.ADMIN_PIN
    assert not k.submit_pin("0000")
    assert k.submit_pin("0412")
    k.admin_clear_faults()
    k.admin_set_stock(20)
    k.admin_set_price(23)            # not a multiple of 5 -> refused
    assert k.price == 25
    k.admin_set_price(30)
    k.exit_admin()
    assert k.state == State.LANDING and k.stock == 20 and k.price == 30


def test_admin_sequence_too_slow_or_wrong_does_nothing(k):
    tap_sequence(k, gap=2.0)             # 8 s total > 5 s window
    assert k.state == State.LANDING
    tap_sequence(k, "GBGGB")
    assert k.state == State.LANDING


def test_no_long_press_actions(k):
    """Holding buttons for a long time must never trigger anything."""
    k.press(Btn.GREEN)
    k.press(Btn.BLUE)
    k.clock.t += 10
    k.tick()
    assert k.state == State.LANDING


def test_admin_sequence_not_available_mid_transaction(k):
    k.store.set_stock(50)
    k.action("start")
    k.action("inc")
    tap_sequence(k, "BGBGB")
    assert k.state == State.QUANTITY


def test_pin_lockout(k):
    k.open_admin()
    for _ in range(5):
        k.submit_pin("9999")
    assert k.pin_locked_seconds > 0
    assert not k.submit_pin("0412")
    k.clock.t += 61
    assert k.submit_pin("0412")


def test_coin_during_dispensing_is_not_carried_to_next_order(k):
    k.action("start")
    k.action("confirm")
    pay(k, 25)
    _, tok = k.fx.dispenses[-1]
    k.on_coin(5, 1)
    k.on_item_dropped(tok, 1)
    k.on_dispense_done(tok)
    k.action("done")
    assert k.state == State.LANDING and k.credit == 0


def test_cancel_only_without_credit(k):
    k.action("start")
    k.action("inc")
    k.action("confirm")
    k.on_coin(5, 1)                     # paid something on the payment screen
    k.action("back")
    assert k.state == State.QUANTITY and k.credit == 5
    k.action("cancel")
    assert k.state == State.QUANTITY    # can't cancel once coins are in


def test_coin_acceptor_only_enabled_on_payment(k):
    assert k.fx.coin_enabled is False   # LANDING
    k.action("start")
    assert k.fx.coin_enabled is False   # QUANTITY
    k.action("confirm")
    assert k.fx.coin_enabled is True    # PAYMENT
    pay(k, 25)
    assert k.fx.coin_enabled is False   # DISPENSING


# -------------------------------------------------------------- dispenser
class SimHAL:
    def __init__(self, drop=True, blocked=False, drop_delay=0.05, hold_after_drop=False):
        self.drop, self.blocked, self.delay = drop, blocked, drop_delay
        self.hold_after_drop = hold_after_drop
        self.motor = False
        self.ev = threading.Event()
        self.n = 0
        self.history = []

    def drop_sensor_active(self):
        return self.blocked

    def arm_drop(self):
        self.ev.clear()
        self.n = 0

    def wait_drop(self, t):
        return self.ev.wait(t)

    def drop_count(self):
        return self.n

    def set_motor(self, on):
        self.motor = on
        self.history.append(on)
        if on and self.drop:
            def fire():
                self.n += 1
                if self.hold_after_drop:
                    self.blocked = True
                self.ev.set()
            threading.Timer(self.delay, fire).start()

    def force_motor_off(self):
        self.motor = False


def _run_dispenser(hal, qty, **cfg_kw):
    cfg = DispenseCfg(motor_on_ms=100, drop_wait_after_ms=100, cooldown_ms=10, **cfg_kw)
    res = {"items": [], "done": False, "fault": None}
    finished = threading.Event()

    def done(tok):
        res["done"] = True
        finished.set()

    def fault(tok, code, msg):
        res["fault"] = code
        finished.set()

    d = Dispenser(hal, cfg, lambda tok, i: res["items"].append(i), done, fault)
    d.start(qty, 1)
    assert finished.wait(5)
    time.sleep(0.05)
    assert hal.motor is False
    return res


def test_dispenser_ok():
    r = _run_dispenser(SimHAL(), 3)
    assert r["items"] == [1, 2, 3] and r["done"]


def test_dispenser_jam():
    r = _run_dispenser(SimHAL(drop=False), 2)
    assert r["items"] == [] and r["fault"] == "dispense_failure"


def test_dispenser_chute_blocked():
    r = _run_dispenser(SimHAL(blocked=True), 1)
    assert r["fault"] == "chute_blocked"


def test_bundle_waits_for_sensor_clear_before_next_item():
    hal = SimHAL(hold_after_drop=True)
    cfg = DispenseCfg(motor_on_ms=100, drop_wait_after_ms=100, cooldown_ms=10)
    result = {"items": [], "done": False}
    finished = threading.Event()
    waiting = []

    def done(_token):
        result["done"] = True
        finished.set()

    def fault(_token, code, _message):
        result["fault"] = code
        finished.set()

    d = Dispenser(hal, cfg,
                  lambda _token, item: result["items"].append(item),
                  done, fault,
                  on_waiting_for_clear=lambda _token, value: waiting.append(value))
    d.start(2, 1)

    deadline = time.monotonic() + 2
    while not waiting and time.monotonic() < deadline:
        time.sleep(0.01)
    assert waiting == [True]
    assert result["items"] == [1]
    assert hal.history == [True, False]

    hal.blocked = False
    assert finished.wait(2)
    assert result["items"] == [1, 2] and result["done"]
    assert waiting == [True, False]


def test_dispenser_without_sensor_assumes_success():
    r = _run_dispenser(SimHAL(drop=False), 2, sensor_enabled=False)
    assert r["items"] == [1, 2] and r["done"]


# ------------------------------------------------------------------ config
def test_config_rejects_bad_price():
    c = Config()
    c.machine.price_per_item = 23
    with pytest.raises(ConfigError):
        c.validate()


def test_config_rejects_display_pins():
    c = Config()
    c.pins.coin = 24
    with pytest.raises(ConfigError):
        c.validate()


# ------------------------------------------------- buttons-only behaviour
def test_green_at_qty_one_cancels_when_unpaid(k):
    k.press(Btn.WHITE)
    k.press(Btn.GREEN)
    assert k.state == State.LANDING


def test_default_limit_is_stock_and_cannot_go_below_paid(tmp_path):
    cfg = Config()                      # default price P5, no per-purchase cap
    store = Store(str(tmp_path / "p.db"), cfg.machine.price_per_item)
    store.set_stock(8)
    kk = Kiosk(cfg, store, FakeFx(), FakeClock())
    kk.boot()
    assert kk.price == 5 and kk.max_qty == 8
    kk.press(Btn.WHITE)
    for _ in range(10):
        kk.press(Btn.BLUE)
    assert kk.qty == 8                  # limited only by stock
    kk.press(Btn.WHITE)
    assert kk.state == State.PAYMENT and kk.coins_needed == 8
    for _ in range(3):
        kk.on_coin(5, 1)
    kk.press(Btn.GREEN)                 # back to quantity with P15 paid
    for _ in range(10):
        kk.press(Btn.GREEN)
    assert kk.state == State.QUANTITY and kk.qty == 3 and kk.min_qty == 3
    kk.press(Btn.WHITE)                 # already paid for 3 -> dispense now
    assert kk.state == State.DISPENSING and kk.overpaid == 0


def test_admin_presses_go_to_admin_ui(k):
    navs = []
    k.admin_nav = lambda b, p: navs.append((b, p))
    tap_sequence(k)
    k.submit_pin("0412")
    k.press(Btn.WHITE)
    k.clock.t += 3
    k.tick()
    k.release(Btn.WHITE)
    assert k.state == State.ADMIN          # holding WHITE does not exit
    assert navs == [(Btn.WHITE, True)]

# ------------------------------------------------------------- CSV export
def test_sales_csv_export(tmp_path):
    import csv as _csv
    from footmop.core.export import export_sales, write_sales_csv
    s = Store(str(tmp_path / "e.db"), 5)
    s.set_stock(10)
    sid = s.open_session(5)
    s.add_coin(sid, 5, 1, "payment")
    s.add_coin(sid, 5, 1, "payment")
    s.begin_dispense(sid, 2, 5, 10)
    s.record_item_dispensed(sid)
    s.record_item_dispensed(sid)
    s.end_session(sid, st.COMPLETED, "=HYPERLINK(evil)")
    cancelled = s.open_session(5)
    s.end_session(cancelled, st.CANCELLED, "timeout")
    path, n = write_sales_csv(s, str(tmp_path / "out"))
    assert n == 1
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(_csv.DictReader(fh))
    assert rows[0]["quantity"] == "2" and rows[0]["amount_paid"] == "10"
    assert rows[0]["status"] == "COMPLETED" and rows[0]["note"].startswith("'=")
    # no USB mounted -> falls back to the local folder
    path2, n2, on_usb = export_sales(s, [str(tmp_path / "no-usb-*")], str(tmp_path / "fallback"))
    assert not on_usb and path2.startswith(str(tmp_path / "fallback"))


def test_admin_export_message(k, tmp_path):
    k.cfg.storage.usb_globs = [str(tmp_path / "nothing-*")]
    k.cfg.storage.export_dir = str(tmp_path / "exp")
    k.open_admin()
    k.submit_pin("0412")
    k.admin_export_csv()
    assert "No USB drive" in k.admin_message
    import os
    assert len(os.listdir(tmp_path / "exp")) == 1


def test_pin_screen_returns_to_landing_after_30s_idle(k):
    assert k.cfg.admin.pin == "0412"
    k.open_admin()                       # e.g. sequence pressed by accident
    assert k.state == State.ADMIN_PIN
    k.clock.t += 20
    k.tick()
    assert k.state == State.ADMIN_PIN and k.admin_pin_seconds_left == 10
    k.press(Btn.BLUE)                    # any press restarts the 30 s
    k.clock.t += 29
    k.tick()
    assert k.state == State.ADMIN_PIN
    k.clock.t += 1.1
    k.tick()
    assert k.state == State.LANDING


def test_servo_stops_as_soon_as_ir_sees_footmop():
    """Continuous servo: spins until the IR sensor fires, not for the full max time."""
    hal = SimHAL(drop_delay=0.05)
    cfg = DispenseCfg(motor_on_ms=3000, drop_wait_after_ms=100, cooldown_ms=10)
    assert cfg.stop_on_drop                     # default for the servo build
    done = threading.Event()
    d = Dispenser(hal, cfg, lambda t, i: None, lambda t: done.set(), lambda t, c, m: done.set())
    t0 = time.monotonic()
    d.start(1, 1)
    assert done.wait(5)
    assert time.monotonic() - t0 < 1.0          # stopped at the drop, not after 3 s
    assert hal.history == [True, False] and hal.motor is False


def test_servo_jam_times_out():
    r = _run_dispenser(SimHAL(drop=False), 1)
    assert r["fault"] == "dispense_failure"


def test_green_on_gpio17_is_allowed_but_screen_pins_are_not():
    c = Config()
    assert (c.pins.button_green, c.pins.button_blue, c.pins.button_white) == (17, 27, 22)
    c.validate()                         # GPIO17 = unused touch IRQ: allowed
    c.pins.drop_sensor = 25              # screen reset line: refused
    with pytest.raises(ConfigError):
        c.validate()

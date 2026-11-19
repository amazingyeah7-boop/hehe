"""Exercise LgpioHAL against a fake lgpio module (same call signatures as
the real python3-lgpio API) so wiring mistakes surface off-device."""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_fake_lgpio():
    m = types.ModuleType("lgpio")
    m.SET_PULL_UP, m.SET_PULL_DOWN, m.SET_PULL_NONE = 32, 64, 128
    m.RISING_EDGE, m.FALLING_EDGE, m.BOTH_EDGES = 1, 2, 3
    m.state = {"levels": {}, "outputs": {}, "claims": {}, "callbacks": {}, "debounce": {}, "open": []}

    chips = {0: [54, "gpiochip0", "pinctrl-rp1"], 10: [32, "gpiochip10", "rp1-other"]}

    def gpiochip_open(n):
        if n not in chips:
            raise RuntimeError("no chip")
        m.state["open"].append(n)
        return n

    m.gpiochip_open = gpiochip_open
    m.gpiochip_close = lambda h: None
    m.gpiochip_info = lambda h: [0] + chips[h]

    def claim_output(h, gpio, level=0, lFlags=0):
        m.state["claims"][gpio] = "out"
        m.state["outputs"][gpio] = level

    def claim_input(h, gpio, lFlags=0):
        m.state["claims"][gpio] = ("in", lFlags)
        m.state["levels"].setdefault(gpio, 1)

    def claim_alert(h, gpio, eFlags, lFlags=0, notify_handle=None):
        m.state["claims"][gpio] = ("alert", eFlags, lFlags)
        m.state["levels"].setdefault(gpio, 1)

    m.gpio_claim_output = claim_output
    m.gpio_claim_input = claim_input
    m.gpio_claim_alert = claim_alert
    m.gpio_set_debounce_micros = lambda h, g, us: m.state["debounce"].__setitem__(g, us)
    m.gpio_read = lambda h, g: m.state["outputs"].get(g, m.state["levels"].get(g, 1))
    m.gpio_write = lambda h, g, lvl: m.state["outputs"].__setitem__(g, lvl)

    class CB:
        def __init__(self, gpio, fn):
            self.gpio, self.fn = gpio, fn

        def cancel(self):
            pass

    def callback(h, gpio, edge, fn):
        m.state["callbacks"][gpio] = fn
        return CB(gpio, fn)

    m.callback = callback

    def tx_servo(h, gpio, pulse_width, servo_frequency=50, offset=0, cycles=0):
        m.state["servo"].append((gpio, pulse_width, servo_frequency))
        return 0

    m.tx_servo = tx_servo
    m.state["servo"] = []
    return m


def test_lgpio_hal_wiring(monkeypatch):
    fake = make_fake_lgpio()
    monkeypatch.setitem(sys.modules, "lgpio", fake)
    import importlib
    from footmop.hal import lgpio_hal
    importlib.reload(lgpio_hal)
    from footmop.config import Config
    from footmop.core.state import Btn

    cfg = Config()
    hal = lgpio_hal.LgpioHAL(cfg)
    s = fake.state
    # chip auto-detect picked the rp1 header chip
    assert s["open"][0] == 0
    # servo pin claimed as output, held LOW (no pulses = stopped)
    assert s["claims"][12] == "out" and s["outputs"][12] == 0
    for pin in (17, 27, 22):                      # GREEN, BLUE, WHITE
        assert s["claims"][pin][1] == fake.SET_PULL_UP
    assert s["claims"][23][0] == "alert" and s["debounce"][23] == 3000   # coin
    assert s["claims"][26][0] == "alert"                                 # IR sensor

    buttons, edges = [], []
    hal.start(lambda b, p: buttons.append((b, p)), lambda lvl, t: edges.append(lvl))

    # press GREEN for 100 ms -> exactly one press + one release
    s["levels"][17] = 0
    time.sleep(0.12)
    s["levels"][17] = 1
    time.sleep(0.12)
    assert buttons == [(Btn.GREEN, True), (Btn.GREEN, False)]

    # coin edges forwarded, watchdog level (2) ignored
    s["callbacks"][23](0, 23, 0, 1_000_000)
    s["callbacks"][23](0, 23, 1, 41_000_000)
    s["callbacks"][23](0, 23, 2, 99_000_000)
    assert edges == [0, 1]

    # drop sensor active-low edge triggers the latch
    hal.arm_drop()
    s["callbacks"][26](0, 26, 0, 5)
    assert hal.wait_drop(0.1) and hal.drop_count() == 1

    # MG995 continuous servo: use spin pulses, then neutral pulses to stop.
    hal.set_motor(True)
    assert s["servo"][-1] == (12, 2000, 50)
    hal.set_motor(False)
    assert s["servo"][-1] == (12, 1500, 50)
    hal.set_motor(True)
    hal.force_motor_off()
    assert s["servo"][-1] == (12, 1500, 50)
    hal.close()
    assert s["servo"][-1] == (12, 1500, 50)


def test_lgpio_hal_active_low_relay(monkeypatch):
    fake = make_fake_lgpio()
    monkeypatch.setitem(sys.modules, "lgpio", fake)
    import importlib
    from footmop.hal import lgpio_hal
    importlib.reload(lgpio_hal)
    from footmop.config import Config
    cfg = Config()
    cfg.dispense.driver = "relay"
    cfg.pins.motor_relay = 20
    cfg.dispense.relay_active_high = False
    hal = lgpio_hal.LgpioHAL(cfg)
    assert fake.state["outputs"][20] == 1     # OFF level for active-low module
    hal.set_motor(True)
    assert fake.state["outputs"][20] == 0
    hal.close()
    assert fake.state["outputs"][20] == 1

"""Real hardware via lgpio (works on Pi 5 / RP1 and older Pis).

Install on the Pi:  sudo apt install python3-lgpio
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from ..config import Config
from ..core.debounce import Debouncer
from ..core.state import Btn
from .base import HAL, ButtonCallback, EdgeCallback

log = logging.getLogger("footmop.hal")

try:
    import lgpio  # type: ignore
except ImportError:  # pragma: no cover - only on non-Pi machines
    lgpio = None


def _find_chip() -> int:
    """Pick the gpiochip that drives the 40-pin header.

    Pi 5 exposes it as 'pinctrl-rp1' (gpiochip4 on early kernels, gpiochip0
    on newer ones); older Pis use 'pinctrl-bcm2711'/'pinctrl-bcm2835'.
    """
    info_fn = getattr(lgpio, "gpiochip_info", None)
    if info_fn is None:
        # Some lgpio builds do not expose chip metadata. For these builds,
        # Pi 5 header chips are 4 on older kernels and 0 on newer kernels.
        for n in (4, 0):
            try:
                h = lgpio.gpiochip_open(n)
            except Exception:
                continue
            lgpio.gpiochip_close(h)
            log.warning("lgpio chip metadata unavailable; using gpiochip%d", n)
            return n
        raise RuntimeError("could not open any GPIO chip")

    for n in range(0, 16):
        try:
            h = lgpio.gpiochip_open(n)
        except Exception:
            continue
        try:
            info = lgpio.gpiochip_info(h)
            label = str(info[-1]) if isinstance(info, (list, tuple)) else str(info)
            log.info("gpiochip%d: %s", n, info)
            if "rp1" in label or "bcm2" in label:
                return n
        finally:
            lgpio.gpiochip_close(h)
    log.warning("could not identify header gpiochip; falling back to 0")
    return 0


class LgpioHAL(HAL):
    def __init__(self, cfg: Config):
        super().__init__()
        if lgpio is None:
            raise RuntimeError("lgpio is not installed (sudo apt install python3-lgpio)")
        self.cfg = cfg
        p = cfg.pins
        chip = _find_chip() if str(p.chip).lower() == "auto" else int(p.chip)
        log.info("using gpiochip%d", chip)
        self.h = lgpio.gpiochip_open(chip)
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self._motor_off_level = 0 if cfg.dispense.relay_active_high else 1
        self._servo = cfg.dispense.driver == "servo"

        # Outputs FIRST, driven to the safe level, before anything else.
        if self._servo:
            # Start with no pulses until the first dispense request.
            lgpio.gpio_claim_output(self.h, p.servo, 0)
        else:
            lgpio.gpio_claim_output(self.h, p.motor_relay, self._motor_off_level)
        if p.coin_enable is not None and p.coin_enable >= 0:
            off = 0 if cfg.coin.enable_active_high else 1
            lgpio.gpio_claim_output(self.h, p.coin_enable, off)

        pull_btn = lgpio.SET_PULL_UP if cfg.buttons.active_low else lgpio.SET_PULL_DOWN
        self._buttons: Dict[Btn, int] = {Btn.GREEN: p.button_green, Btn.BLUE: p.button_blue,
                                         Btn.WHITE: p.button_white}
        for btn, pin in self._buttons.items():
            try:
                lgpio.gpio_claim_input(self.h, pin, pull_btn)
            except Exception as exc:
                hint = (" GPIO17 is the 3.5\" screen's touch-interrupt line and its touch driver "
                        "is holding it: disable the screen's touch (see docs/WIRING.md, "
                        "'Green button on GPIO 17') or move this button to a free pin."
                        if pin == 17 else " Another driver or program is using this pin.")
                raise RuntimeError(f"{btn.name} button: cannot use GPIO{pin} ({exc}).{hint}") from exc

        # Coin line: kernel-timestamped edge alerts with a glitch filter.
        pull_coin = lgpio.SET_PULL_UP if cfg.coin.active_low else lgpio.SET_PULL_DOWN
        lgpio.gpio_claim_alert(self.h, p.coin, lgpio.BOTH_EDGES, pull_coin)
        lgpio.gpio_set_debounce_micros(self.h, p.coin, int(cfg.coin.glitch_filter_us))

        # Drop sensor (TCRT5000 module DO). The module has its own pull-up,
        # so no internal pull (a 5V-powered module would otherwise be fed back).
        if cfg.dispense.sensor_enabled:
            lgpio.gpio_claim_alert(self.h, p.drop_sensor, lgpio.BOTH_EDGES, lgpio.SET_PULL_NONE)
            lgpio.gpio_set_debounce_micros(self.h, p.drop_sensor, int(cfg.dispense.sensor_glitch_us))
        self._sensor_active_level = 0 if cfg.dispense.sensor_active_low else 1

        self._cb_handles = []
        self._poll_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- lifecycle
    def start(self, on_button: ButtonCallback, on_coin_edge: EdgeCallback) -> None:
        def coin_cb(chip, gpio, level, tick):
            if level in (0, 1):            # 2 = watchdog timeout, ignore
                on_coin_edge(level, tick)

        self._cb_handles.append(lgpio.callback(self.h, self.cfg.pins.coin, lgpio.BOTH_EDGES, coin_cb))

        if self.cfg.dispense.sensor_enabled:
            def sensor_cb(chip, gpio, level, tick):
                if level == self._sensor_active_level:
                    self._signal_drop()
            self._cb_handles.append(
                lgpio.callback(self.h, self.cfg.pins.drop_sensor, lgpio.BOTH_EDGES, sensor_cb))

        self._poll_thread = threading.Thread(target=self._poll_buttons, args=(on_button,),
                                             name="button-poll", daemon=True)
        self._poll_thread.start()

    def _poll_buttons(self, on_button: ButtonCallback) -> None:
        """Poll at a fixed rate and debounce in software. Polling (vs edge
        callbacks) makes bounce handling deterministic and cheap: 3 reads per 5 ms."""
        b = self.cfg.buttons
        deb = {btn: Debouncer(b.debounce_ms / 1000, lgpio.gpio_read(self.h, pin), b.active_low)
               for btn, pin in self._buttons.items()}
        # Buttons already held at boot are ignored until released.
        interval = b.poll_interval_ms / 1000
        while not self._stop.is_set():
            now = time.monotonic()
            for btn, pin in self._buttons.items():
                try:
                    level = lgpio.gpio_read(self.h, pin)
                except Exception:
                    log.exception("button read failed")
                    continue
                ev = deb[btn].update(level, now)
                if ev is not None:
                    on_button(btn, ev)
            self._stop.wait(interval)

    def close(self) -> None:
        self._stop.set()
        self.force_motor_off()
        self.set_coin_enable(False)
        for cb in self._cb_handles:
            try:
                cb.cancel()
            except Exception:
                pass
        try:
            lgpio.gpiochip_close(self.h)
        except Exception:
            pass

    # --------------------------------------------------------------- outputs
    def set_motor(self, on: bool) -> None:
        """Drive a continuous servo with spin or neutral pulses."""
        d = self.cfg.dispense
        with self._io_lock:
            if self._servo:
                pulse_us = d.servo_spin_us if on else d.servo_stop_us
                lgpio.tx_servo(self.h, self.cfg.pins.servo, pulse_us, d.servo_freq_hz)
            else:
                level = (1 - self._motor_off_level) if on else self._motor_off_level
                lgpio.gpio_write(self.h, self.cfg.pins.motor_relay, level)

    def force_motor_off(self) -> None:
        try:
            if self._servo:
                d = self.cfg.dispense
                lgpio.tx_servo(self.h, self.cfg.pins.servo, d.servo_stop_us,
                               d.servo_freq_hz)
            else:
                lgpio.gpio_write(self.h, self.cfg.pins.motor_relay, self._motor_off_level)
        except Exception:
            log.exception("force_motor_off failed")

    def set_coin_enable(self, enabled: bool) -> None:
        pin = self.cfg.pins.coin_enable
        if pin is None or pin < 0:
            return
        on = 1 if self.cfg.coin.enable_active_high else 0
        try:
            with self._io_lock:
                lgpio.gpio_write(self.h, pin, on if enabled else 1 - on)
        except Exception:
            log.exception("coin enable write failed")

    # ---------------------------------------------------------------- sensor
    def drop_sensor_active(self) -> bool:
        if not self.cfg.dispense.sensor_enabled:
            return False
        return lgpio.gpio_read(self.h, self.cfg.pins.drop_sensor) == self._sensor_active_level

    def read_inputs(self) -> Dict[str, int]:
        """Raw levels for the admin hardware-test page."""
        p = self.cfg.pins
        out = {}
        for name, pin in (("green", p.button_green), ("blue", p.button_blue),
                          ("white", p.button_white), ("coin", p.coin)):
            out[name] = lgpio.gpio_read(self.h, pin)
        if self.cfg.dispense.sensor_enabled:
            out["sensor"] = lgpio.gpio_read(self.h, p.drop_sensor)
        return out

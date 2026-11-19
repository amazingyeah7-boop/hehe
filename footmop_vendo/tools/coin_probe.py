#!/usr/bin/env python3
"""Print raw pulse widths/gaps from the coin acceptor so you can tune
[coin] pulse_min_ms / pulse_max_ms / min_gap_ms.

Stop the kiosk first:   sudo systemctl stop footmop
Then:                   python3 tools/coin_probe.py --gpio 23
Insert coins one at a time; Ctrl+C to quit.
"""
import argparse
import os
import tempfile
import time

working_dir = tempfile.mkdtemp(prefix="footmop-lgpio-", dir=tempfile.gettempdir())
os.chdir(working_dir)

import lgpio


def find_chip():
    info_fn = getattr(lgpio, "gpiochip_info", None)
    if info_fn is None:
        for n in (4, 0):
            try:
                h = lgpio.gpiochip_open(n)
            except Exception:
                continue
            lgpio.gpiochip_close(h)
            print("lgpio chip metadata unavailable; using gpiochip%d" % n)
            return n
        raise RuntimeError("could not open any GPIO chip")
    for n in range(16):
        try:
            h = lgpio.gpiochip_open(n)
        except Exception:
            continue
        info = lgpio.gpiochip_info(h)
        lgpio.gpiochip_close(h)
        if "rp1" in str(info[-1]) or "bcm2" in str(info[-1]):
            return n
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpio", type=int, default=23)
    ap.add_argument("--active-high", action="store_true")
    ap.add_argument("--glitch-us", type=int, default=0, help="0 = see everything, including noise")
    a = ap.parse_args()
    chip = find_chip()
    h = lgpio.gpiochip_open(chip)
    pull = lgpio.SET_PULL_DOWN if a.active_high else lgpio.SET_PULL_UP
    lgpio.gpio_claim_alert(h, a.gpio, lgpio.BOTH_EDGES, pull)
    if a.glitch_us:
        lgpio.gpio_set_debounce_micros(h, a.gpio, a.glitch_us)
    active = 1 if a.active_high else 0
    state = {"start": None, "last_end": None, "n": 0}

    def cb(chip_, gpio, level, tick):
        if level == active:
            state["start"] = tick
        elif level in (0, 1) and state["start"] is not None:
            width = (tick - state["start"]) / 1e6
            gap = (state["start"] - state["last_end"]) / 1e6 if state["last_end"] else float("nan")
            state["last_end"] = tick
            state["start"] = None
            state["n"] += 1
            print(f"pulse #{state['n']:4d}  width={width:7.2f} ms   gap since previous={gap:9.1f} ms")

    lgpio.callback(h, a.gpio, lgpio.BOTH_EDGES, cb)
    print(f"gpiochip{chip}, GPIO{a.gpio}, idle level={lgpio.gpio_read(h, a.gpio)}. Insert coins...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lgpio.gpiochip_close(h)


if __name__ == "__main__":
    main()

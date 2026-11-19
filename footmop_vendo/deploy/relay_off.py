#!/usr/bin/env python3
"""Stop the dispenser (servo or motor relay). Run by systemd after the
kiosk stops or crashes, so a dead process can never leave the motor on."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir("/var/lib/footmop")

try:
    import lgpio
    from footmop.config import load_config
    from footmop.hal.lgpio_hal import _find_chip

    cfg = load_config(os.environ.get("FOOTMOP_CONFIG", "/etc/footmop/config.toml"))
    chip = _find_chip() if str(cfg.pins.chip).lower() == "auto" else int(cfg.pins.chip)
    if cfg.dispense.driver == "servo":
        pin, off = cfg.pins.servo, 0           # no pulses = servo stops
    else:
        pin, off = cfg.pins.motor_relay, (0 if cfg.dispense.relay_active_high else 1)
    h = lgpio.gpiochip_open(chip)
    lgpio.gpio_claim_output(h, pin, off)
    lgpio.gpio_write(h, pin, off)
    # Note: lgpio may release the line on close; the boot-time config.txt
    # "gpio=" line and the relay module's own pull keep it off afterwards.
    lgpio.gpiochip_close(h)
    print(f"relay_off: dispenser GPIO{pin} set to {off} (stopped)")
except Exception as exc:  # never fail the unit stop
    print(f"relay_off: {exc}", file=sys.stderr)

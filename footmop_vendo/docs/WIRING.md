# Wiring – Footmop ni Kuya J (Raspberry Pi 5 + 3.5" GPIO screen)

> Safety first: the Pi's GPIO pins are **3.3 V only** and have no protection.
> Never connect 5 V or 12 V signals directly to a GPIO pin.

## Pin map

The 3.5" screen plugs onto **physical pins 1–26**. It uses SPI0 (GPIO 7, 8, 9,
10, 11) plus **24** (data/command) and **25** (screen reset). **Never wire
anything else to those.** The app refuses to start if you do. GPIO 17 is the
screen's touch-interrupt line. Touch isn't used, so the Green button shares
it (see "Green button on GPIO 17" below).

| Function | BCM GPIO | Physical pin | Other side |
|---|---|---|---|
| Green button (left) | GPIO 17 | 11 * | GND |
| Blue button (right) | GPIO 27 | 13 * | GND |
| White button (OK) | GPIO 22 | 15 * | GND |
| Coin pulse signal | GPIO 23 | 16 * | PC817 optocoupler output (see below) |
| IR sensor (stops the servo) | GPIO 26 | 37 | sensor module **DO** pin |
| MG995 servo signal | GPIO 12 | 32 | servo **orange/yellow** wire |
| Coin inhibit relay *(optional)* | GPIO 21 | 40 | relay that powers the coin slot |
| GND | – | 30, 34, 39 | |

\* **Pins 11, 13, 15 and 16 are underneath the screen.** You can't reach them
while it's plugged in. Use a **40-pin GPIO splitter / extension board** (or
the screen's pass-through header, or wires soldered under the Pi) to reach
GPIO 17, 27, 22 and 23.

### Green button on GPIO 17

GPIO 17 (pin 11) is wired to the screen's touch chip, which is unused. Wiring
the Green button there is electrically safe: the touch chip's interrupt
output is open-drain, so a button to GND only pulls it low.

The screen's **touch driver may reserve the pin**. If it does, the kiosk shows
*"Hardware initialisation failed: GREEN button: cannot use GPIO17 … busy"*.
You have two fixes:

1. **Turn off the screen's touch driver** (touch isn't used anyway). Check
   who holds the pin: `sudo apt install -y gpiod && gpioinfo | grep -w "17"`.
   If it shows a consumer like `ads7846`/`pendown`, use your screen vendor's
   display-only overlay (no touch), or ask me to help with your exact
   overlay.
2. **Or move Green to a free pin,** e.g. GPIO 5 (pin 29): set
   `button_green = 5` in `/etc/footmop/config.toml`, then
   `sudo systemctl restart footmop`.

**Power for the sensor and modules.** The 3.3 V and 5 V pins (1, 2, 4, 17) are
under the screen. To reach them, use one of:

- a 40-pin GPIO splitter or extension board,
- the pass-through header on the screen, if it has one,
- wires soldered to the underside of those pins on the Pi.

## Buttons

Wire each button between its GPIO and GND. The app turns on the internal
pull-up resistor, so a press reads LOW. Debouncing is done in software
(60 ms stable time).

## Coin acceptor (single-coin ₱5, 12 V)

Most coin slots used in Philippine piso machines run on 12 V and have an
open-collector **COIN** output. Isolate it with a **PC817 optocoupler**:

```
 +12V ──[1 kΩ]──► PC817 pin 1 (anode)
 COIN wire ─────► PC817 pin 2 (cathode)          (COIN sinks to GND during a pulse)
 PC817 pin 4 (collector) ─────► GPIO 23 (pin 16)  (internal pull-up to 3.3 V)
 PC817 pin 3 (emitter)   ─────► Pi GND (pin 39)
 Coin acceptor GND ─────► 12 V supply GND (NOT connected to the Pi)
```

- Set the acceptor's **NO/NC** switch to **NO**, so the line rests HIGH and
  pulses LOW. That matches `coin.active_low = true`.
- If the acceptor has a pulse-speed switch (fast/medium/slow), pick one. Then
  run `tools/coin_probe.py`, read the pulse width it prints, and set
  `pulse_min_ms` and `pulse_max_ms` around it. Example: if the probe shows
  about 50 ms, set min = 20 and max = 120.
- If your acceptor sends more than one pulse per coin, set `pulses_per_coin`.
- Before wiring, measure the COIN wire with a multimeter. If it outputs a
  voltage of its own instead of pulling to GND, the opto LED goes between COIN
  and GND instead.

## IR drop sensor (TCRT5000) – stops the servo

- Power the module from **3.3 V**, not 5 V. Its DO pin is pulled up to the
  module's supply, so a 5 V module would push 5 V into the Pi.
- Mount it in the chute where every footmop passes within about **2–15 mm** of
  the sensor face. The TCRT5000 is a short-range reflective sensor.
- Turn the module's potentiometer until its DO LED switches cleanly when a
  footmop passes and stays off when the chute is empty.
- Most modules pull DO **LOW** when they detect something
  (`sensor_active_low = true`). Check this on the admin **Test hardware**
  page: `sensor=` should change when you hold a footmop in front of it.
- If dark or fluffy footmops are missed, move the sensor closer, block outside
  light from it, or switch to a slotted IR break-beam sensor. The app works
  with either; only the active level may change.

The app checks the sensor **before** each item. If the sensor already reads
"blocked", it refuses to start the servo and shows *Chute Blocked*.

## MG995 continuous servo (the dispenser)

The MG995 360° servo spins the dispenser. **It stops the moment the IR sensor
sees the footmop drop.** If no footmop is seen within 5 s (`motor_on_ms`), the
servo stops and the machine locks with *Dispense Failure* (a jam).

| MG995 wire | Connect to |
|---|---|
| Brown (GND) | Servo power supply **GND**, and also a **Pi GND** pin (e.g. 34). A common ground is required |
| Red (+) | A separate **5–6 V supply rated at least 2–3 A**. **Not** the Pi's 5 V pin: the MG995 can draw over 1 A and would reset the Pi |
| Orange (signal) | **GPIO 12, physical pin 32** |

How the app drives it:

- **Spin:** 50 Hz servo pulses of `servo_spin_us` (2000 µs by default).
- **Stop:** neutral 50 Hz pulses of `servo_stop_us` (1500 µs by default).
  Adjust this value if the servo creeps while stopped.
- **Wrong direction?** If it spins the wrong way, set
  `servo_spin_us = 1000` in `/etc/footmop/config.toml`, then
  `sudo systemctl restart footmop`.
- **Too fast or too slow?** Set `servo_spin_us` closer to `servo_stop_us`
  to spin slower; use a value on the other side of `servo_stop_us` to reverse.
- **Creeps when it should stop?** Adjust `servo_stop_us` in
  `/etc/footmop/config.toml` or turn the MG995's centre trimmer until it is
  completely still.
- **Test it:** Admin → **Test hardware** → **Test dispense 1 item**. The
  servo should spin, drop one footmop, and stop as soon as the IR sensor sees
  it.
- **Bundles:** after each detected drop, the servo stays stopped until the
  sensor clears before the next item starts. If the sensor stays blocked, the
  bundle waits there indefinitely; clear the chute safely to continue.

Optional boot-time safety line for `/boot/firmware/config.txt` (holds the
signal LOW from power-on):

```
gpio=12=op,dl
```

> Using a plain DC motor with a relay instead? Set `driver = "relay"` and
> `pins.motor_relay` in the config. The old relay instructions still apply:
> use an active-HIGH module and set `gpio=<pin>=op,dl`.

## Coin inhibit (strongly recommended)

The kiosk only accepts coins on the **Payment screen**. To enforce that
physically, put a relay on the coin acceptor's 12 V supply line, driven by
GPIO 21, and set `pins.coin_enable = 21`. The coin slot is then powered only
while the Payment screen is showing. On every other screen, coins fall
straight through to the return cup instead of being swallowed.

Without the relay, coins inserted at those times are kept by the machine and
**logged** (Admin → Sales report → Unclaimed coins).

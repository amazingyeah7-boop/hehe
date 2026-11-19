# Footmop ni Kuya J – Vending Kiosk

A full-screen, **buttons-only** kiosk for a Raspberry Pi 5 with a 3.5" SPI
screen. It sells footmops at **₱5 each**: the customer picks a quantity with
the Green/Blue/White buttons, pays with ₱5 coins on the payment screen, and
the machine dispenses one footmop at a time. A TCRT5000 sensor confirms each
drop. Touch and mouse input are ignored completely.

**New here? Start with [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md)** – step-by-step for the PC simulator and the Raspberry Pi.

## Customer flow

```
LANDING ──WHITE──► QUANTITY ──WHITE──► PAYMENT ──credit ≥ total──► DISPENSING ──► SUCCESS ──► LANDING
   │                                                                        │ jam / sensor / timeout
   ▼                                                                        ▼
SOLD OUT (stock 0)                                               FAULT (locked until admin clears)
```

The bottom of every screen shows three coloured keycaps in the same order as
the physical buttons, labelled with what each button does right now.

| Screen   | GREEN (left)                         | WHITE (centre) | BLUE (right) |
|----------|--------------------------------------|----------------|--------------|
| Landing  | –                                    | Buy            | –            |
| Quantity | Less (at 1 with nothing paid: **Cancel**) | OK        | More         |
| Payment  | Back                                 | –              | –            |
| Success  | –                                    | Done           | –            |

- The customer can choose up to the number of footmops in stock. There is no
  other per-purchase limit. To add one, set `max_per_transaction` in the
  config; 0 means no limit.
- **Coins are accepted only on the Payment screen.** On any other screen they
  are not credited.
- If the customer goes Back from Payment after inserting coins, the quantity
  can't go below what's already paid for, so nobody overpays.
- The Payment screen shows one coin circle per ₱5 needed, filling in as coins
  arrive.

## Policies

- **Inactivity:** after 60 s with no button press or coin on the Quantity or
  Payment screen, a *"Still there?"* countdown appears for 15 s. Any input
  dismisses it. If it runs out, the order is cancelled. Any credit is
  **forfeited**: it is not carried over to the next customer. It is logged as
  `ABANDONED` so the owner can reconcile.
- **Overpayment:** not possible. Coins only count on the Payment screen, and
  dispensing starts the moment the exact amount is reached.
- **Coins inserted on any other screen** (welcome, quantity, dispensing,
  sold out, fault) are **not credited**. The screen says *"Coins are accepted
  only on the payment screen"*. To make the slot physically refuse them, so
  they drop back out instead of being kept, wire the **coin-inhibit relay**
  (see `docs/WIRING.md`). Without it the machine keeps those coins and logs
  them as *unclaimed* in the Sales report.

## Reliability features

- **Single-threaded state machine:**
  - GPIO polling, coin decoding and the dispenser each run on their own
    background thread.
  - Those threads only send queued Qt signals.
  - Buttons and coins are therefore handled one at a time on the GUI
    thread. They cannot race, and the UI never blocks on hardware.
- **Buttons:** polled at 200 Hz. A press only counts once the level has been
  stable for 60 ms.
- **Coins**, filtered in four stages:
  1. a kernel glitch filter (3 ms);
  2. pulse width must be 15–150 ms;
  3. a minimum 40 ms gap between pulses, which rejects bounce;
  4. a stuck-line alarm. Optionally, pulses are grouped into bursts for
     multi-pulse acceptors.
- **Dispensing:** items are dispensed one at a time.
  1. A pre-check confirms the chute is clear.
  2. The MG995 continuous servo spins until the IR sensor sees the footmop
     drop, then stops immediately.
  3. For bundles, the next item waits until the sensor clears; the motor stays
     off while waiting. No drop within 5 s = jam: the servo stops and the machine locks.
  4. The stock count goes down only for confirmed items.
- **Servo safety:** an independent hard-cutoff timer, plus `finally` blocks,
  a crash hook, and `ExecStopPost` stop the servo. A watchdog in
  the state machine faults the machine if the dispenser never reports back.
- **Fault lock:** a jam, a blocked chute, a dispenser timeout, or a power cut
  mid-dispense locks the machine on an *Out of Service* screen. The fault
  records the number of items owed and stays until an admin clears it.
- **Persistence:** SQLite with WAL and `synchronous=FULL`. Every coin, every
  dispensed item and every stock change is its own committed transaction. At
  boot, half-finished orders are closed out and owed items are raised as a
  fault.
- **Watchdog:** a systemd watchdog (`WatchdogSec=30`) restarts a frozen UI.
  `Restart=always` restarts the app after a crash.

## Admin

The buttons only register **short presses**. Nothing in the kiosk uses a long
press or holding two buttons at once.

Admin mode can be opened from the Welcome, Sold out or Fault screen:

- **Open admin:** quickly press **GREEN, BLUE, GREEN, BLUE, GREEN** (within
  5 s). GREEN and BLUE do nothing else on those screens, so customers won't
  trigger it. The sequence can be changed with `admin_sequence` in the config.
- **PIN, stock and price** are entered one digit at a time:
  - GREEN = digit −1, BLUE = digit +1.
  - WHITE = next digit; on the last digit, WHITE enters or saves.
  - Each digit cycles 0–9 and then **✕**. WHITE on ✕ cancels.
- **Menus:** GREEN = up, BLUE = down, WHITE = select.
- **PIN:** `0412` by default (set `[admin] pin` in the config).
- **Exit:** choose **Exit admin** in the menu, or ✕ on the PIN screen.
- **Opened by accident?** If nobody presses a button on the PIN screen for
  **30 s**, it goes back to the start screen by itself; the screen counts
  down. The admin menu closes itself after 2 minutes idle.
- **Sales report → Download CSV to USB drive:** plug a FAT32/exFAT flash
  drive into the Pi, then select it. The CSV (one row per order: time,
  status, quantity, price, paid, dispensed, owed, note) is saved to the
  drive, and the screen says when it's safe to remove. With no drive in, it's
  saved to `/var/lib/footmop/exports` instead (`data\exports` in the PC
  simulator). The installer sets up automatic mounting of USB drives.
- **Lockout:** 5 wrong PINs lock the keypad for 60 s.

Admin pages:

- **Stock / Restock:** set the stock count.
- **Price:** in ₱5 steps.
- **Faults:** read and clear faults.
- **Test hardware:** live input levels and events, plus a one-item test
  dispense.
- **Sales report:** today and all-time sales, abandoned credit, unclaimed
  coins, items owed.
- **Exit.**

Change the PIN in `/etc/footmop/config.toml`.

## Try it on a PC (no Pi needed)

```bash
pip install PyQt5
python3 -m footmop --mock --windowed --seed-stock 10            # 480x320
python3 -m footmop --mock --windowed --size 800x480 --seed-stock 10
```

| Key                       | Simulates                                  |
|---------------------------|--------------------------------------------|
| ← or ↑ / A                | GREEN button                               |
| → or ↓ / D                | BLUE button                                |
| Enter / Space / S         | WHITE button                               |
| C or 5                    | insert ₱5 coin (real pulse through the decoder) |
| N                         | coin-line noise (should be rejected)       |
| J                         | toggle motor jam                           |
| B                         | toggle chute blocked                       |
| ← → ← → ← (quick taps)    | admin (PIN 0412); leave via "Exit admin"   |
| Esc                       | quit                                       |

To run the unit tests: `python3 -m pytest -q`

## Install on the Pi

1. Use Raspberry Pi OS Bookworm (64-bit). Lite is recommended for a kiosk.
2. Set up the 3.5" screen driver. See `docs/DISPLAY_SETUP.md`.
3. Wire the hardware. See `docs/WIRING.md`.
4. Install and configure:

   ```bash
   unzip footmop_vendo.zip && cd footmop_vendo
   sudo ./deploy/install.sh                 # or: FOOTMOP_DISPLAY_MODE=desktop sudo -E ./deploy/install.sh
   sudo nano /etc/footmop/config.toml       # set admin PIN, check pins
   sudo nano /boot/firmware/config.txt      # optional: gpio=12=op,dl (servo held still at boot)
   sudo reboot
   ```

5. Calibrate the coin acceptor:

   ```bash
   sudo systemctl stop footmop
   python3 /opt/footmop/tools/coin_probe.py --gpio 23    # insert coins, read widths
   ```

   Set `pulse_min_ms` and `pulse_max_ms` in the config to bracket the widths
   you see, then `sudo systemctl start footmop`.

6. On first boot the stock is 0, so the screen shows SOLD OUT. Open admin and
   set the stock.

Useful commands:

```bash
journalctl -u footmop -f                      # live log
tail -f /var/log/footmop/footmop.log
sqlite3 /var/lib/footmop/footmop.db "select * from sessions order by id desc limit 10;"
```

## Project layout

```
footmop/
  config.py            config dataclasses + validation (bad price, pin clashes with the screen…)
  core/debounce.py     button debouncer (pure)
  core/coin.py         coin pulse decoder + worker thread
  core/store.py        SQLite persistence + boot recovery
  core/state.py        kiosk state machine (pure, unit-tested)
  core/dispenser.py    per-item motor/sensor sequence with hard cutoff
  hal/lgpio_hal.py     real GPIO (Pi 5 aware)
  hal/mock_hal.py      keyboard-driven simulator
  ui/                  PyQt5 screens, scaling, admin, signal bridge
deploy/                installer, systemd unit, launcher, relay-off hook
docs/                  wiring + display setup
tools/coin_probe.py    measure real coin pulse widths
tests/                 46 unit tests (state machine, decoder, debounce, store, dispenser)
```

## Not yet verified on real hardware

All logic has been tested with unit tests and a scripted end-to-end run on
the simulator. These things depend on your physical parts and still need
checking on the real machine:

- coin pulse widths, using `coin_probe.py`;
- the TCRT5000 position and threshold;
- the servo's spin direction and speed (`servo_spin_us`);
- the vendor's display driver.

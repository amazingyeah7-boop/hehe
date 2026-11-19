# How to run Footmop ni Kuya J

There are two ways to run it:

- **Part A – on your Windows PC.** This is a simulator: the keyboard stands
  in for the buttons and the coin slot. Use it to try the screens and test
  changes.
- **Part B – on the Raspberry Pi 5** with the real screen, buttons, coin slot,
  sensor and motor.

---

## Part A – Run it on your Windows PC (simulator)

### 1. Install Python (one time)

1. Download **Python 3.11 or newer** from <https://www.python.org/downloads/>.
2. In the installer, tick **"Add python.exe to PATH"**, then click
   **Install Now**.

### 2. Install PyQt5 (one time)

Open **PowerShell** and run:

```powershell
py -m pip install PyQt5
```

### 3. Start the kiosk

Go to the project folder. This is the `footmop_vendo` folder inside your
`EmbedniJuswa` clone, or inside the unzipped `footmop_vendo.zip`. Then start
it:

```powershell
cd $HOME\Downloads\EmbedniJuswa\footmop_vendo
py -m footmop --mock --windowed --seed-stock 10
```

A 480×320 window opens. That is the same size as the 3.5" screen.

- `--seed-stock 10` sets the stock to 10. Leave it out to keep the stock
  from the last run.
- For a bigger window, add `--size 800x480`.
- The simulator saves its data in a `data\` folder inside the project. Delete
  that folder to start fresh.

### 4. Controls

| Key | Does |
|---|---|
| **←** or **↑** (or A) | GREEN button |
| **→** or **↓** (or D) | BLUE button |
| **Enter** (or Space / S) | WHITE button |
| **C** (or 5) | Insert a ₱5 coin (only counted on the payment screen) |
| **N** | Send electrical noise on the coin wire (should be ignored) |
| **J** | Turn a dispenser jam on/off. The next purchase fails and locks the machine |
| **B** | Turn "chute blocked" on/off |
| Tap **← → ← → ←** quickly | Open admin (PIN **0412**) |
| Menu → **Exit admin** | Leave admin |
| **Esc** | Quit |

### 5. Things to try

1. **Normal sale:**
   1. Press Enter, then → twice to choose 3.
   2. Press Enter.
   3. Press C three times.

   The machine dispenses three footmops and shows "Salamat po!".
2. **Coins outside payment:** press C on the welcome screen. Nothing is
   credited. Coins only count on the payment screen.
3. **Cancel:** press Enter, then ← while the quantity is 1.
4. **Timeout:**
   1. Press Enter, → (choose 2), Enter, then C once.
   2. Wait 60 s. "Still there?" appears.
   3. Wait 15 s more. The order is cancelled and the coin is forfeited.
5. **Jam:**
   1. Press J, then buy one footmop.

      The screen turns red and says "OUT OF SERVICE".
   2. Open admin, then go to **Faults** → **Clear all faults**.
   3. Press J again to stop simulating the jam.
6. **Restock:** in admin, go to **Stock / Restock**. Set each digit with ←/→
   and press Enter to move to the next. Enter on the last digit saves.

### 6. Run the tests (optional)

```powershell
py -m pip install pytest
py -m pytest -q
```

You should see `46 passed`.

---

## Part B – Run it on the Raspberry Pi 5

### What you need

- Raspberry Pi 5, its 27 W power supply, and a microSD card of 16 GB or more.
- The 3.5" SPI screen.
- Three buttons, the ₱5 coin acceptor (12 V), a PC817 optocoupler, the
  IR (TCRT5000) sensor module, and the **MG995 continuous servo** with its
  own 5–6 V supply (2–3 A).
- All of them wired as shown in [`WIRING.md`](WIRING.md).

### 1. Prepare the SD card (one time)

1. On your PC, install **Raspberry Pi Imager** from raspberrypi.com.
2. Choose these:
   - **Device:** Raspberry Pi 5
   - **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
3. Click **Edit settings** and set:
   - **Username:** for example `pi`, with a password.
   - **Wi-Fi:** your network.
   - **Services:** turn on **SSH**.
4. Write the card, put it in the Pi, and power it on. The first boot takes
   about 2 minutes.

### 2. Connect to the Pi from your PC

In PowerShell, run this. Use `raspberrypi.local`, or the Pi's IP address from
your router:

```powershell
ssh pi@raspberrypi.local
```

From here on, every command in Part B runs **on the Pi**, inside this SSH
window.

### 3. Install the screen driver

Follow the instructions from your screen's seller for **Pi 5 / Bookworm**.
See [`DISPLAY_SETUP.md`](DISPLAY_SETUP.md). Reboot afterwards and check that
the screen shows the console text.

### 4. Get the project onto the Pi

Pick one:

```bash
# Option 1 - from GitHub (after you pushed it)
sudo apt install -y git
git clone https://github.com/JushouaS/EmbedniJuswa.git
cd EmbedniJuswa/footmop_vendo
```

```powershell
# Option 2 - copy the zip from your PC (run this in PowerShell on the PC)
scp $HOME\Downloads\footmop_vendo.zip pi@raspberrypi.local:~
```

If you used option 2, unzip it on the Pi:

```bash
unzip footmop_vendo.zip && cd footmop_vendo
```

### 5. Install

```bash
sudo ./deploy/install.sh
```

This does the following:

- installs PyQt5 and lgpio;
- copies the app to `/opt/footmop`;
- creates `/etc/footmop/config.toml`;
- sets the app to start automatically at boot.

### 6. Set it up (one time)

1. **Change the admin PIN** and check the pins and price:

   ```bash
   sudo nano /etc/footmop/config.toml
   ```

   Set `pin = "...."` under `[admin]`. Save with Ctrl+O, then Enter, then
   Ctrl+X.

2. **Keep the servo still while the Pi boots** (optional extra safety). Open
   the boot config:

   ```bash
   sudo nano /boot/firmware/config.txt
   ```

   Add this line at the bottom:

   ```
   gpio=12=op,dl
   ```

3. Reboot:

   ```bash
   sudo reboot
   ```

### 7. Check the hardware

The kiosk starts by itself after boot. On first start it shows **SOLD OUT**,
because the stock is 0.

1. Quickly press **GREEN, BLUE, GREEN, BLUE, GREEN**, then enter your PIN (default **0412**).
   - GREEN/BLUE change the digit.
   - WHITE goes to the next digit and submits after the last one.
   - To back out, press GREEN until the digit shows **✕**, then press WHITE.
2. Go to **Test hardware**. Press each button: its level should change on
   screen. Insert a coin: you should see `COIN +P5`. Hold a footmop in front
   of the sensor: it should change to `BLOCKED`.
3. Select **Test dispense 1 item**. The servo should spin, drop one footmop
   and stop as soon as the IR sensor sees it. If it spins the wrong way, set
   `servo_spin_us = 1000` in `/etc/footmop/config.toml`. The default stop
   pulse is `servo_stop_us = 1500`; adjust it if the servo creeps at rest.
4. Go back, open **Stock / Restock**, and set the real count one digit at a
   time. WHITE on the last digit saves.
5. Choose **Exit admin**. The welcome screen appears.

**If coins are not counted (or counted twice),** measure the acceptor's
pulses:

```bash
sudo systemctl stop footmop
python3 /opt/footmop/tools/coin_probe.py --gpio 23
```

Insert some coins and note the `width=` values it prints. Then:

1. Run `sudo nano /etc/footmop/config.toml`.
2. Under `[coin]`, set `pulse_min_ms` a bit below the smallest width and
   `pulse_max_ms` a bit above the largest.
3. Run `sudo systemctl start footmop`.

---

## Everyday use (owner)

| Task | How |
|---|---|
| Restock | Press G B G B G → PIN → **Stock / Restock** → set each digit (GREEN/BLUE), WHITE = next / save |
| Change price | Admin → **Price** → set digits (must be a multiple of ₱5) → WHITE on the last digit saves |
| "OUT OF SERVICE" | Fix the cause (jammed footmop, blocked chute) → Admin → **Faults** → **Clear all faults**. The screen shows how many items are owed to the customer; give those by hand |
| See sales | Admin → **Sales report** |
| Get sales as a spreadsheet | Plug in a USB flash drive → Admin → **Sales report** → **Download CSV to USB drive** → unplug when it says "safe to remove". Open the `.csv` in Excel |
| Leave admin | Menu → **Exit admin** (or it closes by itself after 2 min) |

## Useful commands on the Pi (over SSH)

```bash
sudo systemctl status footmop      # is it running?
sudo systemctl restart footmop     # restart the kiosk
sudo systemctl stop footmop        # stop it (e.g. for maintenance)
journalctl -u footmop -f           # live log (Ctrl+C to exit)
sudo nano /etc/footmop/config.toml # settings; restart afterwards
```

## Updating to a new version

```bash
cd ~/EmbedniJuswa && git pull
cd footmop_vendo && sudo ./deploy/install.sh
sudo systemctl restart footmop
```

Your settings, stock and sales are kept. They live in `/etc/footmop` and
`/var/lib/footmop`, which the installer does not overwrite.

## Troubleshooting

| Problem | What to check |
|---|---|
| Screen stays black / shows console text | Screen driver installed? `journalctl -u footmop -f` shows errors. Try `FOOTMOP_FB=/dev/fb1` in `/etc/footmop/footmop.env` |
| "Hardware initialisation failed" | Run `sudo apt install python3-lgpio`. Also check that no pin in the config is one the screen uses |
| Buttons do nothing | Admin → Test hardware: does the level change? Check that each button is wired between its GPIO and **GND** |
| Coins not counted | Run `coin_probe.py` (see step 7). Check the opto wiring and set the acceptor's NO/NC switch to **NO** |
| Every purchase says "Dispense Failure" | The sensor never sees the item. Check its position (2–15 mm), turn its potentiometer, and power it from **3.3 V** |
| Servo spins the wrong way | Set `servo_spin_us = 1000` in `/etc/footmop/config.toml`, then `sudo systemctl restart footmop` |
| Servo creeps when stopped | Turn the MG995's centre trimmer (if it has one) until it's still. Check the common ground between the servo supply and the Pi |
| Pi reboots when the servo starts | The servo is powered from the Pi. Give it its own 5–6 V, 2–3 A supply (grounds joined) |
| Admin PIN screen opened by accident | Do nothing - it goes back to the start screen after 30 s |
| Forgot admin PIN | Over SSH: `sudo nano /etc/footmop/config.toml`, set a new `pin`, then `sudo systemctl restart footmop` |

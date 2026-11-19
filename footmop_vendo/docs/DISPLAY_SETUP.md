# 3.5" SPI screen on a Raspberry Pi 5 (display only, no touch)

This kiosk is operated **only with the three physical buttons**. Touch input
is ignored even if the panel has a touch layer (`display.touch_enabled = false`),
so you never need to calibrate or rotate touch.

The screen driver is not part of this app. Install it the way your screen's
vendor documents for **Raspberry Pi 5 / Bookworm**. These are usually
Waveshare-compatible "3.5inch RPi LCD (A)" boards (ILI9486 display chip with
XPT2046/ADS7846 touch).

Waveshare's manual configuration for the 3.5" (A) on Bookworm and Pi 4/5
enables SPI and loads their `waveshare35a` overlay in
`/boot/firmware/config.txt`:

```
dtparam=spi=on
dtoverlay=waveshare35a
```

Their guide then sets up X11 with touch calibration. Follow the vendor's
page for your exact model, since clones differ. Reference:
https://www.waveshare.com/wiki/3.5inch_RPi_LCD_(A)_Manual_Configuration

## Choosing how the kiosk draws

Set `FOOTMOP_DISPLAY_MODE` in `/etc/footmop/footmop.env`.

### A) `linuxfb` (Raspberry Pi OS Lite, no desktop) – recommended

This is the most reliable kiosk setup: no desktop, nothing else on screen,
and fast boot.

- Qt draws straight to the panel's framebuffer. The launcher finds the
  `/dev/fbN` that belongs to the SPI panel automatically. To pick one by
  hand, set `FOOTMOP_FB=/dev/fb1`.
- No input plugins are loaded; the buttons are read directly from GPIO.
- To list the framebuffers and their drivers, run
  `cat /sys/class/graphics/fb*/name`.

### B) `desktop` (Raspberry Pi OS Desktop with auto-login)

Use this if your vendor's driver only works through X11 or Wayland. The
launcher waits for the session and runs the app fullscreen inside it.

- Turn off screen blanking: `raspi-config` → Display → Screen Blanking → No.
- Hide the panel/taskbar if you like. The app is fullscreen anyway.

## Checks

```
journalctl -u footmop -f     # shows which Qt platform and framebuffer were picked
```

Use the admin **Test hardware** page to confirm the buttons, coins and the
drop sensor.

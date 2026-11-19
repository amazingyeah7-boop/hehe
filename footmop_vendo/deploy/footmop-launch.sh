#!/usr/bin/env bash
# Starts the kiosk with the right Qt display backend.
#
#   FOOTMOP_DISPLAY_MODE=linuxfb   (Raspberry Pi OS Lite, no desktop - recommended kiosk)
#   FOOTMOP_DISPLAY_MODE=desktop   (Raspberry Pi OS Desktop; X11 or Wayland session)
#
# Set it in /etc/footmop/footmop.env. Other optional variables:
#   FOOTMOP_FB=/dev/fb1             framebuffer of the 3.5" SPI panel (linuxfb mode)
set -euo pipefail

MODE="${FOOTMOP_DISPLAY_MODE:-linuxfb}"
APP_DIR="${FOOTMOP_APP_DIR:-/opt/footmop}"
CONFIG="${FOOTMOP_CONFIG:-/etc/footmop/config.toml}"

if [[ "$MODE" == "linuxfb" ]]; then
    FB="${FOOTMOP_FB:-}"
    if [[ -z "$FB" ]]; then
        # Prefer the SPI panel's framebuffer (fbtft / tiny DRM drivers), else fb0.
        for f in /sys/class/graphics/fb*; do
            name="$(cat "$f/name" 2>/dev/null || true)"
            case "$name" in
                *ili9486*|*fb_ili*|*waveshare*|*piscreen*|*mipi*|*st7796*) FB="/dev/$(basename "$f")";;
            esac
        done
        if [[ -z "$FB" && -e /dev/fb0 ]]; then
            FB="/dev/fb0"
        fi
    fi
    if [[ -n "$FB" && -e "$FB" ]]; then
        export QT_QPA_PLATFORM="linuxfb:fb=${FB}"
    else
        echo "footmop-launch: no framebuffer device found (configure screen and /dev/fb*)" >&2
        export QT_QPA_PLATFORM="linuxfb"
    fi
    # Buttons-only kiosk: load no touch/mouse/keyboard input plugins at all.
    # (The physical buttons are read from GPIO, not through Qt.)
    export QT_QPA_GENERIC_PLUGINS=""
    export QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS=""
    export QT_QPA_FB_DISABLE_INPUT=1   # do not grab evdev touch/mouse/keyboard devices
    export QT_QPA_FB_HIDECURSOR=1
    # Stop the console cursor blinking through the UI.
    for tty in /dev/tty1; do [[ -w "$tty" ]] && printf '\033[?25l' > "$tty" || true; done
else
    UID_NUM="$(id -u)"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"
    # Wait (max ~45 s) for the desktop session to come up after boot.
    for _ in $(seq 1 45); do
        if ls "${XDG_RUNTIME_DIR}"/wayland-* >/dev/null 2>&1 || [[ -S /tmp/.X11-unix/X0 ]]; then break; fi
        sleep 1
    done
    if [[ -S "${XDG_RUNTIME_DIR}/wayland-0" || -S "${XDG_RUNTIME_DIR}/wayland-1" ]]; then
        export QT_QPA_PLATFORM=wayland
        export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-$( [[ -S ${XDG_RUNTIME_DIR}/wayland-0 ]] && echo wayland-0 || echo wayland-1)}"
    else
        export QT_QPA_PLATFORM=xcb
        export DISPLAY="${DISPLAY:-:0}"
        export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    fi
fi

echo "footmop-launch: mode=$MODE QT_QPA_PLATFORM=$QT_QPA_PLATFORM input=gpio-buttons-only" >&2
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
RUNTIME_DIR="${FOOTMOP_RUNTIME_DIR:-/var/lib/footmop}"
mkdir -p "$RUNTIME_DIR"
cd "$RUNTIME_DIR"
exec /usr/bin/python3 -m footmop --config "$CONFIG"

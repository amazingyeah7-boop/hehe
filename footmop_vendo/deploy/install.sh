#!/usr/bin/env bash
# Footmop ni Kuya J - installer for Raspberry Pi OS Bookworm (Pi 5 / Pi 4).
# Usage (from the unzipped project folder):   sudo ./deploy/install.sh
#   FOOTMOP_USER=pi  sudo -E ./deploy/install.sh     # choose the service user
#   FOOTMOP_DISPLAY_MODE=desktop sudo -E ./deploy/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${FOOTMOP_USER:-${SUDO_USER:-pi}}"
MODE="${FOOTMOP_DISPLAY_MODE:-linuxfb}"
APP=/opt/footmop
ETC=/etc/footmop

echo "==> Installing for user '$RUN_USER' (display mode: $MODE)"
id "$RUN_USER" >/dev/null

echo "==> Packages"
apt-get update -qq
apt-get install -y python3-pyqt5 python3-lgpio fonts-dejavu-core sqlite3

echo "==> Application files -> $APP"
mkdir -p "$APP"
cp -r "$SRC/footmop" "$SRC/deploy" "$SRC/tools" "$SRC/docs" "$APP/"
cp "$SRC/config.example.toml" "$SRC/README.md" "$APP/" 2>/dev/null || true
chmod +x "$APP/deploy/"*.sh "$APP/deploy/relay_off.py" "$APP/tools/"*.py
find "$APP" -name '__pycache__' -prune -exec rm -rf {} +

echo "==> Config -> $ETC"
mkdir -p "$ETC"
if [[ ! -f "$ETC/config.toml" ]]; then
    cp "$SRC/config.example.toml" "$ETC/config.toml"
    echo "    created $ETC/config.toml  (EDIT THE ADMIN PIN!)"
else
    echo "    kept existing $ETC/config.toml"
fi
if [[ ! -f "$ETC/footmop.env" ]]; then
    cat > "$ETC/footmop.env" <<EOF
FOOTMOP_DISPLAY_MODE=$MODE
FOOTMOP_CONFIG=$ETC/config.toml
#FOOTMOP_FB=/dev/fb1
EOF
fi
chown root:"$RUN_USER" "$ETC/config.toml" "$ETC/footmop.env"
chmod 640 "$ETC/config.toml" "$ETC/footmop.env"

echo "==> Data and log folders"
install -d -o "$RUN_USER" -g "$RUN_USER" -m 750 /var/lib/footmop /var/log/footmop

echo "==> Groups"
for g in gpio video input render tty; do
    getent group "$g" >/dev/null && usermod -aG "$g" "$RUN_USER" || true
done

echo "==> systemd service"
sed "s/__USER__/$RUN_USER/g" "$SRC/deploy/footmop.service" > /etc/systemd/system/footmop.service
systemctl daemon-reload
systemctl enable footmop.service

if [[ "$MODE" == "linuxfb" ]]; then
    # Nothing else should draw on the panel's console.
    systemctl disable getty@tty1.service 2>/dev/null || true
fi

echo "==> USB flash drive auto-mount (for Admin > Sales report > Download CSV)"
# Raspberry Pi OS Lite does not auto-mount USB sticks. This rule mounts any
# FAT32/exFAT stick at /media/usb-<dev>, writable by the kiosk user.
RUN_UID="$(id -u "$RUN_USER")"; RUN_GID="$(id -g "$RUN_USER")"
cat > /etc/udev/rules.d/99-footmop-usb.rules <<RULES
ACTION=="add", SUBSYSTEMS=="usb", SUBSYSTEM=="block", ENV{ID_FS_TYPE}=="vfat|exfat", RUN{program}+="/usr/bin/systemd-mount --no-block --collect --options=uid=$RUN_UID,gid=$RUN_GID,umask=0022 \$devnode /media/usb-%k"
ACTION=="remove", SUBSYSTEMS=="usb", SUBSYSTEM=="block", RUN{program}+="/usr/bin/systemd-umount /media/usb-%k"
RULES
udevadm control --reload-rules || true

CMDLINE=/boot/firmware/cmdline.txt
if [[ -f $CMDLINE ]] && ! grep -q "consoleblank=0" $CMDLINE; then
    sed -i '1 s/$/ consoleblank=0/' $CMDLINE
    echo "    added consoleblank=0 to $CMDLINE (screen never blanks)"
fi

cat <<EOF

=====================================================================
 Installed. Before starting:

 1. Edit $ETC/config.toml  - set [admin] pin, check [pins], price.
 2. (Optional) keep the servo still at boot: add this line to
    /boot/firmware/config.txt
       gpio=12=op,dl
    If the servo spins the wrong way, set servo_spin_us = 1000 in config.toml.
 3. Make sure the 3.5" screen driver from your screen's vendor is set up
    (see docs/DISPLAY_SETUP.md), then reboot.
 4. Start / check:
       sudo systemctl start footmop
       journalctl -u footmop -f
 5. First run: stock is 0 (SOLD OUT). Quickly press GREEN, BLUE,
    GREEN, BLUE, GREEN, enter the PIN, and set the stock.
=====================================================================
EOF

"""Qt application: wiring between hardware threads, the state machine and
the screens.

Threading model
---------------
* GPIO callback / polling threads, the coin-decoder thread and the dispenser
  thread NEVER touch the Kiosk or any widget. They only emit ``Bridge``
  signals, which are delivered with Qt.QueuedConnection into the GUI thread.
* Touch events are native GUI-thread events.
* Therefore every Kiosk method runs on one thread, in arrival order.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import traceback
from typing import Dict, Optional

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QFrame, QLabel, QStackedWidget, QVBoxLayout,
                             QWidget)

from ..config import Config
from ..core.coin import CoinWorker
from ..core.dispenser import Dispenser
from ..core.state import Btn, Effects, Kiosk, State
from ..core.store import Store
from . import screens as S
from . import theme as T

log = logging.getLogger("footmop.ui")


# --------------------------------------------------------------- systemd
def sd_notify(msg: str) -> None:
    """Minimal sd_notify (no python3-systemd dependency)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError:
        pass


# ---------------------------------------------------------------- bridge
class Bridge(QObject):
    button = pyqtSignal(int, bool)
    coin = pyqtSignal(int, int)
    coin_reject = pyqtSignal(str)
    item = pyqtSignal(int, int)
    done = pyqtSignal(int)
    fault = pyqtSignal(int, str, str)
    dispense_waiting = pyqtSignal(int, bool)


class HardwareEffects(Effects):
    def __init__(self, hal, dispenser: Dispenser):
        self.hal = hal
        self.dispenser = dispenser

    def start_dispense(self, qty: int, token: int) -> None:
        self.dispenser.start(qty, token)

    def set_coin_acceptor(self, enabled: bool) -> None:
        self.hal.set_coin_enable(enabled)

    def force_motor_off(self) -> None:
        self.hal.force_motor_off()


# ---------------------------------------------------------------- input
class BlockPointerInput(QObject):
    """Buttons-only kiosk: swallow every mouse/touch/wheel event so a stray
    touch panel, mouse or cable noise can never change state."""

    BLOCKED = {QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
               QEvent.MouseMove, QEvent.Wheel, QEvent.TouchBegin, QEvent.TouchUpdate,
               QEvent.TouchEnd, QEvent.TouchCancel, QEvent.TabletPress}

    def eventFilter(self, obj, ev) -> bool:
        return ev.type() in self.BLOCKED


# ---------------------------------------------------------------- window
class WarningOverlay(QFrame):
    """Same centred layout as the welcome screen, on a darkened background."""

    def __init__(self, parent: QWidget, ctx: S.Ctx):
        super().__init__(parent)
        self.ctx = ctx
        sc = ctx.sc
        self.setObjectName("warn")
        self.setStyleSheet(f"QFrame#warn {{ background: {T.BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(sc(10), sc(10), sc(10), sc(10))
        lay.setSpacing(sc(2))
        lay.addStretch(1)
        lay.addWidget(T.label(sc, "No button pressed for a while", 17, T.MUTED))
        lay.addWidget(T.label(sc, "Still there?", 38, T.TEXT, True))
        self.count = T.label(sc, "", 26, T.ACCENT, True)
        lay.addWidget(self.count)
        self.credit = T.label(sc, "", 14, "#FF9C9C", True, wrap=True)
        lay.addWidget(self.credit)
        lay.addStretch(1)
        lay.addWidget(T.label(sc, "Press any button to continue", 17, T.TEXT, True))
        lay.addStretch(1)
        self.hide()

    def refresh(self) -> None:
        k = self.ctx.kiosk
        sec = f"Cancelling in {k.warning_seconds_left} s"
        if self.count.text() != sec:
            self.count.setText(sec)
        cr = (f"Your {self.ctx.money(k.credit)} will be forfeited if cancelled" if k.credit else "")
        if self.credit.text() != cr:
            self.credit.setText(cr)
        self.credit.setVisible(bool(cr))


class MainWindow(QWidget):
    def __init__(self, ctx: S.Ctx, mock_hal=None):
        super().__init__()
        self.ctx = ctx
        self.mock = mock_hal
        self.setWindowTitle(ctx.cfg.machine.name)
        self.setObjectName("root")
        self.setStyleSheet(f"QWidget#root {{ background: {T.BG}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.screens: Dict[State, S.Screen] = {
            State.LANDING: S.LandingScreen(ctx),
            State.SOLD_OUT: S.SoldOutScreen(ctx),
            State.QUANTITY: S.QuantityScreen(ctx),
            State.PAYMENT: S.PaymentScreen(ctx),
            State.DISPENSING: S.DispensingScreen(ctx),
            State.SUCCESS: S.SuccessScreen(ctx),
            State.FAULT: S.FaultScreen(ctx),
            State.ADMIN_PIN: S.AdminPinScreen(ctx),
            State.ADMIN: S.AdminScreen(ctx),
        }
        for w in self.screens.values():
            self.stack.addWidget(w)

        self.overlay = WarningOverlay(self, ctx)
        self.toast = QLabel(self)
        self.toast.setFont(T.font(ctx.sc, 13, True))
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.setWordWrap(True)
        self.toast.setStyleSheet(f"background: {T.ACCENT}; color: {T.ACCENT_TEXT};"
                                 f" border-radius: {ctx.sc(8)}px; padding: {ctx.sc(4)}px;")
        self.toast.hide()

        self._shown_state: Optional[State] = None
        ctx.kiosk.admin_nav = self._admin_nav
        ctx.kiosk.listeners.append(self.render)

    def _admin_nav(self, btn: Btn, pressed: bool) -> None:
        self.ctx.kiosk.note_activity()
        w = self.screens.get(self.ctx.kiosk.state)
        if hasattr(w, "nav"):
            w.nav(btn, pressed)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.overlay.setGeometry(self.rect())
        sc = self.ctx.sc
        # Toast sits over the footer hint bar so it never hides the main content.
        self.toast.setGeometry(sc(10), self.height() - sc(58), self.width() - sc(20), sc(50))

    def render(self) -> None:
        k = self.ctx.kiosk
        w = self.screens[k.state]
        if self._shown_state != k.state:
            self._shown_state = k.state
            self.stack.setCurrentWidget(w)
            if hasattr(w, "on_show"):
                w.on_show()
        w.refresh()
        if k.warning:
            self.overlay.refresh()
            self.overlay.setGeometry(self.rect())
            self.overlay.show()
            self.overlay.raise_()
        else:
            self.overlay.hide()
        if k.toast:
            if self.toast.text() != k.toast:
                self.toast.setText(k.toast)
            if not self.toast.isVisible():
                self.toast.show()
                self.toast.raise_()
        elif self.toast.isVisible():
            self.toast.hide()

    # --- keyboard: mock hardware + developer shortcuts
    # GREEN = Left / Up / A,  BLUE = Right / Down / D,  WHITE = Enter / Space / S
    KEYMAP = {Qt.Key_Left: Btn.GREEN, Qt.Key_Up: Btn.GREEN, Qt.Key_A: Btn.GREEN,
              Qt.Key_Right: Btn.BLUE, Qt.Key_Down: Btn.BLUE, Qt.Key_D: Btn.BLUE,
              Qt.Key_Return: Btn.WHITE, Qt.Key_Enter: Btn.WHITE,
              Qt.Key_Space: Btn.WHITE, Qt.Key_S: Btn.WHITE}

    def keyPressEvent(self, ev) -> None:
        if self.mock is None or ev.isAutoRepeat():
            return
        key = ev.key()
        if key in self.KEYMAP:
            self.mock.press(self.KEYMAP[key], True)
        elif key in (Qt.Key_C, Qt.Key_5):
            self.mock.insert_coin()
        elif key == Qt.Key_N:
            self.mock.noise()
        elif key == Qt.Key_J:
            self.mock.toggle_jam()
        elif key == Qt.Key_B:
            self.mock.toggle_blocked()
        elif key == Qt.Key_Escape:
            QApplication.quit()

    def keyReleaseEvent(self, ev) -> None:
        if self.mock is None or ev.isAutoRepeat():
            return
        if ev.key() in self.KEYMAP:
            self.mock.press(self.KEYMAP[ev.key()], False)


# ---------------------------------------------------------------- runtime
class App:
    def __init__(self, cfg: Config, store: Store, hal, mock: bool, screen_size=None):
        self.cfg = cfg
        self.store = store
        self.hal = hal
        self.bridge = Bridge()
        b = self.bridge
        self.dispenser = Dispenser(hal, cfg.dispense,
                                   on_item=lambda t, n: b.item.emit(t, n),
                                   on_done=lambda t: b.done.emit(t),
                                   on_fault=lambda t, c, m: b.fault.emit(t, c, m),
                                   on_waiting_for_clear=lambda t, w: b.dispense_waiting.emit(t, w))
        self.kiosk = Kiosk(cfg, store, HardwareEffects(hal, self.dispenser))
        k = self.kiosk
        Q = Qt.QueuedConnection
        b.button.connect(lambda btn, p: (k.press if p else k.release)(Btn(btn)), Q)
        b.coin.connect(k.on_coin, Q)
        b.coin_reject.connect(k.on_coin_rejected, Q)
        b.item.connect(k.on_item_dropped, Q)
        b.done.connect(k.on_dispense_done, Q)
        b.fault.connect(k.on_dispense_fault, Q)
        b.dispense_waiting.connect(k.on_dispense_waiting_for_clear, Q)
        self.coin_worker = CoinWorker(cfg.coin, on_coin=lambda v, p: b.coin.emit(v, p),
                                      on_reject=lambda r: b.coin_reject.emit(r))

        geo = screen_size or (cfg.display.window_width, cfg.display.window_height)
        self.ctx = S.Ctx(T.Scale(*geo), k, hal)
        self.window = MainWindow(self.ctx, hal if mock else None)

        self.tick = QTimer()
        self.tick.setInterval(200)
        self.tick.timeout.connect(k.tick)
        self.heartbeat = QTimer()
        self.heartbeat.setInterval(5000)
        self.heartbeat.timeout.connect(lambda: sd_notify("WATCHDOG=1"))

    def start(self) -> None:
        self.kiosk.boot()
        self.coin_worker.start()
        self.hal.start(on_button=lambda btn, p: self.bridge.button.emit(int(btn), p),
                       on_coin_edge=self.coin_worker.push_edge)
        self.tick.start()
        self.heartbeat.start()
        sd_notify("READY=1")
        log.info("kiosk started: state=%s stock=%d price=%d", self.kiosk.state.value,
                 self.kiosk.stock, self.kiosk.price)

    def shutdown(self) -> None:
        log.info("shutting down")
        sd_notify("STOPPING=1")
        try:
            self.hal.force_motor_off()
            self.coin_worker.stop()
            self.hal.close()
        finally:
            self.store.close()


def install_crash_guards(get_hal) -> None:
    """PyQt5 aborts the process on an unhandled exception inside a slot.
    Log it, make outputs safe, and keep running instead."""
    def hook(exc_type, exc, tb):
        log.critical("UNHANDLED EXCEPTION:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        hal = get_hal()
        if hal is not None:
            hal.force_motor_off()
    sys.excepthook = hook


def show_fatal(app: QApplication, sc: T.Scale, message: str, fullscreen: bool, seconds: int = 30) -> int:
    """Hardware failed to initialise: show why, then exit non-zero so
    systemd restarts us."""
    w = QWidget()
    w.setStyleSheet(f"background: {T.DANGER_DARK};")
    lay = QVBoxLayout(w)
    lay.addWidget(T.label(sc, "OUT OF SERVICE", 28, T.TEXT, True))
    lay.addWidget(T.label(sc, "Hardware initialisation failed:", 15, T.TEXT))
    lay.addWidget(T.label(sc, message, 12, T.TEXT, wrap=True))
    w.resize(sc(480), sc(320))
    w.showFullScreen() if fullscreen else w.show()
    QTimer.singleShot(seconds * 1000, app.quit)
    app.exec_()
    return 1


def run(cfg: Config, mock: bool, fullscreen: bool, size=None, screenshot_dir: Optional[str] = None) -> int:
    from ..hal import create_hal

    qapp = QApplication(sys.argv[:1])
    if not cfg.display.touch_enabled:
        qapp._pointer_block = BlockPointerInput()      # keep a reference
        qapp.installEventFilter(qapp._pointer_block)
    qapp.setApplicationName("footmop")
    hal_ref = {"hal": None}
    install_crash_guards(lambda: hal_ref["hal"])

    if fullscreen:
        screen = qapp.primaryScreen()
        if screen is not None:
            g = screen.geometry()
            size = (g.width(), g.height())
    size = size or (cfg.display.window_width, cfg.display.window_height)
    if cfg.display.hide_cursor:
        qapp.setOverrideCursor(Qt.BlankCursor)

    store = Store(cfg.storage.db_path, cfg.machine.price_per_item)
    try:
        hal = create_hal(cfg, mock)
    except Exception as exc:
        log.exception("hardware init failed")
        store.close()
        return show_fatal(qapp, T.Scale(*size), str(exc), fullscreen)
    hal_ref["hal"] = hal

    app = App(cfg, store, hal, mock, size)
    qapp.aboutToQuit.connect(app.shutdown)

    def _sig(*_):
        log.info("signal received, quitting")
        qapp.quit()
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    win = app.window
    if fullscreen:
        win.showFullScreen()
    else:
        win.setFixedSize(*size)
        win.show()
    win.activateWindow()
    win.setFocus()
    app.start()

    if screenshot_dir:  # used by the automated UI walkthrough
        from .walkthrough import Walkthrough
        Walkthrough(app, screenshot_dir).start()

    return qapp.exec_()

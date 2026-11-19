"""All kiosk screens - designed for THREE PHYSICAL BUTTONS, no touch.

Every screen ends with a legend bar whose three keycaps sit in the same
left/centre/right order and colours as the real GREEN / WHITE / BLUE buttons,
so the customer always sees what each button does right now.

Screens only *read* the Kiosk; all input goes through Kiosk.press/release.
To keep a slow SPI panel smooth, widgets are only restyled when their
content actually changes.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.state import Btn, Kiosk
from . import theme as T

Legend = Optional[str]


class Ctx:
    def __init__(self, sc: T.Scale, kiosk: Kiosk, hal):
        self.sc = sc
        self.kiosk = kiosk
        self.cfg = kiosk.cfg
        self.hal = hal

    def money(self, n: int) -> str:
        return f"{self.cfg.machine.currency}{n:,}"


def _set(lbl: QLabel, text: str) -> None:
    if lbl.text() != text:
        lbl.setText(text)


# ============================================================ building blocks
class Header(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        sc = ctx.sc
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.status = T.label(sc, "", 12, T.MUTED, align=Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.status, 1)
        self.setFixedHeight(sc(24))


class LegendBar(QWidget):
    """Non-interactive keycaps mirroring the physical buttons."""

    SPEC = ((Btn.GREEN, T.GREEN, T.TEXT), (Btn.WHITE, T.WHITE, "#10202F"),
            (Btn.BLUE, T.BLUE, T.TEXT))

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.sc = ctx.sc
        sc = self.sc
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(sc(8))
        self.caps = {}
        self._cur = {}
        for btn, bg, fg in self.SPEC:
            lb = QLabel()
            lb.setAlignment(Qt.AlignCenter)
            lb.setFont(T.font(sc, 16, True))
            lay.addWidget(lb, 1)
            self.caps[btn] = (lb, bg, fg)
            self._cur[btn] = object()
        self.setFixedHeight(sc(44))
        self.set()

    def set(self, green: Legend = None, white: Legend = None, blue: Legend = None) -> None:
        sc = self.sc
        for btn, text in ((Btn.GREEN, green), (Btn.WHITE, white), (Btn.BLUE, blue)):
            if self._cur[btn] == text:
                continue
            self._cur[btn] = text
            lb, bg, fg = self.caps[btn]
            r = sc(12)
            if text:
                lb.setText(text)
                # thick bottom edge makes it read as a physical key
                lb.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {r}px;"
                                 f" border-bottom: {sc(4)}px solid rgba(0,0,0,90);")
            else:
                lb.setText("")
                lb.setStyleSheet("background: transparent; border: none;")


class CoinRow(QWidget):
    """One circle per coin needed: filled = inserted, outline = still needed."""

    MAX_DOTS = 12

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.paid = 0
        self.total = 0
        self.setFixedHeight(ctx.sc(46))

    def set(self, paid: int, total: int) -> None:
        if (paid, total) != (self.paid, self.total):
            self.paid, self.total = paid, total
            self.update()

    def paintEvent(self, ev) -> None:
        if self.total <= 0:
            return
        sc = self.ctx.sc
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self.total > self.MAX_DOTS:   # too many coins for dots: draw a bar
            bh = sc(16)
            y = (h - bh) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.PANEL))
            p.drawRoundedRect(QRectF(0, y, w, bh), bh / 2, bh / 2)
            p.setBrush(QColor(T.SUCCESS))
            p.drawRoundedRect(QRectF(0, y, w * min(1, self.paid / self.total), bh), bh / 2, bh / 2)
            return
        gap = sc(6)
        d = min(sc(40), (w - gap * (self.total - 1)) / self.total)
        x = (w - (self.total * d + (self.total - 1) * gap)) / 2
        y = (h - d) / 2
        f = T.font(sc, 1)
        f.setPixelSize(max(8, int(d * 0.32)))
        f.setBold(True)
        p.setFont(f)
        label = f"{self.ctx.cfg.machine.currency}{self.ctx.cfg.coin.value}"
        for i in range(self.total):
            r = QRectF(x + i * (d + gap), y, d, d)
            if i < self.paid:
                p.setPen(QPen(QColor("#C78F00"), sc(2)))
                p.setBrush(QColor(T.ACCENT))
                p.drawEllipse(r)
                p.setPen(QColor(T.ACCENT_TEXT))
            else:
                p.setPen(QPen(QColor(T.MUTED), sc(2), Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(r)
                p.setPen(QColor(T.MUTED))
            p.drawText(r, Qt.AlignCenter, label)


class Screen(QWidget):
    show_header = False        # customer screens stay clean; admin shows a status line

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        sc = ctx.sc
        root = QVBoxLayout(self)
        root.setContentsMargins(sc(10), sc(4), sc(10), sc(8))
        root.setSpacing(sc(4))
        self.header = Header(ctx)
        root.addWidget(self.header)
        self.header.setVisible(self.show_header)
        self.body = QVBoxLayout()
        self.body.setSpacing(sc(2))
        root.addLayout(self.body, 1)
        self.legend = LegendBar(ctx)
        root.addWidget(self.legend)

    def refresh(self) -> None:  # pragma: no cover
        pass


def card(sc: T.Scale, color: str) -> Tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setStyleSheet(f"QFrame {{ background: {color}; border-radius: {sc(12)}px; }}")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(sc(10), sc(6), sc(10), sc(6))
    lay.setSpacing(sc(2))
    return f, lay


# ================================================================= customer
class Hero:
    """The welcome-screen layout, shared by every customer screen so they all
    look alike - a vertically centred stack of:

        kicker   small, muted         ("Welcome to")
        main     big, bold, white     ("Footmop ni Kuya J")
        accent   bold, yellow         ("P5 each")
        sub      small, muted         ("6 available")
        cta      bold, white          ("Press WHITE to buy")
    """

    def __init__(self, screen: "Screen", main: Optional[QWidget] = None,
                 extra: Optional[QWidget] = None, main_px: int = 38):
        sc = screen.ctx.sc
        b = screen.body
        b.addStretch(1)
        self.kicker = T.label(sc, "", 17, T.MUTED)
        b.addWidget(self.kicker)
        if main is None:
            self.main = T.label(sc, "", main_px, T.TEXT, True)
            b.addWidget(self.main)
        else:
            b.addWidget(main)
        self.accent = T.label(sc, "", 26, T.ACCENT, True)
        b.addWidget(self.accent)
        if extra is not None:
            b.addWidget(extra)
        self.sub = T.label(sc, "", 14, T.MUTED, wrap=True)
        b.addWidget(self.sub)
        b.addStretch(1)
        self.cta = T.label(sc, "", 17, T.TEXT, True, wrap=True)
        b.addWidget(self.cta)
        b.addStretch(1)

    def set(self, kicker: str = "", main: Optional[str] = None, accent: str = "",
            sub: str = "", cta: str = "") -> None:
        for lbl, txt in ((self.kicker, kicker), (self.accent, accent), (self.sub, sub),
                         (self.cta, cta)):
            _set(lbl, txt)
            lbl.setVisible(bool(txt))
        if main is not None and hasattr(self, "main"):
            _set(self.main, main)


class LandingScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.hero = Hero(self)
        self.legend.set(white="BUY")

    def refresh(self) -> None:
        k = self.ctx.kiosk
        self.hero.set("Welcome to", self.ctx.cfg.machine.name, f"{self.ctx.money(k.price)} each",
                      f"{k.stock} available", "Press WHITE to buy")


class SoldOutScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.hero = Hero(self)

    def refresh(self) -> None:
        self.hero.set("Sorry", "Sold Out", "Please come back later",
                      "No footmops left right now", "Please don't insert coins")


class QuantityScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        sc = ctx.sc
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.left = T.label(sc, "\u25c0", 30, T.GREEN, True)
        self.qty = T.label(sc, "1", 56, T.TEXT, True)
        self.right = T.label(sc, "\u25b6", 30, T.BLUE, True)
        rl.addStretch(1)
        rl.addWidget(self.left)
        rl.addSpacing(sc(28))
        rl.addWidget(self.qty)
        rl.addSpacing(sc(28))
        rl.addWidget(self.right)
        rl.addStretch(1)
        self.hero = Hero(self, main=row)

    def refresh(self) -> None:
        k, m = self.ctx.kiosk, self.ctx.money
        coins = -(-k.amount_due // self.ctx.cfg.coin.value)
        _set(self.qty, str(k.qty))
        can_less = k.qty > k.min_qty
        can_more = k.qty < k.max_qty
        arrows = (can_less, can_more)
        if getattr(self, "_arrows", None) != arrows:
            self._arrows = arrows
            self.left.setStyleSheet(f"color: {T.GREEN if can_less else 'transparent'}; background: transparent;")
            self.right.setStyleSheet(f"color: {T.BLUE if can_more else 'transparent'}; background: transparent;")
        sub = (f"Paid {m(k.credit)} so far" if k.credit else f"{k.max_qty} available")
        self.hero.set("How many footmops?", None,
                      f"Total {m(k.amount_due)}",
                      f"{coins} coin{'s' if coins != 1 else ''}  \u2022  {sub}",
                      "Press WHITE to confirm")
        if k.qty <= 1 and k.credit == 0:
            green = "Cancel"
        else:
            green = "\u2212 Less" if can_less else None
        self.legend.set(green=green, white="OK", blue="More +" if can_more else None)


class PaymentScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.coins = CoinRow(ctx)
        self.hero = Hero(self, extra=self.coins)
        self.legend.set(green="\u2190 Back")

    def refresh(self) -> None:
        k, m = self.ctx.kiosk, self.ctx.money
        need = k.coins_needed
        self.coins.set(k.coins_paid, k.coins_paid + need)
        self.hero.set(f"Insert {m(self.ctx.cfg.coin.value)} coins",
                      f"{m(k.credit)} / {m(k.amount_due)}",
                      f"{need} more coin{'s' if need != 1 else ''}",
                      f"{k.qty} footmop{'s' if k.qty != 1 else ''} \u00d7 {m(k.price)}",
                      "Exact amount only  \u2022  No change given")


class DispensingScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.hero = Hero(self)

    def refresh(self) -> None:
        k = self.ctx.kiosk
        if k.dispense_waiting_for_clear:
            self.hero.set("Please wait", "Waiting for chute to clear",
                          f"{k.items_done} of {k.qty} dispensed",
                          "Next item starts when the sensor clears", "")
        else:
            self.hero.set("Please wait", "Dispensing\u2026",
                          f"{min(k.qty, k.items_done + 1)} of {k.qty}",
                          "Don't reach into the chute yet", "")


class SuccessScreen(Screen):
    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.hero = Hero(self)
        self.legend.set(white="Done")

    def refresh(self) -> None:
        k = self.ctx.kiosk
        n = k.qty
        over = f"Overpaid {self.ctx.money(k.overpaid)} - no change given  \u2022  " if k.overpaid else ""
        self.hero.set("Thank you!", "Salamat po!",
                      f"Take your {n} footmop{'s' if n != 1 else ''} below",
                      f"{over}Back to start in {k.success_seconds_left}s",
                      "Press WHITE when done")


class FaultScreen(Screen):
    TITLES = {"dispense_failure": "Dispense Failure",
              "chute_blocked": "Chute Blocked",
              "dispense_timeout": "Dispenser Not Responding",
              "power_interrupted": "Power Interrupted",
              "dispenser_exception": "Dispenser Fault"}

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        self.setObjectName("fault")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#fault {{ background: {T.DANGER_DARK}; }}")
        self.hero = Hero(self, main_px=34)
        self.hero.kicker.setStyleSheet("color: #FFC9C9; background: transparent;")
        self.hero.sub.setStyleSheet("color: #FFC9C9; background: transparent;")
        self.legend.hide()

    def refresh(self) -> None:
        f = self.ctx.kiosk.store.active_fault()
        if not f:
            self.hero.set("Sorry", "OUT OF SERVICE", "Error", "", "Please contact the owner")
            return
        owed = f"  \u2022  Items owed: {f['owed_items']}" if f["owed_items"] else ""
        self.hero.set("Sorry", "OUT OF SERVICE",
                      "Error: " + self.TITLES.get(f["code"], f["code"]),
                      f"Code {f['code']}  \u2022  {f['ts'][:16].replace('T', ' ')}{owed}",
                      "Please contact the owner  \u2022  Don't insert coins")


# ==================================================================== admin
CANCEL = "✕"          # the extra "digit" that means cancel / exit


class DigitEntry(QWidget):
    """Enter a number one digit at a time with short presses only.
    GREEN = digit -1, BLUE = digit +1, WHITE = next digit (done after the
    last). Each digit cycles 0-9 and then CANCEL; WHITE on CANCEL cancels."""

    def __init__(self, ctx: Ctx, length: int):
        super().__init__()
        self.ctx = ctx
        sc = ctx.sc
        self.length = length
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(sc(10))
        row.addStretch(1)
        self.boxes: List[QLabel] = []
        for _ in range(length):
            b = QLabel()
            b.setAlignment(Qt.AlignCenter)
            b.setFont(T.font(sc, 34, True))
            b.setFixedSize(sc(56), sc(70))
            row.addWidget(b)
            self.boxes.append(b)
        row.addStretch(1)
        self._styles: List[Optional[str]] = [None] * length
        self.reset()

    def reset(self, value: Optional[int] = None, secret: bool = False) -> None:
        self.secret = secret
        text = "0" * self.length if value is None else str(max(0, value)).zfill(self.length)[-self.length:]
        self.digits = [int(c) for c in text]          # 10 == CANCEL
        self.pos = 0
        self.paint()

    def press(self, btn: Btn):
        """Returns None, ("done", "0123") or ("cancel",)."""
        if btn in (Btn.GREEN, Btn.BLUE):
            d = -1 if btn == Btn.GREEN else 1
            self.digits[self.pos] = (self.digits[self.pos] + d) % 11
        elif btn == Btn.WHITE:
            if self.digits[self.pos] == 10:
                return ("cancel",)
            if self.pos < self.length - 1:
                self.pos += 1
            else:
                return ("done", "".join(map(str, self.digits)))
        self.paint()
        return None

    @property
    def on_last(self) -> bool:
        return self.pos == self.length - 1

    @property
    def on_cancel(self) -> bool:
        return self.digits[self.pos] == 10

    def paint(self) -> None:
        sc = self.ctx.sc
        for i, b in enumerate(self.boxes):
            active = i == self.pos
            d = self.digits[i]
            if active:
                txt = CANCEL if d == 10 else str(d)
            elif self.secret:
                txt = "•" if i < self.pos else ""
            else:
                txt = str(d) if d != 10 else "0"
            _set(b, txt)
            cancel = active and d == 10
            style = (f"background: {T.DANGER if cancel else (T.PANEL_HI if active else T.PANEL)};"
                     f" color: {T.TEXT}; border-radius: {sc(10)}px; border: {sc(3)}px solid "
                     f"{T.ACCENT if active else 'transparent'};")
            if self._styles[i] != style:
                self._styles[i] = style
                b.setStyleSheet(style)

    def legend(self, bar: "LegendBar", done_label: str) -> None:
        white = "Cancel" if self.on_cancel else (done_label if self.on_last else "Next →")
        bar.set(green="− 1", white=white, blue="+ 1")


class AdminPinScreen(Screen):
    show_header = True

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        sc = ctx.sc
        self.body.addWidget(T.label(sc, "Admin PIN", 20, T.TEXT, True))
        self.entry = DigitEntry(ctx, len(str(ctx.cfg.admin.pin)))
        self.body.addWidget(self.entry, 1)
        self.msg = T.label(sc, "", 14, T.ACCENT, True)
        self.body.addWidget(self.msg)
        self.back_hint = T.label(sc, "", 12, T.MUTED)
        self.body.addWidget(self.back_hint)

    def on_show(self) -> None:
        self.entry.reset(secret=True)
        _set(self.msg, "")

    def nav(self, btn: Btn, pressed: bool) -> None:
        if not pressed:
            return
        k = self.ctx.kiosk
        res = self.entry.press(btn)
        if res and res[0] == "cancel":
            k.exit_admin()
            return
        if res and res[0] == "done":
            if k.submit_pin(res[1]):
                return
            self.entry.reset(secret=True)
            _set(self.msg, f"Locked - wait {k.pin_locked_seconds}s"
                 if k.pin_locked_seconds else "Wrong PIN - try again")
        elif btn != Btn.WHITE:
            _set(self.msg, "")
        self.refresh()

    def refresh(self) -> None:
        k = self.ctx.kiosk
        if k.pin_locked_seconds:
            _set(self.msg, f"Locked - wait {k.pin_locked_seconds}s")
        _set(self.back_hint, f"Back to start in {k.admin_pin_seconds_left}s if no button is pressed"
                             f"  \u2022  or choose {CANCEL} + WHITE")
        self.entry.paint()
        self.entry.legend(self.legend, "Enter")
        _set(self.header.status, "ADMIN")


class ListBox(QWidget):
    """Vertical option list; the highlighted row is what WHITE selects."""

    def __init__(self, ctx: Ctx, row_px: int = 30):
        super().__init__()
        self.ctx = ctx
        self.row_px = row_px
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(ctx.sc(3))
        self.rows: List[QLabel] = []
        self._styles: List[Optional[str]] = []
        self.index = 0
        self.count = 0

    def set_items(self, labels: List[str]) -> None:
        sc = self.ctx.sc
        while len(self.rows) < len(labels):
            lb = QLabel()
            lb.setFont(T.font(sc, 15, True))
            lb.setFixedHeight(sc(self.row_px))
            self.lay.addWidget(lb)
            self.rows.append(lb)
            self._styles.append(None)
        for i, lb in enumerate(self.rows):
            lb.setVisible(i < len(labels))
            if i < len(labels):
                _set(lb, labels[i])
        self.count = len(labels)
        self.index = min(self.index, max(0, self.count - 1))
        self.paint()

    def move(self, d: int) -> None:
        if self.count:
            self.index = (self.index + d) % self.count
            self.paint()

    def paint(self) -> None:
        sc = self.ctx.sc
        for i, lb in enumerate(self.rows):
            on = i == self.index
            style = (f"background: {T.ACCENT if on else T.PANEL}; color: {T.ACCENT_TEXT if on else T.TEXT};"
                     f" border-radius: {sc(8)}px; padding-left: {sc(12)}px;")
            if self._styles[i] != style:
                self._styles[i] = style
                lb.setStyleSheet(style)


class AdminScreen(Screen):
    """Admin pages, all operated with single short presses:
      list pages    GREEN up / BLUE down / WHITE select
      stock, price  digit entry: GREEN -1 / BLUE +1 / WHITE next digit, save after the last
    """

    show_header = True
    TITLES = {"menu": "Admin menu", "stock": "Set stock", "price": "Set price per footmop",
              "faults": "Faults", "hw": "Hardware test", "sales": "Sales report"}

    def __init__(self, ctx: Ctx):
        super().__init__(ctx)
        sc = ctx.sc
        self.title = T.label(sc, "", 15, T.TEXT, True, align=Qt.AlignLeft | Qt.AlignVCenter)
        self.body.addWidget(self.title)
        self.info = T.label(sc, "", 12, T.TEXT, align=Qt.AlignLeft | Qt.AlignTop, wrap=True)
        self.body.addWidget(self.info, 1)
        self.stock_entry = DigitEntry(ctx, 4)
        self.price_entry = DigitEntry(ctx, 3)
        self.body.addWidget(self.stock_entry, 2)
        self.body.addWidget(self.price_entry, 2)
        self.list = ListBox(ctx)
        self.body.addWidget(self.list)
        self.msg = T.label(sc, "", 12, T.ACCENT, True, wrap=True)
        self.msg.setMinimumHeight(sc(16))
        self.body.addWidget(self.msg)
        self.page = "menu"
        self.live = QTimer(self)
        self.live.setInterval(300)
        self.live.timeout.connect(self.refresh)

    # ------------------------------------------------------------ pages
    def page_items(self) -> List[Tuple[str, Callable[[], None]]]:
        k = self.ctx.kiosk
        back = ("← Back", lambda: self.go("menu"))
        if self.page == "menu":
            nf = len(k.store.active_faults())
            return [("Stock / Restock", lambda: self.go("stock")),
                    ("Price", lambda: self.go("price")),
                    (f"Faults ({nf})" if nf else "Faults (none)", lambda: self.go("faults")),
                    ("Test hardware", lambda: self.go("hw")),
                    ("Sales report", lambda: self.go("sales")),
                    ("Exit admin", k.exit_admin)]
        if self.page == "faults":
            return ([("Clear all faults", k.admin_clear_faults)] if k.store.active_faults() else []) + [back]
        if self.page == "hw":
            return [("Test dispense 1 item", k.admin_test_dispense), back]
        if self.page == "sales":
            return [("Download CSV to USB drive", k.admin_export_csv), back]
        return [back]

    def go(self, page: str, keep_message: bool = False) -> None:
        k = self.ctx.kiosk
        if not keep_message:
            k.admin_message = ""
        self.page = page
        if page == "stock":
            self.stock_entry.reset(k.stock)
        elif page == "price":
            self.price_entry.reset(k.price)
        self.list.index = 0
        (self.live.start if page == "hw" else self.live.stop)()
        self.refresh()

    def on_show(self) -> None:
        self.go("menu")

    # ------------------------------------------------------------ input
    def nav(self, btn: Btn, pressed: bool) -> None:
        if not pressed:
            return
        k = self.ctx.kiosk
        if self.page in ("stock", "price"):
            entry = self.stock_entry if self.page == "stock" else self.price_entry
            res = entry.press(btn)
            if res and res[0] == "cancel":
                k.admin_message = "Not changed"
                self.go("menu", keep_message=True)
                return
            if res and res[0] == "done":
                value = int(res[1])
                if self.page == "stock":
                    k.admin_set_stock(value)
                else:
                    k.admin_set_price(value)
                self.go("menu", keep_message=True)
                return
            self.refresh()
            return
        if btn == Btn.WHITE:
            items = self.page_items()
            if items:
                items[min(self.list.index, len(items) - 1)][1]()
                if k.state.value == "ADMIN":
                    self.refresh()
        elif btn == Btn.GREEN:
            self.list.move(-1)
        elif btn == Btn.BLUE:
            self.list.move(1)

    # ----------------------------------------------------------- render
    def refresh(self) -> None:
        k, m = self.ctx.kiosk, self.ctx.money
        _set(self.header.status, f"ADMIN • Stock {k.stock} • {m(k.price)}")
        p = self.page
        value_page = p in ("stock", "price")
        self.stock_entry.setVisible(p == "stock")
        self.price_entry.setVisible(p == "price")
        self.list.setVisible(not value_page)
        _set(self.title, self.TITLES[p])

        if value_page:
            entry = self.stock_entry if p == "stock" else self.price_entry
            cur = str(k.stock) if p == "stock" else m(k.price)
            extra = "" if p == "stock" else f"  (must be a multiple of {m(self.ctx.cfg.coin.value)})"
            self.info.setVisible(True)
            _set(self.info, f"Current: {cur}{extra}\nSet each digit, WHITE moves to the next. "
                            f"Choose {CANCEL} + WHITE to cancel.")
            _set(self.msg, k.admin_message)
            entry.paint()
            entry.legend(self.legend, "Save")
            return

        text = ""
        if p == "faults":
            fs = k.store.active_faults()
            text = "No active faults." if not fs else "\n".join(
                f"[{f['ts'][5:16].replace('T', ' ')}] {f['code']}"
                + (f" (owed {f['owed_items']})" if f["owed_items"] else "")
                + f": {f['message'][:90]}" for f in fs[:2])
        elif p == "hw":
            try:
                lv = self.ctx.hal.read_inputs()
            except Exception as e:  # pragma: no cover
                lv = {"error": str(e)}
            sensor = "BLOCKED" if self.ctx.hal.drop_sensor_active() else "clear"
            text = ("Levels: " + "  ".join(f"{n}={v}" for n, v in lv.items())
                    + f"\nDrop sensor: {sensor}\n" + "\n".join(k.admin_live[-3:]))
        elif p == "sales":
            s = k.store.sales_summary()
            t, a = s["today"], s["all_time"]
            text = (f"Today: {t['orders']} orders, {t['items']} items, {m(t['cash'])}\n"
                    f"All time: {a['orders']} orders, {a['items']} items, {m(a['cash'])}\n"
                    f"Abandoned credit: {m(s['abandoned_credit'])}    "
                    f"Unclaimed coins: {m(s['unclaimed_coins'])}\n"
                    f"Items owed to customers: {s['owed_items']}")
        self.info.setVisible(bool(text))
        _set(self.info, text)
        _set(self.msg, k.admin_message or ('Choose "Exit admin" to leave' if p == "menu" else ""))
        self.list.set_items([label for label, _ in self.page_items()])
        self.legend.set(green="▲ Up", white="Select", blue="▼ Down")

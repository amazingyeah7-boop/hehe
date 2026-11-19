"""Colours, scaling and small widget helpers.

Everything is designed at 480x320 (the common 3.5" XPT2046 resolution) and
multiplied by ``Scale`` so 800x480 / 1024x600 panels look identical, just
bigger. High contrast and large type because cheap TFT panels have poor
viewing angles.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLabel, QPushButton, QSizePolicy

BG = "#0B2540"
PANEL = "#143A63"
PANEL_HI = "#1D4C80"
TEXT = "#FFFFFF"
MUTED = "#A8C2DE"
ACCENT = "#FFC233"
ACCENT_TEXT = "#231700"
GREEN = "#17A34A"
BLUE = "#2F7BEA"
WHITE = "#F3F5F8"
DANGER = "#C0262D"
DANGER_DARK = "#7A1015"
SUCCESS = "#1E9E5A"
FONT_FAMILY = "DejaVu Sans"

DESIGN_W, DESIGN_H = 480, 320


class Scale:
    def __init__(self, width: int, height: int):
        self.s = min(width / DESIGN_W, height / DESIGN_H)

    def __call__(self, n: float) -> int:
        return max(1, int(round(n * self.s)))


def font(sc: Scale, px: float, bold: bool = False) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(sc(px))
    f.setBold(bold)
    return f


def label(sc: Scale, text: str = "", px: float = 16, color: str = TEXT, bold: bool = False,
          align=Qt.AlignCenter, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(sc, px, bold))
    lb.setAlignment(align)
    lb.setWordWrap(wrap)
    lb.setStyleSheet(f"color: {color}; background: transparent;")
    lb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return lb


def button(sc: Scale, text: str, bg: str, fg: str, px: float = 18, radius: float = 10,
           min_h: float = 44) -> QPushButton:
    """Touch-friendly button (>= 44 design px tall)."""
    b = QPushButton(text)
    b.setFont(font(sc, px, True))
    b.setFocusPolicy(Qt.NoFocus)   # focus is driven by our own nav, not Qt's
    b.setMinimumHeight(sc(min_h))
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    style_button(sc, b, bg, fg, radius)
    return b


def style_button(sc: Scale, b: QPushButton, bg: str, fg: str, radius: float = 10) -> None:
    r = sc(radius)
    b.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {fg}; border: none; border-radius: {r}px;"
        f" padding: {sc(4)}px {sc(8)}px; }}"
        f"QPushButton:pressed {{ background: {PANEL_HI}; color: {TEXT}; }}"
        f"QPushButton[nav=\"true\"] {{ border: {sc(3)}px solid {ACCENT}; }}"
        f"QPushButton:disabled {{ background: #2A4566; color: #6F87A3; }}")

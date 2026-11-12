"""Floating dictation indicator at the bottom of the screen. Never takes focus."""
from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .native import make_overlay

MESSAGES = {
    "listening": "",
    "handsfree": "Druk op fn om te stoppen",
    "transcribing": "Omzetten…",
    "cleaning": "Opschonen…",
    "empty": "Niets gehoord",
    "clipboard": "Staat op je klembord",
    "error": "Dicteren lukte niet",
}


class DictationHUD(QWidget):
    W, H = 200, 40

    def __init__(self, level_fn: Callable[[], float]):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.level_fn = level_fn
        f = QFont(self.font())
        f.setPointSizeF(12.5)
        f.setWeight(QFont.Weight.Medium)
        self.setFont(f)
        self.state = "listening"
        self.levels = [0.0] * 14
        self._phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_state(self, state: str, auto_hide_ms: int = 0) -> None:
        self.state = state
        text = MESSAGES.get(state, state)
        bars = 4 * 7 + 8 if state == "handsfree" else 0
        width = self.W if not text else max(
            120, 36 + bars + self.fontMetrics().horizontalAdvance(text) + 20)
        self.setFixedSize(width, self.H)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - width // 2, screen.bottom() - self.H - 28)
        self.hide_timer.stop()
        if auto_hide_ms:
            self.hide_timer.start(auto_hide_ms)
        if not self.isVisible():
            self.levels = [0.0] * len(self.levels)
            self.show()
        make_overlay(self, click_through=True)
        self.timer.start(40)
        self.update()

    def hideEvent(self, e):  # noqa: N802
        self.timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._phase += 0.25
        if self.state in ("listening", "handsfree"):
            lv = min(1.0, math.sqrt(max(0.0, self.level_fn())) * 6)
            self.levels = self.levels[1:] + [lv]
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(r, self.H / 2, self.H / 2)
        p.fillPath(path, QColor(22, 24, 29, 235))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)

        cy = self.H / 2
        recording = self.state in ("listening", "handsfree")
        # Status dot: red while listening, pulsing blue while working.
        if recording:
            dot = QColor("#F2555A")
        elif self.state in ("transcribing", "cleaning"):
            a = 140 + int(100 * (0.5 + 0.5 * math.sin(self._phase)))
            dot = QColor(138, 166, 240, a)
        else:
            dot = QColor(160, 168, 184)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QRectF(16, cy - 5, 10, 10))

        text = MESSAGES.get(self.state, self.state)
        x0 = 36
        if recording:
            # Level bars
            n = len(self.levels)
            bw, gap = 4, 3
            n_fit = 4 if text else min(n, int((self.width() - x0 - 18 + gap) // (bw + gap)))
            p.setBrush(QColor(255, 255, 255, 220))
            for i, lv in enumerate(self.levels[-n_fit:]):
                h = 4 + lv * (self.H - 18)
                p.drawRoundedRect(QRectF(x0 + i * (bw + gap), cy - h / 2, bw, h), 2, 2)
            x0 += n_fit * (bw + gap) + 8
        if text:
            p.setPen(QColor(235, 237, 241))
            p.setFont(self.font())
            p.drawText(QRectF(x0, 0, self.width() - x0 - 16, self.H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.end()

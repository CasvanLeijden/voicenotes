"""Small floating prompt in the top-right corner (does not steal focus)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .native import make_overlay

LIGHT = dict(bg="#FFFFFF", ink="#1B2130", muted="#687083", line="#DDE1E8", accent="#E5484D",
             ghost="#F0F2F5")
DARK = dict(bg="#23262D", ink="#E7E9ED", muted="#8C93A3", line="#353A44", accent="#F2555A",
            ghost="#2E323A")


class CallPrompt(QWidget):
    def __init__(self, title: str, text: str, primary: str, secondary: str,
                 on_primary: Callable[[], None], on_secondary: Callable[[], None] | None = None,
                 dark: bool = False, timeout_ms: int = 30_000):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        c = DARK if dark else LIGHT
        self._on_primary, self._on_secondary = on_primary, on_secondary

        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(f"""
            #card {{ background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 12px; }}
            QLabel#title {{ color: {c['ink']}; font-size: 14px; font-weight: 600; }}
            QLabel#text {{ color: {c['muted']}; font-size: 12px; }}
            QPushButton {{ border-radius: 7px; padding: 6px 14px; font-size: 13px; font-weight: 500; }}
            QPushButton#primary {{ background: {c['accent']}; color: white; border: 0; }}
            QPushButton#secondary {{ background: {c['ghost']}; color: {c['ink']}; border: 0; }}
        """)
        t = QLabel(title, objectName="title")
        body = QLabel(text, objectName="text")
        body.setWordWrap(True)
        yes = QPushButton(primary, objectName="primary")
        no = QPushButton(secondary, objectName="secondary")
        yes.clicked.connect(self._yes)
        no.clicked.connect(self._no)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(no)
        buttons.addWidget(yes)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        lay.addWidget(t)
        lay.addWidget(body)
        lay.addSpacing(4)
        lay.addLayout(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.setFixedWidth(340)
        self.adjustSize()

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.top() + 16)
        QTimer.singleShot(timeout_ms, self.close)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        make_overlay(self)

    def _yes(self) -> None:
        self.close()
        self._on_primary()

    def _no(self) -> None:
        self.close()
        if self._on_secondary:
            self._on_secondary()

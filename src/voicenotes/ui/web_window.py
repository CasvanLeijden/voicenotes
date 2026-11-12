"""Window hosting the HTML interface."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        print(f"[web] {message} ({Path(source).name}:{line})")

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
        # Open external links in the default browser instead of inside the app.
        if url.scheme() in ("http", "https"):
            import webbrowser
            webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class WebWindow(QMainWindow):
    def __init__(self, bridge, on_close):
        super().__init__()
        self.setWindowTitle("Voicenotes")
        self.resize(1180, 760)
        self.setMinimumSize(820, 520)
        self._on_close = on_close
        self.view = QWebEngineView(self)
        self.page = _Page(self.view)
        self.page.setBackgroundColor(QColor("#F5F6F8"))
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("vn", bridge)
        self.page.setWebChannel(self.channel)
        self.view.setUrl(QUrl.fromLocalFile(str(WEB_DIR / "index.html")))
        self.setCentralWidget(self.view)

    def set_dark(self, dark: bool) -> None:
        self.page.setBackgroundColor(QColor("#16181D" if dark else "#F5F6F8"))
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != "cocoa":
            return  # winId is only an NSView on the native macOS platform
        try:  # tint the native title bar to match the page
            import objc
            from AppKit import NSAppearance, NSColor
            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            win = ns_view.window()
            win.setTitlebarAppearsTransparent_(True)
            win.setAppearance_(NSAppearance.appearanceNamed_(
                "NSAppearanceNameDarkAqua" if dark else "NSAppearanceNameAqua"))
            c = (0x16, 0x18, 0x1D) if dark else (0xF5, 0xF6, 0xF8)
            win.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                c[0] / 255, c[1] / 255, c[2] / 255, 1.0))
        except Exception:
            pass

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self._on_close()

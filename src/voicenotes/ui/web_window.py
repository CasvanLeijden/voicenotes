"""Window hosting the HTML interface: frosted glass over the desktop, no visible title bar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from .native import set_hidden_from_capture

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
        self._effect = None
        self._effect_kind = None
        # Transparent all the way down, so the macOS blur behind the window shows through.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent")
        self.page = _Page(self.view)
        self.page.setBackgroundColor(Qt.GlobalColor.transparent)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("vn", bridge)
        self.page.setWebChannel(self.channel)
        self.view.setUrl(QUrl.fromLocalFile(str(WEB_DIR / "index.html")))
        self.setCentralWidget(self.view)

    def _ns_window(self):
        if QGuiApplication.platformName() != "cocoa":
            return None  # winId is only an NSView on the native macOS platform
        import objc
        return objc.objc_object(c_void_p=int(self.winId())).window()

    def apply_chrome(self, dark: bool, hide_from_capture: bool, glass: str = "clear") -> None:
        """Glass background, title bar merged into the content, appearance.
        glass: "clear" = Liquid Glass (macOS 26), the desktop shines through;
               "frosted" = classic blurred vibrancy."""
        try:
            win = self._ns_window()
            if win is None:
                return
            import AppKit as A
            win.setStyleMask_(win.styleMask() | A.NSWindowStyleMaskFullSizeContentView)
            win.setTitlebarAppearsTransparent_(True)
            win.setTitleVisibility_(A.NSWindowTitleHidden)
            win.setMovableByWindowBackground_(False)
            win.setAppearance_(A.NSAppearance.appearanceNamed_(
                "NSAppearanceNameDarkAqua" if dark else "NSAppearanceNameAqua"))
            win.setOpaque_(False)
            win.setBackgroundColor_(A.NSColor.clearColor())
            self._clear_titlebar(win, A)
            use_liquid = glass == "clear" and hasattr(A, "NSGlassEffectView")
            kind = "liquid" if use_liquid else "frosted"
            if self._effect is not None and self._effect_kind != kind:
                self._effect.removeFromSuperview()
                self._effect = None
            if self._effect is None:
                # The glass sits behind Qt's content view, inside the window frame.
                content = win.contentView()
                frame = content.superview()
                if use_liquid:
                    effect = A.NSGlassEffectView.alloc().initWithFrame_(frame.bounds())
                    # Regular = frosted Liquid Glass: colour and light come through, but text
                    # and details behind the window are blurred (Clear stayed sharp and busy).
                    effect.setStyle_(A.NSGlassEffectViewStyleRegular)
                    effect.setCornerRadius_(26.0)   # macOS 26 window corners
                else:
                    effect = A.NSVisualEffectView.alloc().initWithFrame_(frame.bounds())
                    effect.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
                    effect.setState_(A.NSVisualEffectStateActive)
                effect.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
                frame.addSubview_positioned_relativeTo_(effect, A.NSWindowBelow, content)
                self._effect, self._effect_kind = effect, kind
            if use_liquid:
                # Clear glass shows whatever is behind the window. A strong white tint keeps
                # light mode light (and readable) even on a dark wallpaper.
                self._effect.setTintColor_(A.NSColor.colorWithSRGBRed_green_blue_alpha_(
                    *((0.05, 0.06, 0.09, 0.30) if dark else (0.97, 0.975, 0.985, 0.45))))
            else:
                self._effect.setMaterial_(A.NSVisualEffectMaterialUnderWindowBackground if dark
                                          else A.NSVisualEffectMaterialSidebar)
            set_hidden_from_capture(self, hide_from_capture)
        except Exception as e:
            print(f"Venster-glas niet beschikbaar: {e}")
            self.page.setBackgroundColor(QColor("#16181D" if dark else "#F5F6F8"))

    @staticmethod
    def _clear_titlebar(win, A) -> None:
        """macOS 26 draws its own glass strip in the title bar (_NSTitlebarDecorationView), on
        top of our page. Hide it so the page runs cleanly up to the window's top edge."""
        if hasattr(win, "setTitlebarSeparatorStyle_"):
            win.setTitlebarSeparatorStyle_(A.NSTitlebarSeparatorStyleNone)
        close = win.standardWindowButton_(A.NSWindowCloseButton)
        if close is None:
            return
        container = close.superview()
        while container.superview() is not None and "TitlebarContainer" not in container.className():
            container = container.superview()
        for v in container.subviews():
            if "Decoration" in v.className():
                v.setHidden_(True)

    # kept for older callers
    def set_dark(self, dark: bool) -> None:
        self.apply_chrome(dark, False)

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self._on_close()

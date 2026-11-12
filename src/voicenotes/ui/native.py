"""macOS window tweaks for floating overlays (HUD, call prompt)."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication


def _ns_window(widget):
    if QGuiApplication.platformName() != "cocoa":
        return None  # winId is only an NSView on the native macOS platform
    import objc
    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def make_overlay(widget, click_through: bool = False) -> None:
    """Keep a window visible while other apps are active, on every Space and over
    full-screen apps. Qt's tool windows hide when the app is not frontmost by default."""
    try:
        win = _ns_window(widget)
        if win is None:
            return
        from AppKit import (NSStatusWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
                            NSWindowCollectionBehaviorFullScreenAuxiliary,
                            NSWindowCollectionBehaviorIgnoresCycle,
                            NSWindowCollectionBehaviorStationary)
        win.setHidesOnDeactivate_(False)
        win.setLevel_(NSStatusWindowLevel)
        win.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | NSWindowCollectionBehaviorFullScreenAuxiliary
                                   | NSWindowCollectionBehaviorStationary
                                   | NSWindowCollectionBehaviorIgnoresCycle)
        if click_through:
            win.setIgnoresMouseEvents_(True)
        win.orderFrontRegardless()  # show without activating Voicenotes
    except Exception:
        pass


def overlay_state(widget) -> dict:
    """For tests: the relevant NSWindow properties."""
    win = _ns_window(widget)
    if win is None:
        return {}
    return {"hidesOnDeactivate": bool(win.hidesOnDeactivate()), "level": int(win.level()),
            "visible": bool(win.isVisible()), "canJoinAllSpaces": bool(win.collectionBehavior() & 1)}

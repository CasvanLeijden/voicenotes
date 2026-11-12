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
        import AppKit as A
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
        win.setSharingType_(A.NSWindowSharingNone if _hide_overlays["on"]
                            else A.NSWindowSharingReadOnly)
        win.orderFrontRegardless()  # show without activating Voicenotes
    except Exception:
        pass


_hide_overlays = {"on": True}


def set_hidden_from_capture(widget, hidden: bool) -> None:
    """Keep a window out of screenshots, screen recordings and screen sharing
    (NSWindow.sharingType = none)."""
    try:
        win = _ns_window(widget)
        if win is None:
            return
        import AppKit as A
        win.setSharingType_(A.NSWindowSharingNone if hidden else A.NSWindowSharingReadOnly)
    except Exception as e:
        print(f"Verbergen voor schermdelen lukte niet: {e}")


def set_overlays_hidden_from_capture(hidden: bool) -> None:
    """Applies to overlays shown from now on (HUD, call prompt)."""
    _hide_overlays["on"] = hidden


def overlay_state(widget) -> dict:
    """For tests: the relevant NSWindow properties."""
    win = _ns_window(widget)
    if win is None:
        return {}
    return {"hidesOnDeactivate": bool(win.hidesOnDeactivate()), "level": int(win.level()),
            "visible": bool(win.isVisible()), "canJoinAllSpaces": bool(win.collectionBehavior() & 1),
            "hiddenFromCapture": int(win.sharingType()) == 0}

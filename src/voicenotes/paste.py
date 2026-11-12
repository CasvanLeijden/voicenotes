"""Put dictated text into the frontmost app: clipboard + simulated Cmd+V, then restore."""
from __future__ import annotations

import threading
import time

V_KEYCODE = 9  # kVK_ANSI_V


def snapshot(pb) -> list[dict]:
    """All items on a pasteboard with all their representations."""
    items = []
    for item in pb.pasteboardItems() or []:
        reps = {}
        for t in item.types():
            data = item.dataForType_(t)
            if data is not None:
                reps[t] = data
        items.append(reps)
    return items


def restore(pb, items: list[dict]) -> None:
    from AppKit import NSPasteboardItem
    pb.clearContents()
    objs = []
    for reps in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in reps.items():
            it.setData_forType_(data, t)
        objs.append(it)
    if objs:
        pb.writeObjects_(objs)


def set_text(pb, text: str) -> int:
    from AppKit import NSPasteboardTypeString
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    return pb.changeCount()


def can_post_keys(prompt: bool = False) -> bool:
    import Quartz
    if Quartz.CGPreflightPostEventAccess():
        return True
    if prompt:
        Quartz.CGRequestPostEventAccess()
    return False


def press_cmd_v() -> None:
    import Quartz
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, V_KEYCODE, down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def paste_text(text: str, restore_after: float = 0.8) -> bool:
    """Paste into the frontmost app. Returns False if only the clipboard could be set
    (no permission to send keystrokes); the text then stays on the clipboard."""
    from AppKit import NSPasteboard
    pb = NSPasteboard.generalPasteboard()
    if not can_post_keys():
        set_text(pb, text)
        return False
    saved = snapshot(pb)
    ours = set_text(pb, text)
    time.sleep(0.03)  # let the pasteboard settle before the target app reads it
    press_cmd_v()

    def put_back():
        time.sleep(restore_after)
        if pb.changeCount() == ours:  # nobody copied something new in the meantime
            restore(pb, saved)

    threading.Thread(target=put_back, daemon=True).start()
    return True

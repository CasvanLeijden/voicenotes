"""Global Fn key handling for dictation.

FnStateMachine is pure logic (tested in tests/test_hotkey.py):
- hold Fn (>= hold_min)            -> push-to-talk: record while held
- tap Fn twice quickly             -> hands-free: record until Fn is pressed again
- Fn together with another key     -> that's a shortcut (Fn+F5, Fn+arrows): cancel
Recording starts the moment Fn goes down so no words are lost; short taps cancel it.

FnListener feeds it from a listen-only Quartz event tap (needs Input Monitoring permission).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

FN_KEYCODES = {63, 179}          # kVK_Function; Globe key on newer keyboards
FN_FLAG = 0x800000               # kCGEventFlagMaskSecondaryFn


@dataclass
class FnStateMachine:
    hold_min: float = 0.3          # shorter presses are taps
    double_tap: float = 0.45       # max gap between two taps for hands-free
    state: str = "idle"            # idle | pressed | handsfree | releasing
    _down_at: float = 0.0
    _last_tap: float = -10.0

    def fn_down(self, t: float) -> list[str]:
        if self.state == "idle":
            self.state, self._down_at = "pressed", t
            return ["start"]
        if self.state == "handsfree":
            self.state = "releasing"
            return ["stop"]
        return []

    def fn_up(self, t: float) -> list[str]:
        if self.state == "releasing":
            self.state = "idle"
            return []
        if self.state != "pressed":
            return []
        if t - self._down_at >= self.hold_min:
            self.state = "idle"
            return ["stop"]
        # Quick tap: second quick tap switches to hands-free, keeping this recording.
        if t - self._last_tap <= self.double_tap:
            self.state, self._last_tap = "handsfree", -10.0
            return ["handsfree"]
        self.state, self._last_tap = "idle", t
        return ["cancel"]

    def other_key(self, t: float) -> list[str]:
        if self.state == "pressed":
            self.state, self._last_tap = "idle", -10.0
            return ["cancel"]
        return []


def input_monitoring_allowed(prompt: bool = False) -> bool:
    import Quartz
    if Quartz.CGPreflightListenEventAccess():
        return True
    if prompt:
        Quartz.CGRequestListenEventAccess()
    return False


class FnListener:
    """Runs a CGEventTap on its own thread; calls on_action(action) for each state change."""

    def __init__(self, on_action: Callable[[str], None]):
        self.on_action = on_action
        self.machine = FnStateMachine()
        self._thread: threading.Thread | None = None
        self._loop = None
        self._tap = None
        self._fn_down = False
        self.ok = threading.Event()
        self.failed = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        import Quartz
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)

    def _emit(self, actions: list[str]) -> None:
        for a in actions:
            self.on_action(a)

    def _callback(self, proxy, etype, event, refcon):
        import time

        import Quartz
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        now = time.monotonic()
        if etype == Quartz.kCGEventFlagsChanged:
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if code in FN_KEYCODES:
                down = bool(Quartz.CGEventGetFlags(event) & FN_FLAG)
                if down and not self._fn_down:
                    self._emit(self.machine.fn_down(now))
                elif not down and self._fn_down:
                    self._emit(self.machine.fn_up(now))
                self._fn_down = down
            elif self._fn_down:
                self._emit(self.machine.other_key(now))  # Fn + another modifier
        elif etype == Quartz.kCGEventKeyDown and self._fn_down:
            self._emit(self.machine.other_key(now))
        return event

    def _run(self) -> None:
        import Quartz
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown))
        self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                            Quartz.kCGEventTapOptionListenOnly, mask,
                                            self._callback, None)
        if self._tap is None:
            self.failed.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self.ok.set()
        Quartz.CFRunLoopRun()

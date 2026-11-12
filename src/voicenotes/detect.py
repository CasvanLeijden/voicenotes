"""Detect calls: a known call app (Teams, WhatsApp, Zoom, ...) using the microphone."""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

from .audio.system_tap import find_audiotap

# (bundle id prefix, display name). A bundle matches its prefix exactly or prefix + ".".
CALL_APPS: list[tuple[str, str]] = [
    ("com.microsoft.teams2", "Teams"),
    ("com.microsoft.teams", "Teams"),
    ("net.whatsapp.WhatsApp", "WhatsApp"),
    ("desktop.WhatsApp", "WhatsApp"),
    ("us.zoom.xos", "Zoom"),
    ("com.apple.FaceTime", "FaceTime"),
    ("com.apple.TelephonyUtilities", "FaceTime"),
    ("com.tinyspeck.slackmacgap", "Slack"),
    ("com.hnc.Discord", "Discord"),
    ("Cisco-Systems.Spark", "Webex"),
    ("com.google.Chrome", "Chrome"),
    ("com.microsoft.edgemac", "Edge"),
    ("company.thebrowser.Browser", "Arc"),
    ("com.brave.Browser", "Brave"),
    ("org.mozilla.firefox", "Firefox"),
    ("com.apple.Safari", "Safari"),
]
APP_NAMES: list[str] = list(dict.fromkeys(name for _, name in CALL_APPS))
BROWSERS = {"Chrome", "Edge", "Arc", "Brave", "Firefox", "Safari"}


def app_for_bundle(bundle: str) -> str | None:
    for prefix, name in CALL_APPS:
        if bundle == prefix or bundle.startswith(prefix + "."):
            return name
    return None


@dataclass
class _AppState:
    first_active: float | None = None   # start of current continuous mic use
    last_active: float | None = None
    in_call: bool = False


@dataclass
class CallEvent:
    kind: str   # "started" | "ended"
    app: str


@dataclass
class CallDetector:
    """Pure state machine; feed it the current mic users regularly via `update`."""
    start_delay: float = 3.0   # mic use must last this long before we call it a call
    end_delay: float = 10.0    # mic idle this long before the call counts as ended
    ignore_pids: set[int] = field(default_factory=set)
    _apps: dict[str, _AppState] = field(default_factory=dict)

    def update(self, now: float, users: list[dict]) -> list[CallEvent]:
        active = set()
        for u in users:
            if u.get("pid") in self.ignore_pids:
                continue
            app = app_for_bundle(u.get("bundle") or "")
            if app:
                active.add(app)
        events: list[CallEvent] = []
        for app in active | set(self._apps):
            st = self._apps.setdefault(app, _AppState())
            if app in active:
                if st.first_active is None:
                    st.first_active = now
                st.last_active = now
                if not st.in_call and now - st.first_active >= self.start_delay:
                    st.in_call = True
                    events.append(CallEvent("started", app))
            elif st.in_call:
                if now - (st.last_active or now) >= self.end_delay:
                    st.in_call = False
                    st.first_active = None
                    events.append(CallEvent("ended", app))
            else:
                st.first_active = None  # mic use stopped before it counted as a call
        return events

    @property
    def active_calls(self) -> list[str]:
        return [a for a, st in self._apps.items() if st.in_call]


def query_mic_users(timeout: float = 2.0) -> list[dict]:
    import os
    # --exclude-pid: the helper must not read our own process object (see Watch.swift).
    out = subprocess.run([str(find_audiotap()), "--mic-users", "--exclude-pid", str(os.getpid())],
                         capture_output=True, text=True, timeout=timeout)
    return json.loads(out.stdout.strip() or "[]")


class MicPoller:
    """Background thread that queries mic users every `interval` seconds."""

    def __init__(self, on_users: Callable[[list[dict]], None], interval: float = 2.0):
        self.on_users = on_users
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.on_users(query_mic_users())
            except Exception:
                pass  # helper missing or slow: try again next round
            self._stop.wait(self.interval)

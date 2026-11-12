"""Run an `audiotap` capture mode as a child process and stream its float32 PCM."""
from __future__ import annotations

import signal
import subprocess
import threading
from typing import Callable

import numpy as np

from .system_tap import find_audiotap


class HelperStream:
    """stderr protocol: first line "FORMAT <rate> <channels> [device name]", errors "ERROR ..."."""

    def __init__(self, args: list[str], on_audio: Callable[[np.ndarray, int], None]):
        self.args = args
        self._on_audio = on_audio
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.rate = 0
        self.channels = 0
        self.name = ""
        self.error: str | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen([str(find_audiotap()), *self.args], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
        line = self._proc.stderr.readline().decode().strip()
        if not line.startswith("FORMAT"):
            rest = self._proc.stderr.read().decode().strip() if self._proc.poll() is not None else ""
            msg = (line + " " + rest).strip().removeprefix("ERROR ").strip()
            raise RuntimeError(msg or "audiotap startte niet")
        parts = line.split(maxsplit=3)
        self.rate, self.channels = int(parts[1]), int(parts[2])
        self.name = parts[3] if len(parts) > 3 else ""
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        frame_bytes = 4 * self.channels
        chunk = frame_bytes * max(1, self.rate // 20)  # ~50 ms
        buf = b""
        while True:
            data = self._proc.stdout.read(chunk)
            if not data:
                break
            buf += data
            usable = len(buf) - len(buf) % frame_bytes
            if usable:
                samples = np.frombuffer(buf[:usable], dtype=np.float32).reshape(-1, self.channels)
                self._on_audio(samples if self.channels > 1 else samples[:, 0], self.rate)
                buf = buf[usable:]
        err = self._proc.stderr.read().decode().strip()
        if err:
            self.error = err

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._thread:
            self._thread.join(timeout=3)

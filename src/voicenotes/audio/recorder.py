"""Records microphone and system audio to separate, time-aligned 16 kHz FLAC files."""
from __future__ import annotations

import time
from pathlib import Path

from .mic import Mic
from .system_tap import SystemTap
from .writer import StreamWriter

MIC_FILE = "mic.flac"
SYSTEM_FILE = "system.flac"


class Recorder:
    def __init__(self, out_dir: Path, *, mic: bool = True, system: bool = True, live=None):
        """live: optional object with feed(channel, samples16k, offset) (e.g. LiveTranscriber)."""
        if not (mic or system):
            raise ValueError("Kies minstens microfoon of systeemaudio")
        self.out_dir = out_dir
        self.use_mic, self.use_system = mic, system
        self.live = live
        self.writers: dict[str, StreamWriter] = {}
        self._mic: Mic | None = None
        self._tap: SystemTap | None = None
        self.t0 = 0.0
        self.mic_name = ""

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        try:
            if self.use_system:
                w = self.writers["system"] = StreamWriter(self.out_dir / SYSTEM_FILE, self.t0)
                self._attach(w, "system")
                self._tap = SystemTap(w.write)
                self._tap.start()
            if self.use_mic:
                w = self.writers["mic"] = StreamWriter(self.out_dir / MIC_FILE, self.t0)
                self._attach(w, "mic")
                self._mic = Mic(w.write)
                self._mic.start()
                self.mic_name = self._mic.name
        except Exception:
            self.stop()
            raise

    def _attach(self, writer: StreamWriter, channel: str) -> None:
        if self.live is not None:
            writer.listeners.append(lambda samples, offset: self.live.feed(channel, samples, offset))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0 if self.t0 else 0.0

    def levels(self) -> dict[str, float]:
        return {k: w.level for k, w in self.writers.items()}

    def stop(self) -> dict[str, Path]:
        total = self.elapsed
        if self._mic:
            self._mic.stop()
        if self._tap:
            self._tap.stop()
        for w in self.writers.values():
            w.close(total_seconds=total)
        for proc in (self._tap, self._mic):
            if proc and proc.error:
                raise RuntimeError(proc.error)
        return {k: w.path for k, w in self.writers.items()}

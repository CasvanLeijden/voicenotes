"""Streaming writer: any rate/channel count -> 16 kHz mono FLAC, aligned to a shared clock."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

TARGET_RATE = 16_000


class StreamWriter:
    def __init__(self, path: Path, t0: float):
        self.path = path
        self.t0 = t0
        self._file = sf.SoundFile(path, "w", samplerate=TARGET_RATE, channels=1,
                                  format="FLAC", subtype="PCM_16")
        self._resampler: soxr.ResampleStream | None = None
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self.level = 0.0  # recent RMS, for level meters
        self.frames_written = 0
        # Called with (16 kHz mono samples, sample offset in the recording).
        self.listeners: list = []

    def write(self, samples: np.ndarray, rate: int) -> None:
        """samples: float32, shape (frames,) or (frames, channels)."""
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        with self._lock:
            if self._closed:
                return
            if not self._started:
                # Pad with silence so every stream starts at t0.
                lead = int((time.monotonic() - self.t0) * TARGET_RATE)
                lead = max(0, lead - int(len(samples) / rate * TARGET_RATE))
                if lead:
                    self._file.write(np.zeros(lead, dtype=np.float32))
                    self.frames_written += lead
                self._resampler = soxr.ResampleStream(rate, TARGET_RATE, 1, dtype="float32")
                self._started = True
            out = self._resampler.resample_chunk(samples)
            if len(out):
                out = np.clip(out, -1, 1)
                self._file.write(out)
                for listener in self.listeners:
                    listener(out, self.frames_written)
                self.frames_written += len(out)
            self.level = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0

    def close(self, total_seconds: float | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            if self._resampler is not None:
                tail = self._resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
                if len(tail):
                    self._file.write(np.clip(tail, -1, 1))
                    self.frames_written += len(tail)
            # Pad to the full recording length so both channels line up at the end.
            if total_seconds is not None:
                missing = int(total_seconds * TARGET_RATE) - self.frames_written
                if missing > 0:
                    self._file.write(np.zeros(missing, dtype=np.float32))
            self._file.close()
            self._closed = True

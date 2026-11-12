"""Voice activity detection: cut a 16 kHz stream into utterances at pauses (Silero VAD)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

RATE = 16_000
FRAME = 512  # Silero needs 512-sample frames at 16 kHz (32 ms)


@dataclass
class Utterance:
    start: float  # seconds since recording start
    end: float
    audio: np.ndarray


def load_vad_model():
    import warnings

    from silero_vad import load_silero_vad
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_silero_vad()


class Segmenter:
    """Feed 16 kHz audio with its position in the recording; get back finished utterances."""

    def __init__(self, model=None, *, start_threshold: float = 0.5, stop_threshold: float = 0.35,
                 min_silence: float = 0.5, max_length: float = 12.0, min_speech: float = 0.3,
                 preroll: float = 0.25, prob_fn=None):
        self.model = model
        self.prob_fn = prob_fn or self._silero_prob
        self.start_threshold = start_threshold
        self.stop_threshold = stop_threshold
        self.min_silence_frames = int(min_silence * RATE / FRAME)
        self.max_frames = int(max_length * RATE / FRAME)
        self.min_speech_frames = max(1, int(min_speech * RATE / FRAME))
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, int(preroll * RATE / FRAME)))
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0  # sample index of the start of _pending
        self._speech: list[np.ndarray] | None = None
        self._probs: list[float] = []
        self.search_frames = int(1.5 * RATE / FRAME)  # where to look for a quiet cut point
        self._speech_start = 0
        self._silence = 0
        self._voiced = 0

    def _silero_prob(self, frame: np.ndarray) -> float:
        import torch
        with torch.no_grad():
            return float(self.model(torch.from_numpy(frame), RATE))

    def feed(self, samples: np.ndarray, offset: int | None = None) -> list[Utterance]:
        if offset is not None and not len(self._pending) and self._speech is None:
            self._pos = offset
        self._pending = np.concatenate([self._pending, samples.astype(np.float32)])
        out: list[Utterance] = []
        while len(self._pending) >= FRAME:
            frame, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            frame_start = self._pos
            self._pos += FRAME
            p = self.prob_fn(frame)
            if self._speech is None:
                if p >= self.start_threshold:
                    pre = list(self._preroll)
                    self._speech = pre + [frame]
                    self._probs = [0.0] * len(pre) + [p]
                    self._speech_start = frame_start - len(pre) * FRAME
                    self._silence, self._voiced = 0, 1
                else:
                    self._preroll.append(frame)
                continue
            self._speech.append(frame)
            self._probs.append(p)
            if p < self.stop_threshold:
                self._silence += 1
            else:
                self._silence = 0
                self._voiced += 1
            if self._silence >= self.min_silence_frames:
                u = self._finish(trim=self._silence)
                if u:
                    out.append(u)
            elif len(self._speech) >= self.max_frames:
                u = self._split_at_quietest()
                if u:
                    out.append(u)
        return out

    def _split_at_quietest(self) -> Utterance | None:
        """Too long without a pause: cut at the least speech-like frame in the last 1.5 s,
        so the cut usually falls between words. The remainder starts the next utterance."""
        frames, probs = self._speech, self._probs
        lo = max(1, len(frames) - self.search_frames)
        window = np.asarray(probs[lo:])
        # Latest frame with the lowest speech probability (ties -> as late as possible).
        quietest = len(window) - 1 - int(np.argmin(window[::-1]))
        cut = lo + quietest + 1  # include the quiet frame in the first part
        head, tail = frames[:cut], frames[cut:]
        start = self._speech_start
        audio = np.concatenate(head)
        u = Utterance(start / RATE, (start + len(audio)) / RATE, audio)
        self._speech = tail or None
        self._probs = probs[cut:]
        self._speech_start = start + len(audio)
        self._voiced = sum(1 for q in self._probs if q >= self.stop_threshold)
        self._silence = 0
        if self._speech is None:
            self._probs = []
        return u

    def _finish(self, trim: int = 0) -> Utterance | None:
        frames, voiced = self._speech or [], self._voiced
        start = self._speech_start
        self._speech, self._silence, self._voiced = None, 0, 0
        self._probs = []
        self._preroll.clear()
        keep = max(1, len(frames) - max(0, trim - 4))  # keep ~130 ms of trailing silence
        frames = frames[:keep]
        if voiced < self.min_speech_frames:
            return None
        audio = np.concatenate(frames)
        return Utterance(start / RATE, (start + len(audio)) / RATE, audio)

    def flush(self) -> Utterance | None:
        if self._speech is None:
            return None
        return self._finish(trim=self._silence)

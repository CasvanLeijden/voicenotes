"""Speaker diarization with pyannote, and mapping of whisper words onto speakers."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .asr.final import Segment, Word

PIPELINE_ID = "pyannote/speaker-diarization-community-1"


@dataclass
class Turn:
    start: float
    end: float
    label: str


@dataclass
class Diarization:
    turns: list[Turn]
    # One centroid embedding per diarization label (e.g. "SPEAKER_00").
    embeddings: dict[str, np.ndarray]


@lru_cache(maxsize=1)
def _pipeline():
    import torch
    from pyannote.audio import Pipeline

    pipe = Pipeline.from_pretrained(PIPELINE_ID)
    if torch.backends.mps.is_available():
        pipe.to(torch.device("mps"))
    return pipe


def unload() -> None:
    _pipeline.cache_clear()


def diarize(audio: np.ndarray, sample_rate: int = 16_000,
            num_speakers: int | None = None) -> Diarization:
    import torch

    kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    out = _pipeline()({"waveform": torch.from_numpy(audio)[None], "sample_rate": sample_rate},
                      **kwargs)
    # Exclusive = no overlapping turns, which is what we want for word assignment.
    annotation = getattr(out, "exclusive_speaker_diarization", None) or out.speaker_diarization
    turns = [Turn(t.start, t.end, label) for t, _, label in annotation.itertracks(yield_label=True)]
    embeddings: dict[str, np.ndarray] = {}
    if out.speaker_embeddings is not None:
        for label, vec in zip(out.speaker_diarization.labels(), out.speaker_embeddings):
            if not np.isnan(vec).any():
                embeddings[label] = np.asarray(vec, dtype=np.float32)
    return Diarization(sorted(turns, key=lambda t: t.start), embeddings)


def _speaker_for(start: float, end: float, turns: list[Turn]) -> str | None:
    """Speaker with the largest overlap; falls back to the nearest turn."""
    best, best_overlap = None, 0.0
    for t in turns:
        if t.start > end:
            break
        overlap = min(end, t.end) - max(start, t.start)
        if overlap > best_overlap:
            best, best_overlap = t.label, overlap
    if best is not None:
        return best
    if not turns:
        return None
    mid = (start + end) / 2
    return min(turns, key=lambda t: min(abs(mid - t.start), abs(mid - t.end))).label


def assign_speakers(segments: list[Segment], turns: list[Turn]) -> list[Segment]:
    """Split whisper segments at speaker changes, using word timestamps."""
    out: list[Segment] = []
    for seg in segments:
        words = seg.words or [Word(seg.start, seg.end, seg.text)]
        cur: Segment | None = None
        for w in words:
            spk = _speaker_for(w.start, w.end, turns) or ""
            if cur is None or spk != cur.speaker:
                if cur is not None:
                    out.append(cur)
                cur = Segment(w.start, w.end, "", spk, [])
            cur.end = w.end
            cur.words.append(w)
        if cur is not None:
            out.append(cur)
    for s in out:
        s.text = "".join(w.text for w in s.words).strip()
    return [s for s in out if s.text]

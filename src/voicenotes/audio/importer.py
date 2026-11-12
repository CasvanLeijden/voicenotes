"""Decode any audio/video file to 16 kHz float32 mono (or per-channel) arrays."""
from __future__ import annotations

from pathlib import Path

import av
import numpy as np

SAMPLE_RATE = 16_000


def load_audio(path: str | Path, *, keep_channels: bool = False) -> np.ndarray:
    """Return float32 audio at 16 kHz.

    Shape is (n,) when mono-mixed, or (channels, n) when keep_channels is True.
    """
    layout = None if keep_channels else "mono"
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        if keep_channels:
            layout = stream.layout.name if stream.channels <= 2 else "stereo"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=SAMPLE_RATE)
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        raise ValueError(f"Geen audio gevonden in {path}")
    audio = np.concatenate(chunks, axis=1).astype(np.float32)
    return audio if keep_channels else audio[0]

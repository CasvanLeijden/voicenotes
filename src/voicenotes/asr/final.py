"""High-quality offline transcription with mlx-whisper."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""
    words: list[Word] = field(default_factory=list)


def build_prompt(vocabulary: list[str], language: str = "nl") -> str | None:
    if not vocabulary:
        return None
    terms = ", ".join(vocabulary) + "."
    # A Dutch lead-in nudges whisper towards Dutch; for other languages keep only the terms.
    return f"Vergadering. Namen en termen: {terms}" if language == "nl" else terms


def transcribe(
    audio: np.ndarray,
    *,
    model: str,
    language: str = "nl",
    vocabulary: list[str] | None = None,
) -> list[Segment]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
        initial_prompt=build_prompt(vocabulary or [], language),
        # Avoid the classic whisper loop/hallucination on long silences.
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
        verbose=None,
    )
    segments = []
    for s in result["segments"]:
        text = s["text"].strip()
        if not text:
            continue
        words = [Word(w["start"], w["end"], w["word"]) for w in s.get("words", [])]
        segments.append(Segment(s["start"], s["end"], text, words=words))
    return segments


def _whisper_model(model: str):
    import sys

    import mlx.core as mx
    import mlx_whisper  # noqa: F401  (registers mlx_whisper.transcribe)
    holder = sys.modules["mlx_whisper.transcribe"].ModelHolder
    return holder.get_model(model, mx.float16)  # same cache transcribe() uses


def language_name(code: str) -> str:
    from mlx_whisper.tokenizer import LANGUAGES
    return LANGUAGES.get(code, code)


def detect_language(audio: np.ndarray, *, model: str, preferred: str = "nl", windows: int = 5,
                    min_share: float = 0.6, min_prob: float = 0.7) -> tuple[str, list[str]]:
    """Spoken language of a recording.

    Looks at up to `windows` 30-second windows with the most speech energy, spread over the
    recording. Another language only wins when it is clearly the majority; otherwise we keep
    `preferred`, so Dutch meetings with some English terms stay Dutch.
    Returns (language code, per-window votes).
    """
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.decoding import detect_language as _detect

    if len(audio) == 0:
        return preferred, []
    n = max(1, int(np.ceil(len(audio) / N_SAMPLES)))
    chunks = [audio[i * N_SAMPLES:(i + 1) * N_SAMPLES] for i in range(n)]
    energy = [float(np.sqrt(np.mean(c ** 2))) if len(c) else 0.0 for c in chunks]
    # Spread: take the loudest window from each of `windows` equal parts of the recording.
    parts = np.array_split(np.arange(n), min(windows, n))
    picks = [int(p[np.argmax([energy[i] for i in p])]) for p in parts if len(p)]
    picks = [i for i in picks if energy[i] > 1e-3]
    if not picks:
        return preferred, []

    m = _whisper_model(model)
    votes, probs = [], {}
    for i in picks:
        mel = log_mel_spectrogram(chunks[i], n_mels=m.dims.n_mels)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, lp = _detect(m, mel)
        lp = lp[0] if isinstance(lp, list) else lp
        lang = max(lp, key=lp.get)
        votes.append(lang)
        probs.setdefault(lang, []).append(lp[lang])
    top = max(set(votes), key=votes.count)
    if (top != preferred and votes.count(top) / len(votes) >= min_share
            and float(np.mean(probs[top])) >= min_prob):
        return top, votes
    return preferred, votes

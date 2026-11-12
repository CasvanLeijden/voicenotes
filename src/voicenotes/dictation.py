"""Dictation: capture the mic while Fn is used, transcribe fast, optionally tidy up, paste."""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import soxr

from .asr.live import PARAKEET, get_fast_asr
from .config import Config

CLEANUP_PROMPT = """Je krijgt gedicteerde tekst. Maak er nette geschreven tekst van:
- zet interpunctie en hoofdletters goed;
- haal stopwoorden (eh, ehm, uh, nou ja) en per ongeluk herhaalde woorden weg;
- verander verder niets aan de inhoud, de woordkeuze of de taal.
{vocab}Geef alleen de verbeterde tekst terug, zonder uitleg of aanhalingstekens."""

import re

_FILLER = re.compile(r"(?i)(?:,\s*)?\b(?:eh|ehm|euh|uh|uhm|um|umm|hmm|hm)\b[,.]?\s*")


def strip_fillers(text: str) -> str:
    """Remove obvious filler sounds without an LLM (fast, always on)."""
    out = _FILLER.sub(" ", text)
    out = re.sub(r"\s+([,.!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"^[,.\s]+", "", out)
    return out[:1].upper() + out[1:] if out else out


MIN_SECONDS = 0.35
MAX_SECONDS = 600


class Capture:
    """Collects mic audio, either from its own helper process or from a running recording."""

    def __init__(self, recorder=None):
        self._chunks: list[np.ndarray] = []
        self._rate = 16_000
        self._lock = threading.Lock()
        self._mic = None
        self._writer = None
        self._listener = None
        self.level = 0.0
        self.started_at = 0.0
        self.recorder = recorder

    def _add(self, samples: np.ndarray, rate: int) -> None:
        with self._lock:
            self._chunks.append(np.asarray(samples, dtype=np.float32).copy())
            self._rate = rate
        self.level = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0

    def start(self) -> None:
        self.started_at = time.monotonic()
        writer = getattr(self.recorder, "writers", {}).get("mic") if self.recorder else None
        if writer is not None:
            # A meeting is being recorded: share its mic stream (already 16 kHz).
            self._writer = writer
            self._listener = lambda s, _off: self._add(s, 16_000)
            writer.listeners.append(self._listener)
            return
        from .audio.mic import Mic
        self._mic = Mic(self._add)
        self._mic.start()

    def stop(self) -> np.ndarray:
        if self._writer is not None:
            try:
                self._writer.listeners.remove(self._listener)
            except ValueError:
                pass
        if self._mic is not None:
            # Stopping the helper takes a moment; the audio we have is complete already.
            mic, self._mic = self._mic, None
            threading.Thread(target=mic.stop, daemon=True).start()
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            rate = self._rate
        if rate != 16_000 and len(audio):
            audio = soxr.resample(audio, rate, 16_000).astype(np.float32)
        return audio


def transcribe(audio: np.ndarray, cfg: Config) -> str:
    asr = get_fast_asr(cfg.dictation_model or PARAKEET, cfg.language)
    # A little silence around the audio helps the model with the first and last word.
    pad = np.zeros(int(0.15 * 16_000), dtype=np.float32)
    audio = np.concatenate([pad, audio, pad])
    if "parakeet" in (cfg.dictation_model or PARAKEET).lower():
        return asr(audio).strip()
    from .asr.final import build_prompt
    return asr(audio, prompt=build_prompt(cfg.vocabulary, cfg.language)).strip()


def cleanup(text: str, cfg: Config, timeout: float = 8.0) -> str:
    from .llm import LLM
    vocab = (f"- schrijf deze namen en termen zo: {', '.join(cfg.vocabulary)};\n"
             if cfg.vocabulary else "")
    result: list[str] = []

    def run():
        try:
            # No thinking here: it would add 10+ s after every dictation for a simple task.
            result.append(LLM(cfg).chat(CLEANUP_PROMPT.format(vocab=vocab), text,
                                        temperature=0.0, think=False,
                                        max_answer_tokens=len(text) // 2 + 100))
        except Exception:
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    out = (result[0] if result else "").strip().strip('"').strip()
    # Guard against the model answering or rewriting instead of tidying.
    if not out or len(out) > 1.6 * len(text) + 20:
        return text
    return out


def process(audio: np.ndarray, cfg: Config, on_step: Callable[[str], None] = lambda s: None) -> str:
    if len(audio) < MIN_SECONDS * 16_000 or float(np.sqrt(np.mean(audio ** 2))) < 3e-4:
        return ""
    on_step("transcribing")
    text = strip_fillers(transcribe(audio, cfg))
    if text and cfg.dictation_cleanup:
        on_step("cleaning")
        text = cleanup(text, cfg)
    return text

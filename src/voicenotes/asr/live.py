"""Live transcription during a recording: VAD-cut utterances -> fast ASR -> callback."""
from __future__ import annotations

import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..audio.vad import Segmenter, Utterance, load_vad_model

PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"

# Phrases fast models sometimes produce on noise/silence.
_HALLUCINATIONS = {"ondertiteling", "bedankt voor het kijken", "tv gelderland", "ondertiteld door",
                   "amara.org", "dank je wel.", "thank you."}


@dataclass
class LiveSegment:
    channel: str  # "mic" or "system"
    start: float
    end: float
    text: str


# MLX ties a model to the thread that created it ("There is no Stream(cpu, 1) in current
# thread" otherwise). So the fast model is loaded *and* run on one dedicated thread; live
# transcription and dictation hand their audio to it and wait for the text.
_mlx_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fast-asr")


class _Parakeet:
    def __init__(self, repo: str):
        from parakeet_mlx import from_pretrained
        self.model = from_pretrained(repo)

    def run(self, audio: np.ndarray) -> str:
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel
        mel = get_logmel(mx.array(audio), self.model.preprocessor_config)
        return self.model.generate(mel)[0].text.strip()

    def __call__(self, audio: np.ndarray) -> str:
        return _mlx_thread.submit(self.run, audio).result()


class _Whisper:
    def __init__(self, repo: str, language: str):
        self.repo, self.language = repo, language

    def __call__(self, audio: np.ndarray, prompt: str | None = None) -> str:
        return _mlx_thread.submit(self.run, audio, prompt).result()

    def run(self, audio: np.ndarray, prompt: str | None = None) -> str:
        import mlx_whisper
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=self.repo, language=self.language,
                                   condition_on_previous_text=False, initial_prompt=prompt,
                                   verbose=None)
        return r["text"].strip()


_shared: dict[tuple[str, str], object] = {}
_shared_lock = threading.Lock()


def get_fast_asr(model: str = PARAKEET, language: str = "nl"):
    """One loaded fast model per process, shared by live transcription and dictation."""
    key = (model, language)
    with _shared_lock:
        if key not in _shared:
            factory = ((lambda: _Parakeet(model)) if "parakeet" in model.lower()
                       else (lambda: _Whisper(model, language)))
            _shared[key] = _mlx_thread.submit(factory).result()  # load on the MLX thread too
        return _shared[key]


def warm_up(model: str = PARAKEET, language: str = "nl") -> None:
    """Load the model and run it once: the first inference after loading is slow."""
    get_fast_asr(model, language)(np.zeros(16_000, dtype=np.float32))


def _is_noise(text: str) -> bool:
    t = text.strip().lower()
    return len(t) < 2 or any(h in t for h in _HALLUCINATIONS)


class LiveTranscriber:
    """Thread-safe: call `feed(channel, samples, offset)` from audio threads."""

    def __init__(self, on_segment: Callable[[LiveSegment], None], *, model: str = PARAKEET,
                 language: str = "nl", on_error: Callable[[str], None] | None = None):
        self.on_segment = on_segment
        self.on_error = on_error or (lambda e: None)
        self.model_name, self.language = model, language
        self._audio_q: queue.Queue = queue.Queue()
        self._utt_q: queue.Queue = queue.Queue()
        self._segmenters: dict[str, Segmenter] = {}
        self._threads: list[threading.Thread] = []
        self._ready = threading.Event()

    def start(self) -> None:
        for target in (self._vad_loop, self._asr_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def feed(self, channel: str, samples: np.ndarray, offset: int) -> None:
        self._audio_q.put((channel, samples, offset))

    def stop(self, timeout: float = 10.0) -> None:
        """Finish pending audio (best effort within timeout) and stop the threads."""
        self._audio_q.put(None)
        for t in self._threads:
            t.join(timeout=timeout)

    # --- threads --------------------------------------------------------

    def _vad_loop(self) -> None:
        try:
            while True:
                item = self._audio_q.get()
                if item is None:
                    break
                channel, samples, offset = item
                seg = self._segmenters.get(channel)
                if seg is None:
                    # Live text is only a preview: cut quickly (short pauses, max ~5 s) so words
                    # appear fast. The final transcript is made separately after the recording.
                    seg = self._segmenters[channel] = Segmenter(load_vad_model(), min_silence=0.3,
                                                                max_length=5.0)
                for u in seg.feed(samples, offset):
                    self._utt_q.put((channel, u))
            for channel, seg in self._segmenters.items():
                u = seg.flush()
                if u:
                    self._utt_q.put((channel, u))
        except Exception:
            self.on_error(traceback.format_exc(limit=3))
        finally:
            self._utt_q.put(None)

    def _load_asr(self):
        return get_fast_asr(self.model_name, self.language)

    def _asr_loop(self) -> None:
        try:
            asr = self._load_asr()
        except Exception as e:
            self.on_error(f"Live-model laden mislukt: {e}")
            asr = None
        self._ready.set()
        while True:
            item = self._utt_q.get()
            if item is None:
                break
            if asr is None:
                continue
            channel, u = item  # type: str, Utterance
            try:
                text = asr(u.audio)
            except Exception as e:
                self.on_error(f"Live-transcriptie fout: {e}")
                continue
            if text and not _is_noise(text):
                self.on_segment(LiveSegment(channel, u.start, u.end, text))

"""Post-processing pipeline: audio file -> transcript -> summary -> Q&A index."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from . import diarize as dz
from . import speakers
from .asr.final import Segment, detect_language, language_name, transcribe
from .audio.importer import load_audio
from .config import Config
from .llm import LLM
from .store import Meeting, Store
from .audio.recorder import MIC_FILE, SYSTEM_FILE
from .transcript import chunk_lines, drop_echo, to_lines, to_markdown

Progress = Callable[[str], None]

QA_CHUNK_CHARS = 1500


def import_file(cfg: Config, store: Store, path: Path, title: str | None,
                progress: Progress = print, num_speakers: int | None = None,
                folder_id: int | None = None) -> Meeting:
    meeting = store.create_meeting(title or path.stem, source=f"import:{path.name}",
                                   meetings_root=cfg.meetings_dir, folder_id=folder_id,
                                   title_auto=not title)
    shutil.copy2(path, meeting.dir / f"source{path.suffix.lower()}")
    process(cfg, store, meeting, path, progress, num_speakers)
    return store.get_meeting(meeting.id)


def process(cfg: Config, store: Store, meeting: Meeting, audio_path: Path,
            progress: Progress = print, num_speakers: int | None = None) -> None:
    progress("Audio laden")
    audio = load_audio(audio_path)
    lang = _detect(cfg, store, meeting.id, audio, progress)
    progress(f"Transcriberen ({len(audio) / 16000 / 60:.1f} min audio, model {cfg.whisper_model})")
    segments = transcribe(audio, model=cfg.whisper_model, language=lang,
                          vocabulary=cfg.vocabulary)
    if cfg.diarize and segments:
        segments = label_speakers(cfg, store, meeting.id, audio, segments, progress, num_speakers)
    store.save_segments(meeting.id, segments)
    write_transcript(store, meeting.id)
    summarize(cfg, store, meeting.id, progress)


DICTATION_FILE = "dictation.json"


def save_dictation_spans(meeting_dir: Path, spans: list[tuple[float, float]]) -> None:
    import json
    if spans:
        (meeting_dir / DICTATION_FILE).write_text(json.dumps([list(s) for s in spans]))


def load_dictation_spans(meeting_dir: Path) -> list[tuple[float, float]]:
    import json
    f = meeting_dir / DICTATION_FILE
    return [tuple(x) for x in json.loads(f.read_text())] if f.exists() else []


def drop_dictation(mic: list[Segment], spans: list[tuple[float, float]],
                   margin: float = 0.3) -> list[Segment]:
    """Mic speech during Fn-dictation was meant for another app, not for the meeting."""
    if not spans:
        return mic
    def inside(s: Segment) -> bool:
        mid = (s.start + s.end) / 2
        return any(a - margin <= mid <= b + margin for a, b in spans)
    return [s for s in mic if not inside(s)]


SILENCE_RMS = 1e-3


def _has_sound(audio: np.ndarray) -> bool:
    if len(audio) == 0:
        return False
    # RMS per second; any second above the threshold counts as sound.
    n = len(audio) // 16000 * 16000
    if n == 0:
        return float(np.sqrt(np.mean(audio ** 2))) > SILENCE_RMS
    rms = np.sqrt(np.mean(audio[:n].reshape(-1, 16000) ** 2, axis=1))
    return bool((rms > SILENCE_RMS).any())


def process_recording(cfg: Config, store: Store, meeting: Meeting,
                      progress: Progress = print, num_speakers: int | None = None) -> None:
    """Transcribe a recording made by Recorder: mic = me, system audio = everyone else."""
    mic_path, sys_path = meeting.dir / MIC_FILE, meeting.dir / SYSTEM_FILE
    segments: list[Segment] = []
    system_segments: list[Segment] = []
    progress("Audio laden")
    sys_audio = load_audio(sys_path) if sys_path.exists() else np.zeros(0, np.float32)
    mic_audio = load_audio(mic_path) if mic_path.exists() else np.zeros(0, np.float32)
    # Detect the language on everything that was said (both channels mixed).
    n = max(len(sys_audio), len(mic_audio))
    mixed = np.zeros(n, np.float32)
    mixed[:len(sys_audio)] += sys_audio
    mixed[:len(mic_audio)] += mic_audio
    lang = _detect(cfg, store, meeting.id, mixed, progress)

    if len(sys_audio):
        if _has_sound(sys_audio):
            progress(f"Systeemaudio transcriberen ({len(sys_audio) / 16000 / 60:.1f} min)")
            system_segments = transcribe(sys_audio, model=cfg.whisper_model,
                                         language=lang, vocabulary=cfg.vocabulary)
            if cfg.diarize and system_segments:
                system_segments = label_speakers(cfg, store, meeting.id, sys_audio,
                                                 system_segments, progress, num_speakers)
        else:
            progress("Systeemaudio is stil, overgeslagen")

    if len(mic_audio):
        if _has_sound(mic_audio):
            progress("Microfoon transcriberen")
            mic_segments = transcribe(mic_audio, model=cfg.whisper_model,
                                      language=lang, vocabulary=cfg.vocabulary)
            mic_segments = drop_dictation(mic_segments, load_dictation_spans(meeting.dir))
            before = len(mic_segments)
            mic_segments = drop_echo(mic_segments, system_segments)
            if before - len(mic_segments):
                progress(f"{before - len(mic_segments)} echo-fragment(en) van de speakers verwijderd")
            for s in mic_segments:
                s.speaker = cfg.my_name
            segments += mic_segments
        else:
            progress("Microfoon is stil, overgeslagen")

    segments = sorted(segments + system_segments, key=lambda s: s.start)
    store.save_segments(meeting.id, segments)
    write_transcript(store, meeting.id)
    summarize(cfg, store, meeting.id, progress)


def _detect(cfg: Config, store: Store, mid: int, audio: np.ndarray, progress: Progress) -> str:
    progress("Taal herkennen")
    try:
        lang, _ = detect_language(audio, model=cfg.whisper_model, preferred=cfg.language)
    except Exception as e:
        progress(f"Taal herkennen lukte niet ({e}); {cfg.language} wordt gebruikt")
        lang = cfg.language
    store.set_language(mid, lang)
    if lang != cfg.language:
        names = {"en": "Engels", "de": "Duits", "fr": "Frans", "es": "Spaans", "it": "Italiaans",
                 "pt": "Portugees", "pl": "Pools", "tr": "Turks", "ar": "Arabisch"}
        progress(f"Gesproken taal: {names.get(lang, language_name(lang).capitalize())}")
    return lang


def label_speakers(cfg: Config, store: Store, mid: int, audio: np.ndarray,
                   segments: list[Segment], progress: Progress,
                   num_speakers: int | None = None) -> list[Segment]:
    progress("Sprekers herkennen (pyannote)")
    try:
        result = dz.diarize(audio, num_speakers=num_speakers)
    except Exception as e:
        progress(f"Sprekerherkenning mislukt, transcript zonder sprekers: {e}")
        return segments
    names = speakers.match(store, result.embeddings, cfg.speaker_threshold)
    for label in {t.label for t in result.turns} - names.keys():
        names[label] = (label, None)  # speaker without a usable embedding
    store.save_meeting_speakers(mid, [(label, name, pid, result.embeddings.get(label))
                                      for label, (name, pid) in names.items()])
    labelled = dz.assign_speakers(segments, result.turns)
    for s in labelled:
        s.speaker = names.get(s.speaker, (s.speaker, None))[0]
    known = sorted({n for n, pid in names.values() if pid is not None})
    progress(f"{len(names)} spreker(s) gevonden" + (f", herkend: {', '.join(known)}" if known else ""))
    return labelled


def write_transcript(store: Store, mid: int) -> None:
    m = store.get_meeting(mid)
    (m.dir / "transcript.md").write_text(to_markdown(m.title, store.get_segments(mid)))


def name_speaker(cfg: Config, store: Store, mid: int, old: str, new: str,
                 progress: Progress = print, resummarize: bool = True) -> None:
    """Rename a speaker in a meeting and remember their voice for next time."""
    match = [s for s in store.get_meeting_speakers(mid) if s[1] == old]
    if not match:
        names = ", ".join(s[1] for s in store.get_meeting_speakers(mid)) or "geen"
        raise ValueError(f"Spreker '{old}' niet gevonden in meeting {mid} (wel: {names})")
    pid = None
    for _, _, _, emb in match:
        if emb is not None:
            pid = speakers.learn(store, new, emb)
    store.rename_speaker(mid, old, new, pid)
    write_transcript(store, mid)
    progress(f"'{old}' heet nu '{new}'" + (" (stemprofiel opgeslagen)" if pid else ""))
    if resummarize:
        summarize(cfg, store, mid, progress)


def reprocess(cfg: Config, store: Store, meeting: Meeting, progress: Progress = print,
              num_speakers: int | None = None) -> None:
    """Run the full pipeline again for a recorded or imported meeting."""
    if (meeting.dir / MIC_FILE).exists() or (meeting.dir / SYSTEM_FILE).exists():
        process_recording(cfg, store, meeting, progress, num_speakers)
        return
    sources = sorted(meeting.dir.glob("source.*"))
    if not sources:
        raise FileNotFoundError(f"Geen audio gevonden in {meeting.dir}")
    process(cfg, store, meeting, sources[0], progress, num_speakers)


def summarize(cfg: Config, store: Store, mid: int, progress: Progress = print) -> None:
    meeting = store.get_meeting(mid)
    lines = to_lines(store.get_segments(mid))
    if not lines:
        progress("Geen spraak gevonden, samenvatting overgeslagen")
        return
    llm = LLM(cfg)
    chunks = chunk_lines(lines, llm.chunk_chars)
    progress(f"Samenvatten met LM Studio ({llm.model}, context {llm.context} tokens, "
             f"{len(chunks)} deel/delen)")
    summary = llm.summarize(chunks, on_progress=progress)
    store.save_summary(mid, summary)
    if meeting.title_auto:
        try:
            progress("Titel bedenken")
            title = llm.title("\n".join(lines)[:3000], summary,
                              language=meeting.language or cfg.language)
            if title:
                store.rename_meeting(mid, title, auto=True)  # stays automatic until renamed
                meeting = store.get_meeting(mid)
                write_transcript(store, mid)
        except Exception as e:  # a missing title is no reason to fail the summary
            progress(f"Titel bedenken lukte niet: {e}")
    (meeting.dir / "summary.md").write_text(f"# {meeting.title}\n\n{summary}\n")

    progress("Index voor vragen opbouwen")
    qa_chunks = chunk_lines(lines, QA_CHUNK_CHARS)
    try:
        emb = llm.embed(qa_chunks)
    except Exception as e:  # embedding model not loaded: Q&A falls back to full text
        progress(f"Embeddings niet beschikbaar ({e}); vragen gebruiken dan de volledige tekst")
        emb = None
    store.save_chunks(mid, qa_chunks, emb)


def ask(cfg: Config, store: Store, question: str, mid: int | None = None, top_k: int = 4,
        folder_id: int | None = None) -> str:
    llm = LLM(cfg)
    chunks = store.get_chunks(mid, folder_id=folder_id)
    if not chunks:
        if folder_id is not None:
            return "Deze map bevat nog geen getranscribeerde meetings."
        return "Er zijn nog geen getranscribeerde meetings om vragen over te stellen."
    with_emb = [c for c in chunks if c[2] is not None]
    if with_emb:
        q = llm.embed([question], query=True)[0]
        scores = np.array([c[2] @ q for c in with_emb])
        best = [with_emb[i] for i in np.argsort(-scores)[:top_k]]
    else:
        best = chunks[:top_k]
    context = []
    for meeting_id, text, _ in best:
        m = store.get_meeting(meeting_id)
        context.append(f"Meeting: {m.title} ({m.created_at[:10]})\n{text}")
    return llm.answer(question, context)

"""Post-processing pipeline: audio file -> transcript -> summary -> Q&A index."""
from __future__ import annotations

import re
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
from .names import names_to_tags, render, speaker_legend
from .store import MIC_LABEL, Meeting, Store
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
    store.save_meeting_speakers(meeting.id, [])
    if cfg.diarize and segments:
        segments = label_speakers(cfg, store, meeting.id, audio, segments, progress, num_speakers)
    _save(store, meeting.id, segments)
    summarize(cfg, store, meeting.id, progress)


def _save(store: Store, mid: int, segments: list[Segment]) -> None:
    store.save_segments(mid, segments)
    store.assign_tags(mid)
    render_files(store, mid)


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
    store.save_meeting_speakers(meeting.id, [])

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
                s.speaker = MIC_LABEL
            if mic_segments:
                store.upsert_meeting_speaker(meeting.id, MIC_LABEL, cfg.my_name,
                                             store.ensure_me(cfg.my_name))
            segments += mic_segments
        else:
            progress("Microfoon is stil, overgeslagen")

    segments = sorted(segments + system_segments, key=lambda s: s.start)
    _save(store, meeting.id, segments)
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
    """Diarize, recognise known voices and store the meeting's speakers.
    Segments get diarization labels as speaker; names come from the linked people."""
    progress("Sprekers herkennen (pyannote)")
    try:
        result = dz.diarize(audio, num_speakers=num_speakers)
    except Exception as e:
        progress(f"Sprekerherkenning mislukt, transcript zonder sprekers: {e}")
        return segments
    people = speakers.match(store, result.embeddings, cfg.speaker_threshold)
    labelled = dz.assign_speakers(segments, result.turns)
    order = list(dict.fromkeys(s.speaker for s in sorted(labelled, key=lambda s: s.start)))
    order += [lb for lb in sorted({t.label for t in result.turns}) if lb not in order]
    store.save_meeting_speakers(mid, [(label, f"Spreker {i}", people.get(label),
                                       result.embeddings.get(label))
                                      for i, label in enumerate(order, 1)])
    known = sorted({store.get_person(pid)["name"] for pid in people.values() if pid})
    progress(f"{len(order)} spreker(s) gevonden" + (f", herkend: {', '.join(known)}" if known else ""))
    return labelled


def render_files(store: Store, mid: int) -> None:
    """(Re)write transcript.md and summary.md with the current names. No model involved."""
    m = store.get_meeting(mid)
    sps = store.get_meeting_speakers(mid)
    names = {sp.label: sp.name for sp in sps}
    (m.dir / "transcript.md").write_text(to_markdown(m.title, store.get_segments(mid), names))
    if m.summary:
        (m.dir / "summary.md").write_text(f"# {m.title}\n\n{render(m.summary, sps)}\n")


write_transcript = render_files   # older name


def assign_speaker(cfg: Config, store: Store, mid: int, label: str,
                   person: str | int | None) -> int | None:
    """Link a meeting speaker to a person (by id or name; a new name creates the person).
    Linking several speakers to one person merges them. None/"" unlinks.
    Summaries follow automatically: they contain codes, not names."""
    sp = next((x for x in store.get_meeting_speakers(mid) if x.label == label), None)
    if sp is None:
        raise ValueError(f"Spreker {label} hoort niet bij meeting {mid}")
    if person is None or (isinstance(person, str) and not person.strip()):
        store.set_speaker_person(mid, label, None)
        render_files(store, mid)
        return None
    pid = person if isinstance(person, int) else (store.find_person(person)
                                                  or store.create_person(person))
    store.set_speaker_person(mid, label, pid)
    if sp.embedding is not None and label != MIC_LABEL:
        talk = sum(s.end - s.start for s in store.get_segments(mid) if s.speaker == label)
        if talk >= speakers.MIN_LEARN_SECONDS:
            speakers.learn(store, pid, sp.embedding)
    render_files(store, mid)
    return pid


def refresh_people(store: Store, meeting_ids: list[int] | None = None) -> None:
    """Rewrite the files of meetings after people were renamed or merged."""
    for mid in meeting_ids or [m.id for m in store.list_meetings()]:
        try:
            render_files(store, mid)
        except (OSError, KeyError):
            pass


def _llm_tags(sps) -> dict[str, str]:
    """label -> code for the model. Speakers that are the same person share one code."""
    by_person: dict[int, str] = {}
    out = {}
    for sp in sps:
        if not sp.tag:
            continue
        if sp.person_id is not None:
            out[sp.label] = f"[{by_person.setdefault(sp.person_id, sp.tag)}]"
        else:
            out[sp.label] = f"[{sp.tag}]"
    return out


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
    segments = store.get_segments(mid)
    sps = store.get_meeting_speakers(mid)
    lines = to_lines(segments, _llm_tags(sps))          # speakers as codes: [S1], [S2]
    named_lines = to_lines(segments, {sp.label: sp.name for sp in sps})
    if not lines:
        progress("Geen spraak gevonden, samenvatting overgeslagen")
        return
    llm = LLM(cfg)
    from .llm import CHARS_PER_TOKEN, RESERVED_TOKENS
    progress("Model klaarzetten in LM Studio")
    needed = int(sum(len(ln) + 1 for ln in lines) / CHARS_PER_TOKEN) + RESERVED_TOKENS
    llm.ensure_context(needed, on_progress=progress)   # whole meeting in one go when possible
    chunks = chunk_lines(lines, llm.chunk_chars)
    parts = "in één keer" if len(chunks) == 1 else f"in {len(chunks)} delen (te lang voor het model)"
    progress(f"Samenvatten met {llm.model} ({llm.context} tokens context), {parts}")
    summary = llm.summarize(chunks, on_progress=progress, legend=speaker_legend(sps))
    summary = names_to_tags(summary, sps)   # in case the model wrote a name instead of a code
    if not summary.strip():
        # Never replace a summary with nothing.
        raise RuntimeError(f"{llm.model} gaf een lege samenvatting; probeer een ander model "
                           "of zet 'Model laten nadenken' uit")
    store.save_summary(mid, summary)
    if meeting.title_auto:
        try:
            progress("Titel bedenken")
            title = llm.title("\n".join(named_lines)[:3000], render(summary, sps),
                              language=meeting.language or cfg.language)
            if title:
                store.rename_meeting(mid, title, auto=True)  # stays automatic until renamed
        except Exception as e:  # a missing title is no reason to fail the summary
            progress(f"Titel bedenken lukte niet: {e}")
    render_files(store, mid)

    progress("Index voor vragen opbouwen")
    qa_chunks = chunk_lines(lines, QA_CHUNK_CHARS)
    try:
        emb = llm.embed(qa_chunks)
    except Exception as e:  # embedding model not loaded: Q&A falls back to full text
        progress(f"Embeddings niet beschikbaar ({e}); vragen gebruiken dan de volledige tekst")
        emb = None
    store.save_chunks(mid, qa_chunks, emb)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())   # "HAP-data" -> "hap", "data"


def _close(a: str, b: str) -> bool:
    """Same word, or one letter different (HAB/HAP, Westerduin/Westerdijn)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 3 or abs(len(a) - len(b)) > 1:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= (0.66 if len(a) <= 4 else 0.8)


_STOP = {"de", "het", "een", "en", "van", "is", "er", "wat", "wie", "hoe", "waar", "wanneer",
         "bij", "in", "op", "te", "dat", "die", "niet", "wel", "we", "ik", "je", "zijn", "werkt",
         "over", "met", "voor", "aan", "om", "gaat", "gaan", "doet", "doen", "kan", "moet",
         "heeft", "hebben", "was", "waren", "ging", "the", "and", "what", "why", "how"}


def keyword_score(question: str, text: str) -> float:
    """Share of the question's meaningful words that occur (or nearly occur) in the text."""
    q = [w for w in _words(question) if w not in _STOP and len(w) >= 2]
    if not q:
        return 0.0
    words = set(_words(text))
    return sum(1 for w in q if any(_close(w, t) for t in words)) / len(q)


def ask(cfg: Config, store: Store, question: str, mid: int | None = None, top_k: int = 4,
        folder_id: int | None = None) -> str:
    llm = LLM(cfg)
    budget = llm.chunk_chars   # what fits in the model's context next to the question

    if mid is not None:
        # One meeting: give the model the whole transcript when it fits (it nearly always
        # does). Searching for pieces misses things, e.g. a term the transcript misspelled.
        sps = store.get_meeting_speakers(mid)
        lines = to_lines(store.get_segments(mid), {sp.label: sp.name for sp in sps})
        full = "\n".join(lines)
        if not full:
            return "Deze meeting heeft nog geen transcript."
        m = store.get_meeting(mid)
        if len(full) <= budget:
            return llm.answer(question, [f"Meeting: {m.title} ({m.created_at[:10]})\n{full}"])

    chunks = store.get_chunks(mid, folder_id=folder_id)
    if not chunks:
        if folder_id is not None:
            return "Deze map bevat nog geen getranscribeerde meetings."
        return "Er zijn nog geen getranscribeerde meetings om vragen over te stellen."
    # Rank by meaning (embeddings) and by words, including near-misses like HAB/HAP.
    scores = np.array([keyword_score(question, c[1]) for c in chunks], dtype=np.float32)
    with_emb = [i for i, c in enumerate(chunks) if c[2] is not None]
    if with_emb:
        try:
            q = llm.embed([question], query=True)[0]
            sem = np.zeros(len(chunks), dtype=np.float32)
            for i in with_emb:
                sem[i] = float(chunks[i][2] @ q)
            scores = scores + sem
        except Exception:
            pass
    context, used = [], 0
    for i in np.argsort(-scores):
        meeting_id, text, _ = chunks[i]
        m = store.get_meeting(meeting_id)
        text = render(text, store.get_meeting_speakers(meeting_id))   # codes -> current names
        piece = f"Meeting: {m.title} ({m.created_at[:10]})\n{text}"
        if used + len(piece) > budget and context:
            break
        context.append(piece)
        used += len(piece)
    return llm.answer(question, context)

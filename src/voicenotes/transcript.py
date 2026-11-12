"""Formatting and chunking of speaker-labelled transcripts."""
from __future__ import annotations

from .asr.final import Segment


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def merge_turns(segments: list[Segment], max_gap: float = 1.5) -> list[Segment]:
    """Join consecutive segments of the same speaker into one turn."""
    turns: list[Segment] = []
    for s in sorted(segments, key=lambda x: x.start):
        last = turns[-1] if turns else None
        if last and last.speaker == s.speaker and s.start - last.end <= max_gap:
            last.end = max(last.end, s.end)
            last.text = f"{last.text} {s.text}"
        else:
            turns.append(Segment(s.start, s.end, s.text, s.speaker))
    return turns


def relabel(segments: list[Segment], names: dict[str, str] | None) -> list[Segment]:
    """Speaker labels -> names (or codes). Labels mapped to the same name merge into one."""
    if not names:
        return segments
    return [Segment(s.start, s.end, s.text, names.get(s.speaker, s.speaker)) for s in segments]


def to_lines(segments: list[Segment], names: dict[str, str] | None = None) -> list[str]:
    lines = []
    for t in merge_turns(relabel(segments, names)):
        who = f"{t.speaker}: " if t.speaker else ""
        lines.append(f"[{fmt_time(t.start)}] {who}{t.text}")
    return lines


def to_markdown(title: str, segments: list[Segment], names: dict[str, str] | None = None) -> str:
    return f"# {title}\n\n" + "\n\n".join(to_lines(segments, names)) + "\n"


def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    """Group lines into chunks of at most max_chars (a single long line stays whole)."""
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for line in lines:
        if cur and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _norm_words(text: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in text).split()


def drop_echo(mic: list[Segment], system: list[Segment], min_similarity: float = 0.6) -> list[Segment]:
    """Remove mic segments that are just the speakers' audio picked up by the microphone.

    A mic segment counts as echo when it overlaps in time with system-audio segments
    whose words largely contain the mic segment's words.
    """
    from difflib import SequenceMatcher

    kept = []
    for m in mic:
        words = _norm_words(m.text)
        overlapping = [s for s in system if s.start < m.end + 1 and s.end > m.start - 1]
        if not words or not overlapping:
            kept.append(m)
            continue
        other = _norm_words(" ".join(s.text for s in overlapping))
        matcher = SequenceMatcher(None, words, other, autojunk=False)
        matched = sum(b.size for b in matcher.get_matching_blocks())
        if matched / len(words) < min_similarity:
            kept.append(m)
    return kept

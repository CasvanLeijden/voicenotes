from voicenotes.asr.final import Segment
from voicenotes.transcript import chunk_lines, fmt_time, merge_turns, to_lines


def test_fmt_time():
    assert fmt_time(65) == "01:05"
    assert fmt_time(3725) == "1:02:05"


def test_merge_turns_joins_same_speaker_and_splits_on_change():
    segs = [
        Segment(0, 2, "Hallo", "A"),
        Segment(2.5, 4, "allemaal", "A"),
        Segment(4.2, 6, "Hoi", "B"),
        Segment(10, 12, "Nog iets", "B"),  # gap > 1.5s -> new turn
    ]
    turns = merge_turns(segs)
    assert [(t.speaker, t.text) for t in turns] == [
        ("A", "Hallo allemaal"), ("B", "Hoi"), ("B", "Nog iets"),
    ]
    assert segs[0].text == "Hallo"  # input not mutated


def test_to_lines_includes_speaker():
    assert to_lines([Segment(61, 63, "Goedemorgen", "Cas")]) == ["[01:01] Cas: Goedemorgen"]


def test_chunk_lines_respects_limit():
    lines = ["x" * 40] * 10
    chunks = chunk_lines(lines, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert sum(c.count("x" * 40) for c in chunks) == 10


def test_chunk_lines_keeps_oversized_line_whole():
    assert chunk_lines(["y" * 500], 100) == ["y" * 500]

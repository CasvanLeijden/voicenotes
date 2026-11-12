import time

import numpy as np
import soundfile as sf

from voicenotes.asr.final import Segment
from voicenotes.audio.writer import StreamWriter
from voicenotes.transcript import drop_echo


def test_drop_echo_removes_speaker_bleed_but_keeps_own_speech():
    system = [Segment(10, 14, "Ik stel voor dat we de lancering verschuiven naar 15 maart.", "Jan")]
    mic = [
        Segment(10.3, 14.2, "stel voor dat we de lancering verschuiven naar 15 maart", ""),  # echo
        Segment(12, 13, "Ja, eens!", ""),            # own interjection during overlap
        Segment(30, 32, "Zelfde woorden maar later: lancering 15 maart", ""),  # no overlap
    ]
    kept = [s.text for s in drop_echo(mic, system)]
    assert kept == ["Ja, eens!", "Zelfde woorden maar later: lancering 15 maart"]


def test_writer_resamples_and_pads_to_total_length(tmp_path):
    t0 = time.monotonic()
    w = StreamWriter(tmp_path / "x.flac", t0)
    for _ in range(10):  # 1 s of 48 kHz stereo
        w.write(np.full((4800, 2), 0.1, dtype=np.float32), 48000)
    w.close(total_seconds=2.0)
    audio, rate = sf.read(tmp_path / "x.flac")
    assert rate == 16000
    assert abs(len(audio) - 32000) < 400
    assert abs(np.median(audio[1000:15000]) - 0.1) < 0.01


def test_dictation_spans_are_dropped_from_meeting_mic(tmp_path):
    from voicenotes.pipeline import drop_dictation, load_dictation_spans, save_dictation_spans
    save_dictation_spans(tmp_path, [(10.0, 14.0)])
    spans = load_dictation_spans(tmp_path)
    mic = [Segment(2, 5, "voor de meeting"), Segment(10.5, 13.5, "gedicteerde mail"),
           Segment(20, 22, "na het dicteren")]
    assert [s.text for s in drop_dictation(mic, spans)] == ["voor de meeting", "na het dicteren"]
    assert load_dictation_spans(tmp_path / "nope") == []

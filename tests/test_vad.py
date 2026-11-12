import numpy as np

from voicenotes.audio.vad import FRAME, RATE, Segmenter


def _seg(**kw):
    # Fake VAD: a frame is "speech" when its mean amplitude is high.
    return Segmenter(prob_fn=lambda f: 1.0 if np.abs(f).mean() > 0.05 else 0.0, **kw)


def _signal(pattern):
    """pattern: list of (seconds, is_speech)."""
    return np.concatenate([np.full(int(s * RATE), 0.2 if sp else 0.0, dtype=np.float32)
                           for s, sp in pattern])


def test_cuts_at_pauses_with_correct_times():
    seg = _seg()
    audio = _signal([(1, False), (2, True), (1, False), (1.5, True), (1, False)])
    utts = []
    for i in range(0, len(audio), 1600):
        utts += seg.feed(audio[i:i + 1600])
    assert len(utts) == 2
    assert abs(utts[0].start - 1.0) < 0.3 and abs(utts[0].end - 3.0) < 0.3
    assert abs(utts[1].start - 4.0) < 0.3


def test_offset_sets_time_base_and_flush_returns_open_speech():
    seg = _seg()
    utts = seg.feed(_signal([(1, True)]), offset=10 * RATE)
    assert utts == []
    u = seg.flush()
    assert u is not None and abs(u.start - 10.0) < 0.3


def test_long_speech_is_split_at_max_length():
    seg = _seg(max_length=2.0)
    utts = seg.feed(_signal([(5, True)]))
    assert len(utts) == 2
    assert all(len(u.audio) <= 2 * RATE + FRAME for u in utts)


def test_short_blips_are_ignored():
    seg = _seg()
    assert seg.feed(_signal([(0.1, True), (1, False)])) == []


def test_long_speech_is_cut_at_a_short_dip_between_words():
    # 4.5 s of speech with a 64 ms dip at 3.5 s; max length 4 s -> cut at the dip, not at 4 s.
    seg = Segmenter(prob_fn=lambda f: 1.0 if np.abs(f).mean() > 0.05 else 0.3, max_length=4.0)
    audio = _signal([(3.5, True), (0.064, False), (1.0, True)])
    utts = seg.feed(audio)
    assert len(utts) == 1
    assert abs(utts[0].end - 3.55) < 0.1

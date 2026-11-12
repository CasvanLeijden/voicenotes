import numpy as np

from voicenotes.asr.final import Segment, Word
from voicenotes.diarize import Turn, assign_speakers
from voicenotes.speakers import learn, match
from voicenotes.store import Store


def test_assign_speakers_splits_segment_on_speaker_change():
    seg = Segment(0, 4, "Hallo daar. Hoi!", words=[
        Word(0.0, 0.5, " Hallo"), Word(0.5, 1.0, " daar."), Word(2.1, 2.6, " Hoi!"),
    ])
    out = assign_speakers([seg], [Turn(0, 1.5, "A"), Turn(2.0, 3.0, "B")])
    assert [(s.speaker, s.text) for s in out] == [("A", "Hallo daar."), ("B", "Hoi!")]


def test_assign_speakers_uses_nearest_turn_for_gaps():
    seg = Segment(5, 6, "Ja", words=[Word(5.0, 5.3, " Ja")])
    out = assign_speakers([seg], [Turn(0, 2, "A"), Turn(5.5, 7, "B")])
    assert out[0].speaker == "B"


def test_known_voices_are_matched_to_people(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    jan = store.create_person("Jan")
    learn(store, jan, np.array([1, 0, 0], dtype=np.float32))
    got = match(store, {"SPEAKER_00": np.array([0, 1, 0], np.float32),
                        "SPEAKER_01": np.array([1, 0, 0.1], np.float32)})
    assert got == {"SPEAKER_00": None, "SPEAKER_01": jan}


def test_one_person_matches_at_most_one_speaker(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    jan = store.create_person("Jan")
    learn(store, jan, np.array([1, 0], dtype=np.float32))
    got = match(store, {"A": np.array([1, 0.05], np.float32), "B": np.array([1, 0.1], np.float32)})
    assert got == {"A": jan, "B": None}


def test_people_without_a_voice_are_not_matched(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_person("Piet")   # created by hand, no voice yet
    assert match(store, {"A": np.array([1, 0], np.float32)}) == {"A": None}


def test_learn_updates_running_mean(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    jan = store.create_person("Jan")
    learn(store, jan, np.array([1, 0], dtype=np.float32))
    learn(store, jan, np.array([0, 1], dtype=np.float32))
    (_, name, emb, n), = store.voice_profiles()
    assert name == "Jan" and n == 2
    assert np.allclose(emb, [0.7071, 0.7071], atol=1e-3)

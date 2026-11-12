import numpy as np

from voicenotes.asr.final import Segment, Word
from voicenotes.diarize import Turn, assign_speakers
from voicenotes.speakers import learn, match
from voicenotes.store import Store


def test_assign_speakers_splits_segment_on_speaker_change():
    seg = Segment(0, 4, "Hallo daar. Hoi!", words=[
        Word(0.0, 0.5, " Hallo"), Word(0.5, 1.0, " daar."), Word(2.1, 2.6, " Hoi!"),
    ])
    turns = [Turn(0, 1.5, "A"), Turn(2.0, 3.0, "B")]
    out = assign_speakers([seg], turns)
    assert [(s.speaker, s.text) for s in out] == [("A", "Hallo daar."), ("B", "Hoi!")]


def test_assign_speakers_uses_nearest_turn_for_gaps():
    seg = Segment(5, 6, "Ja", words=[Word(5.0, 5.3, " Ja")])
    out = assign_speakers([seg], [Turn(0, 2, "A"), Turn(5.5, 7, "B")])
    assert out[0].speaker == "B"


def test_profiles_are_recognised_and_unknowns_numbered(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    jan = np.array([1, 0, 0], dtype=np.float32)
    piet = np.array([0, 1, 0], dtype=np.float32)
    learn(store, "Jan", jan)
    names = match(store, {
        "SPEAKER_00": piet,
        "SPEAKER_01": jan + np.array([0, 0, 0.1], dtype=np.float32),
    })
    assert names["SPEAKER_01"][0] == "Jan"
    assert names["SPEAKER_00"] == ("Spreker 1", None)


def test_one_profile_matches_at_most_one_speaker(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    learn(store, "Jan", np.array([1, 0], dtype=np.float32))
    names = match(store, {"A": np.array([1, 0.05], dtype=np.float32),
                          "B": np.array([1, 0.1], dtype=np.float32)})
    assert sorted(n for n, _ in names.values()) == ["Jan", "Spreker 1"]
    assert names["A"][0] == "Jan"


def test_learn_updates_running_mean(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    learn(store, "Jan", np.array([1, 0], dtype=np.float32))
    learn(store, "jan", np.array([0, 1], dtype=np.float32))
    (_, name, emb, n), = store.get_profiles()
    assert name == "Jan" and n == 2
    assert np.allclose(emb, [0.7071, 0.7071], atol=1e-3)

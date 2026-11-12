"""People are reusable across meetings; speakers link to them; summaries use codes."""
import sqlite3

import numpy as np
import pytest

from voicenotes import pipeline
from voicenotes.asr.final import Segment
from voicenotes.config import Config
from voicenotes.names import names_to_tags, render
from voicenotes.store import MIC_LABEL, Store


def meeting(store, tmp_path, speakers, segments):
    m = store.create_meeting("M", "recording", tmp_path / "m")
    store.save_meeting_speakers(m.id, speakers)
    store.save_segments(m.id, segments)
    store.assign_tags(m.id)
    return m


def test_people_crud(tmp_path):
    store = Store(tmp_path / "db.sqlite", my_name="Cas")
    jan = store.create_person("Jan")
    assert store.create_person("jan") == jan                 # no duplicates
    store.rename_person(jan, "Jan de Vries")
    piet = store.create_person("Piet")
    with pytest.raises(ValueError):
        store.rename_person(piet, "jan de vries")             # name taken: merge instead
    names = [p["name"] for p in store.list_people()]
    assert names[0] == "Cas" and set(names) == {"Cas", "Jan de Vries", "Piet"}
    me = next(p for p in store.list_people() if p["isMe"])
    with pytest.raises(ValueError):
        store.delete_person(me["id"])                         # can't delete yourself
    store.delete_person(piet)
    assert store.find_person("Piet") is None


def test_speakers_linked_to_same_person_merge_and_names_follow(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    m = meeting(store, tmp_path,
                [("SPEAKER_00", "Spreker 1", None, np.ones(3, np.float32)),
                 ("SPEAKER_01", "Spreker 2", None, np.ones(3, np.float32))],
                [Segment(0, 30, "Hallo", "SPEAKER_00"), Segment(30, 31, "ja", "SPEAKER_01"),
                 Segment(31, 40, "Verder", "SPEAKER_00")])
    store.save_summary(m.id, "[S1] stelde voor om te beginnen; [S2] was het eens.")
    cfg = Config()
    pipeline.assign_speaker(cfg, store, m.id, "SPEAKER_00", "Jan")
    pipeline.assign_speaker(cfg, store, m.id, "SPEAKER_01", "Jan")   # same person: merged
    sps = store.get_meeting_speakers(m.id)
    assert {sp.name for sp in sps} == {"Jan"}
    assert render(store.get_meeting(m.id).summary, sps) == \
        "Jan stelde voor om te beginnen; Jan was het eens."
    from voicenotes.transcript import to_lines
    assert len(to_lines(store.get_segments(m.id), store.speaker_names(m.id))) == 1  # one turn
    # Only the long speaker (30+9 s) taught Jan's voice, not the 1-second "ja".
    assert store.voice_profiles()[0][3] == 1
    # Renaming the person updates the summary without generating it again
    store.rename_person(store.find_person("Jan"), "Jan de Vries")
    assert render(store.get_meeting(m.id).summary, store.get_meeting_speakers(m.id)).startswith(
        "Jan de Vries stelde voor")


def test_merge_people_relinks_meetings_and_averages_voices(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    a, b = store.create_person("Jan"), store.create_person("J. de Vries")
    store.set_person_voice(a, np.array([1, 0], np.float32), 1)
    store.set_person_voice(b, np.array([0, 1], np.float32), 1)
    m = meeting(store, tmp_path, [("SPEAKER_00", "Spreker 1", b, None)],
                [Segment(0, 1, "hoi", "SPEAKER_00")])
    store.merge_people(b, a)
    assert store.get_meeting_speakers(m.id)[0].name == "Jan"
    (_, _, emb, n), = store.voice_profiles()
    assert n == 2 and np.allclose(emb, [0.7071, 0.7071], atol=1e-3)


def test_unlink_falls_back_to_default_name(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    m = meeting(store, tmp_path, [("SPEAKER_00", "Spreker 1", None, None)],
                [Segment(0, 1, "hoi", "SPEAKER_00")])
    pipeline.assign_speaker(Config(), store, m.id, "SPEAKER_00", "Jan")
    pipeline.assign_speaker(Config(), store, m.id, "SPEAKER_00", "")
    assert store.get_meeting_speakers(m.id)[0].name == "Spreker 1"


def test_names_to_tags_leaves_common_words_alone(tmp_path):
    store = Store(tmp_path / "db.sqlite", my_name="Ik")
    me = store.ensure_me("Ik")
    m = meeting(store, tmp_path, [(MIC_LABEL, "Ik", me, None), ("SPEAKER_00", "Spreker 1", None, None)],
                [Segment(0, 1, "hoi", MIC_LABEL), Segment(1, 2, "hallo", "SPEAKER_00")])
    sps = store.get_meeting_speakers(m.id)
    text = "Ik denk dat Spreker 1 gelijk had. - [ ] Spreker 1: offerte sturen"
    assert names_to_tags(text, sps) == "Ik denk dat [S2] gelijk had. - [ ] [S2]: offerte sturen"


def test_migration_from_names_to_people(tmp_path):
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
        source TEXT, dir TEXT NOT NULL, summary TEXT);
    CREATE TABLE segments (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, start REAL NOT NULL,
        end REAL NOT NULL, speaker TEXT, text TEXT NOT NULL);
    CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, embedding BLOB NOT NULL,
        samples INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE meeting_speakers (meeting_id INTEGER NOT NULL, label TEXT NOT NULL, name TEXT NOT NULL,
        profile_id INTEGER, embedding BLOB, PRIMARY KEY (meeting_id, label));
    """)
    con.execute("INSERT INTO profiles VALUES (7, 'Ellen', ?, 3)", (np.ones(2, np.float32).tobytes(),))
    con.execute("INSERT INTO meetings VALUES (1, 'Oud', '2026-01-01T10:00:00', 'recording', ?, "
                "'Ellen en Spreker 2 spraken af dat Ik de offerte stuur.')", (str(tmp_path),))
    con.executemany("INSERT INTO meeting_speakers VALUES (1, ?, ?, ?, NULL)",
                    [("SPEAKER_00", "Ellen", 7), ("SPEAKER_01", "Spreker 2", None)])
    con.executemany("INSERT INTO segments (meeting_id, start, end, speaker, text) VALUES (1, ?, ?, ?, ?)",
                    [(0, 1, "Ik", "hoi"), (1, 2, "Ellen", "hallo"), (2, 3, "Spreker 2", "ja")])
    con.commit(); con.close()

    store = Store(db, my_name="Ik")
    segs = store.get_segments(1)
    assert [s.speaker for s in segs] == [MIC_LABEL, "SPEAKER_00", "SPEAKER_01"]
    sps = {sp.label: sp for sp in store.get_meeting_speakers(1)}
    assert sps["SPEAKER_00"].name == "Ellen" and sps["SPEAKER_00"].person_id == 7
    assert sps[MIC_LABEL].is_me and sps[MIC_LABEL].tag == "S1"
    summary = store.get_meeting(1).summary
    assert summary == "[S2] en [S3] spraken af dat Ik de offerte stuur."
    store.rename_person(7, "Ellen Jansen")
    assert render(summary, store.get_meeting_speakers(1)).startswith("Ellen Jansen en Spreker 2")
    assert list(tmp_path.glob("old.voor-personen-*.db"))   # backup made before migrating
    Store(db, my_name="Ik")                                 # second open: no second migration

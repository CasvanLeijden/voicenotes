import numpy as np

from voicenotes.asr.final import Segment
from voicenotes.store import Store


def test_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    m = store.create_meeting("Test", "import:x.wav", tmp_path / "meetings")
    assert m.dir.is_dir()
    store.save_segments(m.id, [Segment(1, 2, "b", "B"), Segment(0, 1, "a", "A")])
    assert [s.text for s in store.get_segments(m.id)] == ["a", "b"]
    store.save_chunks(m.id, ["c1", "c2"], np.eye(2, dtype=np.float32))
    chunks = store.get_chunks(m.id)
    assert [c[1] for c in chunks] == ["c1", "c2"]
    assert np.allclose(chunks[1][2], [0, 1])


def test_folders(tmp_path):
    import pytest
    store = Store(tmp_path / "db.sqlite")
    work = store.create_folder("Klant X")
    assert store.create_folder("klant x") == work  # case-insensitive, no duplicates
    a = store.create_meeting("A", None, tmp_path / "m", folder_id=work)
    b = store.create_meeting("B", None, tmp_path / "m")
    assert [m.title for m in store.list_meetings(work)] == ["A"]
    store.set_meeting_folder(b.id, work)
    assert store.list_folders() == [{"id": work, "name": "Klant X", "count": 2}]
    store.save_chunks(a.id, ["in map"], None)
    assert [c[1] for c in store.get_chunks(folder_id=work)] == ["in map"]
    other = store.create_folder("Intern")
    with pytest.raises(ValueError):
        store.rename_folder(other, "Klant X")
    store.delete_folder(work)
    assert store.get_meeting(a.id).folder_id is None
    assert len(store.list_meetings()) == 2


def test_migration_adds_folder_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT NOT NULL, "
                "created_at TEXT NOT NULL, source TEXT, dir TEXT NOT NULL, summary TEXT)")
    con.execute("INSERT INTO meetings VALUES (1, 'Oud', '2026-01-01T10:00:00', NULL, '/tmp', NULL)")
    con.commit(); con.close()
    store = Store(db)
    assert store.get_meeting(1).folder_id is None


def test_copy_meeting(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    fid = store.create_folder("Klant")
    m = store.create_meeting("Origineel", "recording", tmp_path / "m", folder_id=fid)
    (m.dir / "mic.flac").write_bytes(b"x")
    (m.dir / "summary.md").write_text("oud")
    store.save_segments(m.id, [Segment(0, 1, "hallo", "Jan")])
    jan = store.create_person("Jan")
    store.save_meeting_speakers(m.id, [("SPEAKER_00", "Spreker 1", jan, np.ones(3, np.float32))])
    store.save_summary(m.id, "oud")
    c = store.copy_meeting(m.id, "Kopie", tmp_path / "m")
    assert c.id != m.id and c.folder_id == fid and c.summary is None
    assert (c.dir / "mic.flac").exists() and not (c.dir / "summary.md").exists()
    assert [s.text for s in store.get_segments(c.id)] == ["hallo"]
    assert [x.name for x in store.get_meeting_speakers(c.id)] == ["Jan"]
    assert store.get_meeting(m.id).summary == "oud"   # original untouched

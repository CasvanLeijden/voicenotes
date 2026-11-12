"""SQLite storage for meetings, transcript segments and Q&A chunks."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .asr.final import Segment

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT,
    dir TEXT NOT NULL,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    end REAL NOT NULL,
    speaker TEXT,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    embedding BLOB NOT NULL,
    samples INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS meeting_speakers (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    embedding BLOB,
    PRIMARY KEY (meeting_id, label)
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    embedding BLOB,
    samples INTEGER NOT NULL DEFAULT 0,
    is_me INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON segments(meeting_id);
CREATE INDEX IF NOT EXISTS idx_chunks_meeting ON chunks(meeting_id);
"""


@dataclass
class Meeting:
    id: int
    title: str
    created_at: str
    source: str | None
    dir: Path
    summary: str | None
    folder_id: int | None = None
    title_auto: bool = False   # True while the title is a placeholder we may replace
    language: str | None = None


MIC_LABEL = "MIC"          # the user's own microphone channel
OTHERS_LABEL = "OTHERS"    # everyone else, as long as nobody has been told apart (live)
_DEFAULT_NAME = re.compile(r"^Spreker \d+$")


@dataclass
class Speaker:
    """A voice in one meeting. Its name comes from the linked person, if any, so renaming
    or merging people updates every meeting without touching the meeting itself."""
    label: str                  # diarization label (SPEAKER_00), MIC or OTHERS
    tag: str | None             # stable code used in summaries: S1, S2, ...
    default_name: str           # "Spreker 2", the user's name for MIC, ...
    person_id: int | None
    person_name: str | None
    embedding: np.ndarray | None
    is_me: bool = False

    @property
    def name(self) -> str:
        return self.person_name or self.default_name


class Store:
    def __init__(self, db_path: Path, my_name: str = "Ik"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._migrate()
        self._migrate_people(my_name)

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(meetings)")}
        if "folder_id" not in cols:
            self.db.execute("ALTER TABLE meetings ADD COLUMN folder_id INTEGER "
                            "REFERENCES folders(id) ON DELETE SET NULL")
        if "language" not in cols:
            self.db.execute("ALTER TABLE meetings ADD COLUMN language TEXT")
        if "title_auto" not in cols:
            # Existing meetings keep their titles.
            self.db.execute("ALTER TABLE meetings ADD COLUMN title_auto INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def _version(self) -> int:
        row = self.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 1

    def _migrate_people(self, my_name: str) -> None:
        """v2: speakers point to people; segments store speaker labels instead of names."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(meeting_speakers)")}
        for col, ddl in (("tag", "TEXT"), ("default_name", "TEXT"),
                         ("person_id", "INTEGER REFERENCES people(id) ON DELETE SET NULL")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE meeting_speakers ADD COLUMN {col} {ddl}")
        if self._version() >= 2:
            self.db.commit()
            return
        has_data = self.db.execute("SELECT COUNT(*) AS n FROM meetings").fetchone()["n"]
        if has_data:
            import shutil
            shutil.copy2(self.path, self.path.with_name(
                f"{self.path.stem}.voor-personen-{datetime.now():%Y%m%d-%H%M%S}.db"))
        now = datetime.now().isoformat(timespec="seconds")
        # 1. voice profiles become people
        for r in self.db.execute("SELECT id, name, embedding, samples FROM profiles").fetchall():
            self.db.execute("INSERT OR IGNORE INTO people (id, name, embedding, samples, created_at) "
                            "VALUES (?, ?, ?, ?, ?)", (r["id"], r["name"], r["embedding"],
                                                        r["samples"], now))
        me = self._ensure_me_row(my_name)
        # 2. speakers per meeting: link people, keep a default name
        for r in self.db.execute("SELECT meeting_id, label, name, profile_id FROM meeting_speakers"
                                 ).fetchall():
            pid = r["profile_id"]
            if pid is None and not _DEFAULT_NAME.match(r["name"]) and r["name"] != r["label"]:
                pid = self._person_id_for(r["name"], now)   # named earlier without a voice
            self.db.execute("UPDATE meeting_speakers SET person_id = ?, default_name = ? "
                            "WHERE meeting_id = ? AND label = ?",
                            (pid, r["name"], r["meeting_id"], r["label"]))
        # 3. segments: names -> labels
        for (mid,) in self.db.execute("SELECT id FROM meetings").fetchall():
            by_name = {r["name"]: r["label"] for r in self.db.execute(
                "SELECT label, name FROM meeting_speakers WHERE meeting_id = ?", (mid,))}
            names = [r["speaker"] for r in self.db.execute(
                "SELECT DISTINCT speaker FROM segments WHERE meeting_id = ? AND speaker != ''",
                (mid,))]
            for name in names:
                label = by_name.get(name)
                if label is None:
                    if name == my_name:
                        label, pid = MIC_LABEL, me
                    elif name == "Anderen":
                        label, pid = OTHERS_LABEL, None
                    else:
                        label = f"NAME:{name}"
                        pid = None if _DEFAULT_NAME.match(name) else self._person_id_for(name, now)
                    self.db.execute(
                        "INSERT OR IGNORE INTO meeting_speakers "
                        "(meeting_id, label, name, default_name, person_id) VALUES (?, ?, ?, ?, ?)",
                        (mid, label, name, name, pid))
                self.db.execute("UPDATE segments SET speaker = ? WHERE meeting_id = ? AND speaker = ?",
                                (label, mid, name))
            self.assign_tags(mid, commit=False)
            # 4. existing summaries: names -> tags, so they follow renames from now on
            row = self.db.execute("SELECT summary FROM meetings WHERE id = ?", (mid,)).fetchone()
            if row["summary"]:
                from .names import names_to_tags
                tagged = names_to_tags(row["summary"], self.get_meeting_speakers(mid))
                self.db.execute("UPDATE meetings SET summary = ? WHERE id = ?", (tagged, mid))
        self.db.execute("INSERT INTO schema_version (version) VALUES (2)")
        self.db.commit()

    def _person_id_for(self, name: str, now: str) -> int:
        row = self.db.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        return self.db.execute("INSERT INTO people (name, created_at) VALUES (?, ?)",
                               (name, now)).lastrowid

    def _ensure_me_row(self, name: str) -> int:
        row = self.db.execute("SELECT id FROM people WHERE is_me = 1").fetchone()
        if row:
            return row["id"]
        pid = self._person_id_for(name, datetime.now().isoformat(timespec="seconds"))
        self.db.execute("UPDATE people SET is_me = 1 WHERE id = ?", (pid,))
        return pid

    def create_meeting(self, title: str, source: str | None, meetings_root: Path,
                       folder_id: int | None = None, title_auto: bool = False) -> Meeting:
        """title_auto: the title is a placeholder that may be replaced by a generated one."""
        now = datetime.now()
        cur = self.db.execute(
            "INSERT INTO meetings (title, created_at, source, dir, folder_id, title_auto) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (title, now.isoformat(timespec="seconds"), source, folder_id, int(title_auto)),
        )
        mid = cur.lastrowid
        mdir = meetings_root / f"{now:%Y-%m-%d_%H%M}_{mid}"
        mdir.mkdir(parents=True, exist_ok=True)
        self.db.execute("UPDATE meetings SET dir = ? WHERE id = ?", (str(mdir), mid))
        self.db.commit()
        return self.get_meeting(mid)

    def get_meeting(self, mid: int) -> Meeting:
        row = self.db.execute("SELECT * FROM meetings WHERE id = ?", (mid,)).fetchone()
        if row is None:
            raise KeyError(f"Meeting {mid} bestaat niet")
        return Meeting(row["id"], row["title"], row["created_at"], row["source"],
                       Path(row["dir"]), row["summary"], row["folder_id"], bool(row["title_auto"]),
                       row["language"])

    def list_meetings(self, folder_id: int | None = None) -> list[Meeting]:
        if folder_id is None:
            rows = self.db.execute("SELECT id FROM meetings ORDER BY created_at DESC").fetchall()
        else:
            rows = self.db.execute("SELECT id FROM meetings WHERE folder_id = ? "
                                   "ORDER BY created_at DESC", (folder_id,)).fetchall()
        return [self.get_meeting(r["id"]) for r in rows]

    def save_segments(self, mid: int, segments: list[Segment]) -> None:
        self.db.execute("DELETE FROM segments WHERE meeting_id = ?", (mid,))
        self.db.executemany(
            "INSERT INTO segments (meeting_id, start, end, speaker, text) VALUES (?, ?, ?, ?, ?)",
            [(mid, s.start, s.end, s.speaker, s.text) for s in segments],
        )
        self.db.commit()

    def get_segments(self, mid: int) -> list[Segment]:
        rows = self.db.execute(
            "SELECT start, end, speaker, text FROM segments WHERE meeting_id = ? ORDER BY start",
            (mid,),
        ).fetchall()
        return [Segment(r["start"], r["end"], r["text"], r["speaker"] or "") for r in rows]

    def set_language(self, mid: int, language: str) -> None:
        self.db.execute("UPDATE meetings SET language = ? WHERE id = ?", (language, mid))
        self.db.commit()

    def save_summary(self, mid: int, summary: str) -> None:
        self.db.execute("UPDATE meetings SET summary = ? WHERE id = ?", (summary, mid))
        self.db.commit()

    def save_chunks(self, mid: int, texts: list[str], embeddings: np.ndarray | None) -> None:
        self.db.execute("DELETE FROM chunks WHERE meeting_id = ?", (mid,))
        rows = []
        for i, t in enumerate(texts):
            blob = embeddings[i].astype(np.float32).tobytes() if embeddings is not None else None
            rows.append((mid, t, blob))
        self.db.executemany(
            "INSERT INTO chunks (meeting_id, text, embedding) VALUES (?, ?, ?)", rows
        )
        self.db.commit()

    def get_chunks(self, mid: int | None = None, folder_id: int | None = None
                   ) -> list[tuple[int, str, np.ndarray | None]]:
        q = "SELECT meeting_id, text, embedding FROM chunks"
        args: tuple = ()
        if mid is not None:
            q += " WHERE meeting_id = ?"
            args = (mid,)
        elif folder_id is not None:
            q += " WHERE meeting_id IN (SELECT id FROM meetings WHERE folder_id = ?)"
            args = (folder_id,)
        out = []
        for r in self.db.execute(q, args).fetchall():
            emb = np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None
            out.append((r["meeting_id"], r["text"], emb))
        return out

    # --- people -----------------------------------------------------------

    def list_people(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT p.id, p.name, p.embedding IS NOT NULL AS has_voice, p.samples, p.is_me, "
            "COUNT(DISTINCT ms.meeting_id) AS meetings FROM people p "
            "LEFT JOIN meeting_speakers ms ON ms.person_id = p.id "
            "GROUP BY p.id ORDER BY p.is_me DESC, p.name COLLATE NOCASE").fetchall()
        return [{"id": r["id"], "name": r["name"], "hasVoice": bool(r["has_voice"]),
                 "samples": r["samples"], "isMe": bool(r["is_me"]), "meetings": r["meetings"]}
                for r in rows]

    def get_person(self, pid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None

    def find_person(self, name: str) -> int | None:
        r = self.db.execute("SELECT id FROM people WHERE name = ?", (name.strip(),)).fetchone()
        return r["id"] if r else None

    def create_person(self, name: str) -> int:
        """Returns the existing person's id if the name is taken (case-insensitive)."""
        name = name.strip()
        if not name:
            raise ValueError("Een persoon heeft een naam nodig")
        pid = self._person_id_for(name, datetime.now().isoformat(timespec="seconds"))
        self.db.commit()
        return pid

    def rename_person(self, pid: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Een persoon heeft een naam nodig")
        clash = self.find_person(name)
        if clash is not None and clash != pid:
            raise ValueError(f"Er is al iemand die '{name}' heet. Voeg ze samen als het "
                             "dezelfde persoon is.")
        self.db.execute("UPDATE people SET name = ? WHERE id = ?", (name, pid))
        self.db.commit()

    def delete_person(self, pid: int) -> None:
        """Speakers linked to this person fall back to their default name."""
        if self.db.execute("SELECT is_me FROM people WHERE id = ?", (pid,)).fetchone()["is_me"]:
            raise ValueError("Jezelf kun je niet verwijderen")
        self.db.execute("UPDATE meeting_speakers SET person_id = NULL WHERE person_id = ?", (pid,))
        self.db.execute("DELETE FROM people WHERE id = ?", (pid,))
        self.db.commit()

    def merge_people(self, source: int, target: int) -> None:
        """Fold `source` into `target`: voices are averaged, meetings re-linked."""
        if source == target:
            return
        a, b = self.get_person(source), self.get_person(target)
        if a is None or b is None:
            raise KeyError("Persoon bestaat niet")
        ea = np.frombuffer(a["embedding"], np.float32) if a["embedding"] else None
        eb = np.frombuffer(b["embedding"], np.float32) if b["embedding"] else None
        na, nb = max(a["samples"], 1 if ea is not None else 0), max(b["samples"], 1 if eb is not None else 0)
        if ea is not None and eb is not None:
            emb = (ea * na + eb * nb) / (na + nb)
            emb = (emb / (np.linalg.norm(emb) + 1e-9)).astype(np.float32)
        else:
            emb = eb if eb is not None else ea
        self.db.execute("UPDATE meeting_speakers SET person_id = ? WHERE person_id = ?",
                        (target, source))
        self.db.execute("UPDATE people SET embedding = ?, samples = ?, is_me = ? WHERE id = ?",
                        (emb.tobytes() if emb is not None else None, na + nb,
                         int(a["is_me"] or b["is_me"]), target))
        self.db.execute("DELETE FROM people WHERE id = ?", (source,))
        self.db.commit()

    def set_person_voice(self, pid: int, embedding: np.ndarray, samples: int) -> None:
        self.db.execute("UPDATE people SET embedding = ?, samples = ? WHERE id = ?",
                        (embedding.astype(np.float32).tobytes(), samples, pid))
        self.db.commit()

    def voice_profiles(self) -> list[tuple[int, str, np.ndarray, int]]:
        """People with a known voice: (id, name, embedding, samples)."""
        rows = self.db.execute("SELECT id, name, embedding, samples FROM people "
                               "WHERE embedding IS NOT NULL").fetchall()
        return [(r["id"], r["name"], np.frombuffer(r["embedding"], dtype=np.float32), r["samples"])
                for r in rows]

    def ensure_me(self, name: str) -> int:
        """The person that is the user (linked to the microphone); renamed to `name`."""
        pid = self._ensure_me_row(name)
        current = self.get_person(pid)["name"]
        if name.strip() and current != name.strip():
            other = self.find_person(name)
            if other is not None and other != pid:
                self.merge_people(other, pid)
            self.db.execute("UPDATE people SET name = ? WHERE id = ?", (name.strip(), pid))
        self.db.commit()
        return pid

    # --- speakers per meeting -------------------------------------------

    def save_meeting_speakers(self, mid: int, speakers: list[tuple]) -> None:
        """Replace the speakers of a meeting.
        speakers: (label, default name, person id, embedding) tuples or Speaker objects."""
        self.db.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (mid,))
        rows = []
        for sp in speakers:
            if isinstance(sp, Speaker):
                sp = (sp.label, sp.default_name, sp.person_id, sp.embedding, sp.tag)
            label, default, pid, emb, *rest = sp
            rows.append((mid, label, default, default, pid, rest[0] if rest else None,
                         emb.astype(np.float32).tobytes() if emb is not None else None))
        self.db.executemany(
            "INSERT INTO meeting_speakers (meeting_id, label, name, default_name, person_id, tag, "
            "embedding) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        self.db.commit()

    def upsert_meeting_speaker(self, mid: int, label: str, default_name: str,
                               person_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO meeting_speakers (meeting_id, label, name, default_name, person_id) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(meeting_id, label) DO UPDATE SET "
            "default_name = excluded.default_name, name = excluded.name, "
            "person_id = COALESCE(meeting_speakers.person_id, excluded.person_id)",
            (mid, label, default_name, default_name, person_id))
        self.db.commit()

    def get_meeting_speakers(self, mid: int) -> list[Speaker]:
        rows = self.db.execute(
            "SELECT ms.label, ms.tag, COALESCE(ms.default_name, ms.name) AS default_name, "
            "ms.person_id, ms.embedding, p.name AS person_name, COALESCE(p.is_me, 0) AS is_me "
            "FROM meeting_speakers ms LEFT JOIN people p ON p.id = ms.person_id "
            "WHERE ms.meeting_id = ? ORDER BY ms.tag IS NULL, CAST(SUBSTR(ms.tag, 2) AS INTEGER), "
            "ms.label", (mid,)).fetchall()
        return [Speaker(r["label"], r["tag"], r["default_name"], r["person_id"], r["person_name"],
                        np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None,
                        bool(r["is_me"])) for r in rows]

    def set_speaker_person(self, mid: int, label: str, person_id: int | None) -> None:
        self.db.execute("UPDATE meeting_speakers SET person_id = ? WHERE meeting_id = ? AND label = ?",
                        (person_id, mid, label))
        self.db.commit()

    def assign_tags(self, mid: int, commit: bool = True) -> None:
        """Give speakers stable codes S1, S2, ... in order of first appearance."""
        order = [r["speaker"] for r in self.db.execute(
            "SELECT speaker, MIN(start) AS t FROM segments WHERE meeting_id = ? AND speaker != '' "
            "GROUP BY speaker ORDER BY t", (mid,))]
        labels = [r["label"] for r in self.db.execute(
            "SELECT label FROM meeting_speakers WHERE meeting_id = ? ORDER BY label", (mid,))]
        for label in labels:
            if label not in order:
                order.append(label)
        for i, label in enumerate(order, 1):
            self.db.execute("UPDATE meeting_speakers SET tag = ? WHERE meeting_id = ? AND label = ?",
                            (f"S{i}", mid, label))
        if commit:
            self.db.commit()

    def speaker_names(self, mid: int) -> dict[str, str]:
        """label -> current display name."""
        return {sp.label: sp.name for sp in self.get_meeting_speakers(mid)}

    # --- meeting management ---------------------------------------------

    def rename_meeting(self, mid: int, title: str, auto: bool = False) -> None:
        """auto=False (a person chose this title) stops automatic renaming for good."""
        self.db.execute("UPDATE meetings SET title = ?, title_auto = ? WHERE id = ?",
                        (title, int(auto), mid))
        self.db.commit()

    def delete_meeting(self, mid: int) -> Path:
        """Delete the meeting rows; returns its folder so the caller can remove the files."""
        m = self.get_meeting(mid)
        self.db.execute("DELETE FROM meetings WHERE id = ?", (mid,))
        self.db.commit()
        return m.dir

    # --- folders --------------------------------------------------------

    def list_folders(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT f.id, f.name, COUNT(m.id) AS n FROM folders f "
            "LEFT JOIN meetings m ON m.folder_id = f.id GROUP BY f.id ORDER BY f.name COLLATE NOCASE"
        ).fetchall()
        return [{"id": r["id"], "name": r["name"], "count": r["n"]} for r in rows]

    def create_folder(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Een map heeft een naam nodig")
        row = self.db.execute("SELECT id FROM folders WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.db.execute("INSERT INTO folders (name, created_at) VALUES (?, ?)",
                              (name, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
        return cur.lastrowid

    def rename_folder(self, fid: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Een map heeft een naam nodig")
        clash = self.db.execute("SELECT id FROM folders WHERE name = ? AND id != ?",
                                (name, fid)).fetchone()
        if clash:
            raise ValueError(f"Er bestaat al een map '{name}'")
        self.db.execute("UPDATE folders SET name = ? WHERE id = ?", (name, fid))
        self.db.commit()

    def delete_folder(self, fid: int) -> None:
        """Remove the folder; its meetings stay, without a folder."""
        self.db.execute("UPDATE meetings SET folder_id = NULL WHERE folder_id = ?", (fid,))
        self.db.execute("DELETE FROM folders WHERE id = ?", (fid,))
        self.db.commit()

    def set_meeting_folder(self, mid: int, fid: int | None) -> None:
        self.db.execute("UPDATE meetings SET folder_id = ? WHERE id = ?", (fid, mid))
        self.db.commit()

    def copy_meeting(self, mid: int, title: str, meetings_root: Path) -> Meeting:
        """Duplicate a meeting (audio files, transcript, speakers, folder); no summary."""
        import shutil
        src = self.get_meeting(mid)
        new = self.create_meeting(title, src.source, meetings_root, folder_id=src.folder_id)
        for f in src.dir.iterdir():
            if f.is_file() and f.name != "summary.md":
                shutil.copy2(f, new.dir / f.name)
        self.save_segments(new.id, self.get_segments(mid))
        self.save_meeting_speakers(new.id, self.get_meeting_speakers(mid))   # keeps tags + people
        if src.language:
            self.set_language(new.id, src.language)
        return self.get_meeting(new.id)

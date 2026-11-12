"""SQLite storage for meetings, transcript segments and Q&A chunks."""
from __future__ import annotations

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


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._migrate()

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

    # --- voice profiles -------------------------------------------------

    def get_profiles(self) -> list[tuple[int, str, np.ndarray, int]]:
        rows = self.db.execute("SELECT id, name, embedding, samples FROM profiles").fetchall()
        return [(r["id"], r["name"], np.frombuffer(r["embedding"], dtype=np.float32), r["samples"])
                for r in rows]

    def get_profile_by_name(self, name: str):
        for p in self.get_profiles():
            if p[1].lower() == name.lower():
                return p
        return None

    def create_profile(self, name: str, embedding: np.ndarray) -> int:
        cur = self.db.execute("INSERT INTO profiles (name, embedding) VALUES (?, ?)",
                              (name, embedding.astype(np.float32).tobytes()))
        self.db.commit()
        return cur.lastrowid

    def update_profile(self, pid: int, embedding: np.ndarray, samples: int) -> None:
        self.db.execute("UPDATE profiles SET embedding = ?, samples = ? WHERE id = ?",
                        (embedding.astype(np.float32).tobytes(), samples, pid))
        self.db.commit()

    def delete_profile(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM profiles WHERE lower(name) = lower(?)", (name,))
        self.db.commit()
        return cur.rowcount > 0

    # --- speakers per meeting -------------------------------------------

    def save_meeting_speakers(self, mid: int,
                              speakers: list[tuple[str, str, int | None, np.ndarray | None]]) -> None:
        """speakers: (diarization label, display name, profile id, embedding)."""
        self.db.execute("DELETE FROM meeting_speakers WHERE meeting_id = ?", (mid,))
        self.db.executemany(
            "INSERT INTO meeting_speakers (meeting_id, label, name, profile_id, embedding) "
            "VALUES (?, ?, ?, ?, ?)",
            [(mid, label, name, pid, emb.astype(np.float32).tobytes() if emb is not None else None)
             for label, name, pid, emb in speakers],
        )
        self.db.commit()

    def get_meeting_speakers(self, mid: int) -> list[tuple[str, str, int | None, np.ndarray | None]]:
        rows = self.db.execute(
            "SELECT label, name, profile_id, embedding FROM meeting_speakers "
            "WHERE meeting_id = ? ORDER BY name", (mid,)).fetchall()
        return [(r["label"], r["name"], r["profile_id"],
                 np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None)
                for r in rows]

    def rename_speaker(self, mid: int, old: str, new: str, profile_id: int | None) -> None:
        self.db.execute("UPDATE segments SET speaker = ? WHERE meeting_id = ? AND speaker = ?",
                        (new, mid, old))
        self.db.execute("UPDATE meeting_speakers SET name = ?, profile_id = ? "
                        "WHERE meeting_id = ? AND name = ?", (new, profile_id, mid, old))
        self.db.commit()

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

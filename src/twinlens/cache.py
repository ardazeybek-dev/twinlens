"""SQLite cache so re-scanning a 40 GB photo library takes seconds, not minutes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .imaging import PhotoRecord

_SCHEMA_VERSION = 1
_COLUMNS = ("path", "size", "mtime", "sha", "phash", "dhash", "width", "height", "sharpness", "taken")


class HashCache:
    """Maps ``path`` to a :class:`PhotoRecord`, valid while size and mtime are unchanged."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.hits = 0
        self.misses = 0
        if path is None:
            self._db = sqlite3.connect(":memory:")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version != _SCHEMA_VERSION:
            self._db.execute("DROP TABLE IF EXISTS photos")
            self._db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS photos (
                path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha TEXT,
                phash TEXT, dhash TEXT, width INTEGER, height INTEGER,
                sharpness REAL, taken TEXT)"""
        )

    def get(self, path: Path, size: int, mtime: float) -> PhotoRecord | None:
        row = self._db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM photos WHERE path = ?", (str(path),)
        ).fetchone()
        if row is None or row[1] != size or abs(row[2] - mtime) > 1e-6:
            self.misses += 1
            return None
        self.hits += 1
        return PhotoRecord(*row)

    def put_many(self, records: list[PhotoRecord]) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        self._db.executemany(
            f"INSERT OR REPLACE INTO photos ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            [tuple(getattr(r, c) for c in _COLUMNS) for r in records],
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> HashCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

"""SQLite store for motion events.

One connection per call: sqlite3 connections are cheap to open and this
sidesteps thread-affinity rules, since every caller reaches us through
``asyncio.to_thread``.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    camera     TEXT    NOT NULL,
    start_ts   REAL    NOT NULL,
    end_ts     REAL    NOT NULL,
    duration   REAL    NOT NULL,
    peak_area  REAL    NOT NULL DEFAULT 0,
    clip       TEXT,
    thumb      TEXT,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_camera_start ON events (camera, start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_start ON events (start_ts DESC);
"""


@dataclass
class Event:
    camera: str
    start_ts: float
    end_ts: float
    peak_area: float

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def insert(self, event: Event) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO events (camera, start_ts, end_ts, duration, peak_area, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.camera,
                    event.start_ts,
                    event.end_ts,
                    event.duration,
                    event.peak_area,
                    time.time(),
                ),
            )
            return int(cur.lastrowid)

    def set_media(self, event_id: int, clip: str | None, thumb: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET clip = ?, thumb = ? WHERE id = ?", (clip, thumb, event_id)
            )

    def get(self, event_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def list(
        self,
        camera: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        where, params = ["1=1"], []
        if camera:
            where.append("camera = ?")
            params.append(camera)
        if since is not None:
            where.append("start_ts >= ?")
            params.append(since)
        if until is not None:
            where.append("start_ts < ?")
            params.append(until)
        if before_id is not None:
            where.append("id < ?")
            params.append(before_id)
        params.append(min(max(limit, 1), 500))

        sql = (
            f"SELECT * FROM events WHERE {' AND '.join(where)}"
            " ORDER BY start_ts DESC, id DESC LIMIT ?"
        )
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def expired(self, cutoff: float, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE start_ts < ? ORDER BY start_ts LIMIT ?",
                (cutoff, limit),
            )
            return [dict(row) for row in rows]

    def delete(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        placeholders = ",".join("?" * len(event_ids))
        with self._connect() as conn:
            conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", event_ids)

    def cameras(self) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT camera FROM events ORDER BY 1")]

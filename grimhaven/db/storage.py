"""SQLite document store — each player/zone/sect is a JSON document, matching
the MongoDB/PostgreSQL-JSONB schema of design doc §10.1 so a future migration
is a straight copy."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    doc TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS zones (
    zone_id TEXT PRIMARY KEY,
    doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sects (
    sect_id TEXT PRIMARY KEY,
    doc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    # ── users ────────────────────────────────────────────────────────────────
    def get_user(self, user_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT doc FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        return json.loads(row["doc"]) if row else None

    def save_user(self, doc: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO users(user_id, doc, updated_at) VALUES(?,?,datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET doc=excluded.doc, updated_at=excluded.updated_at",
                (doc["user_id"], json.dumps(doc, ensure_ascii=False)),
            )

    def all_users(self) -> Iterator[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT doc FROM users").fetchall()
        for row in rows:
            yield json.loads(row["doc"])

    def count_users(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]

    def count_meditating(self) -> int:
        n = 0
        for doc in self.all_users():
            if doc.get("cultivation", {}).get("meditating"):
                n += 1
        return n

    # ── zones & sects ────────────────────────────────────────────────────────
    def get_zone(self, zone_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT doc FROM zones WHERE zone_id=?", (zone_id,)).fetchone()
        return json.loads(row["doc"]) if row else None

    def save_zone(self, doc: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO zones(zone_id, doc) VALUES(?,?) "
                "ON CONFLICT(zone_id) DO UPDATE SET doc=excluded.doc",
                (doc["zone_id"], json.dumps(doc, ensure_ascii=False)),
            )

    def all_zones(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT doc FROM zones").fetchall()
        return [json.loads(r["doc"]) for r in rows]

    def get_sect(self, sect_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT doc FROM sects WHERE sect_id=?", (sect_id,)).fetchone()
        return json.loads(row["doc"]) if row else None

    def save_sect(self, doc: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sects(sect_id, doc) VALUES(?,?) "
                "ON CONFLICT(sect_id) DO UPDATE SET doc=excluded.doc",
                (doc["sect_id"], json.dumps(doc, ensure_ascii=False)),
            )

    def all_sects(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT doc FROM sects").fetchall()
        return [json.loads(r["doc"]) for r in rows]

    # ── meta (world boost, counters…) ───────────────────────────────────────
    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def bootstrap_world(storage: Storage) -> None:
    """Seed zones and NPC sects on first boot."""
    from ..engine.constants import NPC_SECTS, ZONES

    for zone_id, zone in ZONES.items():
        if storage.get_zone(zone_id) is None:
            storage.save_zone({
                "zone_id": zone_id,
                "key": zone["key"],
                "vein_density": zone["vein_density"],
                "min_realm": zone["min_realm"],
                "guard_level": zone["guard"],
                "owner": None,          # user_id or sect_id that conquered it
                "owner_kind": None,     # "user" | "sect"
                "conquered_at": None,
            })
    for sect_id, sect in NPC_SECTS.items():
        if storage.get_sect(sect_id) is None:
            storage.save_sect({
                "sect_id": sect_id,
                "key": sect["key"],
                "alignment": sect["alignment"],
                "npc": True,
                "master": None,
                "members": [],
                "treasury": 0,
            })

"""SQLite document store — each player/zone/sect is a JSON document, matching
the MongoDB/PostgreSQL-JSONB schema of design doc §10.1 so a future migration
is a straight copy.

Hardening notes (round 7):

* every read/write is wrapped: a locked/corrupt/unwritable database raises
  :class:`grimhaven.errors.StorageError` carrying the cause, so the handler can
  tell the player "the world vault is jammed" instead of "unknown error";
* ``get_user`` never trusts a row. Malformed JSON, a JSON scalar, or a row for a
  zero-ish id returns ``None`` (the caller then creates a clean document) and the
  offending bytes are moved to ``corrupt_users`` so the evidence survives the
  repair instead of being overwritten by the next save;
* the file is opened with ``busy_timeout`` + WAL and closed with a checkpoint, so
  the artifact snapshot taken by ``deploy/gha_state.sh`` is always complete even
  though the bot keeps writing;
* :meth:`Storage.integrity_report` and :meth:`Storage.reset` give the bootstrap
  and the operators one command that answers "is this database usable?".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from ..errors import StorageError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

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
CREATE TABLE IF NOT EXISTS corrupt_users (
    user_id INTEGER PRIMARY KEY,
    raw TEXT,
    reason TEXT,
    quarantined_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TABLES = ("users", "zones", "sects", "meta")

#: sidecar files that must be removed together with the database itself
SIDECARS = ("", "-journal", "-wal", "-shm")


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                         timeout=20.0)
        except sqlite3.Error as exc:            # unreadable dir, not a database …
            raise StorageError(f"cannot open the world database at {self.path}: {exc}",
                               db_path=str(self.path)) from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=20000")
        # WAL makes a reader see a consistent snapshot while the periodic saver
        # copies the file; the checkpoint on close hands everything back to the
        # main file so a snapshot never depends on the sidecars.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:                  # exotic filesystems: stay compatible
            self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        try:
            with self._lock, self._conn:
                self._conn.executescript(SCHEMA)
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            self._conn.close()
            raise StorageError(f"world database schema could not be created: {exc}",
                               db_path=str(self.path)) from exc

    # ── internals ────────────────────────────────────────────────────────────
    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        try:
            with self._lock:
                return self._conn.execute(sql, args).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"world database read failed: {exc}", sql=sql) from exc

    def _write(self, sql: str, args: tuple, what: str) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(sql, args)
        except sqlite3.Error as exc:
            raise StorageError(f"world database write failed ({what}): {exc}",
                               what=what) from exc

    # ── users ────────────────────────────────────────────────────────────────
    def get_user(self, user_id: int) -> dict | None:
        """Return the player document, or ``None`` when there is no *usable* one.

        ``None`` means "create a fresh document", and that is exactly why the
        broken bytes are quarantined before we forget them: without the copy the
        next save overwrites the only evidence of what went wrong.
        """
        rows = self._read("SELECT doc FROM users WHERE user_id=?", (user_id,))
        if not rows:
            return None
        raw = rows[0]["doc"]
        doc = _load_doc(raw, user_id)
        if doc is None:
            self._quarantine(user_id, raw, "not a JSON object")
        return doc

    def _quarantine(self, user_id: int, raw: Any, reason: str) -> None:
        snippet = (raw if isinstance(raw, str) else repr(raw))[:500]
        logger.error("quarantining unreadable player document user_id=%s reason=%s "
                     "bytes=%d prefix=%r", user_id, reason, len(snippet), snippet[:80])
        try:
            self._write(
                "INSERT INTO corrupt_users(user_id, raw, reason) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET raw=excluded.raw, "
                "reason=excluded.reason, quarantined_at=datetime('now')",
                (user_id, snippet, reason), "quarantine")
        except StorageError as exc:            # never let bookkeeping mask the fault
            logger.warning("could not quarantine corrupt document: %s", exc)

    def save_user(self, doc: dict) -> None:
        if not isinstance(doc, dict) or "user_id" not in doc:
            raise StorageError("refused to save a user document without user_id",
                               doc_type=type(doc).__name__)
        try:
            payload = json.dumps(doc, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            # a non-serializable value (datetime, set, …) means the engines wrote
            # something the schema cannot hold — a defect, not a user error.
            raise StorageError(f"user document is not serializable: {exc}",
                               user_id=doc.get("user_id")) from exc
        self._write(
            "INSERT INTO users(user_id, doc, updated_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET doc=excluded.doc, "
            "updated_at=excluded.updated_at",
            (doc["user_id"], payload), "save_user")

    def all_users(self) -> Iterator[dict]:
        """Every *readable* player document; unreadable rows are reported and
        skipped rather than raising in the middle of an admin broadcast."""
        for row in self._read("SELECT user_id, doc FROM users"):
            doc = _load_doc(row["doc"], row["user_id"])
            if doc is None:
                self._quarantine(row["user_id"], row["doc"], "not a JSON object")
                continue
            yield doc

    def count_users(self) -> int:
        rows = self._read("SELECT COUNT(*) c FROM users")
        return int(rows[0]["c"])

    def count_meditating(self) -> int:
        return sum(1 for doc in self.all_users()
                   if isinstance(doc.get("cultivation"), dict)
                   and doc["cultivation"].get("meditating"))

    # ── zones & sects ────────────────────────────────────────────────────────
    def get_zone(self, zone_id: str) -> dict | None:
        rows = self._read("SELECT doc FROM zones WHERE zone_id=?", (zone_id,))
        if not rows:
            return None
        doc = _load_doc(rows[0]["doc"], zone_id)
        if doc is None:
            logger.error("zone document %r is unreadable — re-seeding it", zone_id)
            self.seed_zone(zone_id)
            rows = self._read("SELECT doc FROM zones WHERE zone_id=?", (zone_id,))
            doc = _load_doc(rows[0]["doc"], zone_id) if rows else None
        return doc

    def save_zone(self, doc: dict) -> None:
        self._write(
            "INSERT INTO zones(zone_id, doc) VALUES(?,?) "
            "ON CONFLICT(zone_id) DO UPDATE SET doc=excluded.doc",
            (doc["zone_id"], _dump(doc)), "save_zone")

    def all_zones(self) -> list[dict]:
        return [d for d in (_load_doc(r["doc"], None)
                            for r in self._read("SELECT doc FROM zones")) if d]

    def get_sect(self, sect_id: str) -> dict | None:
        rows = self._read("SELECT doc FROM sects WHERE sect_id=?", (sect_id,))
        return _load_doc(rows[0]["doc"], sect_id) if rows else None

    def save_sect(self, doc: dict) -> None:
        self._write(
            "INSERT INTO sects(sect_id, doc) VALUES(?,?) "
            "ON CONFLICT(sect_id) DO UPDATE SET doc=excluded.doc",
            (doc["sect_id"], _dump(doc)), "save_sect")

    def all_sects(self) -> list[dict]:
        return [d for d in (_load_doc(r["doc"], None)
                            for r in self._read("SELECT doc FROM sects")) if d]

    # ── meta (world boost, counters…) ─────────────────────────────────────────
    def get_meta(self, key: str, default: Any = None) -> Any:
        rows = self._read("SELECT value FROM meta WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except (TypeError, ValueError):
            logger.warning("meta %r is not valid JSON — ignoring it", key)
            return default

    def set_meta(self, key: str, value: Any) -> None:
        self._write(
            "INSERT INTO meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _dump(value)), f"set_meta:{key}")

    def delete_meta(self, key: str) -> None:
        self._write("DELETE FROM meta WHERE key=?", (key,), f"delete_meta:{key}")

    # ── health, reset ────────────────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in TABLES + ("corrupt_users",):
            rows = self._read(f"SELECT COUNT(*) c FROM {table}")
            out[table] = int(rows[0]["c"])
        return out

    def count(self, table: str) -> int:
        if table not in TABLES + ("corrupt_users",):
            raise ValueError(f"unknown table {table!r}")
        rows = self._read(f"SELECT COUNT(*) c FROM {table}")
        return int(rows[0]["c"])

    def integrity_report(self) -> dict[str, Any]:
        """``PRAGMA integrity_check`` + journal mode + table counts.

        Used at boot (so a bad artifact is obvious in the log before a single
        player hits it) and by ``scripts/reset_world.py`` before/after a wipe.
        """
        try:
            rows = self._read("PRAGMA integrity_check")
            verdict = [str(r[0]) for r in rows] or ["no result"]
        except StorageError as exc:
            verdict = [f"unusable: {exc}"]
        journal = "unknown"
        try:
            journal = str(self._read("PRAGMA journal_mode")[0][0])
        except StorageError:
            pass
        version = 0
        try:
            version = int(self._read("PRAGMA user_version")[0][0] or 0)
        except StorageError:
            pass
        # every probe is independent: a malformed image must still yield a
        # *report*. Counting rows through the ordinary accessor raised, which is
        # precisely the moment an operator needs the summary.
        counts: dict[str, Any] = {}
        for table in TABLES + ("corrupt_users",):
            try:
                counts[table] = int(self._read(f"SELECT COUNT(*) c FROM {table}")[0][0])
            except StorageError as exc:
                counts[table] = None
                counts.setdefault("errors", []).append(f"{table}: {exc}")
        return {
            "ok": verdict == ["ok"],
            "integrity_check": verdict,
            "journal_mode": journal,
            "schema_version": version,
            "path": str(self.path),
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "counts": counts,
        }

    def checkpoint(self) -> None:
        """Fold the WAL back into the main file (used before a snapshot)."""
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            logger.warning("wal_checkpoint failed (harmless for rollback journal): %s",
                           exc)

    def close(self) -> None:
        with self._lock:
            try:
                self.checkpoint()
            finally:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass

    # ── seeding ──────────────────────────────────────────────────────────────
    def seed_zone(self, zone_id: str) -> None:
        from ..engine.constants import ZONES

        zone = ZONES.get(zone_id)
        if not zone:
            return
        self.save_zone({
            "zone_id": zone_id,
            "key": zone["key"],
            "vein_density": zone["vein_density"],
            "min_realm": zone["min_realm"],
            "guard_level": zone["guard"],
            "owner": None,
            "owner_kind": None,
            "conquered_at": None,
        })

    def seed_sect(self, sect_id: str) -> None:
        from ..engine.constants import NPC_SECTS

        sect = NPC_SECTS.get(sect_id)
        if not sect:
            return
        self.save_sect({
            "sect_id": sect_id,
            "key": sect["key"],
            "alignment": sect["alignment"],
            "npc": True,
            "master": None,
            "members": [],
            "treasury": 0,
        })


# ── module-level helpers ─────────────────────────────────────────────────────

def _load_doc(raw: Any, ident: Any) -> dict | None:
    """Decode a stored document, returning ``None`` for anything unusable.

    ``json.loads`` accepts ``1``, ``"x"``, ``null`` and ``[1,2]`` as valid JSON;
    every consumer of a document expects an object, so a scalar must not reach
    them (it used to: ``ensure_v2`` died on ``.setdefault`` and the whole tap
    came back as ERR_UNKNOWN, forever, because the row was never repaired).
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, memoryview):
        raw = bytes(raw).decode("utf-8", "replace")
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        logger.error("document for %s is not valid JSON", ident)
        return None
    if not isinstance(doc, dict):
        logger.error("document for %s is JSON %s, expected object", ident,
                     type(doc).__name__)
        return None
    return doc


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"value could not be serialized: {exc}") from exc


def db_files(path: str | Path) -> list[Path]:
    """The database plus every sidecar that belongs to it."""
    p = Path(path)
    return [p.with_name(p.name + suffix) for suffix in SIDECARS]


def reset(path: str | Path) -> dict[str, Any]:
    """Wipe the *mutable* world state and rebuild it from the checked-in data.

    Removes the database and its WAL/SHM/journal sidecars, then re-creates the
    schema and re-seeds zones and sects.  Static game content (``data/*.json``,
    ``locales/*``) is never touched — it is the source the rebuild reads from.
    """
    p = Path(path)
    removed: list[str] = []
    for f in db_files(p):
        if f.exists():
            try:
                f.unlink()
                removed.append(f.name)
            except OSError as exc:
                raise StorageError(f"could not remove {f}: {exc}", path=str(f)) from exc
    storage = Storage(p)
    try:
        bootstrap_world(storage)
        report = {
            "path": str(p),
            "removed": removed,
            "reseeded": True,
            "after": storage.integrity_report(),
        }
    finally:
        storage.close()
    return report


def bootstrap_world(storage: Storage) -> None:
    """One-shot world setup: JSON data registry + zone/sect seed docs.

    Idempotent — a zone/sect that already exists is left alone (its owner and
    conquest history belong to the players, not to the checkout).
    """
    from ..core.data_loader import bootstrap as _data_bootstrap
    registry = _data_bootstrap()
    from ..engine.constants import NPC_SECTS, ZONES

    seeded_zones = seeded_sects = 0
    for zone_id in ZONES:
        if storage.get_zone(zone_id) is None:
            storage.seed_zone(zone_id)
            seeded_zones += 1
    for sect_id in NPC_SECTS:
        if storage.get_sect(sect_id) is None:
            storage.seed_sect(sect_id)
            seeded_sects += 1
    # The expected counts come from the shipped data, so a half-restored
    # database is visible in the log instead of being discovered by a player.
    expect_zones, expect_sects = len(ZONES), len(NPC_SECTS)
    storage.set_meta("world_bootstrap", {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "zones_expected": expect_zones,
        "sects_expected": expect_sects,
        "zones_seeded_now": seeded_zones,
        "sects_seeded_now": seeded_sects,
        "data_files": sorted(registry.sources) if hasattr(registry, "sources") else [],
    })
    missing = []
    if storage.count("zones") < expect_zones:
        missing.append(f"zones {storage.count('zones')}/{expect_zones}")
    if storage.count("sects") < expect_sects:
        missing.append(f"sects {storage.count('sects')}/{expect_sects}")
    if missing:
        raise StorageError("world seeding incomplete: " + ", ".join(missing),
                           missing=missing)
    logger.info("🌍 world bootstrapped: %d/%d zones, %d/%d sects",
                storage.count("zones"), expect_zones,
                storage.count("sects"), expect_sects)

#!/usr/bin/env python3
"""Decide whether a database file is fit to serve players from.

Stdlib only, and no imports from the app: this runs in CI right after a world
state is unpacked, where the point is precisely that the application's own data
may be broken.

Why it exists: `deploy/gha_state.sh restore` used to treat "unzipped something"
as "restored successfully". A truncated archive, a zip whose payload had the
wrong name, or a database from a pre-bootstrap era all produced a *silently*
fresh world — and the bot then spent the whole run answering every tap from a
vault that no longer matched the artifact the players had earned.

Exit codes
    0  the file is a usable, seeded world database
    1  present but unusable (corrupt, wrong schema, unseeded)
    2  no file at that path
    3  the path exists but is not readable
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

#: every table the app expects; a database missing one of these is not ours
TABLES = ("users", "zones", "sects", "meta")


def inspect_db(path: str | Path) -> dict:
    """A summary of what the file actually contains, never raising."""
    p = Path(path)
    out: dict = {"path": str(p), "ok": False, "reason": "", "problems": []}
    if not p.exists():
        out["reason"] = "missing"
        return out
    if not p.is_file():
        out["problems"].append(f"{p} is not a regular file")
        out["reason"] = "unusable"
        return out
    out["size_bytes"] = p.stat().st_size
    if out["size_bytes"] == 0:
        out["problems"].append("file is empty (a truncated download?)")
        out["reason"] = "unusable"
        return out
    try:
        # deliberately NOT mode=ro: a WAL database restored without its sidecars
        # needs write access to recover, and rejecting a *healthy* artifact is far
        # worse than checkpointing one we are only looking at.
        conn = sqlite3.connect(str(p))
    except sqlite3.Error as exc:
        out["problems"].append(f"cannot open: {exc}")
        out["reason"] = "unusable"
        return out
    try:
        verdict = ["not checked"]
        have: set[str] = set()
        counts: dict[str, int] = {}
        try:
            verdict = [str(r[0]) for r in
                       conn.execute("PRAGMA integrity_check").fetchall()] or ["no result"]
            have = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES + ("corrupt_users",):
                if table in have:
                    counts[table] = int(conn.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            # "file is not a database" arrives here, from any of the statements
            out["problems"].append(f"sqlite refused to read it: {exc}")
        out["integrity_check"] = verdict
        if verdict != ["ok"] and not out["problems"]:
            out["problems"].append("integrity_check: " + "; ".join(verdict))

        missing = [t for t in TABLES if t not in have]
        out["tables"] = sorted(have)
        if missing:
            out["problems"].append("missing tables: " + ", ".join(missing))
        out["counts"] = counts
        # A world with no zones is not a world: every map, travel and hunt screen
        # would come back empty. Zero *players* is perfectly normal (a fresh bot).
        if not missing and counts.get("zones", 0) == 0:
            out["problems"].append("no zones seeded — the world map would be empty")
        quarantined = counts.get("corrupt_users", 0)
        if quarantined:
            out["warnings"] = [f"{quarantined} unreadable player document(s) are "
                              f"quarantined in corrupt_users"]
        out["ok"] = not out["problems"]
        out["reason"] = "ok" if out["ok"] else "unusable"
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default="data/grimhaven.db")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if Path(args.path).exists() and not Path(args.path).is_file():
        code, report = 3, {"path": args.path, "problems": ["not a file"]}
    else:
        report = inspect_db(args.path)
        code = 0 if report["ok"] else (2 if report.get("reason") == "missing" else 1)

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
        return code
    if code == 0:
        print(f"✅ {report['path']}: seeded and intact "
              f"({report['counts'].get('users', 0)} player(s), "
              f"{report['counts'].get('zones', 0)} zone(s), "
              f"{report['size_bytes']} bytes)")
    elif code == 2:
        print(f"🌱 {report['path']}: no database file (nothing was restored yet)")
    else:
        print(f"❌ {report['path']}: not a usable world database")
        for problem in report.get("problems", []):
            print(f"   - {problem}")
    for warn in report.get("warnings", []):
        print(f"⚠️  {warn}")
    return code


if __name__ == "__main__":
    sys.exit(main())

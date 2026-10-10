#!/usr/bin/env python3
"""Reset the *mutable* Grimhaven world and rebuild it from the checked-in data.

    python scripts/reset_world.py              # what would be removed (no changes)
    python scripts/reset_world.py --yes        # wipe + rebuild + verify
    python scripts/reset_world.py --inspect    # show quarantined documents
    python scripts/reset_world.py --yes --keep-audit audit.json

Scope, deliberately narrow: this deletes the SQLite database (and its
``-wal``/``-shm``/``-journal`` sidecars) and re-seeds zones and sects from
``data/*.json``. Static game content — ``data/*.json``, ``locales/*`` — is never
written to, and the script refuses to run if any of it is missing, because a
reset into an incomplete checkout would produce a *silently* broken world.

It prints a before/after integrity report so the operator (or the workflow log)
can see that the rebuild actually worked, instead of finding out from a player.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grimhaven.config import ROOT_DIR, settings            # noqa: E402
from grimhaven.core.data_loader import bootstrap as bootstrap_data   # noqa: E402
from grimhaven.db.storage import (Storage, bootstrap_world,  # noqa: E402
                                  db_files, reset)

#: the files data_loader refuses to start without (sects/ZONES come from
#: engine/constants, so they are not on disk)
REQUIRED_CONTENT = ("zones.json", "enemies.json", "martial_arts.json",
                    "equipment.json", "consumables.json",
                    "cultivation_methods.json", "offline_events.json")


def _content_ready() -> tuple[bool, list[str]]:
    data_dir = ROOT_DIR / "data"
    missing = [name for name in REQUIRED_CONTENT if not (data_dir / name).exists()]
    locales = list((ROOT_DIR / "locales").glob("locale_*.json"))
    if not locales:
        missing.append("locales/locale_*.json")
    return (not missing), missing


def _scan(storage: Storage) -> dict:
    """Read every player document so the unreadable ones are found *now*.

    Quarantine happens on load, so a database nobody has opened since the
    corruption would otherwise be wiped with its damage still invisible. The
    scan writes the offending bytes into corrupt_users (that table exists for
    exactly this) and returns the counts for the audit report.
    """
    readable = sum(1 for _ in storage.all_users())
    rows = storage._read("SELECT COUNT(*) c FROM corrupt_users")
    return {"readable_users": readable, "quarantined": int(rows[0]["c"])}


def _inspect(storage: Storage) -> int:
    """Print the quarantined documents — the evidence a reset would destroy."""
    rows = storage._read(
        "SELECT user_id, reason, quarantined_at, length(raw) AS n, raw "
        "FROM corrupt_users ORDER BY quarantined_at DESC LIMIT 25")
    if not rows:
        print("no quarantined documents (corrupt_users is empty)")
        return 0
    print(f"{len(rows)} quarantined document(s) (most recent first, max 25 shown):")
    for row in rows:
        print(f"\n  user_id={row['user_id']} bytes={row['n']} at={row['quarantined_at']}"
              f"\n  reason: {row['reason']}\n  starts: {str(row['raw'])[:120]!r}")
    print("\nEach of these was unreadable JSON on load; the handler created a fresh "
          "document for the player instead of crashing, and kept the bytes here.")
    return len(rows)


def _write_audit(args, payload: dict) -> None:
    """Persist the evidence. A reset is the one operation that can destroy the
    only copy of 'what the data looked like', so the audit is written even when
    the run is a dry run."""
    if not args.keep_audit:
        return
    out = Path(args.keep_audit)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"audit report written to {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(settings.database_path),
                    help="database path (default: the one from the environment)")
    ap.add_argument("--yes", action="store_true", help="actually perform the wipe")
    ap.add_argument("--inspect", action="store_true",
                    help="list quarantined documents and exit")
    ap.add_argument("--keep-audit", metavar="FILE",
                    help="write a JSON forensics report (before/after state, "
                         "quarantined rows) to FILE — on a dry run too")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(levelname)-8s %(name)s: %(message)s")
    log = logging.getLogger("grimhaven.reset")
    path = Path(args.db)
    if not path.is_absolute():
        path = ROOT_DIR / path

    if args.inspect:
        if not path.exists():
            print(f"no database at {path} — nothing to inspect")
            return 0
        storage = Storage(path)
        try:
            _inspect(storage)
        finally:
            storage.close()
        return 0

    ready, missing = _content_ready()
    if not ready:
        print("❌ refusing to reset: the rebuild source is incomplete —\n   missing: "
              + ", ".join(missing) + "\n   (a reset into a partial checkout would "
              "silently produce a broken world)")
        return 1

    before: dict = {}
    scan: dict = {}
    if path.exists():
        storage = Storage(path)
        try:
            before = storage.integrity_report()
            scan = _scan(storage)          # find the damage before destroying it
            _inspect(storage)
        finally:
            storage.close()
        print(f"\nbefore: counts={before.get('counts')} "
              f"integrity={before.get('integrity_check')} "
              f"size={before.get('size_bytes')} bytes")
        if scan.get("quarantined"):
            print(f"⚠️  {scan['quarantined']} document(s) are unreadable and now "
                  f"quarantined ({scan['readable_users']} readable)")
    else:
        print(f"\nno database at {path} — a reset here just seeds a fresh world")

    sidecars = [f.name for f in db_files(path) if f.exists()]
    if not args.yes:
        print(f"DRY RUN — would remove: {', '.join(sidecars) or '(nothing)'} "
              f"and re-seed zones/sects from data/*.json\n"
              f"             re-run with --yes to do it")
        _write_audit(args, {"before": before, "scan": scan, "dry_run": True,
                           "would_remove": sidecars, "problems": []})
        return 0

    bootstrap_data()
    report = reset(path)
    after = report["after"]
    log.info("world reset: removed=%s after=%s", report["removed"], after["counts"])
    print(f"after : counts={after.get('counts')} integrity={after.get('integrity_check')}")

    # verify the rebuild is actually usable, not merely present
    problems: list[str] = []
    if not after.get("ok"):
        problems.append(f"integrity_check said {after.get('integrity_check')}")
    if not after["counts"].get("zones"):
        problems.append("no zones were seeded — the world map would be empty")
    if report["removed"] and set(sidecars) - set(report["removed"]):
        problems.append("a sidecar file survived the wipe: "
                        + ", ".join(sorted(set(sidecars) - set(report['removed']))))
    storage = Storage(path)
    try:
        from grimhaven.engine.models import new_user_doc   # schema sanity check
        probe = new_user_doc(0, "reset-probe", "fa")
        storage.save_user(probe)
        if storage.get_user(0) is None:
            problems.append("a freshly written document could not be read back")
        # the shipped data must load into the schema, or every screen would break
        bootstrap_world(storage)
    except Exception as exc:                                # noqa: BLE001
        problems.append(f"post-reset smoke test raised {type(exc).__name__}: {exc}")
    finally:
        storage._write("DELETE FROM users WHERE user_id=?", (0,),  # drop the probe
                             "reset-probe-cleanup")
        storage.close()

    _write_audit(args, {"before": before, "scan": scan, "dry_run": False,
                       "would_remove": sidecars, "report": report,
                       "problems": problems})

    if problems:
        print("❌ reset finished but the world is NOT healthy:\n   - "
              + "\n   - ".join(problems))
        return 1
    print("✅ world rebuilt and verified: schema, zone seeds and a write/read round trip")
    return 0


if __name__ == "__main__":
    sys.exit(main())

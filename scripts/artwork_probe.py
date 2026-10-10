#!/usr/bin/env python3
"""Live artwork-retrieval probe — genuine end-to-end verification.

The unit/integration suite fakes provider HTTP (MockTransport) so it runs
offline and deterministically. This script is the counterpart: it exercises
the REAL Wikimedia Commons and Openverse endpoints through the production
code path (search → candidate → download → validate → atomic store → cache
re-hit) and exits non-zero if retrieval does not genuinely work.

Run it from CI (``.github/workflows/tests.yml`` job ``artwork-probe``) or by
hand::

    python scripts/artwork_probe.py            # uses .env / defaults
    ARTWORK_PROVIDERS=openverse python scripts/artwork_probe.py

No Telegram token or paid key is required; everything it touches is public,
attribution-friendly image data. It writes to a throwaway cache directory so
it never pollutes ``assets/artwork/``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grimhaven.config import Settings                      # noqa: E402
from grimhaven.db.storage import Storage, bootstrap_world  # noqa: E402
from grimhaven.images.service import ArtworkService        # noqa: E402

QUERY = ("xianxia heavenly tribulation lightning storm cultivation "
         "breakthrough fantasy digital painting")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(levelname)s %(name)s: %(message)s")
    tmp = Path(tempfile.mkdtemp(prefix="grimhaven-probe-"))
    os.environ["ARTWORK_DIR"] = str(tmp / "artwork")
    os.environ.setdefault("ARTWORK_MAX_RETRIES", "1")
    settings = Settings.load()
    settings.artwork_dir = tmp / "artwork"
    settings.artwork_enabled = True
    storage = Storage(tmp / "probe.db")
    bootstrap_world(storage)
    service = ArtworkService(storage, settings)
    result = {"providers": [p.name for p in service.providers],
              "search": {}, "fetch": None}
    status = 1
    try:
        for provider in service.providers:
            try:
                found = await provider.search(QUERY, limit=5)
                result["search"][provider.name] = len(found)
            except Exception as exc:            # noqa: BLE001 — probe reports, never crashes
                result["search"][provider.name] = f"ERROR {type(exc).__name__}: {exc}"
        try:
            row = await service.get_or_fetch_artwork("breakthrough",
                                                     queries=[QUERY])
        except Exception:                       # noqa: BLE001
            traceback.print_exc()
            row = None
        if row:
            path = service.root / row["file_path"]
            result["fetch"] = {
                "status": row["status"], "provider": row["provider"],
                "mime": row["mime"], "bytes": row["bytes"],
                "width": row["width"], "height": row["height"],
                "license": row["license"], "on_disk": path.is_file(),
                "hash_matches_file": (row["file_hash"] == _sha(path)
                                      if path.is_file() else False),
            }
            # second call must be a pure cache hit (no new download)
            row2 = await service.get_or_fetch_artwork("breakthrough")
            result["cache_hit"] = bool(row2 and row2["file_hash"] == row["file_hash"])
            if path.is_file() and result["cache_hit"]:
                status = 0
    except Exception:                           # noqa: BLE001
        traceback.print_exc()
    finally:
        storage.close()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if status == 0:
        print("PROBE OK — real image retrieved, validated, cached, and re-served "
              "from cache.", file=sys.stderr)
    else:
        print("PROBE FAILED — retrieval path did not produce a validated cache "
              "entry (see JSON above).", file=sys.stderr)
    return status


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

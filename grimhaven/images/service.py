"""The reusable artwork service: discovery → download → validation → cache.

Pipeline for one event illustration (``get_or_fetch_artwork``):

1. **index lookup** — the SQLite ``artwork`` table (the same database as the
   game), keyed by a stable *asset key* (e.g. ``breakthrough``); a validated
   row whose file is still on disk is a pure cache hit — no network at all;
2. **negative cache** — a recently failed asset key stays failed for
   ``artwork_fail_cooldown_minutes`` so a dead provider cannot make every
   button press start new HTTP calls;
3. **discovery** — descriptive event queries from ``data/artwork_map.json``
   run through the configured providers (Commons → Openverse → …) in order;
   a provider error falls through to the next source;
4. **download + validation** — the candidate URL is streamed under a byte cap,
   sniffed by file signature, dimension-checked, and only then written to
   ``<cache>/files/<sha256>.<ext>`` via a tmp file + atomic rename (partial
   downloads never linger, and a remote filename never touches the path);
5. **content-hash deduplication** — two asset keys with the same bytes share
   one file and inherit the stored Telegram ``file_id``;
6. **bounded eviction** — beyond ``artwork_max_assets`` rows, least-recently
   used rows (and orphaned files) are pruned.

The service degrades to "nothing happened": an unwritable cache, a dead
network, or every source rejecting its candidates produce log lines, never
exceptions at the player — the game keeps answering in text.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .fetch import FetchError, Fetcher
from .providers import Candidate, build_providers
from .validate import ImageRejected, validate_image_bytes

logger = logging.getLogger(__name__)

_ASSET_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
MAP_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "artwork_map.json"


def parse_age_minutes(iso_like: Any) -> float:
    """Minutes elapsed since a ``datetime('now')``-style UTC timestamp."""
    if not iso_like:
        return float("inf")
    try:
        stamp = dt.datetime.strptime(str(iso_like), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        try:
            stamp = dt.datetime.fromisoformat(str(iso_like))
        except (TypeError, ValueError):
            return float("inf")
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(dt.timezone.utc).replace(tzinfo=None)
    # storage timestamps are naive UTC (SQLite datetime('now')); compare naive
    return max(0.0,
               (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - stamp)
               .total_seconds() / 60.0)


class ArtworkService:
    def __init__(self, storage: Any, settings: Any, *, transport: Any = None):
        self.storage = storage
        self.settings = settings
        self.root = Path(settings.artwork_dir)
        self.files_dir = self.root / "files"
        self.fetcher = Fetcher(timeout=settings.artwork_timeout,
                               max_retries=settings.artwork_max_retries,
                               concurrency=settings.artwork_concurrency,
                               user_agent=settings.artwork_user_agent,
                               transport=transport)
        names = [n.strip() for n in
                 str(getattr(settings, "artwork_providers", "") or "").split(",")
                 if n.strip()]
        self.providers = build_providers(names, self.fetcher, settings)
        if not self.providers:
            logger.warning("artwork: no providers configured (ARTWORK_PROVIDERS=%r) — "
                           "the game will play text-only", settings.artwork_providers)
        self._events = self._load_event_map()
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    # ── event map ─────────────────────────────────────────────────────────────
    @staticmethod
    def _load_event_map() -> dict[str, dict]:
        try:
            raw = json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning("artwork: %s missing — only ad-hoc queries will work", MAP_FILE)
            return {}
        except (ValueError, OSError) as exc:
            logger.error("artwork: could not read %s: %s", MAP_FILE, exc)
            return {}
        events = raw.get("events") or {}
        return {k: v for k, v in events.items() if isinstance(v, dict)}

    def event_def(self, asset_key: str) -> dict:
        spec = self._events.get(asset_key)
        if not spec and ":" in asset_key:      # zone:zone_mist_peak → zone defaults
            spec = self._events.get(asset_key.split(":", 1)[0])
        return spec or {}

    def queries_for(self, asset_key: str) -> list[str]:
        return [q for q in (self.event_def(asset_key).get("queries") or [])
                if isinstance(q, str) and q.strip()]

    def caption_key_for(self, asset_key: str) -> str:
        return str(self.event_def(asset_key).get("caption_key") or "")

    def throttle_minutes_for(self, asset_key: str) -> int:
        try:
            return int(self.event_def(asset_key).get("throttle_minutes") or 0)
        except (TypeError, ValueError):
            return 0

    # ── discovery ─────────────────────────────────────────────────────────────
    async def search_images(self, query: str, limit: int = 6) -> list[Candidate]:
        """Try each provider in order; the first informative one wins.

        Provider failures are logged per source and swallowed — "nothing
        anywhere" is an empty list, not an exception.
        """
        limit = max(1, min(int(limit), 20))
        for provider in self.providers:
            try:
                found = await provider.search(query, limit)
            except FetchError as exc:
                logger.warning("artwork: provider %s search failed: %s", provider.name, exc)
                continue
            except Exception:      # a provider blowing up must not poison the chain
                logger.exception("artwork: provider %s crashed on search", provider.name)
                continue
            if found:
                for c in found:
                    c.query = query
                return found
        return []

    # ── download + validation ─────────────────────────────────────────────────
    async def fetch_validated(self, source: str, *,
                              max_bytes: int | None = None) -> tuple[bytes, dict]:
        """Download one URL and run the full validation gate over the bytes.

        Returns ``(payload, info)`` — the caller decides where to put it.
        Raises :class:`FetchError` / :class:`ImageRejected`.
        """
        max_bytes = int(max_bytes or self.settings.artwork_max_bytes)
        if not str(source).lower().startswith(("https://", "http://")):
            raise ImageRejected("bad-url", "not an http(s) URL")
        body, ctype = await self.fetcher.get(source, max_bytes=max_bytes, expect="image")
        info = validate_image_bytes(body,
                                    min_width=self.settings.artwork_min_width,
                                    max_side=self.settings.artwork_max_side,
                                    max_bytes=max_bytes,
                                    declared_mime=ctype)
        return body, {"mime": info.mime, "width": info.width, "height": info.height,
                      "sha256": info.sha256, "bytes": info.size, "ext": info.ext}

    @staticmethod
    def write_atomic(data: bytes, destination: Path | str) -> Path:
        """Persist bytes via tmp-file + fsync + atomic rename (no partials)."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(f".{destination.name}.{os.getpid()}.part")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if destination.exists():
                destination.unlink()
            os.replace(tmp, destination)
            return destination
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                logger.debug("artwork: leftover temp file %s", tmp.name)

    async def download_image(self, source: str, destination: Path | str, *,
                             max_bytes: int | None = None) -> dict:
        """Public helper: fetch, validate, and store at exactly ``destination``."""
        body, info = await self.fetch_validated(source, max_bytes=max_bytes)
        self.write_atomic(body, destination)
        return info

    @staticmethod
    def validate_image(path: Path | str) -> dict:
        """Integrity gate for an already-cached file (re-check on demand)."""
        info = validate_image_bytes(Path(path).read_bytes(), min_width=0,
                                    max_side=10_000, max_bytes=64_000_000)
        return {"mime": info.mime, "width": info.width, "height": info.height,
                "sha256": info.sha256, "bytes": info.size}

    # ── cache / index ─────────────────────────────────────────────────────────
    def path_for_hash(self, digest: str, ext: str) -> Path:
        return self.files_dir / f"{re.sub(r'[^0-9a-f]', '', digest)[:64]}{ext}"

    def row_path(self, row: dict) -> Path:
        """Resolve the stored relative path safely (never outside the cache)."""
        rel = str(row.get("file_path") or "")
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            raise ImageRejected("path-traversal", "index row points outside the cache")
        return candidate

    def cached_row(self, asset_key: str) -> dict | None:
        """A *usable* cache entry: validated, on disk. Pure local work."""
        try:
            row = self.storage.artwork_get(asset_key)
        except Exception:
            logger.exception("artwork: index read failed (cache treated as cold)")
            return None
        if not row or row.get("status") != "ok" or not row.get("file_hash"):
            return None
        try:
            path = self.row_path(row)
        except ImageRejected:
            logger.error("artwork: index row %s points outside the cache — dropped",
                         asset_key)
            self.storage.artwork_mark_failed(asset_key, note="path rejected")
            return None
        if not path.is_file() or path.stat().st_size == 0:
            logger.warning("artwork: cached file for %s vanished; refetch needed", asset_key)
            return None
        try:
            self.storage.artwork_touch(asset_key)
        except Exception:
            pass
        return row

    def get_artwork_for_event(self, event_type: str, event_id: str = "",
                              language: str = "en") -> dict | None:
        """Cache-only artwork lookup for a game event (safe to call anywhere).

        Returns ``{"path": Path, "caption": str, "row": dict}``, or ``None``
        when nothing is cached — callers must treat ``None`` as "text-only".
        """
        asset_key = self.asset_key(event_type, event_id)
        row = self.cached_row(asset_key)
        if not row:
            return None
        from ..localization import t
        caption_key = self.caption_key_for(asset_key) or "ART_CAP_GENERIC"
        caption = t(language, caption_key) if caption_key else ""
        return {"path": self.row_path(row), "caption": caption, "row": row,
                "asset_key": asset_key}

    @staticmethod
    def asset_key(event_type: str, event_id: str = "") -> str:
        key = f"{event_type}:{event_id}" if event_id else event_type
        key = re.sub(r"[^a-z0-9_.:-]+", "_", key.lower()).strip("_:")[:64]
        if not _ASSET_KEY_RE.match(key):
            key = re.sub(r"[^a-z0-9_.:-]+", "_", f"art_{key}".lower())[:64]
        return key

    async def get_or_fetch_artwork(self, asset_key: str, queries: list[str] | None = None,
                                   *, max_queries: int = 2,
                                   max_candidates: int = 4) -> dict | None:
        """Return an index row for ``asset_key``, fetching it when absent.

        The single entry point that may hit the network. Every failure mode
        (no providers, dead network, all candidates rejected) records a
        *failed* row with a cooldown and returns ``None``.
        """
        if not _ASSET_KEY_RE.match(asset_key):
            raise ValueError(f"unsafe artwork asset key {asset_key!r}")
        row = self.cached_row(asset_key)
        if row:
            return row
        raw = self.storage.artwork_get(asset_key)
        if raw and raw.get("status") == "failed":
            age = parse_age_minutes(raw.get("attempts_at"))
            cooldown = float(self.settings.artwork_fail_cooldown_minutes or 30)
            if age < cooldown:
                logger.debug("artwork: %s in failure cooldown (%.0f min left)",
                             asset_key, cooldown - age)
                return None
        queries = [q for q in (queries or self.queries_for(asset_key)) if q]
        if not queries:
            return None
        fetched = await self._fetch_first(asset_key, queries, max_queries, max_candidates)
        if fetched:
            self._evict_if_needed()
        return fetched

    async def _fetch_first(self, asset_key: str, queries: list[str],
                           max_queries: int, max_candidates: int) -> dict | None:
        budget = max(1, max_queries) * max(1, max_candidates)
        attempts = 0
        for query in queries[:max_queries]:
            candidates = await self.search_images(query, limit=max_candidates)
            for cand in candidates[:max_candidates]:
                if attempts >= budget:
                    break
                attempts += 1
                try:
                    return await self._store_candidate(asset_key, cand, query)
                except (FetchError, ImageRejected, OSError, ValueError) as exc:
                    logger.warning("artwork: candidate from %s rejected for %s "
                                   "(%s): %s", cand.provider, asset_key,
                                   type(exc).__name__, str(exc)[:160])
                    continue
        try:
            self.storage.artwork_mark_failed(asset_key, query="|".join(queries)[:300])
        except Exception:
            logger.exception("artwork: failure marker could not be stored")
        logger.info("artwork: no source produced a usable image for %s (%d attempt(s))",
                    asset_key, attempts)
        return None

    async def _store_candidate(self, asset_key: str, cand: Candidate,
                               query: str) -> dict | None:
        body, info = await self.fetch_validated(cand.source_url)
        final = self.path_for_hash(info["sha256"], info["ext"])
        fresh = True
        if final.is_file() and final.stat().st_size == info["bytes"]:
            fresh = False      # identical bytes already cached — deduplication
        else:
            self.write_atomic(body, final)
        rel = str(final.relative_to(self.root))
        doc = self._index_doc(asset_key, cand, query, info, rel)
        if not fresh:
            prior = self.storage.artwork_by_hash(info["sha256"])
            if prior and prior.get("telegram_file_id"):
                # inherit the Telegram-side upload as well — same bot, same file
                doc["telegram_file_id"] = prior["telegram_file_id"]
        self.storage.artwork_upsert(doc)
        if fresh:
            logger.info("artwork: cached %s from %s (%dx%d %s, %s)", asset_key,
                        cand.provider, info["width"], info["height"], info["mime"],
                        _human(info["bytes"]))
        else:
            logger.info("artwork: %s re-linked to cached content %s…", asset_key,
                        info["sha256"][:12])
        return self.storage.artwork_get(asset_key)

    def _index_doc(self, asset_key: str, cand: Candidate, query: str, info: dict,
                   rel_path: str) -> dict:
        return {
            "asset_key": asset_key,
            "file_hash": info["sha256"],
            "file_path": rel_path,
            "source_url": (cand.source_url or "")[:1000],
            "page_url": (cand.page_url or "")[:1000],
            "provider": cand.provider,
            "query": (query or "")[:400],
            "mime": info["mime"],
            "width": info["width"],
            "height": info["height"],
            "bytes": info["bytes"],
            "license": (cand.license or "")[:300],
            "author": (cand.author or "")[:300],
            "attribution": (cand.attribution or "")[:800],
            "telegram_file_id": "",
            "status": "ok",
            "validated": 1,
        }

    def _evict_if_needed(self) -> None:
        cap = int(self.settings.artwork_max_assets or 0)
        if cap <= 0:
            return
        try:
            rows = self.storage.artwork_beyond(cap)      # oldest-used first
        except Exception:
            logger.exception("artwork: eviction listing failed")
            return
        for row in rows:
            try:
                path = self.row_path(row)
                self.storage.artwork_delete(row["asset_key"])
                # only drop the file when no surviving row still references the
                # same content hash (dedup keeps two keys on one file)
                if path.is_file() and not self.storage.artwork_by_hash(row["file_hash"]):
                    path.unlink(missing_ok=True)
                logger.info("artwork: evicted %s (cache cap %d)", row["asset_key"], cap)
            except (OSError, ImageRejected):
                logger.warning("artwork: eviction of %s partially failed",
                              row.get("asset_key"))
            except Exception:
                logger.exception("artwork: eviction of %s failed", row.get("asset_key"))

    # ── background prefetch ───────────────────────────────────────────────────
    def schedule_prefetch(self, asset_key: str, queries: list[str] | None = None,
                          *, max_extra_tasks: int = 4) -> bool:
        """Fire-and-forget cache warming. Returns True when a task started.

        * deduplicated per asset key (one in-flight fetch per key ever);
        * globally bounded (``max_extra_tasks``) so retries cannot fan out
          into an unbounded task swarm;
        * a no-op when no event loop is running (sync callers/tests).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if not _ASSET_KEY_RE.match(asset_key):
            return False
        if asset_key in self._inflight or len(self._tasks) >= max_extra_tasks:
            return False
        self._inflight.add(asset_key)

        async def _run() -> None:
            try:
                await self.get_or_fetch_artwork(asset_key, queries)
            except Exception:      # background work stays in the log, out of play
                logger.exception("artwork: background prefetch of %s died", asset_key)
            finally:
                self._inflight.discard(asset_key)

        task = loop.create_task(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def prefetch(self, asset_keys: list[str]) -> int:
        """Sequentially warm the given asset keys (used at startup).

        Sequential on purpose: startup is exactly when the runner also restores
        the database, and the providers deserve one polite request at a time.
        """
        warmed = 0
        for key in asset_keys:
            try:
                if await self.get_or_fetch_artwork(key):
                    warmed += 1
            except Exception:
                logger.debug("artwork: startup prefetch of %s failed", key, exc_info=True)
        return warmed

    async def cancel_tasks(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def stats(self) -> dict:
        try:
            return self.storage.artwork_stats()
        except Exception:
            return {"error": "index unavailable"}

    def clear_cache(self) -> dict:
        """Remove every cached file + index row (operator recovery tool)."""
        removed = 0
        try:
            rows = self.storage.artwork_all_keys()
        except Exception:
            rows = []
        for key in rows:
            try:
                self.storage.artwork_delete(key)
                removed += 1
            except Exception:
                pass
        try:
            for f in self.files_dir.glob("*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
        except OSError:
            logger.warning("artwork: files remain under %s (not writable?)", self.files_dir)
        return {"rows_removed": removed, "dir": str(self.files_dir)}


def _human(n: int) -> str:
    return f"{n / 1024:.0f} KiB" if n < 1_500_000 else f"{n / 1_048_576:.1f} MiB"

"""Resolve a public pack into ids, and choose the right sticker for a moment.

Why resolution is a runtime job
-------------------------------
A Telegram ``file_id`` is **bot-specific** ("unique for each individual bot and
can't be transferred from one bot to another") and Telegram reclaims bot files
over time. Hard-coding ids harvested elsewhere — from a directory, from another
bot, from a screenshot of a log — is the single most common way a sticker
feature rots into 400s. So this service stores **names**, resolves them with
``getStickerSet`` when they are first needed, and keeps the result in memory
with a TTL. The persisted database never sees a ``file_id`` (the artwork
subsystem learned that lesson the hard way: its docstring records that stale
photographic ids are a guaranteed 400, and the same holds for stickers).

Selection
---------
A moment names a category; the category lists emoji in preference order. We
prefer a sticker whose Telegram ``emoji`` tag matches (the same association
Telegram's own sticker search uses), and fall back to a **deterministic
rotation** over the pack, which guarantees the same picture never appears twice
in a row. Nothing here is required for correctness: every failure returns
``None`` and the caller keeps playing in text.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ..errors import TelegramError
from .catalog import Catalog, Pack

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StickerRef:
    """One sendable sticker, resolved from Telegram during this process run."""

    file_id: str
    pack: str
    emoji: str = ""
    is_animated: bool = False
    is_video: bool = False

    @property
    def kind(self) -> str:
        if self.is_video:
            return "video"
        return "animated" if self.is_animated else "static"


class StickerService:
    """Cache-first resolver + picker for the reaction layer.

    Thread/task safety: resolution is guarded per pack by an ``asyncio.Lock``
    (single-flight), so two simultaneous moments cannot both stampede
    ``getStickerSet`` for the same set.
    """

    def __init__(self, settings, catalog: Catalog):
        self.settings = settings
        self.catalog = catalog
        self._cache: dict[str, tuple[float, list[StickerRef]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._rotation: dict[str, int] = {}
        self._failures: dict[str, int] = {}

    # ── cache ────────────────────────────────────────────────────────────────
    def _ttl(self) -> float:
        minutes = float(getattr(self.settings, "sticker_cache_ttl_minutes", 720) or 720)
        return max(60.0, minutes * 60.0)

    def _cached(self, pack_name: str) -> list[StickerRef] | None:
        hit = self._cache.get(pack_name)
        if not hit:
            return None
        stamped, refs = hit
        if time.monotonic() - stamped > self._ttl():
            self._cache.pop(pack_name, None)
            return None
        return refs

    def forget_pack(self, pack_name: str) -> None:
        """Drop a cached set — the recovery path for a stale ``file_id``."""
        self._cache.pop(pack_name, None)

    def packs(self) -> list[Pack]:
        return self.catalog.enabled_packs(getattr(self.settings, "sticker_packs", ""))

    # ── resolution ───────────────────────────────────────────────────────────
    async def resolve(self, bot, pack: Pack) -> list[StickerRef]:
        """All sendable stickers of one pack (cache → API). Never raises."""
        cached = self._cached(pack.short_name)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(pack.short_name, asyncio.Lock())
        async with lock:
            cached = self._cached(pack.short_name)      # another task may have won
            if cached is not None:
                return cached
            refs = await self._fetch(bot, pack)
            if refs:
                self._cache[pack.short_name] = (time.monotonic(), refs)
                self._failures.pop(pack.short_name, None)
                logger.info("sticker: resolved pack %s — %d stickers",
                            pack.short_name, len(refs))
            return refs

    async def _fetch(self, bot, pack: Pack) -> list[StickerRef]:
        timeout = float(getattr(self.settings, "sticker_timeout", 8.0) or 8.0)
        try:
            result = await asyncio.wait_for(
                bot.get_sticker_set(name=pack.short_name), timeout=timeout)
        except asyncio.TimeoutError:
            self._note_failure(pack, "timed out after %.0fs" % timeout)
            return []
        except TelegramError as exc:
            self._note_failure(pack, str(exc)[:160])
            return []
        except Exception as exc:        # noqa: BLE001 — decoration must never bubble
            self._note_failure(pack, f"{type(exc).__name__}: {str(exc)[:120]}")
            return []
        refs: list[StickerRef] = []
        for sticker in getattr(result, "stickers", None) or []:
            file_id = getattr(sticker, "file_id", None)
            if not file_id:
                continue
            refs.append(StickerRef(
                file_id=file_id,
                pack=pack.short_name,
                emoji=str(getattr(sticker, "emoji", "") or ""),
                is_animated=bool(getattr(sticker, "is_animated", False)),
                is_video=bool(getattr(sticker, "is_video", False)),
            ))
        if not refs:
            self._note_failure(pack, "set resolved but contains no stickers")
        return refs

    def _note_failure(self, pack: Pack, reason: str) -> None:
        """One line per pack, then debug — a wrong short name must not flood the log."""
        count = self._failures.get(pack.short_name, 0) + 1
        self._failures[pack.short_name] = count
        if count == 1:
            logger.warning("sticker: pack %s unavailable (%s) — reactions fall back to text",
                           pack.short_name, reason)
        else:
            logger.debug("sticker: pack %s still unavailable (#%d: %s)",
                         pack.short_name, count, reason)

    # ── selection ────────────────────────────────────────────────────────────
    async def pick(self, bot, moment_key: str,
                   exclude: set[str] | None = None) -> StickerRef | None:
        """The sticker for one game moment, or ``None`` (→ text-only play)."""
        moment = self.catalog.moment(moment_key)
        if moment is None:
            logger.debug("sticker: no moment named %r in the map", moment_key)
            return None
        preferences = self.catalog.emojis_for(moment.category)
        exclude = exclude or set()
        for pack in self.packs():
            refs = await self.resolve(bot, pack)
            if not refs:
                continue
            usable = [r for r in refs if r.file_id not in exclude]
            if not usable:
                continue
            chosen = self._prefer(usable, preferences, moment.category)
            if chosen is not None:
                return chosen
        return None

    def _prefer(self, refs: list[StickerRef], preferences: tuple[str, ...],
                category: str) -> StickerRef | None:
        """Emoji match first, deterministic rotation second.

        The rotation key includes the category so that victory and calamity do
        not walk through the pack in lock-step, and the index advances only on
        a real send — a failed send must not silently eat a sticker.
        """
        for wanted in preferences:
            for ref in refs:
                if ref.emoji and ref.emoji == wanted:
                    return ref
        key = f"{category}:{refs[0].pack}"
        index = self._rotation.get(key, 0) % len(refs)
        self._rotation[key] = index + 1
        return refs[index]

    # ── introspection (startup log + tests) ──────────────────────────────────
    def stats(self) -> dict:
        return {
            "packs": [p.short_name for p in self.packs()],
            "resolved": {name: len(refs) for name, (_, refs) in self._cache.items()},
            "failures": dict(self._failures),
            "moments": sorted(self.catalog.moments),
        }

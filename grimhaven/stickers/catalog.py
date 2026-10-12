"""The data side of the sticker layer: which packs, which moments, which emoji.

Everything here is loaded from ``data/sticker_map.json`` so that re-tuning (or
retiring a pack) never needs a code change. The loader is **strict about
shapes and soft about failure**: a malformed file disables stickers with one
loud log line, it never takes the bot down — the same stance the artwork
subsystem takes with its own map.

Licensing note (kept in the data file as well, because it is a policy fact, not
a code fact): only operator-approved packs may be enabled. The 2026-10-12
discovery run found far prettier xianxia/donghua packs (Mo Dao Zu Shi and
Heaven Official's Blessing fan sets); they are deliberately *absent* here
because they are fan-art of copyrighted commercial works. The research pool is
preserved at ``agent/research/sticker-pool.json`` if that decision ever changes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sticker_map.json"

#: a pack the directory/advertisement printed a link for *is* trusted more than
#: one inferred from a page slug — the flag is informational for the operator,
#: the runtime does not branch on it (a wrong name simply fails once and logs).
FORMATS = {"static", "animated", "video", "mixed"}


@dataclass(frozen=True)
class Pack:
    """One public Telegram sticker set the bot is allowed to draw from."""

    id: str
    short_name: str
    title: str
    format: str
    enabled: bool
    verified_add_link: bool
    source: str
    note: str = ""


@dataclass(frozen=True)
class Category:
    """A reaction *family* and the emoji it likes, in preference order."""

    name: str
    emojis: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class Moment:
    """A game moment mapped onto a reaction category.

    ``throttle_minutes`` = 0 means "the once_key alone decides" (one reaction
    per attempt/event); anything higher allows a re-send after that window,
    which is what keeps a repeatable moment (a victory in the same zone) from
    becoming wallpaper.
    """

    key: str
    category: str
    throttle_minutes: int = 0
    note: str = ""


@dataclass
class Catalog:
    packs: list[Pack] = field(default_factory=list)
    categories: dict[str, Category] = field(default_factory=dict)
    moments: dict[str, Moment] = field(default_factory=dict)

    def enabled_packs(self, wanted: str = "") -> list[Pack]:
        """Packs to actually use, in map order.

        ``wanted`` is the operator's ``STICKER_PACKS`` setting (comma-separated
        ids or short names). Empty → every pack the map marks ``enabled``.
        Unknown names are logged and skipped, never fatal — a typo must not
        silence the feature silently.
        """
        usable = [p for p in self.packs if p.enabled]
        if not wanted.strip():
            return usable
        by_key: dict[str, Pack] = {}
        for pack in self.packs:
            by_key[pack.id.lower()] = pack
            by_key[pack.short_name.lower()] = pack
        chosen: list[Pack] = []
        for raw in wanted.split(","):
            name = raw.strip().lower()
            if not name:
                continue
            pack = by_key.get(name)
            if pack is None:
                logger.warning("sticker: STICKER_PACKS names unknown pack %r — skipped", raw.strip())
                continue
            if not pack.enabled:
                # an explicit ask beats the map's default-off flag, but it is
                # announced, because an unconfirmed link is the usual reason a
                # pack is off in the first place
                logger.warning("sticker: pack %r is disabled in the map but requested "
                               "explicitly — using it", pack.short_name)
            if pack not in chosen:
                chosen.append(pack)
        return chosen

    def moment(self, key: str) -> Moment | None:
        return self.moments.get(key)

    def emojis_for(self, category_name: str) -> tuple[str, ...]:
        cat = self.categories.get(category_name)
        return cat.emojis if cat else ()

    def validate(self) -> list[str]:
        """Return the problems found (empty list = the map is coherent).

        Called at load time: a moment pointing at a missing category would
        otherwise silently never fire.
        """
        problems: list[str] = []
        for pack in self.packs:
            if pack.format not in FORMATS:
                problems.append(f"pack {pack.id}: unknown format {pack.format!r}")
            if not pack.short_name.strip():
                problems.append(f"pack {pack.id}: empty short_name")
        seen: set[str] = set()
        for pack in self.packs:
            if pack.id in seen:
                problems.append(f"duplicate pack id {pack.id!r}")
            seen.add(pack.id)
        for key, moment in self.moments.items():
            if moment.category not in self.categories:
                problems.append(f"moment {key!r} points at unknown category {moment.category!r}")
            if moment.throttle_minutes < 0:
                problems.append(f"moment {key!r}: negative throttle")
        for name, cat in self.categories.items():
            if not cat.emojis:
                problems.append(f"category {name!r} has no emoji preferences")
        return problems


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def parse_catalog(raw: object) -> Catalog:
    """Build a :class:`Catalog` from already-decoded JSON (kept separate so the
    loader can be exercised without touching the filesystem in tests)."""
    data = raw if isinstance(raw, dict) else {}
    packs: list[Pack] = []
    for entry in data.get("packs", []) or []:
        if not isinstance(entry, dict):
            continue
        short = str(entry.get("short_name", "")).strip()
        if not short:
            continue
        packs.append(Pack(
            id=str(entry.get("id") or short),
            short_name=short,
            title=str(entry.get("title") or short),
            format=str(entry.get("format") or "static").strip().lower(),
            enabled=_as_bool(entry.get("enabled"), default=False),
            verified_add_link=_as_bool(entry.get("verified_add_link"), default=False),
            source=str(entry.get("source") or ""),
            note=str(entry.get("note") or ""),
        ))
    categories: dict[str, Category] = {}
    for name, entry in (data.get("categories") or {}).items():
        if not isinstance(entry, dict):
            continue
        emojis = tuple(str(e) for e in (entry.get("emojis") or []) if str(e).strip())
        categories[str(name)] = Category(name=str(name), emojis=emojis,
                                         note=str(entry.get("note") or ""))
    moments: dict[str, Moment] = {}
    for key, entry in (data.get("moments") or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            throttle = int(entry.get("throttle_minutes", 0) or 0)
        except (TypeError, ValueError):
            throttle = 0
        moments[str(key)] = Moment(key=str(key),
                                   category=str(entry.get("category") or ""),
                                   throttle_minutes=throttle,
                                   note=str(entry.get("note") or ""))
    return Catalog(packs=packs, categories=categories, moments=moments)


def load_catalog(path: Path | str | None = None) -> Catalog | None:
    """Load and validate the map. ``None`` = stickers are unavailable.

    Never raises: a missing or corrupt file disables the feature with one
    explanatory log line, exactly like a dead artwork provider.
    """
    target = Path(path) if path else DEFAULT_MAP_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("sticker: map not found at %s — reactions stay off", target)
        return None
    except (OSError, ValueError) as exc:
        logger.warning("sticker: map at %s is unreadable (%s) — reactions stay off",
                       target, str(exc)[:120])
        return None
    catalog = parse_catalog(raw)
    problems = catalog.validate()
    if problems:
        # A structurally broken map is worse than none: a moment that can never
        # fire looks like a bug in the handler. Refuse it, loudly, but keep the
        # bot alive.
        for problem in problems[:8]:
            logger.error("sticker: map problem — %s", problem)
        return None
    if not catalog.enabled_packs():
        logger.warning("sticker: map has no enabled packs — reactions stay off")
        return None
    return catalog

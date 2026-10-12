"""Grimhaven sticker-reaction subsystem.

Reaction stickers are **decoration**, exactly like the artwork layer next door:
the game action is the product. This package therefore mirrors
:mod:`grimhaven.images` deliberately — same guarantees, same failure stance:

* :class:`~grimhaven.stickers.service.StickerService` — resolves a public
  sticker pack's ids at runtime (``getStickerSet``), caches them **in memory
  only**, and picks a sticker for a game moment;
* :func:`begin_reaction` / :func:`join_reaction` — the handler-facing delivery
  layer: dedup key claimed synchronously (a retried callback cannot react
  twice), the actual send runs as a bounded background task and **never
  raises**, never delays the rendered screen, and never rolls back a game
  action;
* the pack/moment/emoji policy lives in ``data/sticker_map.json`` — adding a
  pack or re-tuning a moment is data, not code.

Two facts shape the whole design (both verified against Telegram's own
documentation — see ``docs/STICKERS.md``):

1. **Premium sticker effects cannot be sent by a bot.** Premium stickers are
   sendable only by Premium *users*; the Bot API exposes the effect as the
   read-only ``Sticker.premium_animation`` field. This subsystem therefore uses
   ordinary public packs, and no code path claims otherwise.
2. **A ``file_id`` is bot-specific and can expire.** Storing a foreign one
   guarantees 400 errors later, so ids are resolved fresh and cached with a
   TTL; a stale id triggers one refresh-and-retry, then silence.
"""
from .catalog import Catalog, Moment, Pack, load_catalog
from .delivery import (begin_reaction, deliver_reaction, join_reaction,
                       reaction_cooldown_remaining)
from .service import StickerRef, StickerService

__all__ = [
    "Catalog",
    "Moment",
    "Pack",
    "StickerRef",
    "StickerService",
    "begin_reaction",
    "deliver_reaction",
    "join_reaction",
    "load_catalog",
    "reaction_cooldown_remaining",
]

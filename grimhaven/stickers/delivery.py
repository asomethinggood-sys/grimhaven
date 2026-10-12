"""Send the reaction sticker — or don't, and say nothing about it.

Contract with the handlers (mirrors :mod:`grimhaven.images.delivery` on purpose;
the two layers must fail identically or the game grows two personalities):

* :func:`begin_reaction` **claims its dedup key synchronously** — before any
  network work starts — so a retried or double-tapped callback cannot post the
  same reaction twice. Keys live in the player document's existing
  ``ui.artwork_sent`` store under a ``sticker:`` namespace, reusing
  :func:`~grimhaven.images.delivery.claim_once_key` instead of inventing a
  second bookkeeping system;
* a **per-chat cooldown** flattens bursts (a player tapping through battles
  fast must not turn the chat into a flip-book);
* the send runs as a **background task** bounded by ``sticker_task_timeout``.
  It never raises, never delays the rendered screen, and never rolls back the
  game action that already landed;
* on a ``file_id``-shaped rejection the set is re-resolved **once** and the send
  retried — the documented recovery for an expired per-bot id;
* a failure is logged (first occurrence per pack, then debug) and the game
  continues in text. There is no user-visible error for decoration.

What this layer deliberately does **not** do: attach premium effects, claim any
Premium capability, or follow a sticker with an apologetic text message. The
text rung of the fallback ladder already exists — the game's own localized
message with its emoji.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..errors import TelegramError
from ..images.delivery import claim_once_key
from .service import StickerRef, StickerService

logger = logging.getLogger(__name__)

#: once_keys are namespaced so a sticker and a photo may share the store safely
KEY_PREFIX = "sticker:"

#: chat_id → monotonic stamp of the last reaction actually handed to a task.
#: In-memory on purpose: a restart resetting the cooldown is harmless, and the
#: hosted bot restarts routinely (see .github/workflows/bot.yml).
#: ``time.monotonic`` rather than the event loop's clock, because the cooldown
#: is also read from tests and from sync startup code where no loop is running.
_LAST_SEND: dict[int, float] = {}

#: id-refresh heuristics: Telegram reports a dead bot file roughly like this
_STALE_HINTS = ("file_id", "file identifier", "wrong file", "sticker is invalid",
                "not found", "FILE_INVALID", "STICKER_INVALID")


def reaction_cooldown_remaining(chat_id: int, seconds: float) -> float:
    """Seconds left before this chat may receive another sticker (0 = ready).

    Exposed for tests and the startup log; the value is never persisted.
    """
    if seconds <= 0:
        return 0.0
    last = _LAST_SEND.get(chat_id)
    if last is None:
        return 0.0
    elapsed = time.monotonic() - last
    return max(0.0, seconds - elapsed)


def _mark_sent(chat_id: int) -> None:
    _LAST_SEND[chat_id] = time.monotonic()


def begin_reaction(bot, chat_id: int, user: dict, moment: str, *,
                   service: StickerService | None,
                   once_key: str | None = None,
                   throttle_minutes: int = 0) -> asyncio.Task | None:
    """Start (or skip) the background sticker send. Returns the task or ``None``.

    Skipped — silently and correctly — when the layer is off, when the moment
    was already reacted to, when the chat is inside its cooldown, or when no
    running loop can own the task.
    """
    if service is None or not moment:
        return None
    settings = service.settings
    if not getattr(settings, "sticker_enabled", False):
        return None
    # Ask for the loop *before* claiming anything: ``asyncio.create_task`` raises
    # on a missing loop, and by then the dedup key would already be consumed and
    # the coroutine object would exist forever un-awaited (Python warns; the
    # reaction is silently lost). Cheap check, strictly first.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("sticker: no running loop; %r not scheduled", moment)
        return None
    cooldown = float(getattr(settings, "sticker_cooldown_seconds", 0) or 0)
    if reaction_cooldown_remaining(chat_id, cooldown) > 0:
        logger.debug("sticker: %r skipped for chat %s — cooldown", moment, chat_id)
        return None
    key = f"{KEY_PREFIX}{once_key or moment}"
    if not claim_once_key(user, key, throttle_minutes=int(throttle_minutes or 0)):
        logger.debug("sticker: %r already claimed (%s)", moment, key)
        return None
    task = loop.create_task(deliver_reaction(bot, chat_id, user, moment, service=service))
    _mark_sent(chat_id)
    return task


async def deliver_reaction(bot, chat_id: int, user: dict, moment: str, *,
                           service: StickerService) -> None:
    """Resolve, send, recover once, give up quietly. Never raises.

    The whole attempt — resolve, send, refresh, retry — sits under
    ``sticker_task_timeout``. Without that ceiling the ladder can legitimately
    spend four times ``sticker_timeout`` (two resolves, two sends) and keep a
    task alive long after the screen it belonged to has scrolled away.
    """
    ceiling = float(getattr(service.settings, "sticker_task_timeout", 0) or 0) or None
    try:
        if ceiling is None:
            await _attempt(bot, chat_id, moment, service)
        else:
            await asyncio.wait_for(_attempt(bot, chat_id, moment, service),
                                   timeout=ceiling)
    except asyncio.CancelledError:
        raise                                  # shutdown is not a sticker failure
    except asyncio.TimeoutError:
        logger.warning("sticker: %r exceeded its %.1fs budget and was abandoned",
                       moment, ceiling)
    except Exception:      # noqa: BLE001 — a decoration must never surface
        logger.exception("sticker: delivery for moment %r failed", moment)


async def _attempt(bot, chat_id: int, moment: str, service: StickerService) -> None:
    """The retry ladder itself, bounded by the caller."""
    try:
        ref = await service.pick(bot, moment)
        if ref is None:
            return
        if await _send(bot, chat_id, ref, service):
            return
        # A rejected id is the documented failure mode of a long-lived cache:
        # refresh the set once and retry with a freshly quoted id.
        service.forget_pack(ref.pack)
        pack = next((p for p in service.packs() if p.short_name == ref.pack), None)
        if pack is None:
            return
        refs = await service.resolve(bot, pack)
        retry = next((r for r in refs if r.file_id != ref.file_id), None) or (refs[0] if refs else None)
        if retry is not None:
            await _send(bot, chat_id, retry, service)
    except Exception:      # noqa: BLE001 — one bad attempt must not kill the layer
        logger.exception("sticker: attempt for moment %r failed", moment)


async def _send(bot, chat_id: int, ref: StickerRef, service: StickerService) -> bool:
    """One send attempt. ``True`` = Telegram accepted it."""
    timeout = float(getattr(service.settings, "sticker_timeout", 0) or 0) or None
    try:
        call = bot.send_sticker(chat_id=chat_id, sticker=ref.file_id)
        if timeout is None:
            await call
        else:
            await asyncio.wait_for(call, timeout=timeout)
        logger.debug("sticker: sent %s (%s) to chat %s", ref.pack, ref.kind, chat_id)
        return True
    except TelegramError as exc:
        message = str(exc)
        if any(hint.lower() in message.lower() for hint in _STALE_HINTS):
            logger.info("sticker: id rejected (%s) — refreshing the set", message[:120])
            return False
        logger.warning("sticker: send refused for %s: %s", ref.pack, message[:140])
        return False
    except asyncio.TimeoutError:
        logger.warning("sticker: send to chat %s timed out", chat_id)
        return False
    except Exception:      # noqa: BLE001
        logger.exception("sticker: unexpected send failure for %s", ref.pack)
        return False


async def join_reaction(task: asyncio.Task | None, budget_seconds: float) -> None:
    """Wait a bounded moment for a scheduled reaction (used inside the
    tribulation drama, where a sticker landing before the bolt reads better).

    A finished, failed or cancelled task is not an error; this only exists so a
    slow Telegram call cannot eat the scene.
    """
    if task is None or budget_seconds <= 0:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=budget_seconds)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return
    except Exception:      # noqa: BLE001 — deliver_reaction already swallows
        return

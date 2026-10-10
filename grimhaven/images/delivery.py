"""Turn a cached artwork row into an actual Telegram **photo message**.

Contract with the handlers
--------------------------
``begin_artwork(bot, chat_id, user, spec, service=...)`` is the entry point
game code calls. It

* **claims the dedup key first, synchronously** — the player's document
  records ``once_key`` before any network work starts, so a retried/tapped
  twice callback cannot deliver the same artwork twice (a throttled key, like
  a zone view, may re-send after its window);
* resolves the artwork through the service (cache hit → instant send; miss →
  one bounded fetch, then silence — the handler's own document save then
  persists the claim, and startup prefetching warms the cache);
* reuses the Telegram ``file_id`` stored with the row so already-uploaded
  bytes never travel twice, falling back to a fresh ``InputFile`` upload when
  Telegram no longer knows the id;
* clamps captions to Telegram's 1024-character limit, sends plain text, and
  routes the rare oversized file through ``send_document``;
* **never raises, never blocks the handler** — the actual delivery runs as a
  background task capped by ``artwork_task_timeout``; a failure of any kind
  lands in the log, not in the chat, and cannot roll back or corrupt the
  completed game action.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_CAPTION_LIMIT = 1024
#: photo files above this go out as documents instead of being refused
PHOTO_COMFORT_BYTES = 5_000_000


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def claim_once_key(user: dict, once_key: str, *, throttle_minutes: int = 0) -> bool:
    """Return True when this send may proceed (and mark it claimed).

    ``throttle_minutes`` > 0 allows re-sending after that many minutes, so a
    zone screen can show its picture again tomorrow but not on every tap.
    Entries older than 48 h are pruned to keep the document small.
    """
    ui = user.setdefault("ui", {})
    sent = ui.setdefault("artwork_sent", {})
    if not isinstance(sent, dict):
        sent = {}
        ui["artwork_sent"] = sent
    now = dt.datetime.now(dt.timezone.utc)
    previous = None
    for key, stamp in list(sent.items()):
        try:
            when = dt.datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            sent.pop(key, None)
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if now - when > dt.timedelta(hours=48):
            sent.pop(key, None)
        elif key == once_key:
            previous = when
    if previous is not None:
        if throttle_minutes <= 0 or now - previous < dt.timedelta(minutes=throttle_minutes):
            return False
    sent[once_key] = _iso_now()
    return True


async def send_photo_row(bot, chat_id: int, path: Path, caption: str, *,
                         file_id: str = "") -> str | None:
    """Deliver one cached file as a Telegram photo; returns the new file_id.

    Raises the PTB error when the upload itself fails —
    :func:`deliver_artwork` decides how quietly to swallow it.
    """
    from telegram import InputFile
    from telegram.error import TelegramError as PtbTelegramError

    caption = (caption or "")[:TELEGRAM_CAPTION_LIMIT].strip()

    if file_id:
        try:
            msg = await bot.send_photo(chat_id=chat_id, photo=file_id,
                                       caption=caption or None)
            return _extract_file_id(msg) or file_id
        except PtbTelegramError as exc:
            # file ids can expire (token swap, server-side GC) — upload fresh
            logger.debug("artwork: stored file_id rejected (%s); re-uploading",
                         str(exc)[:120])

    size = path.stat().st_size
    try:
        with open(path, "rb") as fh:
            msg = await bot.send_photo(chat_id=chat_id,
                                       photo=InputFile(fh, filename=path.name),
                                       caption=caption or None)
        return _extract_file_id(msg)
    except PtbTelegramError as exc:
        if size > PHOTO_COMFORT_BYTES:
            logger.info("artwork: photo upload failed for a large file (%d bytes) — "
                        "trying send_document", size)
            with open(path, "rb") as fh:
                await bot.send_document(chat_id=chat_id,
                                        document=InputFile(fh, filename=path.name),
                                        caption=caption or None)
            return None
        raise exc


def _extract_file_id(message: Any) -> str:
    try:
        photo = getattr(message, "photo", None)
        if photo:
            return str(photo[-1].file_id)
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


async def deliver_artwork(bot, chat_id: int, user: dict, spec: dict, *,
                          service: Any, claimed: bool = False) -> bool:
    """Send the artwork for one game event. Returns True when a message landed.

    ``spec``: ``{"asset_key": str, "queries": [str]…, "caption": str,
    "once_key": str?, "throttle_minutes": int?}``
    """
    if service is None or bot is None:
        return False
    try:
        asset_key = str(spec.get("asset_key") or "")
        if not asset_key:
            return False
        if not claimed and spec.get("once_key"):
            if not claim_once_key(user, str(spec["once_key"]),
                                  throttle_minutes=int(spec.get("throttle_minutes") or 0)):
                logger.debug("artwork: duplicate send suppressed key=%s user=%s",
                             spec.get("once_key"), user.get("user_id"))
                return False
        timeout = float(getattr(service.settings, "artwork_task_timeout", 45.0) or 45.0)
        row = await asyncio.wait_for(
            service.get_or_fetch_artwork(asset_key, spec.get("queries") or None),
            timeout=timeout)
        if row is None:
            return False
        try:
            path = service.row_path(row)
        except Exception as exc:       # noqa: BLE001 — path guard, stay silent
            logger.warning("artwork: cached path rejected for %s: %s", asset_key, exc)
            return False
        caption = str(spec.get("caption") or "")
        file_id = await asyncio.wait_for(
            send_photo_row(bot, chat_id, path, caption,
                           file_id=str(row.get("telegram_file_id") or "")),
            timeout=timeout)
        if file_id and file_id != row.get("telegram_file_id"):
            try:
                service.storage.artwork_set_file_id(asset_key, file_id)
            except Exception:
                logger.debug("artwork: file_id could not be indexed")
        return True
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("artwork: send for chat %s exceeded its budget — skipped",
                       chat_id)
        return False
    except ImportError:
        logger.debug("artwork: telegram package unavailable — delivery skipped")
        return False
    except Exception:      # an image failure never breaks gameplay
        logger.warning("artwork: delivery to chat %s failed (gameplay unaffected)",
                       chat_id, exc_info=True)
        return False


def begin_artwork(bot, chat_id: int, user: dict, spec: dict, *,
                  service: Any) -> asyncio.Task | None:
    """Claim + launch the background send. Returns the task (or None).

    The claim runs here, synchronously, so the handler's very next
    ``ctx.save(user)`` persists the dedup marker even if the player spams the
    button while the task is still fetching. Callers that want the picture
    *before* the next render can ``await join_artwork(task, budget)``.
    """
    if service is None or bot is None or not isinstance(spec, dict):
        return None
    try:
        once_key = spec.get("once_key")
        if once_key:
            if not claim_once_key(user, str(once_key),
                                  throttle_minutes=int(spec.get("throttle_minutes") or 0)):
                return None
            spec = {**spec, "_claimed_once_key": str(once_key)}
        loop = asyncio.get_running_loop()
    except RuntimeError:               # no loop → nothing to schedule (sync test)
        logger.debug("artwork: no running loop — artwork not scheduled")
        return None
    task = loop.create_task(deliver_artwork(bot, chat_id, user, spec,
                                            service=service, claimed=True))
    # keep a reference so the loop cannot GC the task mid-flight, and swallow
    # nothing silently: any escaped exception is logged at the artwork layer
    registry: set = getattr(service, "_tasks", None)
    if isinstance(registry, set):
        registry.add(task)
        task.add_done_callback(registry.discard)

        def _log_result(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error("artwork: background send task died", exc_info=exc)
        task.add_done_callback(_log_result)
    return task


async def join_artwork(task: asyncio.Task | None, budget_seconds: float) -> None:
    """Wait up to ``budget`` for a scheduled send, then let it finish in the
    background — never keep the player inside a dramatic pause."""
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, float(budget_seconds)))
    except asyncio.TimeoutError:
        logger.debug("artwork: send still running after %.1fs — gameplay continues",
                     budget_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("artwork: awaited send failed; gameplay continues", exc_info=True)

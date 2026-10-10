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
* reuses Telegram ``file_id``s **within this process only** — every
  successful upload registers its id in the service's in-memory registry
  (content hash → id), so identical bytes travel at most once per run and
  every quoted id is one this process just received from Telegram; the
  persisted column is never read on the send path (Telegram reclaims bot
  files after roughly a day, the DB outlives every hosting cycle, and a
  stale id is a guaranteed 400 "can't find file for file_id of type
  'PhotoSize'");
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
            # the id came from this process's own registry, so Telegram only
            # drops it after ~24 h inside one very long run (or after a token
            # swap) — fall through and upload the bytes again
            logger.info("artwork: Telegram no longer knows a remembered file_id "
                        "(%s) — falling back to a fresh upload", str(exc)[:120])

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


def _note_file_id(service: Any, file_hash: str, asset_key: str,
                  file_id: str, row: dict) -> None:
    """Feed a freshly uploaded file_id back into the process-local registry
    and the ops index.  The DB column is diagnostics only — it must never
    reach the send path again (see ``ProcessFileIdMemory``)."""
    memory = getattr(service, "file_id_memory", None)
    if memory is not None and file_hash:
        memory.put(file_hash, file_id)
    if file_id != row.get("telegram_file_id"):
        try:
            service.storage.artwork_set_file_id(asset_key, file_id)
        except Exception:      # the index is cosmetic; the send already landed
            logger.debug("artwork: ops index refused the new file_id")


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
        file_hash = str(row.get("file_hash") or "")
        # file_id reuse is process-local: only an id THIS process received
        # from Telegram may be quoted — never the persisted column, which
        # Telegram has almost certainly already reclaimed (see
        # ProcessFileIdMemory).
        memory = getattr(service, "file_id_memory", None)
        live_id = memory.lookup(file_hash) if memory is not None else ""
        new_id = await asyncio.wait_for(
            send_photo_row(bot, chat_id, path, caption, file_id=live_id),
            timeout=timeout)
        if new_id:
            _note_file_id(service, file_hash, asset_key, new_id, row)
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

"""One place that turns an exception into (a log line, a player-facing message).

Rules the whole module exists to enforce:

* **never** answer with a bare "unknown error" — every unexpected failure gets a
  category-appropriate explanation *and* a short incident reference that appears
  in exactly one log line, so a screenshot is enough to find the traceback;
* expected guard/domain outcomes stay localized and actionable, with no scary
  reference id (being told "you cannot trade during seclusion" is not an error);
* a failure *inside* the reporting path (e.g. the answer call itself was too late)
  is logged and swallowed — never raised, so a broken tap cannot crash the loop;
* logs carry stage, path, category, update id and action, but **no bot token and
  no private player document** (only the numeric user id, needed to correlate).
"""
from __future__ import annotations

import logging
from typing import Any

from telegram.error import BadRequest, TelegramError as PtbTelegramError

from ...errors import (Category, GrimhavenError, classify, incident_id,
                       is_message_gone, is_not_modified, is_query_expired,
                       summarize)
from ...localization import t

logger = logging.getLogger("grimhaven.bot")

#: handler families, for the log line
PATH_MESSAGE = "message"      # guard_update → on_reply_button → commands._run
PATH_CALLBACK = "callback"    # guard_update → on_callback
PATH_ADMIN = "admin"
PATH_LIFECYCLE = "lifecycle"  # root-message bookkeeping outside a route


def log_line(*, stage: str, path: str, category: Category, ref: str,
             update_id: Any, user_id: Any, action: Any) -> str:
    return ("failure path=%s stage=%s category=%s incident=%s update_id=%s "
            "user_id=%s action=%r") % (path, stage, category.value, ref,
                                      update_id, user_id, action)


def describe(exc: BaseException) -> tuple[Category, str | None, str | None]:
    """→ (category, locale key, already-localized text).

    A :class:`GrimhavenError` may carry its own ``user_key``/``user_text``; that
    wins, because the raiser knows what the player should be told.
    """
    cat = classify(exc)
    key = text = None
    if isinstance(exc, GrimhavenError):
        key, text = exc.user_key, exc.user_text
    return cat, key, text


async def notify_failure(update, context, exc: BaseException, *, stage: str,
                         path: str = PATH_MESSAGE, action: Any = None,
                         lang: str = "fa", user_id: Any = None) -> str:
    """Log ``exc`` with full traceback and tell the player what to do.

    Returns the incident reference ("" when the outcome was an expected notice)
    so a caller can attach it to a further log line.
    """
    category, key, text = describe(exc)
    reported = category in (Category.STORAGE, Category.TELEGRAM,
                            Category.CONTENT, Category.INTERNAL)
    ref = incident_id() if reported else ""
    summary = summarize(exc)
    logger.exception("%s | %s", log_line(stage=stage, path=path, category=category,
                                         ref=ref or "-", update_id=getattr(update, "update_id", None),
                                         user_id=user_id, action=action),
                     summary.get("error", ""), exc_info=exc)

    message = t(lang, key) if key else (text or "")
    if not message:
        message = t(lang, _FALLBACK_KEY[category])
    if ref:
        message = f"{message}\n{t(lang, 'ERR_REFERENCE', ref=ref)}"

    if not message.strip():
        return ref
    await _deliver(update, context, message, category=category, stage=stage)
    return ref


_FALLBACK_KEY = {
    Category.GUARD: "ERR_UNKNOWN",
    Category.INPUT: "ERR_INVALID_ACTION",
    Category.DOMAIN: "ERR_UNKNOWN",
    Category.STORAGE: "ERR_STORAGE",
    Category.TELEGRAM: "ERR_DELIVERY",
    Category.CONTENT: "ERR_CONTENT",
    Category.INTERNAL: "ERR_INTERNAL",
}


async def _deliver(update, context, message: str, *, category: Category,
                   stage: str) -> None:
    """Best effort delivery of a failure notice, with no false success.

    ``answerCallbackQuery`` is the only channel that can stay silent forever (the
    30 s window), so a notice that cannot be shown is logged with the stage that
    produced it instead of being reported to the player as a second error.
    """
    query = getattr(update, "callback_query", None)
    try:
        if query is not None:
            await query.answer(message, show_alert=True)
            return
        if getattr(update, "message", None) is not None:
            await update.message.reply_text(message)
            return
        chat = getattr(update, "effective_chat", None)
        if chat is not None:
            await chat.send_message(message)
    except (BadRequest, PtbTelegramError) as exc:
        if is_query_expired(exc):
            logger.warning("failure notice could not be shown (callback expired) "
                           "at stage=%s category=%s", stage, category.value)
            return
        logger.error("failure notice itself was rejected at stage=%s: %s: %s",
                     stage, type(exc).__name__, str(exc)[:200])
    except Exception:  # pragma: no cover — reporting must never crash the loop
        logger.exception("failure notice raised at stage=%s", stage)


def report_stage(exc: BaseException, stage: str) -> str:
    """Where the failure really happened, for the log line.

    The text path renders *inside* ``_dispatch`` (the router returns the screen and
    the handler presents it), so a Telegram rejection there would be logged as
    ``stage=dispatch`` and read like an engine bug. The category already says
    ``telegram``; the stage must say which stage broke.
    """
    if stage == "dispatch" and classify(exc) is Category.TELEGRAM:
        return "telegram-render"
    return stage


def render_failed(exc: BaseException) -> bool:
    """True when an API error means the screen really did not get through.

    ``Message is not modified`` and ``message to delete/edit not found`` are
    bookkeeping no-ops (the player already sees that exact text / the message is
    already gone); everything else is a delivery failure and must be reported as
    one instead of being swallowed as success.
    """
    return not (is_not_modified(exc) or is_message_gone(exc))

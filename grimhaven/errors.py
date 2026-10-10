"""Error taxonomy — one place that decides *what kind* of failure happened.

Before this module every failure collapsed into a single ``except Exception``
that answered with ``ERR_UNKNOWN`` ("خطای ناشناخته"), so a database outage, a
Telegram API hiccup, an expected guard refusal and a genuine defect all looked
identical to the player and to the log.  The layers are:

``guard`` / ``input`` / ``domain``
    Expected outcomes.  Localized, actionable, no incident id — the player was
    told something useful (blocked while in combat, item missing, …).
``storage``
    SQLite could not be read/written or the file is not a usable database.
    Distinct safe user message + incident id; full traceback in the log.
``telegram``
    The Bot API rejected a send/edit/delete, or the network failed.  The game
    state is fine — only delivery broke — so it must not be reported as a game
    error, and a failed render must never be counted as success.
``content``
    Shipped game data / locales are inconsistent (missing key, bad JSON).
``internal``
    Anything unexpected: a real defect.  Safe generic message + incident id.

`classify()` is deliberately conservative: an unknown exception is an
``internal`` defect, never silently "ok".
"""
from __future__ import annotations

import enum
import json
import secrets
import sqlite3
from typing import Any


class Category(str, enum.Enum):
    """Coarse failure families, used in logs and for the user-facing choice."""

    GUARD = "guard"
    INPUT = "input"
    DOMAIN = "domain"
    STORAGE = "storage"
    TELEGRAM = "telegram"
    CONTENT = "content"
    INTERNAL = "internal"


#: categories that must carry a diagnosable reference for the player
REPORTED = (Category.STORAGE, Category.TELEGRAM, Category.CONTENT, Category.INTERNAL)

#: category → locale key of the safe, generic user message
USER_KEY: dict[Category, str] = {
    Category.STORAGE: "ERR_STORAGE",
    Category.TELEGRAM: "ERR_DELIVERY",
    Category.CONTENT: "ERR_CONTENT",
    Category.INTERNAL: "ERR_INTERNAL",
    Category.GUARD: "ERR_UNKNOWN",
    Category.INPUT: "ERR_UNKNOWN",
    Category.DOMAIN: "ERR_UNKNOWN",
}


class GrimhavenError(Exception):
    """Base class: an error that already knows its category and (optionally)
    the localized message the player should see."""

    category: Category = Category.INTERNAL
    #: locale key of the actionable, player-facing explanation (expected flows)
    user_key: str | None = None
    #: free-form detail already localized by the raiser (guards use this)
    user_text: str | None = None

    def __init__(self, *args: Any, user_key: str | None = None,
                 user_text: str | None = None, **context: Any) -> None:
        super().__init__(*args)
        if user_key:
            self.user_key = user_key
        if user_text:
            self.user_text = user_text
        self.context: dict[str, Any] = context


class Notice(GrimhavenError):
    """An *expected* outcome that is not a defect (guard lock, bad tap,
    domain refusal).  ``user_key``/``user_text`` is what the player reads."""

    category = Category.DOMAIN


class StorageError(GrimhavenError):
    """The world database could not be read or written."""

    category = Category.STORAGE


class TelegramError(GrimhavenError):
    """Telegram rejected or lost a send/edit/delete, or answering failed."""

    category = Category.TELEGRAM


class ContentError(GrimhavenError):
    """Shipped content (data/*.json, locales/*) is missing or inconsistent."""

    category = Category.CONTENT


def incident_id() -> str:
    """Short, non-guessable reference tying a player's screenshot to one log
    line.  Contains no user id, no token, no document contents."""
    return secrets.token_hex(3).upper()


def classify(exc: BaseException) -> Category:
    """Map an arbitrary exception onto a category, without importing telegram.

    Deliberately conservative: anything unrecognised is an ``internal`` defect,
    never a shrug.  Telegram's own error classes live in ``telegram.error`` and
    are matched by module, so this file does not need the bot installed.
    """
    if isinstance(exc, GrimhavenError):
        return exc.category
    if isinstance(exc, (sqlite3.Error, json.JSONDecodeError)):
        # a stored document that is not valid JSON is a storage-layer problem
        return Category.STORAGE
    if isinstance(exc, OSError):
        # missing/unreadable/unwritable database file, disk full, …
        return Category.STORAGE
    if (type(exc).__module__ or "").startswith("telegram."):
        return Category.TELEGRAM
    return Category.INTERNAL


def summarize(exc: BaseException) -> dict[str, Any]:
    """Everything worth logging about a failure, and nothing that isn't: no
    token, no private document dump."""
    cat = classify(exc)
    out: dict[str, Any] = {
        "category": cat.value,
        "error": f"{type(exc).__name__}: {str(exc)[:400]}",
    }
    if isinstance(exc, GrimhavenError) and exc.context:
        out.update({k: v for k, v in exc.context.items() if v is not None})
    return out


def is_not_modified(exc: BaseException) -> bool:
    """Telegram's 'nothing changed' reply, which is a no-op, not a failure."""
    return "not modified" in str(exc).lower()


def is_message_gone(exc: BaseException) -> bool:
    """Telegram's 'message to delete/edit not found' family: the message is
    already gone, which the root lifecycle tolerates by design."""
    text = str(exc).lower()
    return ("message to delete not found" in text
            or "message_to_edit_not_found" in text
            or "message to edit not found" in text
            or "reply_message_not_found" in text
            or "message to reply not found" in text
            or "message not found" in text)


def is_query_expired(exc: BaseException) -> bool:
    """`answerCallbackQuery` after Telegram's ~30 s window: cosmetic only."""
    text = str(exc).lower()
    return "query is too old" in text or "query_id_invalid" in text

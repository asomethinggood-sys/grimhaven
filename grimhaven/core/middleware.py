"""State guard / interceptor (v2 — spec P6 lock model).

Cultivation is continuous idle accrual, so the MEDITATING trance lock is
retired; combat still owns the screen (with a pass-through for the round
pipeline, which validates its own round tokens), plus safe bag navigation.
Injury / paralysis locks keep the classic consequences of a true death.
"""
from __future__ import annotations

from typing import Any

from .state_machine import UserStatus, get_status, paralysis_active, is_injured
from ..engine.constants import ZONES

# roots safe to touch in every state (read-only screens & preferences)
_ALWAYS = {"profile", "settings", "setlang", "start", "panel", "noop", "help", "admin",
           "menu", "language", "deep", "backpack"}

# bag sub-actions allowed under every lock: navigation, inspection, consuming a
# pill and (re)assigning battle items — equipping & selling stay gated
_BAG_SAFE_SUBS = {"tab", "inspect", "back", "view", "use"}
_BAG_SAFE_ACTIONS = {"consume", "assign_battle"}

# blocked while heavily injured & paralysed after a true death
_PARALYSIS_BLOCKED = {"travel", "hunt", "conquer", "breakthrough", "map", "sect",
                      "shop", "buy", "martial"}
# blocked while the injury debuff runs
_INJURY_BLOCKED = {"breakthrough", "conquer"}


def callback_blocked(user: dict, data: str, now=None) -> str | None:
    """Return the locale key of the alert to show, or None when allowed."""
    status = get_status(user)
    root, _, arg = data.partition(":")
    subs = arg.split(":") if arg else []

    if status is UserStatus.IN_COMBAT:
        if root == "combat":
            return None                     # round token is validated by the handler
        if root == "bag":
            sub = subs[0] if subs else "tab"
            if sub in _BAG_SAFE_SUBS:
                return None
            if sub == "action" and len(subs) > 1 and subs[1] in _BAG_SAFE_ACTIONS:
                return None
        return "GUARD_COMBAT"

    if root in _ALWAYS or (root == "bag" and subs and subs[0] in _BAG_SAFE_SUBS):
        return None

    if status is UserStatus.SECLUSION:
        # legacy only: an old seclusion timer is treated as idle cultivation
        return None

    if status is UserStatus.HEAVILY_INJURED:
        if paralysis_active(user, now):
            if root in _PARALYSIS_BLOCKED:
                return "GUARD_PARALYSIS"
            return None
        if is_injured(user, now) and root in _INJURY_BLOCKED:
            return "GUARD_INJURED"
        if is_injured(user, now) and root == "travel" and _zone_is_perilous(subs[0] if subs else ""):
            return "GUARD_INJURED_PERILOUS"
        if is_injured(user, now) and (root == "conquer" or "conquer" in subs):
            return "GUARD_INJURED"
        return None

    return None


def _zone_is_perilous(zone_id: str) -> bool:
    zdef = ZONES.get(zone_id)
    return bool(zdef and zdef.get("guard", 1) >= 3)


# commands players can always use, and meditation-time restrictions
_ALWAYS_COMMANDS = {"start", "me", "profile", "bag", "help", "settings",
                    "language", "cultivate", "admin", "panel"}


def command_blocked(user: dict, command: str) -> str | None:
    status = get_status(user)
    cmd = command.lstrip("/").split("@", 1)[0].lower()
    if cmd in _ALWAYS_COMMANDS:
        # /cultivate doubles as the claim/stop button, always allowed
        return None
    if status is UserStatus.IN_COMBAT:
        return "GUARD_COMBAT" if cmd not in {"combat", "flee"} else None
    if status is UserStatus.HEAVILY_INJURED:
        if paralysis_active(user):
            return "GUARD_PARALYSIS"
        if cmd in {"breakthrough", "map", "hunt", "sect", "dao"}:
            return "GUARD_INJURED"
    return None


# ── python-telegram-bot interceptor ─────────────────────────────────────────

def guard_update(storage_get_user, handler):
    """Wrap a PTB coroutine handler (`(update, context)`) with the state guard."""

    async def wrapper(update: Any, context: Any) -> Any:
        try:
            tg_user = update.effective_user
            if tg_user is None:
                return await handler(update, context)
            user = storage_get_user(tg_user.id)
            if not user or user.get("account", {}).get("is_banned"):
                return await handler(update, context)
            lang = user.get("account", {}).get("language", "fa")

            alert_key = None
            if update.callback_query and update.callback_query.data:
                alert_key = callback_blocked(user, update.callback_query.data)
            elif update.message and update.message.text:
                text = update.message.text.strip()
                if text.startswith("/"):
                    alert_key = command_blocked(user, text.split()[0])
                else:
                    alert_key = command_blocked(user, text)  # reply-keyboard buttons

            if alert_key:
                from ..localization import t
                msg = t(lang, alert_key)
                if update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                else:
                    await update.message.reply_text(msg)
                return None
        except Exception:  # guard must never crash the bot
            import traceback
            traceback.print_exc()
        return await handler(update, context)

    return wrapper

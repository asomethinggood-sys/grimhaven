"""Part 1 §2.4 — Telegram handler State Guard / Interceptor.

The pure decision functions (`callback_blocked`, `command_blocked`) are shared by
the bot middleware AND the web-demo API so both layers enforce the same locks.
While MEDITATING / IN_COMBAT / SECLUSION every state-mutating action outside the
whitelist is rejected with a contextual alert (`show_alert=True`) before it can
touch the engine — closing the roam-while-meditating concurrency exploit.
"""
from __future__ import annotations

from typing import Any

from .state_machine import UserStatus, get_status, paralysis_active, is_injured
from ..engine.constants import ZONES

# roots safe to touch in every state (read-only screens & claims)
_ALWAYS = {"menu", "language", "setlang", "help", "settings", "profile", "deep",
           "backpack", "start"}

_MEDITATING_EXTRA = {"meditate", "stop_meditate", "combat"}
_SECLUSION_EXTRA = {"check_tribulation", "breakthrough", "meditate", "stop_meditate"}

# bag sub-actions allowed under any lock: navigation + consuming a pill/herb
# (healing mid-trance is the classic cultivator trope; equip & sell stay gated)
_BAG_SAFE_SUBS = {"tab", "item", "back", "view", "use"}

# blocked while heavily injured & paralysed after a true death
_PARALYSIS_BLOCKED = {"meditate", "stop_meditate", "travel", "hunt", "conquer",
                      "breakthrough", "breakthrough_do", "check_tribulation",
                      "map", "zone", "sect", "combat", "shop", "buy"}
# blocked while the 3-hour injury debuff runs
_INJURY_BLOCKED = {"breakthrough", "breakthrough_do", "check_tribulation",
                   "conquer"}


def callback_blocked(user: dict, data: str, now=None) -> str | None:
    """Return the locale key of the alert to show, or None when allowed."""
    status = get_status(user)
    root, _, arg = data.partition(":")

    if status is UserStatus.IN_COMBAT:
        if root == "combat":
            return None
        return "GUARD_COMBAT"

    bag_sub = arg.split(":", 1)[0] if arg else "tab"
    if root in _ALWAYS or (root == "bag" and bag_sub in _BAG_SAFE_SUBS):
        return None

    if status is UserStatus.MEDITATING:
        if root in _MEDITATING_EXTRA:
            return None
        return "GUARD_MEDITATING"

    if status is UserStatus.SECLUSION:
        if root in _SECLUSION_EXTRA:
            return None
        return "GUARD_SECLUSION"

    if status is UserStatus.HEAVILY_INJURED:
        if paralysis_active(user, now):
            if root in _PARALYSIS_BLOCKED:
                return "GUARD_PARALYSIS"
            return None
        if is_injured(user, now) and root in _INJURY_BLOCKED:
            return "GUARD_INJURED"
        if is_injured(user, now) and root == "travel" and _zone_is_perilous(arg):
            return "GUARD_INJURED_PERILOUS"
        if is_injured(user, now) and root == "conquer":
            return "GUARD_INJURED"
        return None

    return None


def _zone_is_perilous(zone_id: str) -> bool:
    zdef = ZONES.get(zone_id)
    return bool(zdef and zdef.get("guard", 1) >= 3)


# commands players can always use, and meditation-time restrictions
_RESTRICTED_COMMANDS = {
    "map", "hunt", "travel", "battle", "fight", "sect", "shop",
    "breakthrough", "skills", "dao", "sacrifice",
}
_ALWAYS_COMMANDS = {"start", "me", "profile", "bag", "help", "settings",
                    "language", "cultivate", "admin"}


def command_blocked(user: dict, command: str) -> str | None:
    status = get_status(user)
    cmd = command.lstrip("/").split("@", 1)[0].lower()
    if cmd in _ALWAYS_COMMANDS:
        # /cultivate doubles as the claim/stop button, always allowed
        return None
    if status is UserStatus.IN_COMBAT:
        return "GUARD_COMBAT" if cmd not in {"combat", "flee"} else None
    if status is UserStatus.MEDITATING:
        return "GUARD_MEDITATING"
    if status is UserStatus.SECLUSION:
        if cmd in {"breakthrough", "help", "settings"}:
            return None
        return "GUARD_SECLUSION"
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

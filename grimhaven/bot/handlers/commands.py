"""Slash commands + the persistent reply-keyboard router (spec P1 §2–3).

The dock lives at the bottom of the chat forever (is_persistent). Root
screens follow the single-window lifecycle: the previous root message is
deleted (or de-weaponized), a fresh one is sent, and its id is stored in
``user["ui"]["active_menu_message_id"]``. ``/panel`` silently re-attaches
the dock when Telegram has dropped it.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

MessageNotModified = BadRequest  # PTB v21 surfaces "not modified" as BadRequest
from telegram.ext import ContextTypes

from ...core.middleware import callback_blocked
from ...localization import t
from ...render import start_text
from ..keyboards import dock_reply_kb, resolve_dock_action, start_kb
from .callbacks import _canon, _dispatch
from .common import Ctx

logger = logging.getLogger(__name__)

# BotFather command registry (also pushed via set_my_commands on boot)
BOT_COMMANDS = [
    ("start", "آغاز سفر جاودانگی / Begin the immortal journey"),
    ("panel", "بازگرداندن پنل ابزار / Restore the persistent tool panel"),
    ("me", "لوح سرنوشت / Destiny scroll (profile)"),
    ("cultivate", "بارگاه مراقبه / Meditation hub & claim"),
    ("breakthrough", "آزمون شکست سد / Tribulation prep"),
    ("map", "نقشه و شکار / World map & hunt"),
    ("bag", "کوله و حلقه فضایی / Bag & spatial ring"),
    ("skills", "تالار طومارها / Martial pavilion & deck"),
    ("shop", "پاویون تجارت / Spirit Pavilion"),
    ("sect", "فرقه / Sect"),
    ("settings", "زبان و تنظیمات / Settings & language"),
    ("help", "راهنما / Guide"),
]


def get_ctx(context: ContextTypes.DEFAULT_TYPE) -> Ctx:
    return context.bot_data["ctx"]


async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Route a reply/dock press or slash command through the dispatch contract.

    The WHOLE pipeline is guarded: any failure (legacy doc, engine bug, Telegram
    hiccup) must still produce a reply — a silent tap is a lost player.
    """
    ctx: Ctx = get_ctx(context)
    stage = "load-user"
    try:
        user, _created = ctx.get_or_create_user(update.effective_user)
        lang = user["account"]["language"]
        if user["account"].get("is_banned"):
            await update.message.reply_text(t(lang, "ERR_BANNED"))
            return
        stage = "state-guard"
        data = _canon(data, user)
        blocked = callback_blocked(user, data)
        if blocked:
            await update.message.reply_text(t(lang, blocked))
            return
        stage = "settle"
        settle_res = ctx.settle(user)
        stage = "dispatch"
        action, arg = _split_for(data)
        text, kb, opts = _dispatch(ctx, user, action, arg, settle_res)
        stage = "telegram-render"
        await _present(update.message, user, text, kb, opts)
        stage = "persist"
        ctx.save(user)
    except Exception:  # pragma: no cover — safety net, never crash the bot
        logger.exception(
            "Unhandled Telegram message path failure at stage=%s action=%r update_id=%s",
            stage, data, getattr(update, "update_id", None),
        )
        try:
            doc = ctx.storage.get_user(update.effective_user.id) or {}
            lang = (doc.get("account") or {}).get("language", "fa")
            await update.message.reply_text(t(lang, "ERR_UNKNOWN"))
        except Exception:
            pass


def _split_for(data: str) -> tuple[str, str]:
    action, _, arg = data.partition(":")
    return action, arg


async def _present(message, user: dict, text: str, kb, opts: dict) -> None:
    """Single-window lifecycle for command/dock renders."""
    if opts.get("root", True):
        menu_id = (user.get("ui") or {}).get("active_menu_message_id")
        if menu_id:
            try:
                await message.bot.delete_message(message.chat_id, menu_id)
            except BadRequest:
                pass
        try:
            sent = await message.reply_text(text, reply_markup=kb)
            user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
        except BadRequest:
            pass
    else:
        try:
            await message.reply_text(text, reply_markup=kb)
        except BadRequest:
            pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Minimal onboarding: two-line narrative + ONE inline CTA, then the dock."""
    ctx: Ctx = get_ctx(context)
    try:
        user, created = ctx.get_or_create_user(update.effective_user)
        if user["account"].get("is_banned"):
            await update.message.reply_text(t(user["account"]["language"], "ERR_BANNED"))
            return
        lang = user["account"]["language"]
        ctx.settle(user)
        ctx.save(user)
        sent = await update.message.reply_text(start_text(lang), reply_markup=start_kb(lang))
        user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
        dock_msg = await update.message.reply_text(t(lang, "DOCK_LANDED"),
                                                   reply_markup=dock_reply_kb(lang))
        ctx.save(user)
    except Exception:  # pragma: no cover — safety net, never crash the bot
        import traceback
        traceback.print_exc()
        try:
            await update.message.reply_text(t("fa", "ERR_UNKNOWN"))
        except Exception:
            pass


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent recovery of the persistent keyboard (spec P1 §3)."""
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    lang = user["account"]["language"]
    try:
        await update.message.delete()
    except (BadRequest, MessageNotModified):
        pass
    sent = await update.message.chat.send_message(t(lang, "PANEL_RESTORED"),
                                                  reply_markup=dock_reply_kb(lang))
    user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
    ctx.save(user)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "settings:help")


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "profile:view:main")


async def cmd_cultivate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "cultivate:view:hub")


async def cmd_breakthrough(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "breakthrough:view:prep")


async def cmd_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "map:view:world")


async def cmd_bag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "bag:tab:gear:1")


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "martial:view:main")


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "shop:view:hub")


async def cmd_sect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "sect:view:main")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "settings:view:main")


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "settings:view:main")


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔")
        return
    user, _ = ctx.get_or_create_user(update.effective_user)
    user["account"]["is_admin"] = True
    ctx.save(user)
    await _run(update, context, "admin:usage:broadcast")


async def on_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route docked reply-keyboard button taps to their game screens.

    A tap is never swallowed: if the label is not a dock button we fall back to
    the destiny scroll, because silence reads as a dead bot (a stale keyboard
    from an older release used to produce exactly that).
    """
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    data = resolve_dock_action(text)
    if not data:
        ctx: Ctx = get_ctx(context)
        user, _ = ctx.get_or_create_user(update.effective_user)
        await _present(update.message, user, *ctx.profile_reply(user)[:2],
                       {"root": True})
        ctx.save(user)
        return
    await _run(update, context, data)

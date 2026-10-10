"""Slash commands + the persistent reply-keyboard router (spec P1 §2–3).

The dock lives at the bottom of the chat forever (is_persistent). Root
screens follow the single-window lifecycle: the previous root message is
deleted (or de-weaponized), a fresh one is sent, and its id is stored in
``user["ui"]["active_menu_message_id"]``. ``/panel`` silently re-attaches
the dock when Telegram has dropped it.

Failure handling (round 7): every stage of the pipeline is named, and a
failure is reported through :mod:`grimhaven.bot.handlers.reporting` so the
player gets the *right kind* of message (guard notice vs. storage outage vs.
delivery failure vs. internal defect) and the log gets a stage + incident id.
A screen is never "rendered" unless Telegram accepted it.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError as PtbTelegramError
from telegram.ext import ContextTypes

from ...core.middleware import callback_blocked
from ...errors import TelegramError
from ...localization import t
from ...render import start_text
from ..keyboards import dock_reply_kb, resolve_dock_action, start_kb
from .callbacks import _canon, _dispatch
from .common import Ctx
from .reporting import PATH_MESSAGE, notify_failure, report_stage, render_failed

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

    Order matters: the document is persisted *before* the render (so a Telegram
    delivery failure cannot cost the player their settled Qi or a consumed pill)
    and the root anchor is persisted *after* it (so the stored message id is the
    one Telegram actually created).
    """
    ctx: Ctx = get_ctx(context)
    bot = context.bot
    stage = "load-user"
    user = None
    lang = "fa"
    try:
        user, _created = ctx.get_or_create_user(update.effective_user)
        lang = user["account"]["language"]
        stage = "state-guard"
        if user["account"].get("is_banned"):
            await update.message.reply_text(t(lang, "ERR_BANNED"))
            return
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
        stage = "persist"
        ctx.save(user)
        stage = "telegram-render"
        await _present(bot, update.message, user, text, kb, opts)
        # artwork rides the reply path too, with the same guarantees: a separate
        # background photo that can never delay or break the rendered screen
        if opts.get("artwork"):
            from .callbacks import _apply_artwork
            _apply_artwork(bot, update.message.chat_id, user, opts, ctx)
        stage = "persist-render-state"
        ctx.save(user)
    except Exception as exc:  # pragma: no cover — safety net, never crash the bot
        await notify_failure(update, context, exc, stage=report_stage(exc, stage), path=PATH_MESSAGE,
                             action=data, lang=lang,
                             user_id=getattr(update.effective_user, "id", None))


def _split_for(data: str) -> tuple[str, str]:
    action, _, arg = data.partition(":")
    return action, arg


async def _present(bot, message, user: dict, text: str, kb, opts: dict) -> None:
    """Single-window lifecycle for command/dock renders.

    ``bot`` comes from ``ContextTypes.bot`` on purpose: python-telegram-bot v20
    removed the ``.bot`` shortcut from every ``TelegramObject``, and the code
    still called ``message.bot.delete_message(...)`` — an ``AttributeError``
    raised on *every* root render once an anchor existed, which the old blanket
    ``except Exception`` reported to the player as "unknown error".
    """
    if opts.get("no_render"):
        # nothing is worth replacing the player's current screen with; the alert
        # text alone is the answer (the text path has no toast channel)
        await message.reply_text(opts.get("alert") or text)
        return
    if not opts.get("root", True):
        await _send(bot, message, text, kb)
        return
    menu_id = (user.get("ui") or {}).get("active_menu_message_id")
    if menu_id:
        try:
            await bot.delete_message(message.chat_id, menu_id)
        except (BadRequest, PtbTelegramError) as exc:
            if render_failed(exc):
                logger.warning("root message %s could not be deleted: %s", menu_id,
                               str(exc)[:160])
    sent = await _send(bot, message, text, kb)
    if sent is not None:
        user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id


async def _send(bot, message, text: str, kb):
    """Reply to ``message``; a rejected render becomes a reported failure."""
    try:
        return await message.reply_text(text, reply_markup=kb)
    except (BadRequest, PtbTelegramError) as exc:
        if is_noop_error(exc):
            return None
        raise TelegramError(f"screen could not be delivered: {exc}",
                            operation="sendMessage", message_id=getattr(message, "message_id", None),
                            chat_id=getattr(message, "chat_id", None),
                            user_key="ERR_DELIVERY") from exc


def is_noop_error(exc: BaseException) -> bool:
    """'Message is not modified' — the player already sees exactly this text."""
    from ...errors import is_not_modified
    return is_not_modified(exc)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Minimal onboarding: two-line narrative + ONE inline CTA, then the dock."""
    ctx: Ctx = get_ctx(context)
    stage = "load-user"
    lang = "fa"
    try:
        user, created = ctx.get_or_create_user(update.effective_user)
        lang = user["account"]["language"]
        stage = "state-guard"
        if user["account"].get("is_banned"):
            await update.message.reply_text(t(lang, "ERR_BANNED"))
            return
        stage = "settle"
        ctx.settle(user)
        stage = "persist"
        ctx.save(user)
        stage = "telegram-render"
        sent = await update.message.reply_text(start_text(lang), reply_markup=start_kb(lang))
        if sent is not None:
            user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
        await update.message.reply_text(t(lang, "DOCK_LANDED"),
                                        reply_markup=dock_reply_kb(lang))
        stage = "persist-render-state"
        ctx.save(user)
    except Exception as exc:  # pragma: no cover — safety net, never crash the bot
        await notify_failure(update, context, exc, stage=report_stage(exc, stage), path=PATH_MESSAGE,
                             action="/start", lang=lang,
                             user_id=getattr(update.effective_user, "id", None))


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent recovery of the persistent keyboard (spec P1 §3)."""
    ctx: Ctx = get_ctx(context)
    stage = "load-user"
    lang = "fa"
    try:
        user, _ = ctx.get_or_create_user(update.effective_user)
        lang = user["account"]["language"]
        try:
            await update.message.delete()
        except (BadRequest, PtbTelegramError) as exc:
            if render_failed(exc):
                logger.debug("the /panel prompt could not be deleted: %s", str(exc)[:120])
        stage = "telegram-render"
        sent = await update.message.chat.send_message(t(lang, "PANEL_RESTORED"),
                                                       reply_markup=dock_reply_kb(lang))
        if sent is not None:
            user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
        stage = "persist"
        ctx.save(user)
    except Exception as exc:  # pragma: no cover
        await notify_failure(update, context, exc, stage=report_stage(exc, stage), path=PATH_MESSAGE,
                             action="/panel", lang=lang,
                             user_id=getattr(update.effective_user, "id", None))


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

    A tap is never swallowed: text that is not a dock button is answered with
    the destiny scroll, because silence reads as a dead bot (a stale keyboard
    from an older release used to produce exactly that).
    """
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    data = resolve_dock_action(text)
    if data:
        await _run(update, context, data)
        return
    ctx: Ctx = get_ctx(context)
    stage = "load-user"
    user: dict = {}
    try:
        user, _ = ctx.get_or_create_user(update.effective_user)
        stage = "telegram-render"
        await _present(context.bot, update.message, user, *ctx.profile_reply(user)[:2],
                       {"root": True})
        stage = "persist"
        ctx.save(user)
    except Exception as exc:  # pragma: no cover — a near-miss label must not be silent
        await notify_failure(update, context, exc, stage=report_stage(exc, stage), path=PATH_MESSAGE,
                             action=text[:48],
                             lang=(user.get("account") or {}).get("language", "fa"),
                             user_id=getattr(update.effective_user, "id", None))

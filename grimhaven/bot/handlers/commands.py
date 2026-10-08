"""Slash commands: /start /help /profile /language /admin."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ...engine.models import in_seclusion
from ...localization import t
from ...render import admin_text
from ..keyboards import language_kb, admin_kb
from .common import Ctx


def get_ctx(context: ContextTypes.DEFAULT_TYPE) -> Ctx:
    return context.bot_data["ctx"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    tg_user = update.effective_user
    user, created = ctx.get_or_create_user(tg_user)
    if user["account"].get("is_banned"):
        await update.message.reply_text("⛔")
        return
    lang = user["account"]["language"]
    if created:
        await update.message.reply_text(t("fa", "WELCOME_NEW"), reply_markup=language_kb())
        return
    ctx.settle(user)
    ctx.save(user)
    text, kb = ctx.profile_reply(user)
    await update.message.reply_text(text, reply_markup=kb)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    await update.message.reply_text(t(user["account"]["language"], "HELP_TEXT"))


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    if user["account"].get("is_banned"):
        await update.message.reply_text(t(user["account"]["language"], "ERR_BANNED"))
        return
    settle_res = ctx.settle(user)
    ctx.save(user)
    text, kb = ctx.profile_reply(user)
    notes = _settle_notes(user["account"]["language"], settle_res)
    await update.message.reply_text((notes + "\n\n" if notes else "") + text, reply_markup=kb)


def _settle_notes(lang: str, res: dict) -> str:
    if not res or res.get("gained", 0) <= 0 and not res.get("events"):
        return ""
    parts = []
    if res.get("elapsed_hours", 0) >= 0.05 and res.get("gained", 0) > 0:
        parts.append(t(lang, "AFK_REPORT", hours=res["elapsed_hours"],
                       gained=res["gained"], rate=res.get("rate", 0)))
    for event in res.get("events", []):
        if event["type"] == "TREASURE":
            parts.append(t(lang, "LUCKY_TREASURE", stones=event["stones"]))
        elif event["type"] == "AMBUSH":
            parts.append(t(lang, "BANDIT_AMBUSH", stolen=event["stolen"]))
    return "\n".join(parts)


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    await update.message.reply_text("🌐 Language / زبان", reply_markup=language_kb())


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    if not ctx.is_admin(update.effective_user.id):
        await update.message.reply_text("⛔")
        return
    user, _ = ctx.get_or_create_user(update.effective_user)
    user["account"]["is_admin"] = True
    ctx.save(user)
    lang = user["account"]["language"]
    text = admin_text(lang, ctx.storage, ctx.boost_label())
    await update.message.reply_text(text, reply_markup=admin_kb(lang))

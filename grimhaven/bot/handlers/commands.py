"""Slash commands + the persistent reply-keyboard router (Part 3 §2)."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from ...engine.models import in_seclusion
from ...localization import t
from ...render import admin_text
from ..keyboards import REPLY_TO_ACTION, language_kb, reply_kb
from .callbacks import _dispatch
from .common import Ctx

# Part 1/3: BotFather command registry (also pushed via set_my_commands on boot)
BOT_COMMANDS = [
    ("start", "آغاز سفر جاودانگی / Begin the immortal journey"),
    ("me", "لوح سرنوشت / Destiny scroll (profile)"),
    ("cultivate", "مدیتیشن و برداشت چی / Manage meditation & claim Qi"),
    ("breakthrough", "آزمون شکست سد / Attempt realm breakthrough"),
    ("map", "نقشه، شکار و تسخیر / World map, hunt & conquer"),
    ("bag", "کوله و حلقه فضایی / Bag & spatial ring"),
    ("skills", "طومارها و چیدمان نبرد / Martial arts & combat deck"),
    ("shop", "پاویون تجارت / Spirit Pavilion"),
    ("sect", "فرقه / Sect management"),
    ("settings", "زبان و تنظیمات / Settings & language"),
    ("help", "راهنما / Guide"),
]


def get_ctx(context: ContextTypes.DEFAULT_TYPE) -> Ctx:
    return context.bot_data["ctx"]


async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str,
               arg: str = "", fresh_message: bool = True) -> None:
    """Send a fresh message rendering for a game action (commands & reply keys)."""
    ctx: Ctx = get_ctx(context)
    user, _created = ctx.get_or_create_user(update.effective_user)
    if user["account"].get("is_banned"):
        await update.message.reply_text(t(user["account"]["language"], "ERR_BANNED"))
        return
    settle_res = ctx.settle(user)
    lang = user["account"]["language"]
    try:
        text, kb = _dispatch(ctx, user, action, arg, settle_res)
    except Exception:  # pragma: no cover — safety net
        import traceback
        traceback.print_exc()
        text, kb = ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
    ctx.save(user)
    if fresh_message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.edit_text(text, reply_markup=kb)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    tg_user = update.effective_user
    user, created = ctx.get_or_create_user(tg_user)
    if user["account"].get("is_banned"):
        await update.message.reply_text(t(user["account"]["language"], "ERR_BANNED"))
        return
    lang = user["account"]["language"]
    if created:
        await update.message.reply_text(t("fa", "WELCOME_NEW"), reply_markup=language_kb())
        return
    ctx.settle(user)
    ctx.save(user)
    text, kb = ctx.profile_reply(user)
    await update.message.reply_text(text, reply_markup=kb)
    await update.message.reply_text(t(lang, "DOCK_HINT"), reply_markup=reply_kb(lang))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    await update.message.reply_text(t(user["account"]["language"], "HELP_TEXT"),
                                    reply_markup=reply_kb(user["account"]["language"]))


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    if user["account"].get("is_banned"):
        await update.message.reply_text(t(user["account"]["language"], "ERR_BANNED"))
        return
    settle_res = ctx.settle(user)
    ctx.save(user)
    notes = _settle_notes(user["account"]["language"], settle_res)
    text, kb = ctx.profile_reply(user)
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


async def cmd_cultivate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    action = "stop_meditate" if user["cultivation"].get("meditating") else "meditate"
    await _run(update, context, action)


async def cmd_breakthrough(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "breakthrough")


async def cmd_map(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "map")


async def cmd_bag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "bag", "tab:gear")


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "martial")


async def cmd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "shop")


async def cmd_sect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run(update, context, "sect")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: Ctx = get_ctx(context)
    user, _ = ctx.get_or_create_user(update.effective_user)
    await update.message.reply_text(t(user["account"]["language"], "SETTINGS_TITLE"),
                                    reply_markup=language_kb())


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
    from ..keyboards import admin_kb
    await update.message.reply_text(text, reply_markup=admin_kb(lang))


async def on_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route docked reply-keyboard button taps to their game screens."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    action = REPLY_TO_ACTION.get(text)
    if not action:
        return
    await _run(update, context, action)

"""Telegram application assembly — wiring the engine to @Grimheaven_bot."""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import ContextTypes
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from telegram import error as tg_error

from ..config import Settings
from ..core.data_loader import bootstrap as bootstrap_data
from ..core.middleware import guard_update
from ..db.storage import Storage, bootstrap_world
from ..localization import t
from ..errors import Category, USER_KEY, classify, incident_id
from .handlers import admin as admin_handlers
from .handlers import commands as cmd_handlers
from .handlers.callbacks import on_callback
from .handlers.common import Ctx

logger = logging.getLogger(__name__)

ADMIN_COMMANDS = (
    ("broadcast", admin_handlers.cmd_broadcast),
    ("inspect", admin_handlers.cmd_inspect),
    ("modify_qi", admin_handlers.cmd_modify_qi),
    ("set_realm", admin_handlers.cmd_set_realm),
    ("grant_item", admin_handlers.cmd_grant_item),
    ("seal_meridians", admin_handlers.cmd_seal_meridians),
    ("purge_demon", admin_handlers.cmd_purge_demon),
    ("world_boost", admin_handlers.cmd_world_boost),
)


def build_application(settings: Settings, storage: Storage) -> Application:
    bootstrap_world(storage)
    data_registry = bootstrap_data()
    logger.info("Data registry: %d events, %d arts, %d techniques",
                len(data_registry.offline_events), len(data_registry.martial_arts),
                len(data_registry.techniques_index))
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["ctx"] = Ctx(storage, settings.admin_ids, settings=settings)

    get_user = storage.get_user
    guarded = lambda fn: guard_update(get_user, fn)  # noqa: E731

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [BotCommand(name, help_text) for name, help_text in cmd_handlers.BOT_COMMANDS]
        )
        # warm the artwork cache for the marquee event before the first player
        # taps it; bounded, deduplicated, and failure-silent (see ArtworkService)
        service = application.bot_data["ctx"].artworks
        if service is not None:
            keys = [k for k in ("breakthrough", "breakthrough_fail", "milestone_realm")
                    if service.queries_for(k)]
            if keys:
                task = asyncio.create_task(service.prefetch(keys))
                application.bot_data["artwork_prefetch"] = task
                task.add_done_callback(
                    lambda t: logger.info("artwork: startup prefetch warmed %d asset(s)",
                                          t.result()) if not t.exception()
                    else logger.warning("artwork: startup prefetch failed: %s",
                                        t.exception()))

    async def on_error(update: Update | None,
                     context: ContextTypes.DEFAULT_TYPE) -> None:
        """Last line of defence: anything that escapes a handler is logged with a
        reference and told to the player — never swallowed by PTB's default
        "No error handlers are registered" logger."""
        exc = context.error
        category = classify(exc)
        ref = incident_id() if category in (Category.STORAGE, Category.TELEGRAM,
                                           Category.CONTENT, Category.INTERNAL) else ""
        logger.error("unhandled error incident=%s category=%s update_id=%s user_id=%s",
                     ref or "-", category.value,
                     getattr(update, "update_id", None),
                     getattr(getattr(update, "effective_user", None), "id", None))
        # the traceback belongs to *this* log line, so `incident=` and the frames
        # below it are one record — a player's screenshot becomes a stack trace
        logger.debug("unhandled error traceback", exc_info=exc)
        if category is Category.TELEGRAM:
            return              # delivery itself is broken; there is nothing to deliver
        user_id = getattr(getattr(update, "effective_user", None), "id", None)
        lang = "fa"
        try:
            doc = storage.get_user(user_id) if user_id is not None else None
            lang = ((doc or {}).get("account") or {}).get("language") or "fa"
        except Exception:       # the reporter must never be the second failure
            logger.debug("could not resolve the language for the error notice")
        text = t(lang, USER_KEY[category])
        if ref:
            text = f"{text}\n{t(lang, 'ERR_REFERENCE', ref=ref)}"
        try:
            if getattr(update, "callback_query", None) is not None:
                await update.callback_query.answer(text, show_alert=True)
            elif getattr(update, "message", None) is not None:
                await update.message.reply_text(text)
        except tg_error.TelegramError as send_exc:
            logger.warning("could not report incident %s to the player: %s",
                           ref, send_exc)

    async def post_shutdown(application: Application) -> None:
        """Leave no artwork task dangling against a closing loop."""
        service = application.bot_data["ctx"].artworks
        if service is not None:
            try:
                await service.cancel_tasks()
            except Exception:      # pragma: no cover — shutdown noise only
                logger.debug("artwork: background tasks refused a clean cancel")

    app.post_init = post_init
    app.post_shutdown = post_shutdown
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", guarded(cmd_handlers.cmd_start)))
    app.add_handler(CommandHandler("help", guarded(cmd_handlers.cmd_help)))
    app.add_handler(CommandHandler("me", guarded(cmd_handlers.cmd_me)))
    app.add_handler(CommandHandler("profile", guarded(cmd_handlers.cmd_me)))
    app.add_handler(CommandHandler("cultivate", guarded(cmd_handlers.cmd_cultivate)))
    app.add_handler(CommandHandler("breakthrough", guarded(cmd_handlers.cmd_breakthrough)))
    app.add_handler(CommandHandler("map", guarded(cmd_handlers.cmd_map)))
    app.add_handler(CommandHandler("bag", guarded(cmd_handlers.cmd_bag)))
    app.add_handler(CommandHandler("inv", guarded(cmd_handlers.cmd_bag)))
    app.add_handler(CommandHandler("skills", guarded(cmd_handlers.cmd_skills)))
    app.add_handler(CommandHandler("shop", guarded(cmd_handlers.cmd_shop)))
    app.add_handler(CommandHandler("sect", guarded(cmd_handlers.cmd_sect)))
    app.add_handler(CommandHandler("settings", guarded(cmd_handlers.cmd_settings)))
    app.add_handler(CommandHandler("language", guarded(cmd_handlers.cmd_language)))
    app.add_handler(CommandHandler("panel", guarded(cmd_handlers.cmd_panel)))
    app.add_handler(CommandHandler("admin", cmd_handlers.cmd_admin))  # admin bypasses the guard
    for name, handler in ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, handler))

    # persistent reply-keyboard dock
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   guarded(cmd_handlers.on_reply_button)))
    app.add_handler(CallbackQueryHandler(guarded(on_callback)))
    return app

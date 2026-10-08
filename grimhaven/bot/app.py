"""Telegram application assembly — wiring the engine to @Grimheaven_bot."""
from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from ..config import Settings
from ..db.storage import Storage, bootstrap_world
from .handlers import admin as admin_handlers
from .handlers import commands as cmd_handlers
from .handlers.callbacks import on_callback
from .handlers.common import Ctx

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
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["ctx"] = Ctx(storage, settings.admin_ids)

    app.add_handler(CommandHandler("start", cmd_handlers.cmd_start))
    app.add_handler(CommandHandler("help", cmd_handlers.cmd_help))
    app.add_handler(CommandHandler("profile", cmd_handlers.cmd_profile))
    app.add_handler(CommandHandler("language", cmd_handlers.cmd_language))
    app.add_handler(CommandHandler("admin", cmd_handlers.cmd_admin))
    for name, handler in ADMIN_COMMANDS:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CallbackQueryHandler(on_callback))
    return app

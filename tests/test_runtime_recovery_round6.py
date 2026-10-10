"""Round-6 runtime recovery tests for persistent player documents.

The live screenshots show ERR_UNKNOWN on ordinary menu actions, while /start
still responds. A malformed optional combat field in a saved player record can
raise during AFK settlement (and therefore break every routed screen). These
checks cover the migration and real Telegram-handler path, not only _dispatch.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimhaven.bot.handlers import commands
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core.data_loader import bootstrap as bootstrap_data
from grimhaven.core.middleware import guard_update
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc, utcnow


from tests.telegram_fakes import (FakeBot, FakeContext as _Context,  # faithful fakes
                                 FakeUpdate as _Update)


@pytest.fixture()
def game_ctx(tmp_path: Path):
    bootstrap_data()
    storage = Storage(tmp_path / "runtime-recovery.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids=set())
    yield ctx
    storage.close()


@pytest.mark.asyncio
async def test_malformed_legacy_injury_does_not_break_every_menu(game_ctx: Ctx):
    """A stale scalar injury field used to explode in active_debuff()."""
    user = new_user_doc(501, "debug_player", "fa")
    user["status"] = "heavily_injured"
    user["combat"]["injury"] = "legacy-injury-marker"
    user["cultivation"]["last_afk_timestamp"] = (
        utcnow() - dt.timedelta(hours=2)
    ).isoformat()
    game_ctx.storage.save_user(user)

    update = _Update(text="⚙️ تنظیمات", user_id=501)
    context = _Context(game_ctx, update.bot)
    await guard_update(game_ctx.storage.get_user, commands.on_reply_button)(update, context)

    assert update.message.replies, "the dock tap must render a screen"
    assert "تنظیمات آشیان" in update.message.replies[-1]
    assert "خطای ناشناخته" not in update.message.replies[-1][0]
    repaired = game_ctx.storage.get_user(501)
    assert repaired["combat"]["injury"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "handler", "expected"),
    [
        ("/help", commands.cmd_help, "راهنما"),
        ("/settings", commands.cmd_settings, "تنظیمات"),
    ],
)
async def test_help_and_settings_commands_use_the_live_handler_path(
    game_ctx: Ctx, command, handler, expected: str
):
    user = new_user_doc(501, "debug_player", "fa")
    user["cultivation"]["last_afk_timestamp"] = utcnow().isoformat()
    game_ctx.storage.save_user(user)

    update = _Update(text=command, user_id=501)
    context = _Context(game_ctx, update.bot)
    await guard_update(game_ctx.storage.get_user, handler)(update, context)

    assert update.message.replies, f"{command} must reply"
    assert expected in update.message.replies[-1]
    assert "خطای ناشناخته" not in update.message.replies[-1][0]

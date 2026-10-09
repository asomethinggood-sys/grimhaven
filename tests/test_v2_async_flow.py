"""Async-layer acceptance: on_callback render contract (root lifecycle,
combat de-weaponization / stale-token guard, tribulation staging)."""
from __future__ import annotations

import asyncio
import random

import pytest

from grimhaven.bot.handlers import callbacks as cb
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core import combat_engine as ce
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc
from grimhaven.engine.world import travel


class FakeUser:
    id = 101
    username = "async_soul"
    first_name = "Async"
    language_code = "en"


class Sent:
    def __init__(self, mid):
        self.message_id = mid


class FakeMessage:
    def __init__(self, bot, mid):
        self.bot = bot
        self.message_id = mid
        self.chat_id = 555

    async def reply_text(self, text, reply_markup=None):
        self.bot.sent.append(("reply", text, reply_markup))
        return Sent(self.bot.next_id())


class FakeBot:
    def __init__(self):
        self.calls = []
        self.sent = []
        self._mid = 1000

    def next_id(self):
        self._mid += 1
        return self._mid

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", message_id))

    async def edit_message_reply_markup(self, chat_id=None, message_id=None, reply_markup=None):
        self.calls.append(("strip", message_id))

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(("send", text, reply_markup))
        return Sent(self.next_id())


class FakeQuery:
    def __init__(self, bot, data, mid=77):
        self.bot = bot
        self.data = data
        self.answered = []
        self.edits = []
        self.stripped = 0
        self.message = FakeMessage(bot, mid)

    async def answer(self, text=None, show_alert=False):
        self.answered.append((text, show_alert))

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.stripped += 1


class FakeUpdate:
    def __init__(self, query):
        self.callback_query = query
        self.effective_user = FakeUser()


class FakeCtxHolder:
    def __init__(self, ctx):
        self.bot_data = {"ctx": ctx}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "a.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999})
    user = new_user_doc(FakeUser.id, FakeUser.username, "fa")
    storage.save_user(user)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: _real_sleep(0))
    yield ctx, storage
    storage.close()


def fire(ctx_holder, query):
    asyncio.run(cb.on_callback(FakeUpdate(query), ctx_holder))


def test_root_lifecycle_sends_and_stores_anchor(env):
    ctx, storage = env
    holder = FakeCtxHolder(ctx)
    bot = FakeBot()
    q = FakeQuery(bot, "profile:view:main")
    fire(holder, q)
    assert bot.sent, "root screen must be a fresh message"
    user = storage.get_user(FakeUser.id)
    anchor = user["ui"]["active_menu_message_id"]
    assert anchor  # stored message id
    # a second root deletes the old anchor first
    q2 = FakeQuery(bot, "map:view:world")
    fire(holder, q2)
    assert ("delete", anchor) in bot.calls


def test_blocked_root_alerts_without_render(env):
    ctx, storage = env
    holder = FakeCtxHolder(ctx)
    user = storage.get_user(FakeUser.id)
    travel(storage, user, "zone_ordinary_cave")
    rng = random.Random(3)
    from grimhaven.core.data_loader import data_registry
    beast = None
    for z in data_registry.zones_ordered():
        for e in data_registry.enemies_by_zone_list(z.zone_id):
            beast = ce.make_beast(e.enemy_id, rng)
            break
        if beast:
            break
    ce.start_session(user, beast, "hunt", "zone_ordinary_cave")
    storage.save_user(user)
    bot = FakeBot()
    q = FakeQuery(bot, "map:view:world")
    fire(holder, q)
    assert q.answered and q.answered[0][1] is True  # show_alert popup
    assert not bot.sent and not q.edits  # screen untouched


def test_combat_turn_deactivates_trigger_then_appends(env):
    ctx, storage = env
    holder = FakeCtxHolder(ctx)
    user = storage.get_user(FakeUser.id)
    travel(storage, user, "zone_ordinary_cave")
    rng = random.Random(4)
    from grimhaven.core.data_loader import data_registry
    beast = None
    for z in data_registry.zones_ordered():
        for e in data_registry.enemies_by_zone_list(z.zone_id):
            beast = ce.make_beast(e.enemy_id, rng)
            break
        if beast:
            break
    ce.start_session(user, beast, "hunt", "zone_ordinary_cave")
    user["combat"]["session"]["player"]["qi"] = 999
    storage.save_user(user)
    rnd = user["combat"]["session"]["round"]
    bot = FakeBot()
    q = FakeQuery(bot, f"combat:act:basic:none:{rnd}")
    fire(holder, q)
    assert q.stripped == 1  # the pressed message loses its buttons instantly
    assert bot.sent or q.edits  # new battle state delivered
    # stale round token → soft toast only, no engine mutation
    user = storage.get_user(FakeUser.id)
    cur = user["combat"]["session"]
    if cur and not cur.get("finished"):
        before = cur["round"]
        q2 = FakeQuery(bot, f"combat:act:basic:none:{before + 9}")
        fire(holder, q2)
        after = storage.get_user(FakeUser.id)["combat"]["session"]
        assert after["round"] == before
        assert q2.answered and q2.answered[0][0] and not q2.edits


def test_tribulation_stages_two_edits_then_root_card(env):
    ctx, storage = env
    holder = FakeCtxHolder(ctx)
    user = storage.get_user(FakeUser.id)
    cul = user["cultivation"]
    cul["qi_current"] = cul["qi_capacity"] = 10_000
    cul["dao_path"] = "sword"
    storage.save_user(user)
    from grimhaven.engine.cultivation import CultivationEngine
    if CultivationEngine.tribulation_prep(user)["status"] != "READY":
        pytest.skip("gate closed for this realm state")
    bot = FakeBot()
    q = FakeQuery(bot, "breakthrough:action:confirm")
    fire(holder, q)
    assert len(q.edits) == 2, f"two dramatic interim edits expected, got {q.edits}"
    assert "☁️" in q.edits[0] and "⚡" in q.edits[1]
    assert bot.sent, "resolution card must land as the new root message"

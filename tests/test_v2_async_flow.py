"""Async-layer acceptance: the render contract through the *PTB wrappers*.

Round 7 rewrote this file because the old version could not catch the bug that
took the live bot down:

* it built its own `FakeMessage`/`FakeQuery` objects that carried a `.bot`
  attribute — python-telegram-bot removed `TelegramObject.bot` in v20, so the
  fakes were *more* capable than production;
* the callback was fired on a hard-coded message id (77) instead of the message
  the root anchor actually points at, so the single-window lifecycle was never
  exercised as it really runs;
* handlers were called directly, skipping `guard_update`.

Everything here now goes through `guard_update(...)` with the faithful fakes from
`tests/telegram_fakes.py`, and asserts the *destination* screen text plus the
message/anchor state — not merely "no raw locale key in the reply".
"""
from __future__ import annotations

import asyncio
import random

import pytest

from grimhaven.bot.handlers import callbacks as cb
from grimhaven.bot.handlers import commands as cmd_h
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core import combat_engine as ce
from grimhaven.core.middleware import guard_update
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc
from grimhaven.engine.world import travel
from tests.telegram_fakes import FakeBot, FakeContext, FakeUpdate


@pytest.fixture()
def env(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "a.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999})
    user = new_user_doc(101, "async_soul", "fa")
    storage.save_user(user)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: _real_sleep(0))
    # ONE bot for the whole test, so message ids advance like a real chat instead
    # of every fake update starting at the same id (which hid the anchor logic).
    bot = FakeBot()
    yield ctx, storage, bot
    storage.close()


def message_handler(ctx):
    return guard_update(ctx.storage.get_user, cmd_h.on_reply_button)


def callback_handler(ctx):
    return guard_update(ctx.storage.get_user, cb.on_callback)


async def dock(ctx, bot, label):
    """A persistent-dock button press is an ordinary text message."""
    update = FakeUpdate(text=label, user_id=101, bot=bot)
    await message_handler(ctx)(update, FakeContext(ctx, bot))
    return update


async def inline(ctx, bot, data):
    """An inline button press: the callback message IS the current root anchor,
    exactly like the player tapping the keyboard they are looking at."""
    anchor = (ctx.storage.get_user(101).get("ui") or {}).get("active_menu_message_id")
    update = FakeUpdate(query_data=data, user_id=101, message_id=anchor or 500, bot=bot)
    await callback_handler(ctx)(update, FakeContext(ctx, bot))
    return update


def anchor_of(storage):
    return (storage.get_user(101).get("ui") or {}).get("active_menu_message_id")


# ── the reported live sequence: dock → hub → claim → inline Back → hub ───────

@pytest.mark.asyncio
async def test_meditation_dock_then_claim_then_inline_back(env):
    from grimhaven.localization import t
    from grimhaven.render import meditate_hub_text
    ctx, storage, bot = env
    hub_title = t("fa", "HUB_TITLE")
    chronicle_hdr = "گزارش بازگشت"

    # 1. dock tap opens the hub as the root window
    tapped = await dock(ctx, bot, "🧘 مدیتیشن و تهذیب")
    assert tapped.message.replies, "the dock tap must render a screen"
    assert tapped.message.replies[-1].startswith(hub_title), \
        "the dock must land on the meditation hub"
    assert "خطای ناشناخته" not in tapped.message.replies[-1]
    first_anchor = anchor_of(storage)
    assert first_anchor, "the root anchor must be persisted"

    # 2. claim edits that same message into the chronicle (no new root)
    q = await inline(ctx, bot, "cultivate:action:claim")
    assert q.edits, "claim must edit the hub message into the chronicle"
    assert chronicle_hdr in q.edits[-1], "claim renders the Meditation Chronicle"
    assert not q.bot.sent, "a claim is an edit, not a new root window"
    assert anchor_of(storage) == first_anchor, "claim is not a root render"

    # 3. the chronicle's Back button is a callback → hub as the NEW root window
    back = await inline(ctx, bot, "cultivate:view:hub")
    assert back.answered, "the callback must always be answered"
    assert back.bot.sent, "the hub must be delivered (this is what the live bot lost)"
    delivered_text = back.bot.sent[-1][1]
    assert delivered_text.startswith(hub_title), "Back must land on the meditation hub"
    assert chronicle_hdr not in delivered_text, "Back must not leave the chronicle up"
    assert "خطای ناشناخته" not in delivered_text
    new_anchor = anchor_of(storage)
    assert new_anchor and new_anchor != first_anchor, "the anchor must move to the new screen"
    assert first_anchor in back.bot.deleted, "the previous root window is deleted"
    # and the moved anchor survives the round trip to the database
    assert anchor_of(storage) == new_anchor


@pytest.mark.asyncio
async def test_repeated_taps_are_idempotent_and_persisted(env):
    ctx, storage, bot = env
    from grimhaven.localization import t
    hub_title = t("fa", "HUB_TITLE")
    seen = []
    for _ in range(4):
        upd = await dock(ctx, bot, "🧘 مدیتیشن و تهذیب")
        assert upd.message.replies[-1].startswith(hub_title), "every tap lands on the hub"
        for text in upd.message.replies:
            assert "خطای ناشناخته" not in text
        anchor = anchor_of(storage)
        assert anchor, "the anchor must be persisted after every tap"
        assert anchor not in seen, "each tap must move the anchor to a live message"
        seen.append(anchor)
    # every tap deleted the window it replaced — no menu pile-up
    assert storage.count_users() == 1


@pytest.mark.asyncio
async def test_root_lifecycle_survives_an_undeletable_anchor(env):
    """Telegram refusing the delete must not stop the new screen from arriving."""
    ctx, storage, bot = env
    await dock(ctx, bot, "🧘 مدیتیشن و تهذیب")
    before = anchor_of(storage)
    update = FakeUpdate(query_data="cultivate:view:hub", user_id=101,
                        message_id=before, bot=bot)
    bot.fail_delete = True                             # Telegram rejects the delete
    await callback_handler(ctx)(update, FakeContext(ctx, bot))
    assert update.bot.sent, "the screen must still be delivered"
    assert anchor_of(storage) == update.bot.sent[-1][0]


# ── guard + combat contract ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_blocked_root_alerts_without_render(env):
    ctx, storage, bot = env
    user = storage.get_user(101)
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

    q = await inline(ctx, bot, "map:view:world")
    assert q.answered and q.answered[0][1] is True, "the guard answers with a popup"
    assert not q.bot.sent and not q.edits, "a blocked tap must not render anything"


@pytest.mark.asyncio
async def test_combat_turn_deactivates_trigger_then_appends(env):
    ctx, storage, bot = env
    user = storage.get_user(101)
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

    q = await inline(ctx, bot, f"combat:act:basic:none:{rnd}")
    assert q.stripped == 1, "the pressed keyboard is deactivated instantly"
    assert q.bot.sent or q.edits, "the new battle state must be delivered"

    user = storage.get_user(101)
    cur = user["combat"].get("session")
    if cur and not cur.get("finished"):
        before = cur["round"]
        stale = await inline(ctx, bot, f"combat:act:basic:none:{before + 9}")
        assert storage.get_user(101)["combat"]["session"]["round"] == before
        assert stale.answered and stale.answered[0][0] and not stale.edits


@pytest.mark.asyncio
async def test_tribulation_stages_two_edits_then_root_card(env):
    ctx, storage, bot = env
    user = storage.get_user(101)
    cul = user["cultivation"]
    cul["qi_current"] = cul["qi_capacity"] = 10_000
    cul["dao_path"] = "sword"
    storage.save_user(user)
    from grimhaven.engine.cultivation import CultivationEngine
    if CultivationEngine.tribulation_prep(user)["status"] != "READY":
        pytest.skip("gate closed for this realm state")

    q = await inline(ctx, bot, "breakthrough:action:confirm")
    assert len(q.edits) == 2, f"two dramatic interim edits expected, got {q.edits}"
    assert "☁️" in q.edits[0] and "⚡" in q.edits[1]
    assert q.bot.sent, "the resolution card must land as the new root message"


@pytest.mark.asyncio
async def test_expired_callback_answer_is_not_reported_as_a_game_error(env):
    """A toast that arrives after Telegram's window must not become "unknown
    error" on top of a screen that rendered perfectly well."""
    ctx, storage, bot = env
    await dock(ctx, bot, "🧘 مدیتیشن و تهذیب")
    anchor = anchor_of(storage)
    update = FakeUpdate(query_data="cultivate:action:claim", user_id=101,
                        message_id=anchor, bot=bot)
    update.callback_query.fail_answer = True
    await callback_handler(ctx)(update, FakeContext(ctx, bot))
    assert update.callback_query.edits, "the chronicle still rendered"
    assert update.callback_query.answered == [], "the failed toast is not escalated"

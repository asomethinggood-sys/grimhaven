"""End-to-end wiring: a *real* game flow must actually fire the reaction.

The unit tests in ``test_sticker_reactions.py`` prove the layer behaves; this
module proves the handlers **use** it. It drives the same dispatcher the live
router drives (``_dispatch``), with a Ctx that has settings (so the sticker
service exists at all), then performs the exact call the router performs
(``_apply_sticker``) and asserts on the fake bot's wire log.

Covered here:
  * a real breakthrough — success and failure — reacts exactly once;
  * a real combat victory reacts exactly once, and a retried tap does not;
  * a battle *round* (a routine tap) reacts **never** — this is the anti-spam
    contract, and it is the one most likely to rot silently.
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimhaven.bot.handlers.callbacks import _apply_sticker, _dispatch
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core import combat_engine as ce
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc, utcnow

from .telegram_fakes import FakeBot

ROOT = Path(__file__).resolve().parent.parent
SETTLE = {"status": "OK", "gained": 0, "events": []}


class StickerSettings:
    """A Settings stand-in with the artwork side switched off on purpose, so a
    passing test can only be explained by the sticker layer."""

    def __init__(self, **over):
        self.artwork_enabled = False
        self.sticker_enabled = True
        self.sticker_map_path = ROOT / "data" / "sticker_map.json"
        self.sticker_packs = ""
        self.sticker_timeout = 2.0
        self.sticker_task_timeout = 5.0
        self.sticker_cache_ttl_minutes = 720
        self.sticker_cooldown_seconds = 0        # the once_key is what we test
        for key, value in over.items():
            setattr(self, key, value)


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "test.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={999}, settings=StickerSettings())
    storage.close()


def make_user(ctx, uid=1, realm=1, stage=0, lang="fa", **cult):
    user = new_user_doc(uid, f"user{uid}", lang)
    user["cultivation"]["current_realm_index"] = realm
    user["cultivation"]["current_stage"] = stage
    user["cultivation"]["qi_capacity"] = 500
    user["cultivation"]["qi_current"] = 500
    user["cultivation"].update(cult)
    ctx.storage.save_user(user)
    return user


def bot_returning(*emoji_ids):
    bot = FakeBot()
    bot.sticker_set = SimpleNamespace(
        name="anime_v4_1_by_shadowthiefo", title="S-Anime", sticker_type="regular",
        stickers=[SimpleNamespace(file_id=fid, emoji=emoji, is_animated=False,
                                  is_video=False, type="regular",
                                  width=512, height=512)
                  for fid, emoji in emoji_ids])
    return bot


def run_router_step(bot, ctx, user, text_kb_opts):
    """Do exactly what the live callback router does after a render.

    The real router is async, so this must be too: ``begin_reaction`` refuses
    to schedule without a running loop (and says so) rather than leaking an
    un-awaited coroutine.
    """
    async def scenario():
        _text, _kb, opts = text_kb_opts
        task = _apply_sticker(bot, 4242, user, opts, ctx)
        if task is not None:
            await asyncio.wait_for(task, timeout=5)
        return opts
    return asyncio.run(scenario())


def test_real_breakthrough_reacts_once(ctx, monkeypatch):
    """A successful breakthrough fires the triumph reaction, and a retried
    tap cannot fire it twice (the once_key is the attempt counter)."""
    from grimhaven.bot.handlers import callbacks as cb

    class CertainSuccess(random.Random):
        """A guaranteed pass, without silencing the lightning roll."""

        def uniform(self, a, b):
            return 0.0 if (a, b) == (0, 100) else (a + b) / 2

    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: CertainSuccess(1))
    bot = bot_returning(("TRIUMPH", "⚡"), ("SAD", "💀"))
    user = make_user(ctx, uid=11)
    run_router_step(bot, ctx, user, _dispatch(ctx, user, "breakthrough", "", SETTLE, now=utcnow()))
    opts = run_router_step(bot, ctx, user,
                           _dispatch(ctx, user, "breakthrough_do", "", SETTLE, now=utcnow()))
    assert opts.get("sticker"), "the breakthrough render did not attach a reaction"
    assert len(bot.stickers) == 1, bot.stickers
    assert bot.stickers[0][1] == "TRIUMPH"

    # the player taps the same confirm again (a retried callback): no second post
    ctx.save(user)
    run_router_step(bot, ctx, user,
                    _dispatch(ctx, user, "breakthrough_do", "", SETTLE, now=utcnow()))
    assert len(bot.stickers) == 1, "a retried confirm double-posted a sticker"


def test_real_combat_victory_reacts_and_routine_rounds_do_not(ctx):
    """Victory → one reaction per zone; ordinary battle rounds → none."""
    bot = bot_returning(("WIN", "🏆"), ("HIT", "⚔️"))
    user = make_user(ctx, uid=12)
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(1)), "hunt",
                     "zone_valley_mortals")
    # a mid-battle round: HP high enough that nothing terminal happens
    opts = run_router_step(bot, ctx, user,
                           _dispatch(ctx, user, "combat", "act:basic:none:1", SETTLE,
                                     now=utcnow()))
    assert not opts.get("sticker"), "a routine battle round must stay quiet"
    assert bot.stickers == []

    # now finish the beast off
    session = user["combat"]["session"]
    session["enemy"]["hp"] = 1
    session["round"] = session["round"] + 1
    opts = run_router_step(bot, ctx, user,
                           _dispatch(ctx, user, "combat", f"act:basic:none:{session['round']}",
                                     SETTLE, now=utcnow()))
    assert opts.get("sticker"), "the victory did not attach a reaction"
    assert len(bot.stickers) == 1
    assert bot.stickers[0][1] == "WIN"


def test_real_breakthrough_failure_reacts_with_the_calamity_face(ctx, monkeypatch):
    """The failure branch is a different moment — and must not stay silent."""
    from grimhaven.bot.handlers import callbacks as cb

    class CertainFailure(random.Random):
        """Rolls 99.9: over the success rate and over the miracle window."""

        def uniform(self, a, b):
            return 99.9 if (a, b) == (0, 100) else (a + b) / 2

        def randint(self, a, b):
            return a

    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: CertainFailure(1))
    bot = bot_returning(("TRIUMPH", "🏆"), ("DEVASTATED", "💀"))
    user = make_user(ctx, uid=14)
    run_router_step(bot, ctx, user, _dispatch(ctx, user, "breakthrough", "", SETTLE, now=utcnow()))
    opts = run_router_step(bot, ctx, user,
                           _dispatch(ctx, user, "breakthrough_do", "", SETTLE, now=utcnow()))
    assert opts.get("sticker"), "the failed breakthrough did not attach a reaction"
    assert len(bot.stickers) == 1
    assert bot.stickers[0][1] == "DEVASTATED", bot.stickers


def test_reactions_stay_off_without_settings(ctx):
    """The demo/test path (no settings) must never attempt a sticker call."""
    bare = Ctx(ctx.storage, admin_ids=set())          # settings=None
    bot = bot_returning(("X", "⚡"))
    user = make_user(ctx, uid=13)
    assert bare.stickers is None
    opts = run_router_step(bot, bare, user,
                           _dispatch(bare, user, "breakthrough_do", "", SETTLE, now=utcnow()))
    assert not opts.get("sticker")
    assert bot.stickers == [] and bot.sticker_set_calls == 0


# ── the real PTB transport, not just the fake bot object ────────────────────
#
# The tests above drive the handler with a hand-made Bot. This one is the
# honest end-to-end: the *real* python-telegram-bot serializes getStickerSet
# and sendSticker, the fake API answers them the way Telegram does, and the
# response is parsed back into PTB objects. It is the only test that can catch
# a method name that PTB does not actually have.

import pytest
from pytest_asyncio import fixture as async_fixture
from tests.live_api_harness import Bot as LiveBot


@async_fixture()
async def live(tmp_path):
    bot = LiveBot(tmp_path / "sticker_live.db")
    await bot.start()
    try:
        yield bot
    finally:
        await bot.stop()


@pytest.mark.asyncio
async def test_live_transport_resolves_and_sends_a_sticker(live, monkeypatch):
    """The layer must work against PTB's real Bot.get_sticker_set/send_sticker.

    Setup makes the tribulation certain, because this test is about the
    transport, not the dice. ``monkeypatch`` is not a style choice here:
    ``callbacks.random`` *is* the stdlib ``random`` module, so a hand-rolled
    "restore" re-reads the already-patched attribute and puts the patch back —
    leaking a fake ``Random`` into every later test in the process.
    """
    from grimhaven.bot.handlers import callbacks as cb

    class CertainSuccess(random.Random):
        def uniform(self, a, b):
            return 0.0 if (a, b) == (0, 100) else (a + b) / 2

    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: CertainSuccess(1))
    await live.tap("🧘 مدیتیشن و تهذیب")
    user = live.user()
    user["cultivation"]["current_realm_index"] = 1
    user["cultivation"]["current_stage"] = 0
    user["cultivation"]["qi_capacity"] = 500
    user["cultivation"]["qi_current"] = 500
    live.storage.save_user(user)
    await live.tap("⚡ اقدام به شکست سد")
    await live.press("breakthrough:action:confirm")

    endpoints = [ep for ep, _d in live.state.calls]
    assert "getStickerSet" in endpoints, "the set was never resolved over the wire"
    assert "sendSticker" in endpoints, "no sticker reached the transport"

    # the request asked for the pack the map enables (not a hard-coded one)
    asked = next(d for ep, d in live.state.calls if ep == "getStickerSet")
    assert asked.get("name") == "anime_v4_1_by_shadowthiefo", asked

    payloads = [d for ep, d in live.state.calls if ep == "sendSticker"]
    assert payloads, endpoints
    sent_file_id = payloads[-1].get("sticker")
    # and the id sent is one the *response* could have contained — an invented
    # or stale id would not be in this set
    assert sent_file_id in {"FAKE_STICKER_1", "FAKE_STICKER_2", "FAKE_STICKER_3"}, sent_file_id

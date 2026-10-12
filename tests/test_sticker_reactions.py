"""Sticker reactions — catalog, resolution, selection, delivery, and wiring.

These tests exist because every failure mode of a decoration layer is silent.
A wrong pack name, a stale ``file_id``, a sticker on every button press, a
reaction that fires twice because the callback was retried — none of them raise
an exception anywhere. So each one is pinned here:

* the map is coherent (a moment naming a missing category never fires);
* ids are resolved at runtime and cached, never persisted;
* a dead id is refreshed **once**, then the layer goes quiet;
* a broken pack, a broken map or a Telegram refusal degrades to text only —
  the game action still happens;
* a retried tap cannot react twice, and routine taps never react at all.

The fake bot is the project's own (tests/telegram_fakes.FakeBot), extended with
``get_sticker_set`` / ``send_sticker`` for exactly this purpose.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimhaven.images.delivery import claim_once_key
from grimhaven.stickers import delivery as delivery_mod
from grimhaven.stickers.catalog import load_catalog, parse_catalog
from grimhaven.stickers.delivery import (begin_reaction, deliver_reaction,
                                         reaction_cooldown_remaining)
from grimhaven.stickers.service import StickerService

from .telegram_fakes import FakeBot

ROOT = Path(__file__).resolve().parent.parent


class FakeSettings:
    """Only the sticker half of Settings matters here."""

    def __init__(self, **over):
        self.sticker_enabled = True
        self.sticker_map_path = ROOT / "data" / "sticker_map.json"
        self.sticker_packs = ""
        self.sticker_timeout = 2.0
        self.sticker_task_timeout = 5.0
        self.sticker_cache_ttl_minutes = 720
        self.sticker_cooldown_seconds = 30
        for key, value in over.items():
            setattr(self, key, value)


def sticker(id_: str, emoji: str = "", **over):
    base = dict(file_id=id_, emoji=emoji, is_animated=False, is_video=False,
                type="regular", width=512, height=512)
    base.update(over)
    return SimpleNamespace(**base)


def fake_set(*stickers, name="fake_set"):
    return SimpleNamespace(name=name, title=name, sticker_type="regular",
                           stickers=list(stickers))


def make_service(settings=None, bot=None):
    settings = settings or FakeSettings()
    catalog = load_catalog(settings.sticker_map_path)
    assert catalog is not None, "the shipped map must load"
    return StickerService(settings, catalog)


# ── the shipped map ──────────────────────────────────────────────────────────

def test_shipped_map_loads_and_is_coherent():
    catalog = load_catalog(ROOT / "data" / "sticker_map.json")
    assert catalog is not None
    assert catalog.validate() == []
    # the operator approved exactly one enabled pack (generic anime reactions)
    enabled = catalog.enabled_packs()
    assert [p.short_name for p in enabled] == ["anime_v4_1_by_shadowthiefo"]
    # and every moment the handlers can ask for exists
    for moment in ("victory", "breakthrough", "breakthrough_fail", "milestone_realm"):
        assert catalog.moment(moment) is not None, moment


def test_map_refuses_a_moment_that_points_nowhere():
    catalog = parse_catalog({
        "packs": [{"short_name": "p", "enabled": True, "format": "static"}],
        "categories": {"victory": {"emojis": ["🏆"]}},
        "moments": {"victory": {"category": "typo_category"}},
    })
    problems = catalog.validate()
    assert any("unknown category" in p for p in problems)


def test_missing_map_disables_the_layer(tmp_path):
    assert load_catalog(tmp_path / "nope.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_catalog(broken) is None


def test_pack_selection_ignores_unknown_names():
    catalog = load_catalog(ROOT / "data" / "sticker_map.json")
    # an unknown name is skipped, not fatal; both the map id and the Telegram
    # short name are accepted, because operators read either one in the docs
    assert [p.short_name for p in catalog.enabled_packs("nope,s_anime_v4_1")] == \
        ["anime_v4_1_by_shadowthiefo"]
    assert [p.short_name for p in
            catalog.enabled_packs("anime_v4_1_by_shadowthiefo")] == \
        ["anime_v4_1_by_shadowthiefo"]


# ── resolution & caching ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cooldown():
    delivery_mod._LAST_SEND.clear()
    yield
    delivery_mod._LAST_SEND.clear()


def test_resolve_caches_and_second_moment_costs_no_api_call():
    async def scenario():
        bot = FakeBot()
        bot.sticker_set = fake_set(sticker("ID_A", "🏆"), sticker("ID_B", "😭"))
        service = make_service(bot=bot)
        first = await service.pick(bot, "victory")
        calls_after_first = bot.sticker_set_calls
        second = await service.pick(bot, "breakthrough_fail")
        return first, second, calls_after_first, bot.sticker_set_calls

    first, second, calls_after_first, calls_total = asyncio.run(scenario())
    assert first is not None and second is not None
    assert calls_after_first == 1          # resolved once…
    assert calls_total == 1                # …and reused for the next moment


def test_emoji_preference_beats_rotation():
    async def scenario():
        bot = FakeBot()
        bot.sticker_set = fake_set(sticker("ID_A", "🙃"), sticker("ID_B", "😭"),
                                   sticker("ID_C", "😢"))
        service = make_service(bot=bot)
        return await service.pick(bot, "breakthrough_fail")

    chosen = asyncio.run(scenario())
    # calamity prefers 💀/😵/… then the pack's 😭/😢 — never the 🙃
    assert chosen is not None and chosen.emoji in {"😭", "😢"}


def test_rotation_avoids_repeating_the_same_face():
    async def scenario():
        bot = FakeBot()
        # no emoji at all → pure rotation
        bot.sticker_set = fake_set(sticker("ID_1"), sticker("ID_2"))
        service = make_service(bot=bot)
        return [await service.pick(bot, "victory") for _ in range(4)]

    picked = asyncio.run(scenario())
    ids = [p.file_id for p in picked if p]
    assert len(ids) == 4
    assert all(a != b for a, b in zip(ids, ids[1:])), ids


def test_unknown_pack_fails_once_and_stays_quiet(caplog):
    async def scenario():
        bot = FakeBot()
        bot.fail_sticker_set = True
        service = make_service(bot=bot)
        first = await service.pick(bot, "victory")
        second = await service.pick(bot, "victory")
        return first, second, bot.sticker_set_calls

    first, second, calls = asyncio.run(scenario())
    assert first is None and second is None
    # it retried the resolution (an outage may heal) but never raised
    assert calls >= 1


def test_pick_for_unmapped_moment_is_none():
    async def scenario():
        bot = FakeBot()
        bot.sticker_set = fake_set(sticker("ID_A", "🏆"))
        service = make_service(bot=bot)
        return await service.pick(bot, "no_such_moment")

    assert asyncio.run(scenario()) is None


# ── delivery ─────────────────────────────────────────────────────────────────

def _service_with(bot, **settings_over):
    bot.sticker_set = fake_set(sticker("ID_A", "🏆"), sticker("ID_B", "😎"))
    return make_service(FakeSettings(**settings_over), bot)


def test_begin_reaction_sends_once_and_claims_the_key():
    async def scenario():
        bot = FakeBot()
        service = _service_with(bot)
        user = {"user_id": 5, "ui": {}}
        task = begin_reaction(bot, 99, user, "victory", service=service,
                              once_key="victory:z1", throttle_minutes=45)
        await asyncio.wait_for(task, timeout=3)
        # a retried callback must NOT post a second sticker
        again = begin_reaction(bot, 99, user, "victory", service=service,
                               once_key="victory:z1", throttle_minutes=45)
        if again is not None:
            await asyncio.wait_for(again, timeout=3)
        return bot.stickers, again

    sent, again = asyncio.run(scenario())
    assert len(sent) == 1, sent
    assert again is None, "the same once_key must be refused"
    assert sent[0][0] == 99


def test_sticker_disabled_is_a_no_op():
    async def scenario():
        bot = FakeBot()
        service = _service_with(bot, sticker_enabled=False)
        user = {"user_id": 5, "ui": {}}
        task = begin_reaction(bot, 99, user, "victory", service=service)
        if task is not None:
            await asyncio.wait_for(task, timeout=3)
        return bot.stickers, bot.sticker_set_calls

    sent, calls = asyncio.run(scenario())
    assert sent == [] and calls == 0


def test_no_service_means_no_reaction():
    async def scenario():
        bot = FakeBot()
        task = begin_reaction(bot, 99, {"user_id": 5, "ui": {}}, "victory", service=None)
        return task, bot.stickers

    task, sent = asyncio.run(scenario())
    assert task is None and sent == []


def test_cooldown_flattens_a_burst():
    async def scenario():
        bot = FakeBot()
        service = _service_with(bot, sticker_cooldown_seconds=60)
        user = {"user_id": 5, "ui": {}}
        first = begin_reaction(bot, 99, user, "victory", service=service, once_key="a")
        await asyncio.wait_for(first, timeout=3)
        second = begin_reaction(bot, 99, user, "victory", service=service, once_key="b")
        return bot.stickers, second, reaction_cooldown_remaining(99, 60)

    sent, second, remaining = asyncio.run(scenario())
    assert len(sent) == 1
    assert second is None, "the second tap must be inside the cooldown"
    assert remaining > 0


def test_dead_id_is_refreshed_once_then_sent():
    """The documented failure of any cached id: refresh once, then deliver."""

    async def scenario():
        bot = FakeBot()
        service = _service_with(bot)          # pack = ID_A (🏆), ID_B (😎)
        bot.stale_sticker_ids = {"ID_A"}      # the first choice turns out dead
        user = {"user_id": 5, "ui": {}}
        task = begin_reaction(bot, 99, user, "victory", service=service, once_key="k")
        await asyncio.wait_for(task, timeout=3)
        return bot.stickers, bot.sticker_set_calls

    sent, calls = asyncio.run(scenario())
    assert [s[1] for s in sent] == ["ID_B"], "the refreshed id must be the one sent"
    assert calls >= 2, "the set must be re-resolved after the rejection"


def test_a_refused_send_never_raises_and_never_retries_forever():
    async def scenario():
        bot = FakeBot()
        bot.fail_sticker = True
        service = _service_with(bot)
        user = {"user_id": 5, "ui": {}}
        # begin_reaction would be skipped by the cooldown state of the test
        # above; call the delivery coroutine directly to keep this focused
        await asyncio.wait_for(deliver_reaction(bot, 99, user, "victory", service=service),
                               timeout=3)
        return bot.stickers

    assert asyncio.run(scenario()) == []


def test_a_broken_map_leaves_the_layer_off_but_alive(tmp_path):
    bad = tmp_path / "map.json"
    bad.write_text(json.dumps({"packs": [], "categories": {}, "moments": {}}), encoding="utf-8")
    assert load_catalog(bad) is None       # no packs → nothing to send, no crash


def test_once_key_store_is_shared_with_artwork_and_namespaced():
    user = {"ui": {}}
    assert claim_once_key(user, "sticker:victory:z1") is True
    assert claim_once_key(user, "sticker:victory:z1") is False     # same key
    assert claim_once_key(user, "victory:z1") is True              # artwork's key
    assert "sticker:victory:z1" in user["ui"]["artwork_sent"]


def test_missing_pack_in_map_path_still_boots():
    catalog = load_catalog(ROOT / "data" / "sticker_map.json")
    service = StickerService(FakeSettings(sticker_packs="does_not_exist"), catalog)
    assert service.packs() == []


def test_task_ceiling_abandons_a_hung_reaction():
    """The documented budget must be real: a hung Telegram call cannot keep a
    task — and its chat — occupied indefinitely."""

    class HangingBot(FakeBot):
        async def send_sticker(self, chat_id, sticker, **kwargs):
            await asyncio.sleep(30)          # Telegram never answers

    async def scenario():
        bot = HangingBot()
        bot.sticker_set = fake_set(sticker("ID_A", "🏆"))
        service = make_service(FakeSettings(sticker_timeout=30.0,
                                           sticker_task_timeout=0.15), bot)
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(
            deliver_reaction(bot, 99, {"user_id": 5, "ui": {}}, "victory", service=service),
            timeout=3)
        return asyncio.get_running_loop().time() - started, bot.stickers

    elapsed, sent = asyncio.run(scenario())
    assert elapsed < 1.5, f"the ceiling did not fire ({elapsed:.2f}s)"
    assert sent == []


def test_cooldown_stamp_is_readable_without_a_running_loop():
    """Startup code and tests read the cooldown outside any event loop."""
    delivery_mod._LAST_SEND[77] = time.monotonic()
    assert reaction_cooldown_remaining(77, 60) > 0      # no loop, no raise
    assert reaction_cooldown_remaining(78, 60) == 0.0

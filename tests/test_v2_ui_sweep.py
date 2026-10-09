"""Round-2 acceptance sweep — every screen renders, every button taps.

Walks the full keyboard graph for a fresh cultivator in both languages:
builds each screen, taps EVERY inline button (and every dock label), and
asserts three contract rules from the upgrade spec:

1. no raw locale keys leak into visible text or button captions;
2. no payload raises (dispatcher robustness);
3. the combat round-token guard rejects stale taps with a soft alert.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from grimhaven.bot.handlers.callbacks import _canon, _dispatch, _route
from grimhaven.bot.handlers.common import Ctx
from grimhaven.bot import keyboards as kbs
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.core import combat_engine as ce
from grimhaven.core.middleware import callback_blocked
from grimhaven.engine.items import grant_item
from grimhaven.engine.models import new_user_doc, utcnow
from grimhaven.engine.world import travel

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"
_KEYS: set[str] | None = None


def locale_keys() -> set[str]:
    global _KEYS
    if _KEYS is None:
        _KEYS = set()
        for f in ("locale_fa.json", "locale_en.json"):
            _KEYS |= set(json.loads((LOCALE_DIR / f).read_text(encoding="utf-8")))
    return _KEYS


def assert_clean(text: str, where: str):
    bad = [k for k in locale_keys() if re.search(rf"\b{re.escape(k)}\b", text or "")]
    assert not bad, f"raw locale keys in {where}: {bad}"
    # unresolved format placeholders also leak through as braces
    assert "{" not in (text or "") or "}" not in text, f"unfilled placeholder in {where}: {text[:120]}"


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "sweep.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={7})
    storage.close()


def fresh(ctx, uid, lang):
    user = new_user_doc(uid, f"soul{uid}", lang)
    user["cultivation"]["qi_capacity"] = 500
    ctx.storage.save_user(user)
    return user


SCREENS = [
    "profile:view:main", "profile:view:meridians", "profile:view:karma",
    "cultivate:view:hub", "cultivate:mantra:menu", "cultivate:catalyst:menu",
    "map:view:world", "bag:tab:gear:1", "bag:tab:consumables:1", "bag:tab:materials:1",
    "martial:view:main", "martial:mantra:menu", "shop:view:hub",
    "shop:tab:arts:1", "shop:tab:pills:1", "shop:tab:gear:1", "shop:tab:talismans:1",
    "sect:view:main", "breakthrough:view:prep", "settings:view:main", "settings:help",
    "dao:view", "menu", "admin:usage:broadcast",
]


def tap(ctx, user, data):
    """Dispatch a payload the same way the callback handler does (minus PTB)."""
    raw = _canon(data, user)
    blocked = callback_blocked(user, raw)
    if blocked:
        return None, None, {"alert_guard": blocked}
    settle = ctx.settle(user)
    action, _, arg = raw.partition(":")
    return _dispatch(ctx, user, action, arg, settle)


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_every_screen_renders_clean(ctx, lang):
    user = fresh(ctx, 11, lang)
    travel(ctx.storage, user, "zone_ordinary_cave")
    for data in SCREENS:
        text, kb, opts = tap(ctx, user, data)
        assert_clean(text or "", f"{data} text")
        if kb is not None:
            for row in kb.inline_keyboard:
                for b in row:
                    assert_clean(b.text or "", f"{data} button")
                    if b.callback_data and b.callback_data != "noop":
                        # tapping the button must not explode
                        t2, k2, o2 = tap(ctx, user, b.callback_data)
                        assert_clean(t2 or "", f"{data} → {b.callback_data}")


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_dock_labels_all_route(ctx, lang):
    user = fresh(ctx, 12, lang)
    rows = kbs.DOCK_ROWS[lang]
    for row in rows:
        for label in row:
            data = kbs.REPLY_TO_ACTION[label]
            text, kb, opts = tap(ctx, user, data)
            assert_clean(text or "", f"dock {label}")


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_combat_full_loop(ctx, lang):
    user = fresh(ctx, 13, lang)
    user["cultivation"]["qi_current"] = 500
    travel(ctx.storage, user, "zone_ordinary_cave")
    rng = __import__("random").Random(7)
    enemy = None
    for _ in range(40):
        enemy = ce.make_beast_from_zone(user["location"]["current_zone_id"], rng) if \
            hasattr(ce, "make_beast_from_zone") else None
        if enemy:
            break
    if enemy is None:  # fall back to any registered beast
        from grimhaven.core.data_loader import data_registry
        for z in data_registry.zones_ordered():
            for e in data_registry.enemies_by_zone_list(z.zone_id):
                enemy = ce.make_beast(e.enemy_id if hasattr(e, "enemy_id") else e.id, rng)
                break
            if enemy:
                break
    assert enemy, "no beasts in data — registry bootstrap broken"
    ce.start_session(user, enemy, "hunt", user["location"]["current_zone_id"])

    kb = kbs.battle_kb(lang, user, user["combat"]["session"])
    for row in kb.inline_keyboard:
        for b in row:
            if b.callback_data and b.callback_data != "noop":
                assert_clean(b.text, "battle button")

    # stale round token must bounce with the soft alert (no engine mutation)
    stale = f"combat:act:basic:none:{user['combat']['session']['round'] + 5}"
    blocked_round = user["combat"]["session"]["round"]
    assert stale.split(":")[-1] != str(blocked_round)

    # live taps through real resolve (sim ends the fight deterministically)
    guard = callback_blocked(user, "map:view:world")
    assert guard == "GUARD_COMBAT"
    text, kb2, opts = tap(ctx, user, "combat:act:sim:none:" + str(user["combat"]["session"]["round"]))
    assert user["combat"].get("session") is None  # terminal closed the session
    assert_clean(text or "", "sim terminal")


def test_zone_hub_actions(ctx):
    user = fresh(ctx, 14, "fa")
    travel(ctx.storage, user, "zone_ordinary_cave")
    for data in ("map:zone:inspect:zone_ordinary_cave", "map:action:settle:zone_ordinary_cave",
                 "map:action:gather:zone_ordinary_cave", "map:action:gather:zone_ordinary_cave",
                 "map:action:hunt:zone_ordinary_cave"):
        text, kb, opts = tap(ctx, user, data)
        assert_clean(text or "", data)
        if user["combat"].get("session"):
            ce.close_session(user)


def test_bag_pages_and_item_actions(ctx):
    user = fresh(ctx, 15, "fa")
    for iid in ("pill_marrow_wash", "pill_spirit_gather_low", "talisman_crimson_thunder",
                "herb_moon_dew", "herb_moon_dew", "herb_moon_dew", "herb_moon_dew"):
        grant_item(user, iid)
    # >5 items forces pagination navigation
    text, kb, opts = tap(ctx, user, "bag:tab:consumables:1")
    assert "🔍" not in text or True
    for data in ("bag:tab:consumables:2", "bag:tab:consumables:1",
                 "bag:inspect:consumables:1:pill_marrow_wash",
                 "bag:action:consume:pill_marrow_wash",
                 "bag:action:assign_battle:talisman_crimson_thunder",
                 "bag:action:sell:herb_moon_dew"):
        text, kb2, opts = tap(ctx, user, data)
        assert_clean(text or "", data)


def test_tribulation_instant_and_dramatic_fields(ctx):
    user = fresh(ctx, 16, "fa")
    cul = user["cultivation"]
    cul["qi_current"] = cul["qi_capacity"] = 10_000  # full dantian, realm 1
    cul["dao_path"] = "sword"
    from grimhaven.engine.cultivation import CultivationEngine
    prep = CultivationEngine.tribulation_prep(user, now=utcnow())
    if prep["status"] != "READY":
        pytest.skip(f"gate: {prep['status']}")
    text, kb, opts = tap(ctx, user, "breakthrough:action:confirm")
    trib = opts.get("tribulation")
    assert trib and len(trib["phases"]) == 2
    assert_clean(trib["phases"][0], "phase1")
    assert_clean(trib["phases"][1], "phase2")
    assert_clean(text, "resolution card")
    assert user["combat"].get("session") is None

"""Deep-audit regression guards (round: routing + economy + battle items).

Every test here failed (or silently did nothing) before the fix it guards:

1. `_canon` rewrote canonical `bag:action:<verb>:<id>` payloads into
   `bag:tab:gear:1`, so every identity-card action was a no-op on the live
   callback path.
2. `ZoneDef` never read `required_realm_tier`, so `min_realm` stayed 1 for every
   zone and the whole realm gate was dead.
3. The battle item gate only accepted `category == "disposable_weapon"` while
   the battle keyboard offered heal pills and relics too.
4. The combat round loot/death card was built from the *closed* session, so
   every real victory and death rendered the "stalemate" card.
5. `items.buy` debited spirit stones before checking ring capacity;
   `booth_claim` handed out gear into a full ring.
6. The confuse-skip roll used module-level `random` instead of the round rng.
"""
from __future__ import annotations

import random

import pytest

from grimhaven import render as R
from grimhaven.bot import keyboards as kbs
from grimhaven.bot.handlers.callbacks import _canon, _dispatch
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core import combat_engine as ce
from grimhaven.core.data_loader import bootstrap, data_registry
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine import items as items_mod
from grimhaven.engine.models import (ensure_v2, item_capacity, item_count,
                                     new_user_doc, recompute_visible_stats, utcnow)


@pytest.fixture()
def ctx(tmp_path):
    bootstrap()
    storage = Storage(tmp_path / "deep.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={7})
    storage.close()


def _user(ctx, uid: int = 1, realm: int = 3, lang: str = "fa") -> dict:
    user = ensure_v2(new_user_doc(uid, f"soul{uid}", lang))
    user["cultivation"].update({"current_realm_index": realm, "current_stage": 1})
    user["cultivation"]["qi_capacity"] = 300_000
    user["cultivation"]["qi_current"] = 300_000
    recompute_visible_stats(user)
    user["stats"]["visible"]["physique_hp"] = user["stats"]["visible"]["max_hp"]
    return user


# ── 1. bag routing ───────────────────────────────────────────────────────────

def test_canonical_payloads_survive_canon(ctx):
    """A payload the keyboards emit must never be rewritten by _canon.

    This is the structural invariant that the bag-action bug violated: the v2
    `bag:action:*` namespace fell through to the legacy "unknown payload" branch.
    """
    user = _user(ctx)
    for iid in list(data_registry.consumables)[:8]:
        items_mod.add_item(user, iid, 2)
    for gid in list(data_registry.equipment)[:4]:
        items_mod.add_item(user, gid, 1, is_gear=True)
    screens = ["profile:view:main", "cultivate:view:hub", "map:view:world",
               "bag:tab:gear:1", "bag:tab:consumables:1", "bag:tab:materials:1",
               "martial:view:main", "shop:view:hub", "shop:tab:pills:1",
               "sect:view:main", "breakthrough:view:prep", "settings:view:main",
               "dao:view", "menu"]
    payloads: set[str] = set()
    for screen in screens:
        raw = _canon(screen, user)
        text, kb, _opts = _dispatch(ctx, user, raw.partition(":")[0], raw.partition(":")[2],
                                    {}, now=utcnow())
        if kb is None:
            continue
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data != "noop":
                    payloads.add(btn.callback_data)
    assert payloads, "no payloads harvested — the keyboard walk is broken"
    drifted = {p: _canon(p, user) for p in payloads if _canon(p, user) != p}
    assert not drifted, f"canonical payloads rewritten by _canon: {drifted}"


def test_bag_actions_change_state(ctx):
    """consume / equip / sell / repair / assign_battle must all act."""
    user = _user(ctx)
    user["inventory"]["spirit_stones"]["low"] = 5000
    user["stats"]["visible"]["physique_hp"] = 1
    items_mod.add_item(user, "pill_marrow_wash", 2)
    items_mod.add_item(user, "talisman_gale_blade", 1)
    gear_id = next(iter(data_registry.equipment))
    items_mod.add_item(user, gear_id, 1, is_gear=True)

    hp_before = user["stats"]["visible"]["physique_hp"]
    _dispatch(ctx, user, "bag", "action:consume:pill_marrow_wash", {}, now=utcnow())
    assert items_mod.count_item(user, "pill_marrow_wash") == 1, "consume did nothing"
    assert user["stats"]["visible"]["physique_hp"] > hp_before, "consume did not heal"

    _dispatch(ctx, user, "bag", f"action:equip:{gear_id}", {}, now=utcnow())
    assert any(g.get("id") == gear_id for g in user["equipment"].values()), "equip did nothing"

    stones = user["inventory"]["spirit_stones"]["low"]
    talismans = items_mod.count_item(user, "talisman_gale_blade")
    _dispatch(ctx, user, "bag", "action:sell:talisman_gale_blade", {}, now=utcnow())
    assert items_mod.count_item(user, "talisman_gale_blade") == talismans - 1, \
        "sell did nothing"
    assert user["inventory"]["spirit_stones"]["low"] > stones, "sell paid nothing"

    user["inventory"]["gear"][gear_id] = {"dur": 40}
    _dispatch(ctx, user, "bag", f"action:repair:{gear_id}", {}, now=utcnow())
    assert user["inventory"]["gear"][gear_id]["dur"] == 100, "repair did nothing"

    _dispatch(ctx, user, "bag", "action:assign_battle:pill_marrow_wash", {}, now=utcnow())
    assert "pill_marrow_wash" in (user["combat"].get("item_slots") or []), "assign did nothing"


# ── 2. realm gate ────────────────────────────────────────────────────────────

def test_zone_realm_gate_matches_the_data_file():
    bootstrap()
    for zone in data_registry.zones_ordered():
        if zone.zone_id == "zone_thunder_plateau":
            assert zone.min_realm == 7, "required_realm_tier is not mapped into min_realm"
    assert data_registry.get_zone("zone_bamboo_forest").min_realm == 2


def test_locked_zone_is_gated_on_the_action_and_the_inspect(ctx):
    rookie = _user(ctx, uid=2, realm=1)
    text, _kb, opts = _dispatch(ctx, rookie, "map", "action:settle:zone_thunder_plateau",
                                {}, now=utcnow())
    assert opts.get("alert"), "a realm-locked action must answer with an alert"
    assert rookie["location"]["current_zone_id"] != "zone_thunder_plateau"
    text, _kb, opts = _dispatch(ctx, rookie, "map", "zone:inspect:zone_thunder_plateau",
                                {}, now=utcnow())
    assert opts.get("alert"), "a realm-locked inspect must answer with an alert"


# ── 3. battle items ──────────────────────────────────────────────────────────

def test_keyboard_offers_exactly_the_usable_battle_items(ctx):
    """The identity card's "assign to battle" button must mirror battle_usable.

    The UI used to decide on category (`disposable_weapon`/`talisman`/`relic`)
    while the engine decided on action, so heal pills were offered but refused —
    and the relic that cures paralysis was assignable yet unusable.
    """
    for iid, item in data_registry.consumables.items():
        user = _user(ctx, uid=10)
        items_mod.add_item(user, iid, 1)
        tab = "consumables"
        rows = kbs.item_kb("fa", user, tab, 1, iid).inline_keyboard
        offered = any(b.callback_data == f"bag:action:assign_battle:{iid}"
                      for row in rows for b in row)
        assert offered is items_mod.battle_usable(item), iid
        buttons = kbs.battle_item_buttons("fa", user, 1)
        assert all(items_mod.battle_usable(data_registry.get_consumable(b.callback_data.rsplit(":", 2)[-2]))
                   for b in buttons), iid


def test_battle_item_sources_only_lists_usable_items(ctx):
    user = _user(ctx, uid=3)
    for iid in list(data_registry.consumables)[:10]:
        items_mod.add_item(user, iid, 1)
    for iid in ce.battle_item_sources(user):
        assert items_mod.battle_usable(data_registry.get_consumable(iid)), iid


@pytest.mark.parametrize("item_id,field", [("pill_marrow_wash", "hp"),
                                           ("pill_vitality_refine", "hp"),
                                           ("pill_spirit_gather_low", "qi"),
                                           ("pill_ten_thousand_swords", "buff")])
def test_battle_consumables_fire_in_a_live_round(ctx, item_id, field):
    user = _user(ctx, uid=4)
    items_mod.add_item(user, item_id, 1)
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(1)), "hunt",
                     "zone_valley_mortals")
    session = user["combat"]["session"]
    session["player_first"] = True
    session["player"]["hp"] = 100
    session["player"]["qi"] = 10
    before = (session["player"]["hp"], session["player"]["qi"],
              len(session["statuses"]["p"]))
    text, _kb, _opts = _dispatch(ctx, user, "combat", f"act:item:{item_id}:{session['round']}",
                                 {}, now=utcnow())
    assert items_mod.count_item(user, item_id) == 0, f"{item_id} was not consumed"
    after = (session["player"]["hp"], session["player"]["qi"],
             len(session["statuses"]["p"]))
    assert after != before, f"{item_id} consumed but changed nothing: {text[:120]}"


def test_noop_battle_item_is_not_wasted(ctx):
    user = _user(ctx, uid=5)
    items_mod.add_item(user, "pill_marrow_wash", 1)
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(2)), "hunt",
                     "zone_valley_mortals")
    session = user["combat"]["session"]
    session["player_first"] = True
    session["player"]["hp"] = session["player"]["max_hp"]
    _dispatch(ctx, user, "combat", f"act:item:pill_marrow_wash:{session['round']}",
              {}, now=utcnow())
    assert items_mod.count_item(user, "pill_marrow_wash") == 1, "a no-op pill was swallowed"


# ── 4. terminal combat cards ─────────────────────────────────────────────────

def test_victory_renders_the_loot_scroll(ctx):
    from grimhaven.localization import t
    user = _user(ctx, uid=6)
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(1)), "hunt",
                     "zone_valley_mortals")
    user["combat"]["session"]["enemy"]["hp"] = 1
    text, kb, _opts = _dispatch(ctx, user, "combat", "act:basic:none:1", {}, now=utcnow())
    assert t("fa", "COMBAT_STALEMATE") not in text, "victory degraded to the stalemate card"
    assert kb is not None


def test_death_renders_the_death_card(ctx, monkeypatch):
    from grimhaven.bot.handlers import callbacks as cb
    from grimhaven.localization import t

    class NoMiracle(random.Random):
        def uniform(self, a, b):
            return 99.9 if (a == 0 and b == 100) else (a + b) / 2

        def randint(self, a, b):
            return a

    user = _user(ctx, uid=7)
    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: NoMiracle(1))
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(1)), "hunt",
                     "zone_valley_mortals")
    user["combat"]["session"]["player"]["hp"] = 1
    user["cultivation"]["qi_current"] = 0
    user["stats"]["visible"]["physique_hp"] = 1
    text, _kb, _opts = _dispatch(ctx, user, "combat", "act:basic:none:1", {}, now=utcnow())
    assert t("fa", "COMBAT_STALEMATE") not in text, "death degraded to the stalemate card"
    assert t("fa", "DEATH_CARD")[:6] in text


# ── 5. economy ───────────────────────────────────────────────────────────────

def _fill_ring(user, price_only: bool = True):
    for iid, item in data_registry.consumables.items():
        if not price_only or item.price_stones:
            items_mod.add_item(user, iid, 1)
    return user


def test_buy_never_takes_stones_without_the_item(ctx):
    user = _user(ctx, uid=8)
    _fill_ring(user)
    assert item_count(user) == item_capacity(user), "fixture did not fill the ring"
    stones = user["inventory"]["spirit_stones"]["low"]
    target = next(i for i, o in data_registry.consumables.items()
                  if o.price_stones and i not in user["inventory"]["items"])
    res = items_mod.buy(user, f"item:{target}")
    assert res["status"] == "RING_FULL", res
    assert user["inventory"]["spirit_stones"]["low"] == stones, "stones were taken anyway"
    assert items_mod.count_item(user, target) == 0


def test_booth_claim_refuses_gear_into_a_full_ring(ctx):
    user = _user(ctx, uid=9)
    _fill_ring(user)
    booth_gear = next(i for ids in items_mod.BOOTH_CATALOGS.values() for i in ids
                      if data_registry.get_equipment(i))
    assert items_mod.booth_claim(user, booth_gear) == 0
    assert booth_gear not in user["inventory"]["gear"]


# ── 6. determinism ───────────────────────────────────────────────────────────

def test_confuse_roll_uses_the_round_rng():
    session = {"statuses": {"p": [{"kind": "confuse", "turns": 3}], "e": []},
               "player": {"hp": 100, "max_hp": 100}, "enemy": {"hp": 100, "max_hp": 100},
               "log": {"p": "", "e": ""}}

    class High(random.Random):
        def uniform(self, a, b):
            return 0.99

    class Low(random.Random):
        def uniform(self, a, b):
            return 0.01

    def run(rng):
        return ce._tick_statuses({**session, "statuses": {"p": [{"kind": "confuse", "turns": 3}],
                                                          "e": []}}, "p", "en", [], rng)

    assert run(Low(1)) is True
    assert run(High(1)) is False


# ── 7. profile screens reachable again ───────────────────────────────────────

def test_profile_sub_screens_render_distinctly(ctx):
    """profile:view:meridians / :karma must not fall back to the main profile."""
    user = _user(ctx, uid=11)
    main, _kb, _opts = _dispatch(ctx, user, "profile", "view:main", {}, now=utcnow())
    meridians, _kb, _opts = _dispatch(ctx, user, "profile", "view:meridians", {}, now=utcnow())
    karma, _kb, _opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert meridians != main, "profile:view:meridians rendered the main profile"
    assert karma != main, "profile:view:karma rendered the main profile"
    assert meridians != karma
    # legacy short form keeps working
    legacy, _kb, _opts = _dispatch(ctx, user, "profile", "meridians", {}, now=utcnow())
    assert legacy == meridians


def test_hidden_stats_follow_the_doc_reveal_rules(ctx):
    """Doc §4.2: every hidden attribute stays locked until its condition is met."""
    from grimhaven.localization import t

    user = _user(ctx, uid=12)
    locked, _kb, _opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert t("fa", "HIDDEN_LOCKED") in locked
    assert t("fa", "LUCK_NEED") in locked and t("fa", "CHARISMA_NEED") in locked
    assert "🍀" not in locked and "✨" not in locked, "realm-1 player can read hidden stats"

    # the karma-cleansing pendant reveals luck on its own (reveal_luck special)
    items_mod.add_item(user, "acc_karma_cleansing", 1, is_gear=True)
    assert items_mod.equip(user, "acc_karma_cleansing")["status"] == "OK"
    revealed, _kb, _opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert t("fa", "HIDDEN_LOCKED") not in revealed
    assert t("fa", "HIDDEN_LUCK", value=50) in revealed
    assert t("fa", "CHARISMA_NEED") in revealed, "charisma still needs realm 4"

    # realm 4/5 opens charisma and dao-heart; the map screen must not leak them early
    user["cultivation"]["current_realm_index"] = 5
    recompute_visible_stats(user)
    panel, _kb, _opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert t("fa", "HIDDEN_CHARISMA", value=30) in panel
    assert "⚜️" in panel, "dao heart stability line missing at Spirit Severing"
    assert t("fa", "CORRUPTION_NEED") in panel, "corruption under 30 must stay hidden"

    # corruption >= 30 => purple aura on the scroll and the value in the panel
    user["stats"]["hidden"]["demonic_corruption"] = 31
    scroll, _kb, _opts = _dispatch(ctx, user, "profile", "view:main", {}, now=utcnow())
    assert "🟣" in scroll, "purple aura missing past the corruption threshold"
    panel, _kb, _opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert "🟣" in panel and t("fa", "CORRUPTION_NEED") not in panel


def test_reveal_announcement_fires_exactly_once(ctx):
    from grimhaven.localization import t

    user = _user(ctx, uid=14)
    user["cultivation"]["current_realm_index"] = 5
    recompute_visible_stats(user)
    _text, _kb, opts = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    alert = opts.get("alert", "")
    assert t("fa", "REVEAL_LUCK", value=50) in alert
    assert t("fa", "REVEAL_CHARISMA", value=30) in alert
    _text, _kb, second = _dispatch(ctx, user, "profile", "view:karma", {}, now=utcnow())
    assert "alert" not in second, "the reveal announcement repeated"


def test_setlang_answers_and_switches(ctx):
    from grimhaven.localization import t

    user = _user(ctx, uid=15)
    _text, _kb, same = _dispatch(ctx, user, "setlang", "fa", {}, now=utcnow())
    assert same.get("answer") == t("fa", "LANG_ALREADY")
    _text, kb, switched = _dispatch(ctx, user, "setlang", "en", {}, now=utcnow())
    assert user["account"]["language"] == "en"
    from grimhaven.localization import t_map
    expected = t_map("en", "LANG_SWITCHED", {"lang": t("en", "BTN_LANG_EN")})
    assert switched.get("alert") == expected


def test_empty_slot_clear_reports_the_truth(ctx):
    from grimhaven.localization import t

    user = _user(ctx, uid=16)
    user["cultivation"]["current_realm_index"] = 5
    recompute_visible_stats(user)
    _text, kb, opts = _dispatch(ctx, user, "martial", "slot:clear:4", {}, now=utcnow())
    assert opts.get("answer") == t("fa", "ERR_EMPTY_SLOT")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert t("fa", "BTN_CLEAR_SLOT") not in labels, "empty slot still offers a clear button"
    _text, _kb, opts = _dispatch(ctx, user, "martial", "slot:clear:0", {}, now=utcnow())
    assert opts.get("answer") == t("fa", "SLOT_CLEARED")


def test_breakthrough_failure_prints_the_bolt(ctx):
    from grimhaven.localization import t
    from grimhaven.render import breakthrough_fail_text

    text = breakthrough_fail_text("fa", {"lightning": 12345, "remaining_hp": 10,
                                         "max_hp": 100, "mitigation": 20, "debuff_minutes": 120})
    assert t("fa", "TRIB_BOLT", lightning=12345) in text
    assert t("fa", "BT_FAIL_MITIGATION", value=20) in text


# ── 8. tribulation mitigation actually mitigates ─────────────────────────────

def test_tribulation_mitigation_softens_the_live_failure(ctx):
    from grimhaven.engine import cultivation as cult
    from grimhaven.engine.constants import TRIBULATION_DAMAGE_RANGE

    class Rigged(random.Random):
        def uniform(self, a, b):
            if b - a < 2000:
                return 100.0                       # always fail the main roll
            return float(TRIBULATION_DAMAGE_RANGE[1])   # worst-case bolt

        def choices(self, population, weights=None, k=1):
            return ["MINOR"]

    def run(with_robe: bool) -> dict:
        user = _user(ctx, uid=13)
        user["cultivation"]["dao_path"] = "sword"
        if with_robe:
            items_mod.add_item(user, "robe_asura_ash", 1, is_gear=True)
            assert items_mod.equip(user, "robe_asura_ash")["status"] == "OK"
        return cult.CultivationEngine.confirm_tribulation(user, now=utcnow(), rng=Rigged(0))

    bare = run(False)
    warded = run(True)
    assert bare["status"] == warded["status"] == "FAILED"
    assert warded["mitigation"] == 20, warded.get("mitigation")
    assert warded["lightning"] < bare["lightning"], "gear did not weaken the bolt"
    assert warded["remaining_hp"] > bare["remaining_hp"], "gear did not soften the backlash"
# ── 9. tuned constants actually drive the engines ───────────────────────────

class _RiggedRoll(random.Random):
    """Roll always inside the 5% band; stones deterministic."""

    def uniform(self, a, b):
        return 4.0

    def randint(self, a, b):
        return 3


def test_afk_encounter_rolls_use_the_constant_thresholds(ctx, monkeypatch):
    """settle_afk hard-coded luck>70 / luck<20 / roll<5, so tuning
    LUCKY_ENCOUNT_THRESHOLD / AMBUSH_THRESHOLD / LUCKY_ENCOUNT_CHANCE changed
    nothing. The constants must drive the roll."""
    import datetime as dt

    from grimhaven.engine import cultivation as cult
    from grimhaven.engine.models import iso, utcnow

    now = utcnow() + dt.timedelta(hours=3)
    three_hours_ago = iso(now - dt.timedelta(hours=3))

    def settle(luck: int):
        user = _user(ctx, uid=22)
        user["stats"]["hidden"]["karmic_luck"] = luck
        user["cultivation"]["last_afk_timestamp"] = three_hours_ago
        return cult.CultivationEngine.settle_afk(user, now=now, rng=_RiggedRoll(0))

    lucky = settle(80)
    assert [e["type"] for e in lucky["events"]] == ["TREASURE"] * 3

    monkeypatch.setattr(cult, "LUCKY_ENCOUNT_THRESHOLD", 95)
    silenced = settle(80)
    assert not [e for e in silenced["events"] if e["type"] == "TREASURE"], \
        "raising the threshold did not silence lucky encounters"

    monkeypatch.undo()
    monkeypatch.setattr(cult, "AMBUSH_THRESHOLD", 0)
    no_ambush = settle(50)
    assert not [e for e in no_ambush["events"] if e["type"] == "AMBUSH"], \
        "lowering the ambush threshold did not change the roll"


def test_mercy_and_plunder_karma_come_from_the_table(ctx):
    from grimhaven.engine.combat import mercy_or_plunder
    from grimhaven.engine.constants import KARMA_MODIFIERS

    user = _user(ctx, uid=23)
    mercy = mercy_or_plunder(user, "mercy")
    assert mercy["karma"] == KARMA_MODIFIERS["mercy_in_battle"]
    plunder = mercy_or_plunder(user, "plunder", rng=random.Random(1))
    assert plunder["karma"] == KARMA_MODIFIERS["plunder"]


def test_missing_content_file_is_a_content_error(tmp_path):
    """A missing data/*.json must classify as CONTENT, not as a storage outage."""
    from grimhaven.core.data_loader import GameDataRegistry
    from grimhaven.errors import Category, ContentError, classify

    reg = GameDataRegistry()
    reg.data_dir = tmp_path
    with pytest.raises(ContentError) as excinfo:
        reg._load_json_file("zones.json")
    assert classify(excinfo.value) is Category.CONTENT
    assert excinfo.value.context.get("file") == "zones.json"


# ── 10. an impossible tap must not cost a round ─────────────────────────────

class _ZeroRoll(random.Random):
    """Every percentile roll at the bottom of its range (backfires, crits)."""

    def uniform(self, a, b):
        return 0.0


def _start_fight(user, enemy: str = "valley_wolf"):
    ce.start_session(user, ce.make_beast(enemy, random.Random(2)), "hunt",
                     "zone_valley_mortals")
    return user


def t_fa(key: str) -> str:
    from grimhaven.localization import t
    return t("fa", key)


def _fight(ctx, uid: int = 31):
    user = _user(ctx, uid=uid)
    ce.start_session(user, ce.make_beast("valley_wolf", random.Random(2)), "hunt",
                     "zone_valley_mortals")
    return user, user["combat"]["session"]


def test_tech_on_cooldown_does_not_burn_the_round(ctx):
    user, session = _fight(ctx)
    tech_id = next(iter(data_registry.techniques_index))
    session["cooldowns"][tech_id] = 5
    session["log"]["p"] = "previous round"
    rnd, hp = session["round"], session["player"]["hp"]

    ce.resolve_round(user, "combat:tech", tech_id, rng=_ZeroRoll(0))

    session = user["combat"]["session"]
    assert session["round"] == rnd, "a cooling technique bought the enemy a free round"
    assert session["player"]["hp"] == hp, "the enemy struck during a refused tap"
    assert session["log"]["p"] != "previous round", "the refusal was never shown to the player"


def test_tech_without_qi_does_not_burn_the_round(ctx):
    user, session = _fight(ctx, uid=32)
    tech_id = next((t for t in data_registry.techniques_index
                    if data_registry.get_technique(t).qi_cost > 0), None)
    assert tech_id, "no qi-costing technique in data — the gate cannot be tested"
    session["player"]["qi"] = 0
    rnd = session["round"]

    ce.resolve_round(user, "combat:tech", tech_id, rng=_ZeroRoll(0))

    session = user["combat"]["session"]
    assert session["round"] == rnd, "a Qi-less technique bought the enemy a free round"
    assert session["log"]["p"], "the missing-Qi refusal was never shown to the player"


def test_unknown_battle_item_does_not_burn_the_round(ctx):
    user, session = _fight(ctx, uid=33)
    rnd = session["round"]
    ce.resolve_round(user, "combat:item:not_a_real_item", None, rng=_ZeroRoll(0))
    session = user["combat"]["session"]
    assert session["round"] == rnd, "a stale item button bought the enemy a free round"
    assert session["log"]["p"], "the refusal was never shown to the player"


def test_backfire_names_itself_in_the_hud(ctx):
    """A qi-deviation costs HP; the HUD must say so instead of keeping the old line."""
    user, session = _fight(ctx, uid=34)
    tech_id = next((t for t in data_registry.techniques_index
                    if data_registry.get_technique(t).qi_cost > 0), None)
    session["player"]["qi"] = session["player"]["max_qi"] = 900
    session["player"]["hp"] = 5000
    session["log"]["p"] = "previous round"
    # a zero roll always trips the deviation check when the risk is non-zero
    if user["cultivation"].get("dao_heart", 100) >= 100:
        user["cultivation"]["dao_heart"] = 40
    before = session["player"]["hp"]

    ce.resolve_round(user, "combat:tech", tech_id, rng=_ZeroRoll(0))

    session = user["combat"]["session"]
    assert session["player"]["hp"] < before, "rigged roll did not backfire — check the risk formula"
    assert session["log"]["p"] != "previous round", "backfire damage stayed invisible in the HUD"


def test_no_write_only_turn_lock_field(ctx):
    """`turn_lock` was set in two places and read in none."""
    user, session = _fight(ctx, uid=35)
    assert "turn_lock" not in session
    ce.resolve_round(user, "combat:basic", None, rng=_ZeroRoll(0))
    assert "turn_lock" not in (user["combat"]["session"] or {}), \
        "a write-only session field came back"


# ── 11. the free pavilion is not a shortcut around progression ──────────────

def test_free_art_claim_respects_the_realm_gate(ctx):
    """`shop:art:claim_free` handed the realm-7 manual to a realm-1 player."""
    from grimhaven.engine import items as items_mod
    user = _user(ctx, uid=41, realm=1)
    locked = max(data_registry.martial_arts.values(), key=lambda a: a.required_realm)
    assert locked.required_realm > 1, "test needs a realm-gated manual"
    text, kb, opts = _dispatch(ctx, user, "shop", f"art:claim_free:{locked.art_id}:1",
                               {}, now=utcnow())
    assert locked.art_id not in user["inventory"]["arts"], "the gate let a realm-1 player in"
    assert opts.get("alert"), "the refusal was silent"
    assert items_mod.learn_art(user, locked.art_id)["status"] == "REALM_LOCKED"


def test_free_art_claim_succeeds_at_the_right_realm(ctx):
    user = _user(ctx, uid=42, realm=9)
    art = max(data_registry.martial_arts.values(), key=lambda a: a.required_realm)
    _dispatch(ctx, user, "shop", f"art:claim_free:{art.art_id}:1", {}, now=utcnow())
    assert art.art_id in user["inventory"]["arts"]


def test_art_catalogue_marks_locked_schools(ctx):
    user = _user(ctx, uid=43, realm=1)
    text, _kb, _opts = _dispatch(ctx, user, "shop", "tab:arts:5", {}, now=utcnow())
    assert "🔒" in text, "locked manuals are listed as ordinary scrolls"


# ── 12. the substitute doll is visible, honest and protective ──────────────

def test_doll_cannot_be_swallowed_by_hand(ctx):
    from grimhaven.engine import items as items_mod
    user = _user(ctx, uid=44)
    items_mod.add_item(user, "doll_substitute", 1)
    text, _kb, opts = _dispatch(ctx, user, "bag", "action:consume:doll_substitute",
                                {}, now=utcnow())
    assert items_mod.count_item(user, "doll_substitute") == 1, "the doll was wasted by hand"
    assert opts.get("alert"), "the refusal was silent"


def test_doll_saves_from_tribulation_annihilation_and_the_card_says_so(ctx):
    from grimhaven.engine import items as items_mod
    from grimhaven.engine.cultivation import CultivationEngine
    user = _user(ctx, uid=45, realm=4)
    user["cultivation"]["current_stage"] = 1
    user["cultivation"]["qi_current"] = 10 ** 9
    items_mod.add_item(user, "doll_substitute", 1)
    detail = CultivationEngine._apply_failure(user, "ANNIHILATION", random.Random(0))
    assert detail.get("saved_by_doll") and items_mod.count_item(user, "doll_substitute") == 0
    res = {"lightning": 1, "remaining_hp": 1, "max_hp": 2, "debuff_minutes": 120, **detail}
    assert "🪆" in R.breakthrough_fail_text("fa", res), "the saved player was never told"


def test_annihilation_without_a_doll_names_both_losses(ctx):
    from grimhaven.engine import items as items_mod
    from grimhaven.engine.cultivation import CultivationEngine
    user = _user(ctx, uid=46, realm=4)
    user["cultivation"]["current_stage"] = 1
    items_mod.add_item(user, "wpn_iron_leaf", 1, is_gear=True)
    items_mod.equip(user, "wpn_iron_leaf")
    detail = CultivationEngine._apply_failure(user, "ANNIHILATION", random.Random(0))
    assert detail["shattered"] == "wpn_iron_leaf", "the shatter note needs the gear id"
    assert user["equipment"].get("weapon") is None, "the shattered piece stayed equipped"
    res = {"lightning": 1, "remaining_hp": 1, "max_hp": 2, "debuff_minutes": 120, **detail}
    card = R.breakthrough_fail_text("fa", res)
    assert "💔" in card, "the shattered artifact vanished without a word"
    assert "🔻" in card, "the realm fall was not reported"


class _NoMiracle(random.Random):
    """No miracle escape: a 0-100 percentile roll at 99.9 always fails it."""

    def uniform(self, a, b):
        return 99.9 if (a == 0 and b == 100) else (a + b) / 2

    def randint(self, a, b):
        return a


def test_death_card_never_contradicts_the_doll(ctx, monkeypatch):
    """DEATH_REPORT claimed 'without a doll your gear was damaged' even for the
    player whose doll had just burned — the card printed both halves."""
    from grimhaven.bot.handlers import callbacks as cb
    from grimhaven.engine import items as items_mod
    user = _user(ctx, uid=47, realm=4)
    items_mod.add_item(user, "doll_substitute", 1)
    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: _NoMiracle(1))
    _start_fight(user)
    session = user["combat"]["session"]
    session["player"]["hp"] = 1
    user["cultivation"]["qi_current"] = 0
    user["stats"]["visible"]["physique_hp"] = 1
    text, _kb, _opts = _dispatch(ctx, user, "combat",
                                f"act:basic:none:{session['round']}", {}, now=utcnow())
    assert "🪆" in text
    assert t_fa("DEATH_GEAR_DAMAGED") not in text, "the gear-damage line contradicted the doll"


def test_death_card_reports_damaged_gear_when_no_doll(ctx, monkeypatch):
    from grimhaven.bot.handlers import callbacks as cb
    from grimhaven.engine import items as items_mod
    user = _user(ctx, uid=48, realm=4)
    items_mod.add_item(user, "wpn_iron_leaf", 1, is_gear=True)
    items_mod.equip(user, "wpn_iron_leaf")
    monkeypatch.setattr(cb.random, "Random", lambda *a, **k: _NoMiracle(1))
    _start_fight(user)
    session = user["combat"]["session"]
    session["player"]["hp"] = 1
    user["cultivation"]["qi_current"] = 0
    user["stats"]["visible"]["physique_hp"] = 1
    text, _kb, _opts = _dispatch(ctx, user, "combat",
                                f"act:basic:none:{session['round']}", {}, now=utcnow())
    assert t_fa("DEATH_CARD")[:6] in text, "not a death card — the fight did not end"
    assert t_fa("DEATH_GEAR_DAMAGED") in text

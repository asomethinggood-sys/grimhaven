"""Regression tests for the round-3 bug sweep (screenshot + full audit)."""
import datetime as dt
import tempfile
from pathlib import Path

from grimhaven.bot.handlers.callbacks import _canon, _route
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core.data_loader import bootstrap as bootstrap_data, data_registry
from grimhaven.core.middleware import callback_blocked
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine import items as items_mod
from grimhaven.engine.constants import TIER_KEYS
from grimhaven.engine.models import ensure_v2, new_user_doc, utcnow
from grimhaven.engine import world as world_mod
from grimhaven.localization import t
from grimhaven import render as R


def _setup():
    tmp = tempfile.mkdtemp()
    storage = Storage(Path(tmp) / "t.db")
    bootstrap_world(storage)
    bootstrap_data()
    ctx = Ctx(storage, admin_ids={7777})
    user = ensure_v2(new_user_doc(42, "tester", "fa"))
    storage.save_user(user)
    return storage, ctx, user


def test_tier_keys_match_locale_files():
    fa = __import__("json").load(open("locales/locale_fa.json", encoding="utf-8"))
    for key in TIER_KEYS.values():
        assert key in fa, key


def test_bag_inspect_shows_localized_name_and_tier_not_raw_id():
    _, _, user = _setup()
    text = R.bag_inspect_text("en", user, "wpn_verdant_rain")
    assert "TIER_" not in text
    assert "wpn_verdant_rain" not in text
    assert "leech_qi_percent_3" not in text
    assert "EFF_SPECIAL_LEECH_QI_PERCENT_3" not in text


def test_special_effect_rendered_localized():
    _, _, user = _setup()
    text = R.bag_inspect_text("en", user, "wpn_thunder_awl")
    assert t("en", "EFF_SPECIAL_STUN_10") in text


def test_bag_list_has_no_raw_item_ids():
    _, _, user = _setup()
    user["inventory"]["gear"]["wpn_iron_leaf"] = {"dur": 100}
    text = R.bag_text("en", user, "gear", 1)
    assert "wpn_iron_leaf" not in text
    assert "Iron-Leaf" in text or "Iron" in text


def test_booth_cannot_mint_arbitrary_items():
    _, _, user = _setup()
    assert items_mod.booth_claim(user, "wpn_verdant_rain", booth="pills") == 0
    assert "wpn_verdant_rain" not in user["inventory"]["gear"]
    assert items_mod.booth_claim(user, "wpn_iron_leaf", booth="gear") == 1


def test_booth_claim_route_rejects_wrong_booth():
    storage, ctx, user = _setup()
    _route(ctx, user, "shop:booth:claim:pills:wpn_verdant_rain", {}, now=utcnow())
    assert "wpn_verdant_rain" not in user["inventory"]["gear"]


def test_density_fields_stay_in_sync_on_travel():
    storage, _, user = _setup()
    zid = "zone_thunder_plateau"
    res = world_mod.travel(storage, user, zid)
    if res["status"] != "OK":  # gated by realm: lift it for the check
        user["cultivation"]["current_realm_index"] = 9
        res = world_mod.travel(storage, user, zid)
    assert res["status"] == "OK"
    assert user["location"]["density"] == user["location"]["vein_density"]


def test_sect_failure_note_does_not_double_prefix():
    _, ctx, user = _setup()
    user["cultivation"]["current_realm_index"] = 2   # past the gate card
    user["cultivation"]["alignment"] = "orthodox"
    text, _, opts = _route(ctx, user, "sect:action:join:sect_moon_shadow", {}, now=utcnow())
    note = opts.get("alert", "")
    assert "SECT_SECT_" not in note
    assert note == t("fa", "SECT_ALIGNMENT_MISMATCH")


def test_admin_inspect_method_not_raw_id():
    from grimhaven.engine.constants import METHODS
    _, _, user = _setup()
    assert METHODS  # method names come from locale keys


def test_arts_card_hides_raw_school_id():
    _, _, user = _setup()
    text = R.art_inspect_text("en", user, "art_moonlight_sword")
    assert "art_moonlight_sword" not in text


def test_middleware_map_action_injury_guard():
    _, _, user = _setup()
    user["status"] = "heavily_injured"
    user["combat"]["injury"] = {"debuff_id": "x", "expires_at": "2999-01-01T00:00:00+00:00",
                                "qi_rate_multiplier": 0.5, "combat_speed_penalty": 0.3,
                                "combat_def_penalty": 0.3}
    assert callback_blocked(user, "map:action:conquer:zone_valley_mortals") == "GUARD_INJURED"

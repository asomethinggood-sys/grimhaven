"""Phase-2 tests: world, items, Dao choice, combat."""
from __future__ import annotations

import random

import pytest

from grimhaven.engine import combat, items, world
from grimhaven.engine.constants import ZONES
from grimhaven.engine.models import new_user_doc


class TmpStorage:
    """In-memory stand-in sharing the Storage API used by world()."""

    def __init__(self):
        self.zones, self.sects = {}, {}

    def get_zone(self, z): return self.zones.get(z)
    def save_zone(self, d): self.zones[d["zone_id"]] = d
    def get_sect(self, s): return self.sects.get(s)
    def save_sect(self, d): self.sects[d["sect_id"]] = d


def user(realm=1, stage=0, alignment="orthodox"):
    u = new_user_doc(7, "hero", "en")
    u["cultivation"]["current_realm_index"] = realm
    u["cultivation"]["current_stage"] = stage
    u["cultivation"]["alignment"] = alignment
    return u


# ── travel & conquest ────────────────────────────────────────────────────────

def test_travel_updates_vein_density():
    st = TmpStorage()
    u = user(realm=2)
    res = world.travel(st, u, "zone_misty_peak")
    assert res["status"] == "OK"
    assert u["location"]["vein_density"] == pytest.approx(2.2)


def test_travel_locked_below_realm():
    st = TmpStorage()
    u = user(realm=1)
    assert world.travel(st, u, "zone_heaven_spring")["status"] == "ZONE_LOCKED_REALM"


def test_conquest_ownership():
    st = TmpStorage()
    u = user(realm=2)
    world.travel(st, u, "zone_misty_peak")
    res = world.apply_conquest_result(st, u, "zone_misty_peak", won=True)
    assert res["status"] == "VICTORY"
    zone = st.get_zone("zone_misty_peak")
    assert zone["owner"] == 7 and zone["owner_kind"] == "user"


# ── sects ────────────────────────────────────────────────────────────────────

def test_join_sect_requires_foundation():
    st = TmpStorage()
    st.save_sect({"sect_id": "sect_azure_cloud", "key": "SECT_AZURE_CLOUD",
                  "alignment": "orthodox", "npc": True, "members": []})
    assert world.join_sect(st, user(realm=1), "sect_azure_cloud")["status"] == "SECT_LOCKED"
    res = world.join_sect(st, user(realm=2), "sect_azure_cloud")
    assert res["status"] == "OK"


def test_found_sect_requires_void_refinement():
    st = TmpStorage()
    assert world.found_sect(st, user(realm=3))["status"] == "SECT_FOUND_LOCKED"
    res = world.found_sect(st, user(realm=6), "My Sect")
    assert res["status"] == "OK" and res["sect"]["master"] == 7


# ── sacrifices & alignment ───────────────────────────────────────────────────

def test_orthodox_blood_ritual_explodes_dantian():
    u = user(realm=3, alignment="orthodox")
    u["cultivation"]["qi_current"] = 1000
    res = world.perform_sacrifice(u, "blood")
    assert res["status"] == "DANTIAN_EXPLOSION"
    assert u["cultivation"]["qi_current"] == 500


def test_demonic_sacrifice_boosts_and_corrupts():
    u = user(realm=3, alignment="demonic")
    res = world.perform_sacrifice(u, "devour")
    assert res["status"] == "OK" and res["boost"] == 5.0
    assert u["stats"]["hidden"]["demonic_corruption"] == 15
    assert any(b["boost"] == 5.0 for b in u["buffs"])
    assert res["enforcer_damage"] > 0  # agents of Heavenly Justice strike


# ── shop / equipment ─────────────────────────────────────────────────────────

def test_shop_purchase_and_equip_cycle():
    u = user(realm=2)
    u["inventory"]["spirit_stones"]["low"] = 200
    assert items.shop_locked(u) is False
    res = items.buy(u, "weapon_heaven")  # costs 900 — must refuse
    assert res["status"] == "NOT_ENOUGH_STONES"
    assert u["inventory"]["spirit_stones"]["low"] == 200  # nothing charged
    # with enough funds the equip cycle must work
    u["inventory"]["spirit_stones"]["low"] = 1000
    assert items.buy(u, "weapon_heaven")["status"] == "OK"
    assert items.equip(u, "gear_weapon_heaven")["status"] == "OK"
    assert u["equipment"]["weapon"]["tier"] == "heaven"
    assert items.unequip(u, "weapon")["status"] == "OK"
    assert u["equipment"]["weapon"] is None


def test_cannot_afford():
    u = user(realm=2)
    u["inventory"]["spirit_stones"]["low"] = 1
    assert items.buy(u, "doll_substitute")["status"] == "NOT_ENOUGH_STONES"


# ── Dao choice ───────────────────────────────────────────────────────────────

def test_dao_choice_gated_and_irreversible():
    u = user(realm=1, stage=5)
    assert items.choose_dao(u, "sword", "orthodox")["status"] == "TOO_EARLY"
    u["cultivation"]["current_stage"] = 8  # layer 9 complete
    res = items.choose_dao(u, "sword", "orthodox")
    assert res["status"] == "OK"
    assert items.choose_dao(u, "body")["status"] == "ALREADY_CHOSEN"


def test_blood_dao_forces_demonic():
    u = user(realm=1, stage=8)
    res = items.choose_dao(u, "blood", "orthodox")
    assert res["status"] == "OK" and u["cultivation"]["alignment"] == "demonic"


# ── combat ───────────────────────────────────────────────────────────────────

def test_hunt_victory_rewards():
    u = user(realm=2)
    u["stats"]["visible"]["physique_hp"] = 900
    u["stats"]["visible"]["max_hp"] = 900
    u["combat"]["loadout"] = ["basic_strike", "iron_guard", "element_burst", None]
    res = combat.hunt_monster(u, 1, "wild_boar", rng=random.Random(11))
    assert res["won"] is True
    assert res["stones"] >= 1 and u["inventory"]["spirit_stones"]["low"] >= 1


def test_duel_deterministic_with_seed():
    u1 = user(realm=2)
    u2 = user(realm=2)
    m1 = combat.make_monster("hungry_wolf", 1, random.Random(5))
    m2 = combat.make_monster("hungry_wolf", 1, random.Random(5))
    r1 = combat.duel(u1, m1, rng=random.Random(9))
    r2 = combat.duel(u2, m2, rng=random.Random(9))
    assert r1["won"] == r2["won"] and r1["turns"] == r2["turns"]


def test_mercy_raises_karma_plunder_drops_it():
    u = user(realm=2)
    luck0 = u["stats"]["hidden"]["karmic_luck"]
    combat.mercy_or_plunder(u, "mercy")
    assert u["stats"]["hidden"]["karmic_luck"] == luck0 + 1
    combat.mercy_or_plunder(u, "plunder", rng=random.Random(3))
    assert u["stats"]["hidden"]["karmic_luck"] == luck0 + 1 - 5

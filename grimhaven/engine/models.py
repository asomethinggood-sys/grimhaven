"""User document model — mirrors the MongoDB/JSONB schema of design doc §10.1,
extended by the 3-part overhaul: FSM status, battle sessions, injury debuffs,
spatial-ring capacity, and data-driven combat stats.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from .constants import (
    BASE_RATES,
    DEMONIC_AFK_MULT_CAP,
    EQUIP_SLOTS,
    MAX_REALM,
    METHODS,
    REALM_ENTRY_SPIKES,
    REALM_STAGES,
)

UTC = dt.timezone.utc


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(ts: dt.datetime | None = None) -> str:
    return (ts or utcnow()).isoformat()


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


# ── new schema ───────────────────────────────────────────────────────────────
BASE_RING_CAPACITY = 12

START_INVENTORY = {
    "spirit_stones": {"low": 30, "mid": 0, "high": 0, "heavenly": 0},
    "methods": ["method_breath_mortal", "method_sky_cleaving"],
    "arts": ["art_moonlight_sword"],          # every mortal starts on the moonlight path
    "items": {"pill_guardian": 1, "talisman_gale_blade": 2},
    "gear": {},                               # gear_id -> {"dur": 100}
}


def new_user_doc(user_id: int, username: str = "", language: str = "fa") -> dict:
    """Brand-new cultivator: Qi Condensation, layer 1, empty Dantian."""
    doc = {
        "_id": f"tg_user_{user_id}",
        "user_id": user_id,
        "status": "idle",
        "account": {
            "username": username or f"cultivator_{user_id}",
            "language": language,
            "is_banned": False,
            "registered_at": iso(),
            "is_admin": False,
        },
        "cultivation": {
            "current_realm_index": 1,
            "current_stage": 0,
            "qi_current": 0,
            "qi_capacity": REALM_STAGES[1][0],
            "dao_path": None,
            "alignment": "orthodox",
            "alignment_locked": False,
            "active_method_id": "method_breath_mortal",
            "active_stone": None,
            "active_pill": None,
            "meditating": False,
            "meditation_started_at": None,
            "seclusion_finish_time": None,
            "seclusion_target": None,
            "meridian_sealed_until": None,
            "last_afk_timestamp": iso(),
        },
        "stats": {
            "visible": {
                "physique_hp": 100,
                "max_hp": 100,
                "spiritual_sense": 10,
                "circulation_velocity": 10,
            },
            "hidden": {
                "karmic_luck": 50,
                "karmic_luck_revealed": False,
                "dao_affinity_charisma": 30,
                "dao_affinity_revealed": False,
                "dao_heart_stability": 70,
                "demonic_corruption": 0,
            },
        },
        "inventory": copy.deepcopy(START_INVENTORY),
        "equipment": {slot: None for slot in EQUIP_SLOTS},
        "combat": {
            # deck builder: technique ids, grown up to max_slots (3 → 6)
            "loadout": ["moon_slash", "lunar_mist", "zenith_eclipse"],
            "session": None,
            "injury": None,           # {debuff_id, expires_at, ...}
            "paralysis_until": None,  # coma lock after a true death
            "wins": 0,
            "losses": 0,
            "item_slots": [],          # battle keyboard row-4 consumables (max 2)
        },
        "location": {
            "current_zone_id": "zone_valley_mortals",
            "zone_id": "zone_valley_mortals",
            "vein_density": 1.0,
            "density": 1.0,
            "name": "دره فانی‌ها",
            "name_en": "Valley of Mortals",
            "since": iso(),
            "sect_id": None,
        },
        "ui": {
            "active_menu_message_id": None,   # single-window lifecycle (P1)
            "bag_tab": "gear",
            "bag_page": 1,
            "map_page": 1,
            "shop_page": 1,
        },
        "buffs": [],
        "progress": {
            "qi_total_accumulated": 0,
            "breakthrough_attempts": 0,
            "breakthrough_successes": 0,
            "failures_minor": 0,
            "failures_deviation": 0,
            "failures_annihilation": 0,
            "encounters_found": 0,
            "root_ancient_used": False,
            "deaths": 0,
            "miracle_escapes": 0,
            "adventures": 0,
            "last_gather_at": None,
        },
    }
    return doc


def ensure_v2(doc: dict) -> dict:
    """Idempotent migration for documents written before the overhaul."""
    doc.setdefault("status", "idle")
    inv = doc.setdefault("inventory", {})
    inv.setdefault("arts", ["art_moonlight_sword"])
    inv.setdefault("methods", ["method_breath_mortal"])
    inv.setdefault("gear", {})
    items = inv.setdefault("items", {})
    # merge legacy pills/herbs dicts into the unified item ledger
    for pid, qty in (inv.get("pills") or {}).items():
        if qty and pid not in items:
            items[pid] = qty
    for hid, qty in (inv.get("herbs") or {}).items():
        if hid == "root_used":
            if qty:
                doc.setdefault("progress", {})["root_ancient_used"] = True
            continue
        if qty and hid not in items:
            items[hid] = qty
    inv.pop("pills", None)
    inv.pop("herbs", None)
    inv.pop("techniques", None)
    # normalize gear (old docs used {slot,tier}; new ones use {dur})
    for gid, g in list(inv["gear"].items()):
        if not isinstance(g, dict):
            inv["gear"][gid] = {"dur": 100}
        else:
            g.setdefault("dur", 100)
    eq = doc.setdefault("equipment", {})
    for slot, gear in list(eq.items()):
        if isinstance(gear, dict):
            gear.setdefault("dur", 100)
    cul = doc.get("cultivation", {})
    if cul.get("meditating") and doc["status"] == "idle":
        doc["status"] = "meditating"
    cmb = doc.setdefault("combat", {})
    cmb.setdefault("session", None)
    cmb.setdefault("injury", None)
    cmb.setdefault("paralysis_until", None)
    cmb.setdefault("wins", 0)
    cmb.setdefault("losses", 0)
    loadout = cmb.setdefault("loadout", [])
    # drop legacy placeholder techniques that no longer exist in the data set
    from ..core.data_loader import data_registry
    cmb["loadout"] = [t for t in loadout if isinstance(t, str) and t in data_registry.techniques_index][:6]
    if not cmb["loadout"] and "art_moonlight_sword" in inv.get("arts", []):
        cmb["loadout"] = ["moon_slash", "lunar_mist", "zenith_eclipse"]
    prog = doc.setdefault("progress", {})
    for k, v in (("root_ancient_used", False), ("deaths", 0),
                 ("miracle_escapes", 0), ("adventures", 0), ("last_gather_at", None)):
        prog.setdefault(k, v)
    # ── spec P3: zone renames → data/zones.json ids (one-time alias migration)
    _ZONE_ALIASES = {"zone_mortal_valley": "zone_valley_mortals",
                     "zone_common_cave": "zone_ordinary_cave",
                     "zone_misty_peak": "zone_mist_peak",
                     "zone_heaven_spring": "zone_heavenly_spring"}
    loc = doc.setdefault("location", {})
    zid = loc.get("current_zone_id") or loc.get("zone_id") or "zone_valley_mortals"
    zid = _ZONE_ALIASES.get(zid, zid)
    from ..core.data_loader import data_registry
    zdef = data_registry.get_zone(zid) or data_registry.get_zone("zone_valley_mortals")
    loc["current_zone_id"] = loc["zone_id"] = zid
    if zdef:
        loc.setdefault("name", zdef.name)
        loc.setdefault("name_en", zdef.name_en)
        # vein_density is the LIVE field (travel/conquest update it); the legacy
        # density mirror must follow it — never let a stale value win.
        vd = loc.get("vein_density")
        if vd is None:
            vd = loc.get("density")
        vd = float(vd) if vd else float(zdef.density)
        loc["vein_density"] = vd
        loc["density"] = vd
    loc.setdefault("since", iso())
    loc.setdefault("sect_id", None)
    ui = doc.setdefault("ui", {})
    for k, v in (("active_menu_message_id", None), ("bag_tab", "gear"),
                 ("bag_page", 1), ("map_page", 1), ("shop_page", 1)):
        ui.setdefault(k, v)
    cmb.setdefault("item_slots", [])
    return doc


# ── derived stats ────────────────────────────────────────────────────────────

LIFESPANS: dict[int, int] = {1: 80, 2: 120, 3: 200, 4: 350, 5: 600, 6: 1000, 7: 1800}


def meridians_opened(user: dict) -> int:
    """8 mortal meridians; realms + stages unseal them."""
    cul = user["cultivation"]
    return min(8, cul["current_realm_index"] + cul["current_stage"] // 3)


def lifespan_years(user: dict) -> int:
    cul = user["cultivation"]
    base = LIFESPANS.get(cul["current_realm_index"], 80)
    bonus = int(user["stats"]["hidden"].get("dao_heart_stability", 50) / 25)
    return base + bonus * 5


def qi_purity(user: dict) -> int:
    """0–100 refinement of the circulating Qi (active mantra drives it)."""
    from ..core.data_loader import data_registry
    cul = user["cultivation"]
    m = data_registry.get_method(cul.get("active_method_id") or "")
    mult = m.qi_mult if m else 1.0
    return max(1, min(100, int(40 + mult * 12 + cul["current_realm_index"] * 3)))


def crit_chance(user: dict) -> float:
    """Percent crit chance — same formula the combat resolver uses."""
    prof = combat_profile(user)
    return float(max(5.0, min(60.0, prof["divine_sense"] * 0.5 + prof["crit_bonus"] * 100)))


def comprehension(user: dict) -> int:
    vis = user["stats"]["visible"]
    hidden = user["stats"]["hidden"]
    return int(vis.get("spiritual_sense", 10) * 1.5 + hidden.get("dao_heart_stability", 50) * 0.2
               + user["cultivation"]["current_realm_index"] * 3)



def stage_cost(user: dict) -> int:
    cul = user["cultivation"]
    realm = cul["current_realm_index"]
    stage = cul["current_stage"]
    stages = REALM_STAGES[realm]
    return stages[min(stage, len(stages) - 1)]


def hp_multiplier(user: dict) -> float:
    realm = user["cultivation"]["current_realm_index"]
    mult = 1.0
    for r in range(2, realm + 1):
        mult *= REALM_ENTRY_SPIKES.get(r, (1.0, 1.0))[0]
    return mult


def gear_durability(gear: dict | None) -> float:
    if not gear:
        return 0.0
    return max(0.1, min(1.0, float(gear.get("dur", 100)) / 100.0))


def gear_bonus(user: dict, field: str) -> float:
    """Sum a numeric field across equipped items from data/equipment.json."""
    from ..core.data_loader import data_registry
    total = 0.0
    for slot, gear in (user.get("equipment") or {}).items():
        if not gear:
            continue
        item = data_registry.get_equipment(gear.get("id", ""))
        if not item:
            continue
        total += float(getattr(item, field, 0) or 0) * gear_durability(gear)
    return total


def gear_specials(user: dict) -> set[str]:
    from ..core.data_loader import data_registry
    out = set()
    for slot, gear in (user.get("equipment") or {}).items():
        if not gear:
            continue
        item = data_registry.get_equipment(gear.get("id", ""))
        if item and item.special_effect:
            out.add(item.special_effect)
    return out


def recompute_visible_stats(user: dict) -> None:
    """Recompute HP / sense / circulation from realm, stage, Dao and gear."""
    realm = user["cultivation"]["current_realm_index"]
    stage = user["cultivation"]["current_stage"]
    base_hp = 100.0 * hp_multiplier(user) * (1.0 + 0.10 * stage)
    dao = user["cultivation"].get("dao_path")
    if dao == "body":
        base_hp *= 1.5
    elif dao:
        from .constants import DAO_PATHS
        base_hp *= DAO_PATHS[dao]["hp"]
    gear_bonus_hp = 0.0
    for slot, gear in (user.get("equipment") or {}).items():
        if gear and slot in ("robe", "companion", "natal"):
            from .constants import TIER_POWER
            gear_bonus_hp += 0.05 * (1 + TIER_POWER[gear.get("tier", "mortal")])
    max_hp = int(base_hp * (1.0 + gear_bonus_hp)) + int(gear_bonus(user, "hp_bonus"))
    vis = user["stats"]["visible"]
    vis["max_hp"] = max(1, max_hp)
    vis["physique_hp"] = min(vis.get("physique_hp", vis["max_hp"]), vis["max_hp"]) or vis["max_hp"]
    vis["spiritual_sense"] = int(10 * (1.5 ** (realm - 1)) * (1 + 0.05 * stage))
    method = METHODS.get(user["cultivation"].get("active_method_id", ""), {})
    circ = int(10 + realm * 5 + stage + method.get("tech_mult", 1.0))
    circ += int(gear_bonus(user, "speed_bonus"))
    vis["circulation_velocity"] = circ


def afk_hourly_rate(user: dict, world_boost: float = 1.0,
                    now: dt.datetime | None = None) -> float:
    """Doc §5 master formula, plus the injury debuff multiplier."""
    now = now or utcnow()
    cul = user["cultivation"]
    rate_mult = 1.0
    realm = cul["current_realm_index"]
    base = BASE_RATES.get(realm, 100)
    from ..core.data_loader import data_registry
    _m = data_registry.get_method(cul.get("active_method_id", ""))
    tech = _m.qi_mult if _m else METHODS.get(cul.get("active_method_id", ""), {}).get("tech_mult", 1.0)
    for _b in user.get("buffs", []) or []:  # timed debuffs (e.g. inner-demon deviation)
        if _b.get("rate_mult") and _b.get("until"):
            _u = parse_iso(_b["until"])
            if _u and _u > now:
                rate_mult *= float(_b["rate_mult"])

    catalyst = 0.0
    stone = cul.get("active_stone")
    if stone:
        from .constants import SPIRIT_STONES
        catalyst += SPIRIT_STONES[stone]["boost"]
    if user.get("progress", {}).get("root_ancient_used"):
        catalyst += 0.25  # ancient ginseng permanently widens the channels
    for buff in user.get("buffs", []):
        until = parse_iso(buff.get("until"))
        if until and until > now:
            catalyst += float(buff.get("boost", 0.0))
    if cul["alignment"] == "demonic":
        catalyst = min(1.0 + catalyst, DEMONIC_AFK_MULT_CAP) - 1.0

    vein = user["location"].get("vein_density", 1.0)
    luck_bonus = user["stats"]["hidden"]["karmic_luck"] * 0.002
    rate = base * tech * (1.0 + catalyst) * vein * rate_mult
    rate *= (1.0 + luck_bonus) * world_boost
    if _meridians_sealed(user, now):
        rate *= 0.5
    from ..core.state_machine import qi_rate_multiplier
    rate *= qi_rate_multiplier(user, now)
    return rate


def _meridians_sealed(user: dict, now: dt.datetime | None = None) -> bool:
    until = parse_iso(user["cultivation"].get("meridian_sealed_until"))
    return bool(until and until > (now or utcnow()))


def meridians_sealed(user: dict, now: dt.datetime | None = None) -> bool:
    return _meridians_sealed(user, now)


def in_seclusion(user: dict, now: dt.datetime | None = None) -> bool:
    finish = parse_iso(user["cultivation"].get("seclusion_finish_time"))
    return bool(finish and finish > (now or utcnow()))


def is_max_realm_final(user: dict) -> bool:
    cul = user["cultivation"]
    return cul["current_realm_index"] >= MAX_REALM and cul["current_stage"] >= len(REALM_STAGES[MAX_REALM]) - 1


# ── spatial ring capacity ───────────────────────────────────────────────────

def item_capacity(user: dict) -> int:
    cap = BASE_RING_CAPACITY + int(gear_bonus(user, "capacity_bonus"))
    return cap


def item_count(user: dict) -> int:
    inv = user["inventory"]
    return len([1 for q in inv.get("items", {}).values() if q > 0]) + len(inv.get("gear", {}))


def ring_has_room(user: dict, item_id: str) -> bool:
    if item_id in user["inventory"].get("items", {}) or item_id in user["inventory"].get("gear", {}):
        return True  # stacks are free once slotted
    return item_count(user) < item_capacity(user)


def add_item(user: dict, item_id: str, qty: int = 1, *, is_gear: bool = False) -> bool:
    """Deposit one item; returns False when the spatial ring is full."""
    inv = user["inventory"]
    if is_gear or (item_id in _gear_ids()):
        if item_id in inv["gear"]:
            inv["gear"][item_id] = {"dur": 100}
            return True
        if not ring_has_room(user, item_id):
            return False
        inv["gear"][item_id] = {"dur": 100}
        return True
    if item_id not in inv["items"] and not ring_has_room(user, item_id):
        return False
    inv["items"][item_id] = inv["items"].get(item_id, 0) + qty
    return True


def _gear_ids() -> set[str]:
    from ..core.data_loader import data_registry
    return set(data_registry.equipment.keys())


def consume_item(user: dict, item_id: str, qty: int = 1) -> bool:
    inv = user["inventory"]
    if inv["items"].get(item_id, 0) >= qty:
        inv["items"][item_id] -= qty
        if inv["items"][item_id] <= 0:
            inv["items"].pop(item_id, None)
        return True
    return False


# ── combat profile (Part 2 §1) ───────────────────────────────────────────────

def max_slots(user: dict) -> int:
    """Action deck capacity: 3 base, +1 Foundation, +1 Golden Core, +1 mind ring."""
    realm = user["cultivation"]["current_realm_index"]
    slots = 3
    if realm >= 2:
        slots += 1
    if realm >= 3:
        slots += 1
    if "slot_bonus_1" in gear_specials(user):
        slots += 1
    return min(slots, 6)


def combat_profile(user: dict, now: dt.datetime | None = None) -> dict:
    """Full attribute set that governs the Part-2 battle formulas."""
    from ..core.data_loader import data_registry
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis = user["stats"]["visible"]
    hidden = user["stats"]["hidden"]

    dao = cul.get("dao_path")
    atk_mult = 1.0
    crit_extra = 0.05
    if dao:
        from .constants import DAO_PATHS
        atk_mult *= DAO_PATHS[dao]["atk"]
        crit_extra += DAO_PATHS[dao]["crit"]

    spiritual_atk = int((8 * (1.7 ** (realm - 1)) + stage * 3) * atk_mult)
    physical_atk = int(spiritual_atk * (1.15 if dao == "body" else 0.95))
    spiritual_atk += int(gear_bonus(user, "spiritual_atk_bonus"))
    physical_atk += int(gear_bonus(user, "physical_atk_bonus"))

    physical_def = int(4 * (1.55 ** (realm - 1))) + int(gear_bonus(user, "physical_def_bonus"))
    spiritual_def = int(3 * (1.55 ** (realm - 1))) + int(gear_bonus(user, "spiritual_def_bonus"))
    if realm >= 3:
        physical_def = int(physical_def * 1.25)  # Golden Core: mortal blows shrug off
        spiritual_def = int(spiritual_def * 1.25)
    if dao == "body":
        physical_def = int(physical_def * 1.3)

    # injury penalties
    from ..core.state_machine import combat_penalties
    spd_mult, def_mult = combat_penalties(user, now)
    speed = int((vis["circulation_velocity"] + realm * 2) * spd_mult)
    physical_def = int(physical_def * def_mult)
    spiritual_def = int(spiritual_def * def_mult)

    divine_sense = vis["spiritual_sense"]
    weapon_crit = gear_bonus(user, "crit_rate_bonus")

    qi_dev_risk = max(0.0, (100 - hidden["dao_heart_stability"]) / 4.0)
    if "stabilize_5" in gear_specials(user):
        qi_dev_risk = max(0.0, qi_dev_risk - 5.0)

    return {
        "spiritual_atk": max(1, spiritual_atk),
        "physical_atk": max(1, physical_atk),
        "physical_def": max(0, physical_def),
        "spiritual_def": max(0, spiritual_def),
        "meridian_speed": max(1, speed),
        "divine_sense": divine_sense,
        "karmic_luck": hidden["karmic_luck"],
        "qi_deviation_risk": qi_dev_risk,
        "crit_bonus": weapon_crit,
        "hp": vis["physique_hp"],
        "max_hp": vis["max_hp"],
        "qi": int(cul["qi_current"]),
        "max_qi": int(cul["qi_capacity"]),
    }


def tribulation_mitigation(user: dict) -> float:
    """Sum of tribulation_mitigation_percent across gear (0..0.6 cap)."""
    return min(0.60, gear_bonus(user, "tribulation_mitigation_percent") / 100.0)

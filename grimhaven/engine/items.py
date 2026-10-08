"""Inventory, shop, equipment, herbs, pills and Dao/alignment choices (ch. 4, 6, 7)."""
from __future__ import annotations

from .constants import (
    ALIGNMENTS,
    DAO_CHOICE_REALM,
    DAO_CHOICE_STAGE,
    DAO_PATHS,
    EQUIP_SLOTS,
    HERBS,
    ITEM_TIERS,
    METHODS,
    PAVILION_PRICES,
    PILLS,
    SPIRIT_STONES,
    TECHNIQUES,
    TIER_KEYS,
)
from .models import recompute_visible_stats

# shop_id → what the pavilion delivers
SHOP_CATALOG: dict[str, dict] = {
    "stone_mid":      {"kind": "stone", "grade": "mid", "qty": 1},
    "stone_high":     {"kind": "stone", "grade": "high", "qty": 1},
    "pill_foundation": {"kind": "pill", "pill": "pill_foundation"},
    "pill_guardian":  {"kind": "pill", "pill": "pill_guardian"},
    "pill_spirit":    {"kind": "pill", "pill": "pill_spirit"},
    "root_ancient":   {"kind": "herb", "herb": "root_ancient"},
    "lotus_seven":    {"kind": "herb", "herb": "lotus_seven"},
    "method_jade_purity": {"kind": "method", "method": "method_jade_purity"},
    "method_dragon_vein": {"kind": "method", "method": "method_dragon_vein"},
    "method_star_field":  {"kind": "method", "method": "method_star_field"},
    "method_chaos_origin": {"kind": "method", "method": "method_chaos_origin"},
    "tech_sword_rain":    {"kind": "technique", "tech": "sword_rain"},
    "tech_ult_heaven_split": {"kind": "technique", "tech": "ult_heaven_split"},
    "tech_ult_blood_moon":   {"kind": "technique", "tech": "ult_blood_moon"},
    "weapon_earth":   {"kind": "gear", "gear_id": "gear_weapon_earth", "slot": "weapon", "tier": "earth"},
    "weapon_heaven":  {"kind": "gear", "gear_id": "gear_weapon_heaven", "slot": "weapon", "tier": "heaven"},
    "robe_earth":     {"kind": "gear", "gear_id": "gear_robe_earth", "slot": "robe", "tier": "earth"},
    "ring_spirit":    {"kind": "gear", "gear_id": "gear_ring_spirit", "slot": "ring", "tier": "spirit"},
    "accessory_karma": {"kind": "gear", "gear_id": "gear_accessory_spirit", "slot": "accessory", "tier": "spirit"},
    "companion_sun_orb": {"kind": "gear", "gear_id": "gear_companion_saint", "slot": "companion", "tier": "saint"},
    "doll_substitute": {"kind": "doll"},
}

GEAR_NAME_KEYS: dict[str, str] = {
    "gear_weapon_mortal": "GEAR_WEAPON_mortal",
    "gear_weapon_earth": "GEAR_WEAPON_earth",
    "gear_weapon_heaven": "GEAR_WEAPON_heaven",
    "gear_weapon_spirit": "GEAR_WEAPON_spirit",
    "gear_robe_earth": "GEAR_ROBE_earth",
    "gear_robe_heaven": "GEAR_ROBE_heaven",
    "gear_ring_spirit": "GEAR_RING_spirit",
    "gear_accessory_spirit": "GEAR_ACCESSORY_spirit",
    "gear_companion_saint": "GEAR_COMPANION_saint",
    "gear_natal_divine": "GEAR_NATAL_divine",
}


def low_stones(user: dict) -> int:
    return user["inventory"]["spirit_stones"].get("low", 0)


def pay_low_stones(user: dict, amount: int) -> bool:
    if low_stones(user) < amount:
        return False
    user["inventory"]["spirit_stones"]["low"] -= amount
    return True


def shop_locked(user: dict) -> bool:
    """The Pavilion opens at Foundation Establishment (doc §2.3)."""
    return user["cultivation"]["current_realm_index"] < 2


def buy(user: dict, shop_id: str) -> dict:
    if shop_id not in SHOP_CATALOG:
        return {"status": "UNKNOWN_ITEM"}
    price = PAVILION_PRICES[shop_id]
    item = SHOP_CATALOG[shop_id]
    if not pay_low_stones(user, price):
        return {"status": "NOT_ENOUGH_STONES", "price": price}
    inv = user["inventory"]
    if item["kind"] == "stone":
        inv["spirit_stones"][item["grade"]] += item["qty"]
    elif item["kind"] == "pill":
        inv["pills"][item["pill"]] = inv["pills"].get(item["pill"], 0) + 1
    elif item["kind"] == "herb":
        inv["herbs"][item["herb"]] = inv["herbs"].get(item["herb"], 0) + 1
    elif item["kind"] == "method":
        if item["method"] not in inv["methods"]:
            inv["methods"].append(item["method"])
    elif item["kind"] == "technique":
        if item["tech"] not in inv["techniques"]:
            inv["techniques"].append(item["tech"])
    elif item["kind"] == "gear":
        inv["gear"][item["gear_id"]] = {"slot": item["slot"], "tier": item["tier"]}
    elif item["kind"] == "doll":
        inv["gear"]["doll_substitute"] = {"slot": "trinket", "tier": "ancient"}
    return {"status": "OK", "price": price, "item": item}


def grant_item(user: dict, item_id: str, tier: str) -> dict:
    """Admin `/grant_item` — deposits gear, stones or pills directly."""
    if item_id in SPIRIT_STONES:
        user["inventory"]["spirit_stones"][item_id] += 1
        return {"status": "OK", "label_key": f"STONE_{item_id.upper()}"}
    if item_id in PILLS:
        user["inventory"]["pills"][item_id] = user["inventory"]["pills"].get(item_id, 0) + 1
        return {"status": "OK", "label_key": PILLS[item_id]["key"]}
    if item_id in METHODS:
        if item_id not in user["inventory"]["methods"]:
            user["inventory"]["methods"].append(item_id)
        return {"status": "OK", "label_key": METHODS[item_id]["key"]}
    if item_id in TECHNIQUES:
        if item_id not in user["inventory"]["techniques"]:
            user["inventory"]["techniques"].append(item_id)
        return {"status": "OK", "label_key": TECHNIQUES[item_id]["key"]}
    if tier not in ITEM_TIERS:
        tier = "mortal"
    slot = item_id if item_id in EQUIP_SLOTS else "weapon"
    gear_id = f"gear_{slot}_{tier}"
    user["inventory"]["gear"][gear_id] = {"slot": slot, "tier": tier}
    return {"status": "OK", "label_key": GEAR_NAME_KEYS.get(gear_id, gear_id)}


def equip(user: dict, gear_id: str) -> dict:
    inv = user["inventory"]
    if gear_id == "doll_substitute":
        return {"status": "NOT_EQUIPPABLE"}
    if gear_id not in inv.get("gear", {}):
        return {"status": "NOT_OWNED"}
    slot = inv["gear"][gear_id]["slot"]
    if slot not in EQUIP_SLOTS:
        return {"status": "NOT_EQUIPPABLE"}
    current = user["equipment"].get(slot)
    if current:  # swap back into the backpack
        inv["gear"][current["id"]] = {"slot": slot, "tier": current["tier"]}
    user["equipment"][slot] = {"id": gear_id, "tier": inv["gear"][gear_id]["tier"]}
    del inv["gear"][gear_id]
    recompute_visible_stats(user)
    from .cultivation import CultivationEngine
    CultivationEngine._reveal_stats_if_due(user)
    return {"status": "OK", "slot": slot}


def unequip(user: dict, slot: str) -> dict:
    current = user["equipment"].get(slot)
    if not current:
        return {"status": "EMPTY_SLOT"}
    if slot == "natal":  # natal artifacts are soul-bound
        return {"status": "NOT_EQUIPPABLE"}
    user["inventory"]["gear"][current["id"]] = {"slot": slot, "tier": current["tier"]}
    user["equipment"][slot] = None
    recompute_visible_stats(user)
    return {"status": "OK"}


def activate_stone(user: dict, grade: str) -> dict:
    if grade not in SPIRIT_STONES:
        return {"status": "UNKNOWN"}
    if user["inventory"]["spirit_stones"].get(grade, 0) <= 0:
        return {"status": "NO_STONES"}
    user["cultivation"]["active_stone"] = grade
    return {"status": "OK", "boost": SPIRIT_STONES[grade]["boost"]}


def use_pill(user: dict, pill_id: str) -> dict:
    pills = user["inventory"].get("pills", {})
    if pill_id not in PILLS or pills.get(pill_id, 0) <= 0:
        return {"status": "NO_PILL"}
    pills[pill_id] -= 1
    user["cultivation"]["active_pill"] = pill_id
    return {"status": "OK", "bonus": PILLS[pill_id]["bonus"]}


def use_herb(user: dict, herb_id: str) -> dict:
    herbs = user["inventory"].get("herbs", {})
    if herb_id not in HERBS or herbs.get(herb_id, 0) <= 0:
        return {"status": "NO_HERB"}
    herbs[herb_id] -= 1
    if HERBS[herb_id]["effect"] == "circulation_perm":
        herbs["root_used"] = True
        return {"status": "ROOT"}
    return {"status": "LOTUS"}


def activate_method(user: dict, method_id: str) -> dict:
    if method_id not in user["inventory"]["methods"]:
        return {"status": "NOT_LEARNED"}
    user["cultivation"]["active_method_id"] = method_id
    recompute_visible_stats(user)
    return {"status": "OK", "tech_mult": METHODS[method_id]["tech_mult"]}


def set_loadout(user: dict, slot_index: int, tech_id: str | None) -> dict:
    if not 1 <= slot_index <= 4:
        return {"status": "BAD_SLOT"}
    if tech_id is not None:
        if tech_id not in user["inventory"]["techniques"]:
            return {"status": "NOT_LEARNED"}
        expected = TECHNIQUES[tech_id]["slot"]
        if expected != slot_index:
            return {"status": "BAD_SLOT"}
    user["combat"]["loadout"][slot_index - 1] = tech_id
    return {"status": "OK"}


def choose_dao(user: dict, dao: str, alignment: str | None = None) -> dict:
    """The irreversible choice at the end of Qi Condensation (doc §6)."""
    cul = user["cultivation"]
    if cul.get("dao_path"):
        return {"status": "ALREADY_CHOSEN", "dao": cul["dao_path"]}
    if dao not in DAO_PATHS:
        return {"status": "UNKNOWN_DAO"}
    if alignment and alignment not in ALIGNMENTS:
        return {"status": "UNKNOWN_ALIGNMENT"}
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    if not (realm > DAO_CHOICE_REALM or (realm == DAO_CHOICE_REALM and stage >= DAO_CHOICE_STAGE)):
        return {"status": "TOO_EARLY"}
    cul["dao_path"] = dao
    if alignment:
        cul["alignment"] = alignment
        cul["alignment_locked"] = True
    if dao == "blood":  # the Blood Dao is inherently dark
        cul["alignment"] = "demonic"
        cul["alignment_locked"] = True
    if dao == "alchemy":  # gift of the life dao: a guardian pill
        user["inventory"]["pills"]["pill_guardian"] = \
            user["inventory"]["pills"].get("pill_guardian", 0) + 1
    recompute_visible_stats(user)
    return {"status": "OK", "dao": dao, "alignment": cul["alignment"]}

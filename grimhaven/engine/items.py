"""Inventory, shop, equipment, pills/herbs/talismans, Dao & alignment choices.

Part of the overhaul: every catalogue entry now comes from the data registry
(data/equipment.json, data/consumables.json, data/martial_arts.json) instead
of hardcoded dicts — the shop, loot and offline events share one item ledger.
"""
from __future__ import annotations

import datetime as dt

from ..core.data_loader import data_registry
from ..core.state_machine import UserStatus, set_status, paralysis_active
from .constants import (
    ALIGNMENTS,
    DAO_CHOICE_REALM,
    DAO_CHOICE_STAGE,
    DAO_PATHS,
    EQUIP_SLOTS,
    ITEM_TIERS,
    METHODS,
    SPIRIT_STONES,
    TIER_KEYS,
    TIER_POWER,
)
from .models import (
    add_item,
    consume_item,
    max_slots,
    recompute_visible_stats,
    ring_has_room,
)

# ── ledger helpers ───────────────────────────────────────────────────────────

def count_item(user: dict, item_id: str) -> int:
    return int(user["inventory"].get("items", {}).get(item_id, 0))


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


# ── shop ─────────────────────────────────────────────────────────────────────

def shop_entries(user: dict) -> list[dict]:
    """Catalogue rows: [{shop_id, price, label fields, kind}…] — only what the
    player may buy at their realm, with owned manuals/methods filtered out."""
    from ..engine.constants import PAVILION_PRICES
    entries: list[dict] = []
    realm = user["cultivation"]["current_realm_index"]
    inv = user["inventory"]

    for art_id, art in data_registry.martial_arts.items():
        if art_id in inv.get("arts", []):
            continue
        if art.required_realm > realm:
            continue
        entries.append({"shop_id": f"art:{art_id}", "kind": "art", "ref": art_id,
                        "price": art.price_stones})
    for item_id, item in data_registry.consumables.items():
        if item.price_stones is None:
            continue
        entries.append({"shop_id": f"item:{item_id}", "kind": "item", "ref": item_id,
                        "price": int(item.price_stones)})
    for item_id, item in data_registry.equipment.items():
        if item.price_stones is None:
            continue
        entries.append({"shop_id": f"equip:{item_id}", "kind": "equip", "ref": item_id,
                        "price": int(item.price_stones)})
    for m_id, m in METHODS.items():
        if m_id in inv.get("methods", []):
            continue
        price = PAVILION_PRICES.get(m_id)
        if price:
            entries.append({"shop_id": f"method:{m_id}", "kind": "method", "ref": m_id,
                            "price": price})
    entries.sort(key=lambda e: (e["kind"], e["price"]))
    return entries


def buy(user: dict, shop_id: str) -> dict:
    kind, _, ref = shop_id.partition(":")
    lang = user["account"]["language"]
    if kind == "art":
        art = data_registry.get_martial_art(ref)
        if not art:
            return {"status": "UNKNOWN_ITEM"}
        if ref in user["inventory"]["arts"]:
            return {"status": "ALREADY_OWNED"}
        if art.required_realm > user["cultivation"]["current_realm_index"]:
            return {"status": "REALM_LOCKED", "realm": art.required_realm}
        if not pay_low_stones(user, art.price_stones):
            return {"status": "NOT_ENOUGH_STONES", "price": art.price_stones}
        user["inventory"]["arts"].append(ref)
        auto_fit_deck(user)
        return {"status": "OK", "price": art.price_stones, "label": art.name_for(lang)}
    if kind == "item":
        item = data_registry.get_consumable(ref)
        if not item or item.price_stones is None:
            return {"status": "UNKNOWN_ITEM"}
        if not pay_low_stones(user, int(item.price_stones)):
            return {"status": "NOT_ENOUGH_STONES", "price": int(item.price_stones)}
        add_item(user, ref)
        return {"status": "OK", "price": int(item.price_stones), "label": item.name_for(lang)}
    if kind == "equip":
        item = data_registry.get_equipment(ref)
        if not item or item.price_stones is None:
            return {"status": "UNKNOWN_ITEM"}
        if not pay_low_stones(user, int(item.price_stones)):
            return {"status": "NOT_ENOUGH_STONES", "price": int(item.price_stones)}
        add_item(user, ref, is_gear=True)
        return {"status": "OK", "price": int(item.price_stones), "label": item.name_for(lang)}
    if kind == "method":
        from ..engine.constants import PAVILION_PRICES
        price = PAVILION_PRICES.get(ref)
        if ref not in METHODS or price is None:
            return {"status": "UNKNOWN_ITEM"}
        if not pay_low_stones(user, price):
            return {"status": "NOT_ENOUGH_STONES", "price": price}
        if ref not in user["inventory"]["methods"]:
            user["inventory"]["methods"].append(ref)
        recompute_visible_stats(user)
        from ..localization import t
        return {"status": "OK", "price": price, "label": t(lang, METHODS[ref]["key"])}
    return {"status": "UNKNOWN_ITEM"}


def sell(user: dict, kind: str, item_id: str) -> dict:
    """Sell a gear piece or a stack of consumables back to the Pavilion."""
    lang = user["account"]["language"]
    inv = user["inventory"]
    if kind == "gear" and item_id in inv.get("gear", {}):
        item = data_registry.get_equipment(item_id)
        price = (item.sell_price if item and item.sell_price else
                 10 * (TIER_POWER.get(item.tier if item else "mortal", 0) + 1))
        if item and item.slot == "natal":
            return {"status": "SOUL_BOUND"}
        del inv["gear"][item_id]
        inv["spirit_stones"]["low"] += price
        return {"status": "OK", "price": price, "label": item.name_for(lang) if item else item_id}
    if kind == "item":
        item = data_registry.get_consumable(item_id)
        if not item or count_item(user, item_id) <= 0:
            return {"status": "UNKNOWN_ITEM"}
        price = item.sell_price or max(1, int((item.price_stones or 20) * 0.35))
        qty = item_id
        inv["items"][qty] -= 1
        if inv["items"][qty] <= 0:
            inv["items"].pop(qty)
        inv["spirit_stones"]["low"] += price
        return {"status": "OK", "price": price, "label": item.name_for(lang)}
    return {"status": "UNKNOWN_ITEM"}


# ── use / equip ──────────────────────────────────────────────────────────────

def grant_item(user: dict, item_id: str, tier: str = "mortal") -> dict:
    """Admin /grant_item — accepts spirit-stone grades, item ids from the data
    files, `art:<id>` manuals, methods, or a bare equipment slot + tier."""
    lang = user["account"]["language"]
    if item_id in SPIRIT_STONES:
        user["inventory"]["spirit_stones"][item_id] += 1
        return {"status": "OK", "label": t_stone(lang, item_id)}
    if item_id.startswith("art:"):
        art = data_registry.get_martial_art(item_id[4:])
        if art and art.art_id not in user["inventory"]["arts"]:
            user["inventory"]["arts"].append(art.art_id)
            auto_fit_deck(user)
            return {"status": "OK", "label": art.name_for(lang)}
        return {"status": "ALREADY_OWNED" if art else "UNKNOWN_ITEM", "label": item_id}
    if item_id in data_registry.consumables:
        add_item(user, item_id)
        return {"status": "OK", "label": data_registry.consumables[item_id].name_for(lang)}
    if item_id in data_registry.equipment:
        add_item(user, item_id, is_gear=True)
        return {"status": "OK", "label": data_registry.equipment[item_id].name_for(lang)}
    if item_id in METHODS:
        if item_id not in user["inventory"]["methods"]:
            user["inventory"]["methods"].append(item_id)
        from ..localization import t
        return {"status": "OK", "label": t(lang, METHODS[item_id]["key"])}
    # slot + tier shorthand (weapon/robe/…)
    from .constants import ITEM_TIERS
    if tier not in ITEM_TIERS:
        tier = "mortal"
    for eq in data_registry.equipment.values():
        if eq.slot == item_id and eq.tier == tier:
            add_item(user, eq.item_id, is_gear=True)
            return {"status": "OK", "label": eq.name_for(lang)}
    return {"status": "UNKNOWN_ITEM", "label": item_id}


def t_stone(lang: str, grade: str) -> str:
    from ..localization import t
    return t(lang, f"STONE_{grade.upper()}")


def use_item(user: dict, item_id: str, now: dt.datetime | None = None) -> dict:
    """Consume one item outside of battle; successful use consumes one stack.
    Combat-only items (talismans & battle pills) are used via the combat keys."""
    lang = user["account"]["language"]
    item = data_registry.get_consumable(item_id)
    if not item or count_item(user, item_id) <= 0:
        return {"status": "NONE_LEFT", "label": item_id}
    if user["combat"].get("session"):
        return {"status": "IN_COMBAT"}
    cul = user["cultivation"]
    vis = user["stats"]["visible"]
    action = item.action

    def ok(key: str, value=None):
        consume_item(user, item_id)
        out = {"status": "OK", "key": key, "label": item.name_for(lang)}
        if value is not None:
            out["value"] = value
        return out

    if action == "restore_qi":
        room = cul["qi_capacity"] - cul["qi_current"]
        gained = min(max(0, int(item.value)), room)
        if gained <= 0:
            return {"status": "QI_FULL"}
        cul["qi_current"] += gained
        user["progress"]["qi_total_accumulated"] += gained
        return ok("USE_QI", gained)
    if action in ("heal_hp", "heal_and_cure"):
        heal = min(item.value, vis["max_hp"] - vis["physique_hp"])
        if action == "heal_hp" and heal <= 0:
            return {"status": "FULL_HP"}
        vis["physique_hp"] += heal
        if action == "heal_and_cure":
            user["combat"]["injury"] = None
            user["combat"]["paralysis_until"] = None
            set_status(user, UserStatus.IDLE)
            return ok("USE_HEAL_CURE", heal)
        return ok("USE_HEAL", heal)
    if action == "breakthrough_bonus":
        cul["active_pill"] = item_id
        return ok("USE_PILL_ARMED", item.value)
    if action == "core_stability":
        if cul["current_realm_index"] != 3:
            return {"status": "WRONG_REALM", "label": item.name_for(lang)}
        return ok("USE_LOTUS")
    if action == "permanent_qi_rate":
        if user["progress"].get("root_ancient_used"):
            return {"status": "ALREADY_USED"}
        user["progress"]["root_ancient_used"] = True
        return ok("USE_ROOT", item.value)
    if action == "cure_paralysis":
        if not paralysis_active(user, now) and not user["combat"].get("injury"):
            return {"status": "NOT_INJURED"}
        user["combat"]["paralysis_until"] = None
        user["combat"]["injury"] = None
        set_status(user, UserStatus.IDLE)
        return ok("USE_CURE")
    if action in ("combat_damage_buff", "instant_damage", "shield", "enemy_debuff"):
        return {"status": "COMBAT_ONLY", "label": item.name_for(lang)}
    if action in ("sell_only", "auto_revive"):
        return {"status": "NOT_USABLE", "label": item.name_for(lang)}
    return {"status": "UNKNOWN_ACTION"}


def equip(user: dict, gear_id: str) -> dict:
    from ..core.data_loader import data_registry as reg
    inv = user["inventory"]
    if gear_id not in inv.get("gear", {}):
        return {"status": "NOT_OWNED"}
    item = reg.get_equipment(gear_id)
    if not item or item.slot not in EQUIP_SLOTS:
        return {"status": "NOT_EQUIPPABLE"}
    slot = item.slot
    current = user["equipment"].get(slot)
    if current:
        inv["gear"][current["id"]] = {"dur": current.get("dur", 100)}
    user["equipment"][slot] = {"id": gear_id, "dur": inv["gear"][gear_id].get("dur", 100),
                                "tier": item.tier}
    del inv["gear"][gear_id]
    recompute_visible_stats(user)
    from .cultivation import CultivationEngine
    CultivationEngine._reveal_stats_if_due(user)
    return {"status": "OK", "slot": slot, "label": item.name_for(user["account"]["language"])}


def unequip(user: dict, slot: str) -> dict:
    current = user["equipment"].get(slot)
    if not current:
        return {"status": "EMPTY_SLOT"}
    if slot == "natal":
        return {"status": "SOUL_BOUND"}
    item = data_registry.get_equipment(current["id"])
    if item and item.slot == "natal":
        return {"status": "SOUL_BOUND"}
    user["inventory"]["gear"][current["id"]] = {"dur": current.get("dur", 100)}
    user["equipment"][slot] = None
    recompute_visible_stats(user)
    return {"status": "OK"}


def repair_gear(user: dict, gear_id: str) -> dict:
    """Pavilion smithy: restore durability for spirit stones (~½ stone/point).
    Works on bagged and equipped artifacts alike."""
    gear = user["inventory"]["gear"].get(gear_id)
    if not gear:
        for slot_gear in user["equipment"].values():
            if slot_gear and slot_gear.get("id") == gear_id:
                gear = slot_gear
                break
    if not gear:
        return {"status": "UNKNOWN_ITEM"}
    missing = 100 - int(gear.get("dur", 100))
    if missing <= 0:
        return {"status": "ALREADY_PERFECT"}
    price = max(2, missing // 2)
    if not pay_low_stones(user, price):
        return {"status": "NOT_ENOUGH_STONES", "price": price}
    gear["dur"] = 100
    recompute_visible_stats(user)
    return {"status": "OK", "price": price}


# ── spirit stones / methods / dao (kept from the original engine) ────────────

def activate_stone(user: dict, grade: str) -> dict:
    if grade not in SPIRIT_STONES:
        return {"status": "UNKNOWN"}
    if user["inventory"]["spirit_stones"].get(grade, 0) <= 0:
        return {"status": "NO_STONES"}
    user["cultivation"]["active_stone"] = grade
    return {"status": "OK", "boost": SPIRIT_STONES[grade]["boost"]}


def activate_method(user: dict, method_id: str) -> dict:
    if method_id not in user["inventory"]["methods"]:
        return {"status": "NOT_LEARNED"}
    user["cultivation"]["active_method_id"] = method_id
    recompute_visible_stats(user)
    return {"status": "OK", "tech_mult": METHODS[method_id]["tech_mult"]}


# ── deck builder (Part 2 §2, Part 3 §4) ─────────────────────────────────────

def owned_techniques(user: dict) -> list[str]:
    out: list[str] = []
    for art_id in user["inventory"].get("arts", []):
        art = data_registry.get_martial_art(art_id)
        if art:
            out.extend(t.id for t in art.techniques)
    return out


def set_loadout(user: dict, tech_id: str, slot_index: int | None = None) -> dict:
    """Equip a known technique into a deck slot; slot auto-picks the first
    empty one when omitted. Respects the realm-gated slot cap (3 → 6)."""
    if tech_id is None:
        if slot_index is None or not 0 <= slot_index < max_slots(user):
            return {"status": "BAD_SLOT"}
        loadout = user["combat"]["loadout"]
        if slot_index < len(loadout):
            loadout[slot_index] = None
            _compact(user)
        return {"status": "OK", "cleared": slot_index}
    if tech_id not in owned_techniques(user):
        return {"status": "NOT_LEARNED"}
    loadout = user["combat"]["loadout"]
    if tech_id in loadout:  # move, not duplicate
        loadout.remove(tech_id)
    cap = max_slots(user)
    if slot_index is not None and 0 <= slot_index < cap:
        while len(loadout) <= slot_index:
            loadout.append(None)
        loadout[slot_index] = tech_id
    else:
        if len([x for x in loadout if x]) >= cap:
            return {"status": "DECK_FULL", "cap": cap}
        first_empty = next((i for i, x in enumerate(loadout) if x is None), len(loadout))
        while len(loadout) <= first_empty:
            loadout.append(None)
        loadout[first_empty] = tech_id
    _compact(user)
    return {"status": "OK", "tech": tech_id}


def _compact(user: dict) -> None:
    loadout = user["combat"]["loadout"]
    while loadout and loadout[-1] is None:
        loadout.pop()
    cap = max_slots(user)
    user["combat"]["loadout"] = loadout[:cap]


def auto_fit_deck(user: dict) -> None:
    """When a new manual is bought, top up empty slots automatically."""
    loadout = user["combat"]["loadout"]
    owned = owned_techniques(user)
    for tid in owned:
        if tid not in loadout and len([x for x in loadout if x]) < max_slots(user):
            loadout.append(tid)
    _compact(user)


def learn_art(user: dict, art_id: str) -> dict:
    """Free-claim a manual: register ownership + auto-fit the deck."""
    art = data_registry.get_martial_art(art_id)
    if not art:
        return {"status": "UNKNOWN"}
    arts = user["inventory"].setdefault("arts", [])
    if art_id in arts:
        return {"status": "ALREADY_LEARNED"}
    arts.append(art_id)
    auto_fit_deck(user)
    return {"status": "OK", "art": art.name_for(user["account"]["language"])}


# ── Dao & alignment ──────────────────────────────────────────────────────────

def choose_dao(user: dict, dao: str, alignment: str | None = None) -> dict:
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
    if dao == "blood":
        cul["alignment"] = "demonic"
        cul["alignment_locked"] = True
    if dao == "alchemy":
        add_item(user, "pill_guardian")
    recompute_visible_stats(user)
    return {"status": "OK", "dao": dao, "alignment": cul["alignment"]}


def gear_label(user: dict, gear: dict | None) -> tuple[str, str]:
    """(name, tier-key) for an equipped/bagged gear slot."""
    lang = user["account"]["language"]
    if not gear:
        return "—", ""
    item = data_registry.get_equipment(gear.get("id", ""))
    if not item:
        return gear.get("id", "?"), ""
    return item.name_for(lang), TIER_KEYS.get(item.tier, "")


def tier_power(tier: str) -> int:
    return TIER_POWER.get(tier, 0)


# ── liberated pavilion free booths & battle slots (spec P5 §4.4–4.6 / P2) ────

BOOTH_CAP_PER_CLAIM = 5
BOOTH_CATALOGS = {
    "pills": ["pill_marrow_wash", "pill_spirit_gather_low", "pill_vitality_refine", "pill_guardian"],
    "gear": ["wpn_rusty_spirit_dagger", "wpn_iron_leaf", "robe_hemp", "ring_canvas"],
    "talismans": ["talisman_crimson_thunder", "talisman_gale_blade",
                  "talisman_iron_wall", "talisman_seal_flame"],
}


def booth_entries(user: dict, booth: str) -> list[dict]:
    """Item list offered by a free testing booth (data-driven, price 0)."""
    from ..core.data_loader import data_registry
    out = []
    for iid in BOOTH_CATALOGS.get(booth, []):
        item = data_registry.get_consumable(iid) or data_registry.get_equipment(iid)
        if not item:
            continue
        owned = count_item(user, iid) if not data_registry.get_equipment(iid) else \
            (1 if iid in (user["inventory"].get("gear") or {}) else 0)
        out.append({"item_id": iid, "name": item.name_for(user.get("account", {}).get("language", "fa")),
                    "price": 0, "owned": owned})
    return out


def booth_claim(user: dict, item_id: str, cap: int = BOOTH_CAP_PER_CLAIM,
                booth: str | None = None) -> int:
    """Free claim, capped per claim to keep ledgers sane. Returns granted qty.

    Only items listed in the booth's catalog (or, when ``booth`` is None, in any
    booth) may be claimed — otherwise players could mint any shop item for free.
    """
    from ..core.data_loader import data_registry
    if not (data_registry.get_equipment(item_id) or data_registry.get_consumable(item_id)):
        return 0  # unknown item — never mint phantom ring entries
    if booth is not None:
        if item_id not in BOOTH_CATALOGS.get(booth, []):
            return 0
    elif not any(item_id in ids for ids in BOOTH_CATALOGS.values()):
        return 0
    if data_registry.get_equipment(item_id):
        gear = user["inventory"].setdefault("gear", {})
        if item_id not in gear:
            gear[item_id] = {"dur": 100}
            return 1
        return 0
    have = int(user["inventory"].get("items", {}).get(item_id, 0))
    granted = max(0, min(cap, cap * 2 - have))  # top-up to 2×cap ceiling
    if granted:
        add_item(user, item_id, granted)
    return granted


def set_battle_item(user: dict, item_id: str) -> str:
    """Assign a usable item to the battle keyboard's first free slot."""
    from ..core.data_loader import data_registry
    item = data_registry.get_consumable(item_id)
    if not item:
        return "ERR_UNKNOWN"
    if not user["combat"].get("item_slots"):
        user["combat"]["item_slots"] = []
    slots = user["combat"]["item_slots"]
    if item_id in slots:
        return "OK"
    if len([s for s in slots if s]) >= 2:
        slots[0] = item_id
    else:
        while len(slots) < 2:
            slots.append(None)
        for i in range(2):
            if not slots[i]:
                slots[i] = item_id
                break
    return "OK"

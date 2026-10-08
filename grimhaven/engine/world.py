"""World systems — zones, travel, vein conquest, sects, sacrifices (ch. 1, 5, 6)."""
from __future__ import annotations

import datetime as dt

from .constants import (
    CORRUPTION_VISIBLE_AT,
    DEFAULT_ZONE,
    NPC_SECTS,
    SACRIFICES,
    SECT_FOUND_REALM,
    ZONES,
)
from .models import in_seclusion, iso, meridians_sealed, parse_iso, utcnow

MONSTERS_PER_GUARD = {
    1: ["wild_boar", "hungry_wolf"],
    2: ["rock_serpent", "mist_panther"],
    3: ["blood_croc", "marsh_wraith"],
    4: ["spring_serpent_king", "jade_guardian"],
    7: ["thunder_roc", "storm_qilin"],
}


def zone_doc(storage, zone_id: str) -> dict:
    doc = storage.get_zone(zone_id)
    if doc is None:
        z = ZONES[zone_id]
        doc = {
            "zone_id": zone_id, "key": z["key"], "vein_density": z["vein_density"],
            "min_realm": z["min_realm"], "guard_level": z["guard"],
            "owner": None, "owner_kind": None, "conquered_at": None,
        }
        storage.save_zone(doc)
    return doc


def travel(storage, user: dict, zone_id: str, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    if zone_id not in ZONES:
        return {"status": "UNKNOWN_ZONE"}
    if in_seclusion(user, now):
        return {"status": "SECLUSION_BLOCKED"}
    zdef = ZONES[zone_id]
    if user["cultivation"]["current_realm_index"] < zdef["min_realm"]:
        return {"status": "ZONE_LOCKED_REALM", "min_realm": zdef["min_realm"]}
    zone = zone_doc(storage, zone_id)
    user["location"]["current_zone_id"] = zone_id
    user["location"]["vein_density"] = zone["vein_density"]
    return {"status": "OK", "zone": zone}


def zone_owner_label(zone: dict, user_id: int | None, storage) -> tuple[str, str | None]:
    """Returns (kind, sect_key_or_None); kind in: none|you|user|sect."""
    if not zone.get("owner"):
        return "none", None
    if zone["owner_kind"] == "user" and zone["owner"] == user_id:
        return "you", None
    if zone["owner_kind"] == "sect":
        sect = storage.get_sect(zone["owner"])
        if sect:
            return "sect", sect.get("key") or sect["sect_id"]
    return "user", None


def conquest_battle_power(guard_level: int) -> dict:
    """Guardian stat block — scales with zone tier."""
    return {
        "hp": 90 * guard_level * guard_level + 60,
        "atk": 14 * guard_level + 6,
        "def": 6 * guard_level,
        "name_key": f"GUARDIAN_{guard_level}",
    }


def apply_conquest_result(storage, user: dict, zone_id: str, won: bool,
                          now: dt.datetime | None = None) -> dict:
    zone = zone_doc(storage, zone_id)
    if not won:
        return {"status": "DEFEAT"}
    zone["owner"] = user["user_id"]
    zone["owner_kind"] = "user"
    zone["conquered_at"] = iso(now or utcnow())
    storage.save_zone(zone)
    # ownership grants a tithe: +0.25 effective density for the owner
    if user["location"]["current_zone_id"] == zone_id:
        user["location"]["vein_density"] = zone["vein_density"]
    return {"status": "VICTORY", "zone": zone}


# ── sects ────────────────────────────────────────────────────────────────────

def join_sect(storage, user: dict, sect_id: str) -> dict:
    if user["cultivation"]["current_realm_index"] < 2:
        return {"status": "SECT_LOCKED"}
    sect = storage.get_sect(sect_id)
    if sect is None:
        return {"status": "UNKNOWN_SECT"}
    if sect.get("alignment") != user["cultivation"]["alignment"] and sect.get("npc"):
        return {"status": "ALIGNMENT_MISMATCH"}
    # leave any previous sect
    leave_sect(storage, user)
    sect.setdefault("members", []).append(user["user_id"])
    storage.save_sect(sect)
    user["location"]["sect_id"] = sect_id
    return {"status": "OK", "sect": sect}


def leave_sect(storage, user: dict) -> dict:
    sect_id = user["location"].get("sect_id")
    if not sect_id:
        return {"status": "NO_SECT"}
    sect = storage.get_sect(sect_id)
    if sect and user["user_id"] in sect.get("members", []):
        sect["members"].remove(user["user_id"])
        storage.save_sect(sect)
    user["location"]["sect_id"] = None
    return {"status": "OK"}


def found_sect(storage, user: dict, name: str | None = None) -> dict:
    if user["cultivation"]["current_realm_index"] < SECT_FOUND_REALM:
        return {"status": "SECT_FOUND_LOCKED"}
    sect_id = f"sect_player_{user['user_id']}"
    leave_sect(storage, user)
    sect = {
        "sect_id": sect_id,
        "key": None,
        "name": name or f"Sect of {user['account']['username']}",
        "alignment": user["cultivation"]["alignment"],
        "npc": False,
        "master": user["user_id"],
        "members": [user["user_id"]],
        "treasury": 0,
    }
    storage.save_sect(sect)
    user["location"]["sect_id"] = sect_id
    return {"status": "OK", "sect": sect}


# ── demonic sacrifices (doc §5.1.4 & §6.1) ───────────────────────────────────

def perform_sacrifice(user: dict, kind: str, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    if kind not in SACRIFICES:
        return {"status": "UNKNOWN"}
    cul = user["cultivation"]
    if cul["alignment"] != "demonic":
        # Orthodox attempting blood arts: the Dantian detonates (doc §6.1)
        lost = int(cul["qi_current"] * 0.5)
        cul["qi_current"] -= lost
        return {"status": "DANTIAN_EXPLOSION", "qi_lost": lost}
    spec = SACRIFICES[kind]
    cul.setdefault("active_stone", cul.get("active_stone"))
    user["buffs"] = [b for b in user.get("buffs", [])
                     if (parse_iso(b.get("until")) or now) > now]
    user["buffs"].append({
        "key": spec["key"], "boost": spec["boost"],
        "until": iso(now + dt.timedelta(hours=spec["hours"])),
    })
    hidden = user["stats"]["hidden"]
    hidden["demonic_corruption"] = min(100, hidden["demonic_corruption"] + spec["corruption"])
    hidden["karmic_luck"] = max(0, hidden["karmic_luck"] + spec["karma"])
    result = {"status": "OK", "boost": spec["boost"], "hours": spec["hours"],
              "corruption": hidden["demonic_corruption"]}
    if hidden["demonic_corruption"] >= CORRUPTION_VISIBLE_AT:
        result["aura_revealed"] = True
    # devouring souls attracts the agents of Heavenly Justice
    if kind == "devour":
        result["enforcer_damage"] = int(user["stats"]["visible"]["max_hp"] * 0.15)
        user["stats"]["visible"]["physique_hp"] = max(
            1, user["stats"]["visible"]["physique_hp"] - result["enforcer_damage"])
    return result

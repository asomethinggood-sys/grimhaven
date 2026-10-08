"""User document model — mirrors the MongoDB/JSONB schema of design doc §10.1,
plus the extensions needed by the playable systems."""
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
    STARTING_ITEMS,
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


def new_user_doc(user_id: int, username: str = "", language: str = "fa") -> dict:
    """Brand-new cultivator: Qi Condensation, layer 1, empty Dantian."""
    return {
        "_id": f"tg_user_{user_id}",
        "user_id": user_id,
        "account": {
            "username": username or f"cultivator_{user_id}",
            "language": language,
            "is_banned": False,
            "registered_at": iso(),
            "is_admin": False,
        },
        "cultivation": {
            "current_realm_index": 1,
            "current_stage": 0,          # 0-based index into REALM_STAGES[realm]
            "qi_current": 0,
            "qi_capacity": REALM_STAGES[1][0],
            "dao_path": None,            # chosen at the end of Qi Condensation
            "alignment": "orthodox",     # orthodox | demonic
            "alignment_locked": False,   # becomes True once chosen freely
            "active_method_id": "method_breath_mortal",
            "active_stone": None,        # low | mid | high | heavenly | None
            "active_pill": None,         # consumed on next breakthrough
            "meditating": False,
            "meditation_started_at": None,
            "seclusion_finish_time": None,
            "seclusion_target": None,    # {"realm": r, "stage": s}
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
        "inventory": copy.deepcopy(STARTING_ITEMS),
        "equipment": {slot: None for slot in EQUIP_SLOTS},
        "combat": {
            "loadout": ["basic_strike", "iron_guard", "element_burst", None],
            "wins": 0,
            "losses": 0,
        },
        "location": {
            "current_zone_id": "zone_mortal_valley",
            "vein_density": 1.0,
            "sect_id": None,
        },
        "buffs": [],  # [{ "key":..., "boost":1.0, "until": iso }]
        "progress": {
            "qi_total_accumulated": 0,
            "breakthrough_attempts": 0,
            "breakthrough_successes": 0,
            "failures_minor": 0,
            "failures_deviation": 0,
            "failures_annihilation": 0,
            "encounters_found": 0,
        },
    }


# ── derived stats ────────────────────────────────────────────────────────────

def stage_cost(user: dict) -> int:
    cul = user["cultivation"]
    realm = cul["current_realm_index"]
    stage = cul["current_stage"]
    stages = REALM_STAGES[realm]
    return stages[min(stage, len(stages) - 1)]


def hp_multiplier(user: dict) -> float:
    """Cumulative ability-spike multipliers of every realm entered (doc §2.3)."""
    realm = user["cultivation"]["current_realm_index"]
    mult = 1.0
    for r in range(2, realm + 1):
        mult *= REALM_ENTRY_SPIKES.get(r, (1.0, 1.0))[0]
    return mult


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
    gear_bonus = 0.0
    for slot, gear in user.get("equipment", {}).items():
        if gear and slot in ("robe", "companion", "natal"):
            from .constants import TIER_POWER
            gear_bonus += 0.05 * (1 + TIER_POWER[gear["tier"]])
    max_hp = int(base_hp * (1.0 + gear_bonus))
    vis = user["stats"]["visible"]
    vis["max_hp"] = max_hp
    vis["physique_hp"] = min(vis.get("physique_hp", max_hp), max_hp) or max_hp
    vis["spiritual_sense"] = int(10 * (1.5 ** (realm - 1)) * (1 + 0.05 * stage))
    method = METHODS.get(user["cultivation"].get("active_method_id", ""), {})
    vis["circulation_velocity"] = int(10 + realm * 5 + stage + method.get("tech_mult", 1.0))


def afk_hourly_rate(user: dict, world_boost: float = 1.0, now: dt.datetime | None = None) -> float:
    """Doc §5 master formula:
    Qi/h = [Base_realm × Tech_mult × (1 + Catalyst_boost) × Vein_density] × (1 + Luck_bonus)
    """
    now = now or utcnow()
    cul = user["cultivation"]
    realm = cul["current_realm_index"]
    base = BASE_RATES.get(realm, 100)
    tech = METHODS.get(cul.get("active_method_id", ""), {}).get("tech_mult", 1.0)

    catalyst = 0.0
    stone = cul.get("active_stone")
    if stone:
        from .constants import SPIRIT_STONES
        catalyst += SPIRIT_STONES[stone]["boost"]
    for herb, data in (("root_ancient", {"boost": 0.25}),):
        if user["inventory"].get("herbs", {}).get("root_used"):
            catalyst += data["boost"]
    for buff in user.get("buffs", []):
        until = parse_iso(buff.get("until"))
        if until and until > now:
            catalyst += float(buff.get("boost", 0.0))
    if cul["alignment"] == "demonic":
        # Demonic path: up to ×3 AFK speed (doc §6.1), at the price of corruption.
        catalyst = min(1.0 + catalyst, DEMONIC_AFK_MULT_CAP) - 1.0

    vein = user["location"].get("vein_density", 1.0)
    luck_bonus = user["stats"]["hidden"]["karmic_luck"] * 0.002
    rate = base * tech * (1.0 + catalyst) * vein
    rate *= (1.0 + luck_bonus) * world_boost
    if _meridians_sealed(user, now):
        rate *= 0.5  # doc §3.2: sealed disciples run at 50% efficiency
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

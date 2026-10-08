"""Turn-based combat engine (doc §7.3 & §2.3 ability spikes).

Duels are deterministic given an RNG seed: turn order by Qi circulation
velocity, techniques from the 4-slot loadout, crit/guard/evasion effects,
Dao path modifiers and gear tier bonuses.
"""
from __future__ import annotations

import random

from .constants import BLOOD_DAO_QI_STEAL, DAO_PATHS, TECHNIQUES, TIER_POWER
from .models import in_seclusion, meridians_sealed, recompute_visible_stats, utcnow

MONSTER_NAMES = {
    "wild_boar": "MONSTER_WILD_BOAR",
    "hungry_wolf": "MONSTER_HUNGRY_WOLF",
    "rock_serpent": "MONSTER_ROCK_SERPENT",
    "mist_panther": "MONSTER_MIST_PANTHER",
    "blood_croc": "MONSTER_BLOOD_CROC",
    "marsh_wraith": "MONSTER_MARSH_WRAITH",
    "spring_serpent_king": "MONSTER_SERPENT_KING",
    "jade_guardian": "MONSTER_JADE_GUARDIAN",
    "thunder_roc": "MONSTER_THUNDER_ROC",
    "storm_qilin": "MONSTER_STORM_QILIN",
}


def player_combat_stats(user: dict) -> dict:
    """Attack/defence derived from realm, Dao, gear and HP."""
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis = user["stats"]["visible"]
    atk = 8.0 * (1.6 ** (realm - 1)) + stage * 2
    defence = 3.0 * (1.5 ** (realm - 1))
    crit = 0.05
    dao = cul.get("dao_path")
    if dao and dao in DAO_PATHS:
        atk *= DAO_PATHS[dao]["atk"]
        defence *= DAO_PATHS[dao]["def"]
        crit += DAO_PATHS[dao]["crit"]
    for slot, gear in user.get("equipment", {}).items():
        if not gear:
            continue
        power = TIER_POWER[gear["tier"]]
        if slot == "weapon":
            atk *= 1.0 + 0.15 * (power + 1)
        elif slot in ("robe", "companion"):
            defence *= 1.0 + 0.12 * (power + 1)
        elif slot == "natal":
            atk *= 1.0 + 0.10 * (power + 1)
            defence *= 1.0 + 0.10 * (power + 1)
    return {"atk": atk, "def": defence, "crit": min(0.75, crit),
            "hp": vis["physique_hp"], "max_hp": vis["max_hp"],
            "speed": vis["circulation_velocity"]}


def make_monster(monster_key: str, zone_guard: int, rng: random.Random) -> dict:
    scale = 1.0 + 0.35 * (zone_guard - 1)
    return {
        "key": MONSTER_NAMES.get(monster_key, monster_key),
        "hp": int((70 + 40 * zone_guard) * scale * rng.uniform(0.85, 1.15)),
        "atk": (10 + 7 * zone_guard) * scale,
        "def": (4 + 3 * zone_guard) * scale,
        "speed": 10 + zone_guard * 4,
    }


def make_guardian(guard_level: int, rng: random.Random) -> dict:
    return {
        "key": f"GUARDIAN_{guard_level}",
        "hp": int((90 * guard_level * guard_level + 60) * rng.uniform(0.9, 1.1)),
        "atk": (14 * guard_level + 6) * 1.0,
        "def": 6 * guard_level,
        "speed": 12 + guard_level * 5,
    }


def _pick_technique(loadout: list, slot_turn: int) -> str | None:
    """Rotate through occupied loadout slots; slot 4 only when available."""
    techs = [t for t in loadout if t]
    if not techs:
        return None
    # prefer slot-1 basics, sprinkle specials, ultimate sparingly
    for t in techs:
        if TECHNIQUES[t]["slot"] == ((slot_turn - 1) % 2 + 1):
            return t
    return techs[slot_turn % len(techs)]


def duel(user: dict, enemy: dict, rng: random.Random | None = None,
         max_turns: int = 40) -> dict:
    """Run the duel. Returns {won, log:[(actor, tech_key|None, damage, crit)],
    hp_left, enemy_hp_left, qi_spent}."""
    rng = rng or random
    p = player_combat_stats(user)
    php, ehp = p["hp"], enemy["hp"]
    p_qi = user["cultivation"]["qi_current"]
    log: list[dict] = []
    loadout = user["combat"]["loadout"]
    qi_spent = 0.0
    guarding = False

    player_first = p["speed"] >= enemy.get("speed", 10)
    turn = 0
    while php > 0 and ehp > 0 and turn < max_turns:
        turn += 1
        order = ("player", "enemy") if player_first else ("enemy", "player")
        for actor in order:
            if php <= 0 or ehp <= 0:
                break
            if actor == "player":
                tech_id = _pick_technique(loadout, turn)
                spec = TECHNIQUES.get(tech_id) if tech_id else None
                power = spec["power"] if spec else 1.0
                qi_cost = 0.0
                if spec and spec.get("qi_cost_pct"):
                    qi_cost = user["cultivation"]["qi_capacity"] * spec["qi_cost_pct"]
                    if p_qi < qi_cost:  # not enough Qi — fall back to basic
                        tech_id, spec, power, qi_cost = None, None, 1.0, 0.0
                p_qi -= qi_cost
                qi_spent += qi_cost
                guarding = False
                if spec and spec.get("guard"):
                    guarding = True
                    log.append({"actor": "player", "tech": tech_id, "damage": 0, "guard": True})
                    continue
                dmg = max(1.0, p["atk"] * power - enemy["def"])
                crit = rng.uniform(0, 1) < p["crit"]
                if crit:
                    dmg *= 1.75
                if spec and spec.get("evade"):
                    dmg *= 0.4  # evasive move: half damage but dodges next hit
                    guarding = True
                ehp -= dmg
                if spec and spec.get("lifesteal"):
                    php = min(p["max_hp"], php + dmg * spec["lifesteal"])
                log.append({"actor": "player", "tech": tech_id, "damage": round(dmg), "crit": crit})
            else:
                dmg = max(1.0, enemy["atk"] - p["def"])
                if guarding:  # guarded/evasive: heavy mitigation
                    dmg *= 0.35
                    guarding = False
                # Dao Sovereign-tier immunity spikes etc. abstracted as defence
                if user["cultivation"]["current_realm_index"] >= 3:
                    dmg *= 0.8  # Golden Core+ shrugs off mortal-grade blows
                php -= dmg
                log.append({"actor": "enemy", "tech": None, "damage": round(dmg)})

    user["stats"]["visible"]["physique_hp"] = max(0, int(php))
    user["cultivation"]["qi_current"] = max(0.0, p_qi)
    won = ehp <= 0 and php > 0
    user["combat"]["wins" if won else "losses"] += 1
    return {"won": won, "log": log, "hp_left": max(0, int(php)),
            "enemy_hp_left": max(0, int(ehp)), "qi_spent": int(qi_spent),
            "turns": turn}


def hunt_monster(user: dict, zone_guard: int, monster_key: str,
                 rng: random.Random | None = None) -> dict:
    """PvE hunt in the current zone. Loot: spirit stones + Qi."""
    rng = rng or random
    if in_seclusion(user) or meridians_sealed(user):
        return {"status": "BLOCKED"}
    monster = make_monster(monster_key, zone_guard, rng)
    result = duel(user, monster, rng=rng)
    result["monster"] = monster["key"]
    recompute_visible_stats(user)
    if result["won"]:
        stones = rng.randint(1, 3) * zone_guard
        qi = int(25 * zone_guard * rng.uniform(0.8, 1.4))
        cul = user["cultivation"]
        room = cul["qi_capacity"] - cul["qi_current"]
        qi_gain = min(qi, room)
        cul["qi_current"] += qi_gain
        user["inventory"]["spirit_stones"]["low"] += stones
        result.update({"stones": stones, "qi": qi_gain})
    return result


def mercy_or_plunder(user: dict, choice: str, rng: random.Random | None = None) -> dict:
    """After defeating a rival cultivator (rival encounters, Blood Dao)."""
    rng = rng or random
    hidden = user["stats"]["hidden"]
    if choice == "mercy":
        hidden["karmic_luck"] = min(100, hidden["karmic_luck"] + 1)
        hidden["dao_heart_stability"] = min(100, hidden["dao_heart_stability"] + 1)
        return {"status": "MERCY", "karma": +1}
    # plunder
    hidden["karmic_luck"] = max(0, hidden["karmic_luck"] - 5)
    stolen_qi = int(rng.randint(50, 200) * (user["cultivation"]["current_realm_index"] ** 2))
    stones = rng.randint(2, 8)
    cul = user["cultivation"]
    room = cul["qi_capacity"] - cul["qi_current"]
    qi_gain = min(stolen_qi, room)
    cul["qi_current"] += qi_gain
    user["inventory"]["spirit_stones"]["low"] += stones
    if cul.get("dao_path") == "blood":
        qi_gain = int(qi_gain * (1 + BLOOD_DAO_QI_STEAL * 10))  # blood dao drinks deep
        cul["qi_current"] = min(cul["qi_capacity"], cul["qi_current"] + qi_gain)
        hidden["demonic_corruption"] = min(100, hidden["demonic_corruption"] + 2)
    return {"status": "PLUNDER", "karma": -5, "qi": qi_gain, "stones": stones}

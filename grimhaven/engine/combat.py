"""Turn-based combat facade (doc §7.3 & overhaul Part 2).

The heavy lifting lives in `grimhaven.core.combat_engine` (live sessions) —
this module keeps the stateless helpers: mercy/plunder choices, instant
settlement used by /auto commands, and the legacy name tables.
"""
from __future__ import annotations

import random

from ..core import combat_engine as engine
from ..core.data_loader import data_registry
from ..engine import models
from .constants import BLOOD_DAO_QI_STEAL

# legacy locale-keyed names still referenced by the map screens
MONSTER_NAMES = {b.enemy_id: f"MONSTER_{b.enemy_id.upper()}" for b in data_registry.beasts.values()}


def player_combat_stats(user: dict) -> dict:
    prof = models.combat_profile(user)
    return {"atk": prof["spiritual_atk"], "def": prof["physical_def"],
            "crit": 0.1, "hp": prof["hp"], "max_hp": prof["max_hp"],
            "speed": prof["meridian_speed"]}


def make_beast(monster_key: str, rng: random.Random | None = None) -> dict:
    return engine.make_beast(monster_key, rng or random)


def make_guardian(guard_level: int, rng: random.Random | None = None) -> dict:
    return engine.make_guardian(guard_level, rng or random)


def instant_settle(user: dict, enemy: dict, rng: random.Random | None = None) -> dict:
    """⏩ Run a whole battle headlessly with the same pipeline (auto-farm)."""
    rng = rng or random.Random()
    engine.start_session(user, enemy, "hunt")
    outcome, _summary, session = engine.simulate(user, rng=rng)
    return {
        "outcome": outcome,
        "won": outcome == engine.OUT_VICTORY,
        "hp_left": session.get("player", {}).get("hp", user["stats"]["visible"]["physique_hp"]),
        "reward": session.get("reward"),
        "rounds": session.get("round", 1),
    }


def mercy_or_plunder(user: dict, choice: str, rng: random.Random | None = None) -> dict:
    """After defeating a rival cultivator (rival encounters, Blood Dao)."""
    rng = rng or random
    hidden = user["stats"]["hidden"]
    if choice == "mercy":
        hidden["karmic_luck"] = min(100, hidden["karmic_luck"] + 1)
        hidden["dao_heart_stability"] = min(100, hidden["dao_heart_stability"] + 1)
        return {"status": "MERCY", "karma": +1}
    hidden["karmic_luck"] = max(0, hidden["karmic_luck"] - 5)
    stolen_qi = int(rng.randint(50, 200) * (user["cultivation"]["current_realm_index"] ** 2))
    stones = rng.randint(2, 8)
    cul = user["cultivation"]
    room = cul["qi_capacity"] - cul["qi_current"]
    qi_gain = min(stolen_qi, room)
    cul["qi_current"] += qi_gain
    user["inventory"]["spirit_stones"]["low"] += stones
    if cul.get("dao_path") == "blood":
        qi_gain = int(qi_gain * (1 + BLOOD_DAO_QI_STEAL * 10))
        cul["qi_current"] = min(cul["qi_capacity"], cul["qi_current"] + qi_gain)
        hidden["demonic_corruption"] = min(100, hidden["demonic_corruption"] + 2)
    return {"status": "PLUNDER", "karma": -5, "qi": qi_gain, "stones": stones}

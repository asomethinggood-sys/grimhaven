"""Part 2 — Tactical turn-based combat engine.

A battle is a live session embedded in the user document; each callback
resolves exactly one round through the reference pipeline:

  initiative & double-action → cooldowns → status ticks → player action
  (backfire check → damage → mitigation → crit/grazed tiers + flavor) →
  enemy counter → lethal check → miracle escape / true death.

Session documents store only technique ids; all text is resolved from the
data registry at render time.
"""
from __future__ import annotations

import datetime as dt
import random
import uuid
from typing import Any

from ..core.data_loader import data_registry
from ..core.state_machine import (
    INJURY_HOURS,
    PARALYSIS_MINUTES,
    UserStatus,
    set_status,
)
from ..engine import items as items_mod
from ..engine import models as models
from ..engine.models import iso, parse_iso

SESSION_STALE_MINUTES = 15


class _NarrDict(dict):
    """format_map dict that leaves unknown ``{placeholder}`` tokens intact."""
    def __missing__(self, key):  # pragma: no cover - defensive
        return "{" + key + "}"


def fmt_narrative(template: str, **vals) -> str:
    """Fill dataset templates ({damage}, {target}, {name}) without KeyErrors."""
    try:
        return template.format_map(_NarrDict({k: str(v) for k, v in vals.items()}))
    except (ValueError, AttributeError, TypeError):
        return template


# outcomes of resolve_round()
OUT_CONTINUE = "CONTINUE"
OUT_VICTORY = "VICTORY"
OUT_FLED = "FLED"
OUT_MIRACLE = "MIRACLE_ESCAPE"
OUT_DEATH = "TRUE_DEATH"


# ── enemy construction ───────────────────────────────────────────────────────

def make_beast(enemy_id: str, rng: random.Random) -> dict | None:
    proto = data_registry.get_beast(enemy_id)
    if not proto:
        return None
    return _snap(proto, rng, 0.85, 1.15)


def _snap(proto, rng: random.Random, lo: float, hi: float) -> dict:
    jitter = lambda v: max(1, int(v * rng.uniform(lo, hi)))
    return {"id": proto.enemy_id, "hp": jitter(proto.hp), "max_hp": jitter(proto.hp),
            "atk": jitter(proto.atk), "def": jitter(proto.def_), "sdef": jitter(proto.sdef),
            "speed": jitter(proto.speed), "guard_level": proto.guard_level}


def make_guardian(guard_level: int, rng: random.Random) -> dict | None:
    proto = data_registry.get_guardian(guard_level)
    if not proto:
        # fall back to the strongest beast of that sector tier
        return make_beast((data_registry.beasts_for_guard(guard_level) or [None])[0].enemy_id
                          if data_registry.beasts_for_guard(guard_level) else "", rng)
    return _snap(proto, rng, 0.9, 1.1)


def make_rival(realm: int, rng: random.Random) -> dict:
    proto = data_registry.rival
    scale = 1.0 + 0.55 * (realm - 1)
    j = lambda v: max(1, int(v * scale * rng.uniform(0.85, 1.2)))
    return {"id": proto.enemy_id, "hp": j(proto.hp), "max_hp": j(proto.hp), "atk": j(proto.atk),
            "def": j(proto.def_), "sdef": j(proto.sdef), "speed": j(proto.speed) + realm * 2,
            "guard_level": 0}


def enemy_proto(enemy: dict):
    """Dataset record backing a live session enemy (spec-schema aware)."""
    return (data_registry.get_beast(enemy.get("id", ""))
            or data_registry.get_guardian(enemy.get("guard_level", 0))
            or data_registry.rival)


def enemy_name(enemy: dict, lang: str) -> str:
    obj = (data_registry.get_beast(enemy["id"]) or data_registry.get_guardian(enemy.get("guard_level", 0))
           or data_registry.rival)
    if obj and obj.enemy_id == enemy["id"]:
        return obj.name_for(lang)
    return enemy["id"]


# ── session lifecycle ────────────────────────────────────────────────────────

def start_session(user: dict, enemy: dict, kind: str, zone_id: str | None = None) -> dict:
    prof = models.combat_profile(user)
    session = {
        "battle_id": uuid.uuid4().hex[:12],
        "kind": kind,                      # hunt | conquest | rival
        "zone_id": zone_id,
        "enemy": enemy,
        "player": {"hp": prof["hp"], "max_hp": prof["max_hp"],
                   "qi": prof["qi"], "max_qi": prof["max_qi"]},
        "round": 1,
        "cooldowns": {},
        "statuses": {"p": [], "e": []},
        "shield": 0,
        "player_first": prof["meridian_speed"] >= enemy["speed"],
        "finished": False,
        "outcome": None,
        "reward": None,
        "log": {"p": "", "e": ""},
        "started_at": iso(),
        "updated_at": iso(),
    }
    user["combat"]["session"] = session
    set_status(user, UserStatus.IN_COMBAT)
    return session


def session_expired(session: dict, now: dt.datetime) -> bool:
    ts = parse_iso(session.get("updated_at"))
    return bool(ts and (now - ts).total_seconds() > SESSION_STALE_MINUTES * 60)


def close_session(user: dict) -> None:
    user["combat"]["session"] = None
    if user.get("status") == UserStatus.IN_COMBAT.value:
        set_status(user, UserStatus.IDLE)


# ── helpers ──────────────────────────────────────────────────────────────────

def _status_mod(statuses: list[dict], kind: str, field: str, default: float = 0.0) -> float:
    value = default
    for st in statuses:
        if st["kind"] == kind:
            value += float(st.get(field, default))
    return value


def _has_status(statuses: list[dict], kind: str) -> bool:
    return any(st["kind"] == kind for st in statuses)


def _tick_statuses(session: dict, side: str, lang: str, lines: list[str]) -> bool:
    """Apply damage-over-time / heals; decrement turns. Returns True if the actor
    is stunned or confused this round (action skipped)."""
    st_list: list[dict] = session["statuses"][side]
    blocked = False
    p = session["player"]
    e = session["enemy"]
    for st in list(st_list):
        kind = st["kind"]
        if kind in ("burn", "poison", "bleed"):
            pool = p if side == "p" else e
            dmg = max(1, int(pool["max_hp"] * float(st.get("dot_pct", 0.04))))
            pool["hp"] = max(0, pool["hp"] - dmg)
            icon = {"burn": "🔥", "poison": "☠️", "bleed": "🩸"}[kind]
            who = t_side(lang, side)
            lines.append(f"{icon} {who} — {dmg}")
        elif kind == "regen":
            heal = max(1, int(p["max_hp"] * float(st.get("heal_pct", 0.05))))
            p["hp"] = min(p["max_hp"], p["hp"] + heal)
            if side == "p":
                lines.append(f"🌿 {t_side(lang, side)} +{heal}")
        elif kind == "stun":
            blocked = True
        elif kind == "confuse" and random.uniform(0, 1) < 0.5:
            blocked = True
        st["turns"] -= 1
        if st["turns"] <= 0:
            st_list.remove(st)
    return blocked


def t_side(lang: str, side: str) -> str:
    from ..localization import t
    return t(lang, "COMBAT_YOU" if side == "p" else "COMBAT_THE_ENEMY")


def _flavor(tech, tier: str, target_name: str, damage: int, lang: str) -> str:
    """3-tier flavor interpolation from data/martial_arts.json."""
    try:
        if lang == "en" and tech.descriptions_en:
            template = getattr(tech.descriptions_en, tier, None) or getattr(tech.descriptions, tier)
        else:
            template = getattr(tech.descriptions, tier)
    except (AttributeError, KeyError, ValueError, IndexError):
        from ..localization import t
        return t(lang, "COMBAT_HIT_GENERIC", target=target_name, damage=damage)
    if template:
        return fmt_narrative(template, target=target_name, damage=damage, name=target_name)
    from ..localization import t
    return t(lang, "COMBAT_HIT_GENERIC", target=target_name, damage=damage)


# ── the turn resolver ────────────────────────────────────────────────────────

def resolve_round(user: dict, action: str, action_id: str | None = None,
                  rng: random.Random | None = None) -> tuple[str, list[str]]:
    """Execute one combat round. Mutates the user document (session + stats).

    Round order: cooldowns → player status ticks (DoT / stun / regen) →
    player action → enemy status ticks → enemy strikes → lethal judgement.
    Returns (outcome, round_log_lines).
    """
    from ..localization import t
    rng = rng or random.Random()
    lang = user["account"]["language"]
    session = user["combat"]["session"]
    if not session or session["finished"]:
        return OUT_CONTINUE, []
    p = session["player"]
    e = session["enemy"]
    prof = models.combat_profile(user)
    lines: list[str] = []
    enemy = enemy_name(e, lang)

    # cooldowns tick down at round start
    for k in list(session["cooldowns"].keys()):
        session["cooldowns"][k] -= 1
        if session["cooldowns"][k] <= 0:
            session["cooldowns"].pop(k)

    # ── player status tick (burn/poison may kill you!) ──
    skip_player = _tick_statuses(session, "p", lang, lines)
    if "regen_3" in models.gear_specials(user):
        heal = max(1, int(p["max_hp"] * 0.03))
        p["hp"] = min(p["max_hp"], p["hp"] + heal)

    # ── player action ──
    if p["hp"] > 0 and not skip_player:
        if action.startswith("combat:item:") and len(action) > len("combat:item:"):
            item_id = action.split(":", 2)[2]
            lines.extend(_use_combat_item(user, session, item_id, rng))
        elif action == "combat:basic":
            atk = max(prof["physical_atk"], prof["spiritual_atk"])
            restore = max(1, int(p["max_qi"] * 0.05))
            p["qi"] = min(p["max_qi"], p["qi"] + restore)
            buff = 1 + _status_mod(session["statuses"]["p"], "atk_buff", "pct")
            dmg = max(5, int(atk * 0.7 * buff) - e["def"] // 4)
            e["hp"] = max(0, e["hp"] - dmg)
            lines.append(t(lang, "COMBAT_BASIC", damage=dmg, qi=restore))
            session["log"]["p"] = lines[-1]
            _weapon_specials(user, session, dmg, lines, rng)
        elif action == "combat:tech" and action_id:
            tech = data_registry.get_technique(action_id)
            if tech is None:
                lines.append(t(lang, "ERR_UNKNOWN"))
            else:
                lines.extend(_player_technique(user, session, tech, prof, rng, enemy))
        elif action == "combat:flee":
            chance = max(15, min(85, 50 + (prof["meridian_speed"] - e["speed"]) * 1.2))
            if rng.uniform(0, 100) < chance:
                session["finished"] = True
                session["outcome"] = OUT_FLED
                lines.append(t(lang, "COMBAT_FLED"))
                _finish(user, session, lines)
                return OUT_FLED, lines
            lines.append(t(lang, "COMBAT_FLEE_FAIL"))
            session["log"]["p"] = lines[-1]
        elif skip_player and lines:
            pass  # stun/confuse message already logged
    elif p["hp"] <= 0:
        pass  # died to damage-over-time before acting

    if p["hp"] <= 0:  # died to DoT
        return _judge_death(user, session, lines, prof, rng)

    # ── enemy death from the player's swing ──
    if e["hp"] <= 0:
        return _judge_victory(user, session, lines, rng)

    # ── enemy status tick (burn can finish it first) ──
    skip_enemy = _tick_statuses(session, "e", lang, lines)
    if e["hp"] <= 0:
        lines.append("🔥")
        return _judge_victory(user, session, lines, rng)

    # ── enemy turn (dataset action pool + hit/crit/miss narratives, P4 §3.2) ──
    if skip_enemy:
        lines.append(t(lang, "COMBAT_ENEMY_STUNNED", enemy=enemy))
        session["log"]["e"] = lines[-1]
    else:
        et = enemy_traits(user, e, session)
        proto = enemy_proto(e)
        pool = list(proto.actions) if proto else []
        strikes = 2 if (et["double"] and rng.uniform(0, 100) < 15) else 1
        for _ in range(strikes):
            if p["hp"] <= 0:
                break
            act = rng.choice(pool) if pool else None
            mult = act.damage_multiplier if act else 1.0
            eva = max(0, min(40, (prof["meridian_speed"] - e["speed"]) * 1.2 + 5))
            eva += int(_status_mod(session["statuses"]["p"], "evade", "chance", 0.0) * 100)
            if rng.uniform(0, 100) < min(55, eva):
                miss_line = t(lang, "COMBAT_DODGED", enemy=enemy)
                if act:
                    ms = act.desc_for(lang).miss
                    if ms:
                        miss_line = fmt_narrative(ms, damage=0, target=t(lang, "COMBAT_YOU"), name=enemy)
                lines.append(miss_line)
                session["log"]["e"] = miss_line
                continue
            raw = max(1.0, e["atk"] * mult * (1 - _status_mod(session["statuses"]["e"], "atk_cut", "pct")))
            p_def = prof["physical_def"] * (1 + _status_mod(session["statuses"]["p"], "fortify", "def_pct"))
            red = max(0.0, min(0.75, p_def / (p_def + 100)))
            dmg = raw * (1 - red)
            guard_mit = _status_mod(session["statuses"]["p"], "guard", "mitigation")
            if guard_mit:
                dmg *= max(0.2, 1 - guard_mit)
            is_e_crit = rng.uniform(0, 100) < 12
            if is_e_crit:
                dmg *= 1.45
            dmg = max(1, int(dmg))
            if session["shield"] > 0:
                absorbed = min(session["shield"], dmg)
                session["shield"] -= absorbed
                dmg -= absorbed
                if absorbed:
                    lines.append(t(lang, "COMBAT_SHIELD", absorbed=absorbed))
            if dmg > 0:
                p["hp"] -= dmg
                hit_line = t(lang, "COMBAT_ENEMY_HIT", enemy=enemy, damage=dmg)
                if act:
                    dset = act.desc_for(lang)
                    tpl = dset.crit if is_e_crit else dset.hit
                    if tpl:
                        hit_line = fmt_narrative(tpl, damage=dmg, target=t(lang, "COMBAT_YOU"), name=enemy)
                        if is_e_crit:
                            hit_line = "💢 " + hit_line
                lines.append(hit_line)
                session["log"]["e"] = hit_line

    # ── lethal check → miracle escape or true death ──
    if p["hp"] <= 0 and not session["finished"]:
        return _judge_death(user, session, lines, prof, rng)

    if not session["finished"]:
        session["log"].setdefault("p", "")
        session["log"].setdefault("e", "")
        session["round"] += 1
        session["updated_at"] = iso()
        _sync_hp(user, session)
    return OUT_CONTINUE, lines


def _judge_victory(user, session, lines, rng):
    from ..localization import t
    lang = user["account"]["language"]
    session["finished"] = True
    session["outcome"] = OUT_VICTORY
    lines.append("🎉 " + t(lang, "COMBAT_ENEMY_SLAIN", enemy=enemy_name(session["enemy"], lang)))
    _apply_victory(user, session, lines, rng)
    _finish(user, session, lines)
    return OUT_VICTORY, lines


def _judge_death(user, session, lines, prof, rng):
    from ..localization import t
    lang = user["account"]["language"]
    session["finished"] = True
    e = session["enemy"]
    escape = max(10, min(65, (prof["meridian_speed"] / max(1, e["speed"])) * 25
                        + prof["karmic_luck"] * 0.3))
    if rng.uniform(0, 100) < escape:
        session["outcome"] = OUT_MIRACLE
        lines.append("⚡️ " + t(lang, "COMBAT_MIRACLE"))
        _apply_injury(user, now=dt.datetime.now(dt.timezone.utc))
        user["progress"]["miracle_escapes"] += 1
        _finish(user, session, lines)
        return OUT_MIRACLE, lines
    session["outcome"] = OUT_DEATH
    lines.append("☠️ " + t(lang, "COMBAT_DEATH"))
    _apply_death(user, now=dt.datetime.now(dt.timezone.utc))
    _finish(user, session, lines)
    return OUT_DEATH, lines


def enemy_traits(user: dict, e: dict, session: dict) -> dict:
    from ..engine.models import combat_profile
    prof = combat_profile(user)
    slow = 1.0 + max(-0.5, _status_mod(session["statuses"]["e"], "slow", "speed_pct"))
    return {"double": prof["meridian_speed"] >= 1.5 * e["speed"] * slow,
            "slow_pct": slow}


def _player_technique(user, session, tech, prof, rng, enemy_label: str | None = None) -> list[str]:
    from ..localization import t
    lang = user["account"]["language"]
    lines: list[str] = []
    p = session["player"]
    e = session["enemy"]
    if tech.id in session["cooldowns"]:
        return [t(lang, "COMBAT_ON_CD", skill=tech.name_en or tech.name)]
    if p["qi"] < tech.qi_cost:
        return [t(lang, "COMBAT_NO_QI")]
    p["qi"] -= tech.qi_cost
    if tech.cooldown:
        session["cooldowns"][tech.id] = tech.cooldown + 1  # ticks down at next round start

    # backfire / qi-deviation check
    if rng.uniform(0, 100) < prof["qi_deviation_risk"] * 0.6:
        backlash = max(5, int(tech.qi_cost * 0.2))
        p["hp"] = max(1, p["hp"] - backlash)
        lines.append(t(lang, "COMBAT_BACKFIRE", damage=backlash))
        return lines

    scaling = prof["spiritual_atk"] if tech.scaling_stat != "physical_atk" else prof["physical_atk"]
    atk_buff = _status_mod(session["statuses"]["p"], "atk_buff", "pct")
    raw = scaling * tech.base_damage_multiplier * (1 + atk_buff)
    dao = user["cultivation"].get("dao_path")
    if dao == "sword":
        raw *= 1.08  # sword intent sharpens every blade technique
    e_def = e["sdef"] if tech.scaling_stat != "physical_atk" else e["def"]
    e_def = max(0, e_def * (1 + max(-0.6, _status_mod(session["statuses"]["e"], "armor_break", "def_pct"))))
    reduction = max(0.0, min(0.7, e_def / (e_def + 100)))
    dmg = raw * (1 - reduction)

    crit_chance = max(5, min(60, prof["divine_sense"] * 0.5 + prof["crit_bonus"]))
    tier = "normal"
    if rng.uniform(0, 100) < crit_chance:
        dmg *= 1.5 + prof["divine_sense"] * 0.01
        tier = "crit"
    elif reduction > 0.45:
        tier = "grazed"
    dmg = max(1, int(dmg))
    e["hp"] = max(0, e["hp"] - dmg)

    name = tech.name if lang != "en" else (tech.name_en or tech.name)
    lines.append(t(lang, "COMBAT_SKILL", skill=name))
    lines.append(_flavor(tech, tier, enemy_name(e, lang), dmg, lang))
    session.setdefault("log", {})["p"] = lines[-1]

    if tech.lifesteal_pct:
        heal = max(1, int(dmg * tech.lifesteal_pct))
        p["hp"] = min(p["max_hp"], p["hp"] + heal)
        lines.append(t(lang, "COMBAT_LIFESTEAL", heal=heal))
    st = tech.status_inflict
    if st and rng.uniform(0, 1) < float(st.get("chance", 1.0)):
        entry = {"kind": st["kind"], "turns": int(st.get("turns", 1))}
        for k in ("dot_pct", "speed_pct", "def_pct", "mitigation", "heal_pct"):
            if k in st:
                entry[k] = st[k]
        side = "p" if st.get("target") == "self" else "e"
        if st["kind"] in ("burn", "poison", "bleed") and side == "p":
            entry["dot_pct"] = -entry.get("dot_pct", 0)
        session["statuses"][side].append(entry)
        lines.append(t(lang, "COMBAT_STATUS", status=t(lang, f"STATUS_{st['kind'].upper()}"),
                       who=enemy_name(e, lang) if side == "e" else t(lang, "COMBAT_YOU")))
    _weapon_specials(user, session, dmg, lines, rng)
    return lines


def _weapon_specials(user, session, dmg, lines, rng) -> None:
    from ..localization import t
    lang = user["account"]["language"]
    specials = models.gear_specials(user)
    if "leech_qi_percent_3" in specials:
        gain = max(1, int(dmg * 0.03))
        session["player"]["qi"] = min(session["player"]["max_qi"], session["player"]["qi"] + gain)
    if "stun_10" in specials and rng.uniform(0, 1) < 0.10:
        session["statuses"]["e"].append({"kind": "stun", "turns": 1})
        lines.append(t(lang, "COMBAT_STATUS", status=t(lang, "STATUS_STUN"),
                       who=enemy_name(session["enemy"], lang)))
    if "burn_15" in specials and rng.uniform(0, 1) < 0.15:
        session["statuses"]["e"].append({"kind": "burn", "turns": 2, "dot_pct": 0.03})
        lines.append(t(lang, "COMBAT_STATUS", status=t(lang, "STATUS_BURN"),
                       who=enemy_name(session["enemy"], lang)))


def _use_combat_item(user, session, item_id, rng) -> list[str]:
    from ..localization import t
    lang = user["account"]["language"]
    item = data_registry.get_consumable(item_id)
    if not item or item.category != "disposable_weapon":
        return [t(lang, "ERR_UNKNOWN")]
    if not items_mod.count_item(user, item_id):
        return [t(lang, "ITEM_NONE_LEFT", item=item.name_for(lang))]
    if not items_mod.consume_item(user, item_id):
        return [t(lang, "ITEM_NONE_LEFT", item=item.name_for(lang))]
    lines = [t(lang, "COMBAT_ITEM_USED", item=item.name_for(lang))]
    session.setdefault("log", {})["p"] = lines[-1]
    e = session["enemy"]
    if item.action == "instant_damage":
        raw = item.fixed_damage * (1 - min(1.0, item.bypass_defense_percent / 100.0) *
                                   min(1.0, e["def"] / (e["def"] + 100)))
        dmg = max(1, int(raw))
        e["hp"] = max(0, e["hp"] - dmg)
        lines.append(t(lang, "COMBAT_ITEM_HIT", damage=dmg))
        if item.stun_chance_percent and rng.uniform(0, 100) < item.stun_chance_percent:
            session["statuses"]["e"].append({"kind": "stun", "turns": 1})
            lines.append(t(lang, "COMBAT_STATUS", status=t(lang, "STATUS_STUN"), who=enemy_name(e, lang)))
        if item.burn_on_use:
            session["statuses"]["e"].append({"kind": "burn", "turns": 2, "dot_pct": 0.03})
    elif item.action == "shield":
        session["shield"] += item.shield_value
        lines.append(t(lang, "COMBAT_SHIELD_UP", value=item.shield_value))
    elif item.action == "enemy_debuff":
        session["statuses"]["e"].append({"kind": "atk_cut", "turns": item.turns or 3,
                                         "pct": (item.atk_cut_percent or 20) / 100.0})
        lines.append(t(lang, "COMBAT_ENEMY_WEAKENED"))
    return lines


# ── resolution & consequences ────────────────────────────────────────────────

def _apply_victory(user: dict, session: dict, lines: list[str], rng: random.Random) -> None:
    """Spec P4 §5.2 rewards: fixed exp_qi + spirit_stones, per-drop chance rolls."""
    from ..localization import t
    lang = user["account"]["language"]
    e = session["enemy"]
    proto = enemy_proto(e)
    rewards = proto.rewards if proto else None
    if rewards and (rewards.exp_qi or rewards.spirit_stones or rewards.loot_drops):
        qi = rewards.exp_qi
        st_lo, st_hi = rewards.spirit_stones, rewards.spirit_stones
    else:
        loot = (proto.loot if proto else {}) or {}
        qi_rng, st_rng = loot.get("qi") or [15, 40], loot.get("stones") or [1, 2]
        qi = rng.randint(*qi_rng) if len(qi_rng) == 2 else 0
        st_lo, st_hi = st_rng
    stones = rng.randint(st_lo, st_hi) if st_hi else 0
    cul = user["cultivation"]
    room = max(0, cul["qi_capacity"] - cul["qi_current"])
    qi_gain = min(qi, room)
    cul["qi_current"] += qi_gain
    user["inventory"]["spirit_stones"]["low"] += stones

    drops: list[str] = []
    from ..engine.models import add_item, ring_has_room
    for drop in (rewards.loot_drops if rewards else []):
        if rng.randint(1, 100) <= drop.chance_percent:
            obj = data_registry.get_consumable(drop.item_id) or data_registry.get_equipment(drop.item_id)
            is_gear = data_registry.get_equipment(drop.item_id) is not None
            if obj and (is_gear or drop.item_id in user["inventory"]["items"] or ring_has_room(user, drop.item_id)):
                add_item(user, drop.item_id, 1, is_gear=is_gear)
                drops.append(drop.name_for(lang) or obj.name_for(lang))
            else:
                drops.append((drop.name_for(lang) or obj.name_for(lang)) + (" (حلقه پُر)" if lang != "en" else " (ring full)"))
    reward = {"stones": stones, "qi": qi_gain, "drops": drops}
    session["reward"] = reward
    user["combat"]["wins"] += 1
    lines.append(t(lang, "COMBAT_REWARD", stones=stones, qi=qi_gain))
    if drops:
        lines.append(t(lang, "COMBAT_LOOT", items="،" if lang == "fa" else ", ", item_list=" / ".join(drops)))
    if session["kind"] == "conquest":
        lines.append("🏴 " + t(lang, "COMBAT_PLANT_FLAG"))
    elif session["kind"] == "hunt" and rng.uniform(0, 1) < 0.28:
        # a rival cultivator saw your fight — they want your stash
        user["pending_rival"] = True
        lines.append("🗡 " + t(lang, "RIVAL_SPOTTED"))


def _apply_injury(user: dict, now: dt.datetime) -> None:
    """Miracle escape ⇒ 3h severe-meridian-injury debuff (Part 2 §4.1)."""
    from ..engine.models import consume_item  # noqa: F401  (clarity)
    expires = now + dt.timedelta(hours=INJURY_HOURS)
    user["combat"]["injury"] = {
        "debuff_id": "severe_meridian_injury",
        "expires_at": iso(expires),
        "qi_rate_multiplier": 0.5,
        "combat_speed_penalty": 0.3,
        "combat_def_penalty": 0.3,
    }
    user["stats"]["visible"]["physique_hp"] = max(1, int(user["stats"]["visible"]["max_hp"] * 0.05))
    set_status(user, UserStatus.HEAVILY_INJURED)


def _apply_death(user: dict, now: dt.datetime) -> dict:
    """True death (Part 2 §4.2): drain Qi, shatter resolve, loot stones, coma."""
    cul = user["cultivation"]
    lost_qi = int(cul["qi_current"] * 0.25)
    cul["qi_current"] -= lost_qi
    hidden = user["stats"]["hidden"]
    hidden["dao_heart_stability"] = max(0, hidden["dao_heart_stability"] - 5)
    stones = user["inventory"]["spirit_stones"]
    lost_low = int(stones.get("low", 0) * 0.15)
    lost_mid = int(stones.get("mid", 0) * 0.15)
    stones["low"] -= lost_low
    stones["mid"] -= lost_mid
    user["stats"]["visible"]["physique_hp"] = 1
    user["combat"]["losses"] += 1
    user["progress"]["deaths"] = user["progress"].get("deaths", 0) + 1

    doll_saved = False
    if cul["current_realm_index"] >= 3:
        if items_mod.consume_item(user, "doll_substitute"):
            doll_saved = True
        else:
            # no substitute doll: weapon & robe lose 20 durability
            for slot in ("weapon", "robe"):
                g = user["equipment"].get(slot)
                if g:
                    g["dur"] = max(20, int(g.get("dur", 100)) - 20)
    par_until = now + dt.timedelta(minutes=PARALYSIS_MINUTES)
    user["combat"]["paralysis_until"] = iso(par_until)
    set_status(user, UserStatus.HEAVILY_INJURED)
    return {"lost_qi": lost_qi, "lost_stones": lost_low + lost_mid,
            "doll_saved": doll_saved, "paralysis_until": iso(par_until)}


def death_report(user: dict, now: dt.datetime) -> dict:
    """Used after _apply_death to enrich the UI message."""
    return {}


def _finish(user: dict, session: dict, lines: list[str]) -> None:
    _sync_hp(user, session)
    session["updated_at"] = iso()
    if session.get("outcome") in (OUT_VICTORY, OUT_FLED, "MIRACLE_ESCAPE", "TRUE_DEATH"):
        close_session(user)


def _sync_hp(user: dict, session: dict) -> None:
    vis = user["stats"]["visible"]
    vis["physique_hp"] = max(1, min(vis["max_hp"], int(session["player"]["hp"])))
    cul = user["cultivation"]
    cul["qi_current"] = max(0, int(session["player"]["qi"]))


# ── fast-forward auto battle ─────────────────────────────────────────────────

def battle_item_sources(user: dict) -> list[str]:
    """Row-4 sources for the battle keyboard: the two assigned slots, falling
    back to the first two usable combat items owned (spec P2)."""
    slots = [s for s in (user.get("combat", {}).get("item_slots") or []) if s]
    picked = []
    for iid in slots:
        item = data_registry.get_consumable(iid)
        if item and (user["inventory"].get("items", {}).get(iid, 0) > 0):
            picked.append(iid)
        if len(picked) == 2:
            return picked
    if picked:
        return picked
    for iid, qty in sorted(user["inventory"].get("items", {}).items()):
        item = data_registry.get_consumable(iid)
        if not item or qty <= 0:
            continue
        if item.category in ("disposable_weapon", "talisman", "relic") or \
           item.action in ("instant_damage", "shield", "enemy_debuff", "heal_hp"):
            picked.append(iid)
        if len(picked) == 2:
            break
    return picked


def auto_pick_action(user: dict, session: dict) -> tuple[str, str | None]:
    loadout = [t for t in user["combat"]["loadout"] if t]
    qi = session["player"]["qi"]
    best = None
    best_score = -1.0
    for tid in loadout:
        tech = data_registry.get_technique(tid)
        if not tech or tech.id in session["cooldowns"] or tech.qi_cost > qi:
            continue
        score = tech.base_damage_multiplier * (1.3 if tech.status_inflict else 1.0) / max(1.0, tech.qi_cost / 20.0)
        if score > best_score:
            best, best_score = tid, score
    if best:
        return "combat:tech", best
    return "combat:basic", None


def simulate(user: dict, rng: random.Random | None = None, max_rounds: int = 34
             ) -> tuple[str, list[str], dict]:
    """⏩ Fast-forward the whole battle with the deterministic pipeline."""
    rng = rng or random.Random()
    session = user["combat"]["session"]
    if not session:
        return OUT_CONTINUE, [], {}
    snapshot: dict = {}
    outcome = OUT_CONTINUE
    last: list[str] = []
    summary: list[str] = [f"⏩ #{session['round']}"]
    while not session["finished"] and session["round"] < max_rounds:
        action, action_id = auto_pick_action(user, session)
        if action == "combat:tech":
            session_action = f"combat:tech"
            outcome, last = resolve_round(user, session_action, action_id, rng)
        else:
            outcome, last = resolve_round(user, "combat:basic", None, rng)
    if not session["finished"]:
        # stalemate → walk away
        session["finished"] = True
        session["outcome"] = OUT_FLED
        outcome = OUT_FLED
        close_session(user)
    snapshot = dict(session)
    from ..localization import t
    lang = user["account"]["language"]
    summary.append(t(lang, "COMBAT_SIM_ROUNDS", n=min(max_rounds, session["round"])))
    if last:
        summary.extend(last[-2:])
    return outcome, summary, snapshot

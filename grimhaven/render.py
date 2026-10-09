"""Screen renderers — shared by the Telegram bot and the web demo console.

Overhaul Part 3: streamlined destiny scroll + hidden-stats deep view, tabbed
spatial-ring bag, martial-hall deck builder screens, and the live combat view
with HP/Qi bars and the round chronicle. Every string is locale-keyed.
"""
from __future__ import annotations

import datetime as dt

from .core.combat_engine import enemy_name
from .core.data_loader import data_registry
from .core.state_machine import (UserStatus, active_debuff, get_status,
                                 paralysis_active)
from .engine import items as items_mod
from .engine.constants import (
    DAO_PATHS,
    EQUIP_SLOTS,
    METHODS,
    REALM_NAMES,
    REALM_STAGES,
    SPIRIT_STONES,
    TIER_KEYS,
    ZONES,
)
from .engine.models import (
    afk_hourly_rate,
    in_seclusion,
    item_capacity,
    item_count,
    meridians_sealed,
    parse_iso,
    stage_cost,
    utcnow,
)
from .localization import t

BAR_WIDTH = 10


def _bar(fraction: float) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * BAR_WIDTH))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def realm_stage_label(lang: str, realm: int, stage: int) -> str:
    if realm == 1:
        return t(lang, "STAGE_LAYER", n=stage + 1)
    if realm == 5:
        if stage <= 2:
            return t(lang, "STAGE_SEVER", n=stage + 1)
        return t(lang, "STAGE_PEAK")
    if realm == 7:
        return t(lang, "STAGE_TRIBULATION", n=stage + 1)
    if realm == 9:
        return t(lang, "STAGE_FUSION")
    return t(lang, ("STAGE_EARLY", "STAGE_MID", "STAGE_LATE", "STAGE_PEAK")[stage])


def target_label(lang: str, realm: int, stage: int) -> str:
    return f"{t(lang, REALM_NAMES[realm])} [{realm_stage_label(lang, realm, stage)}]"


def item_name(item_id: str, lang: str) -> str:
    obj = data_registry.get_consumable(item_id) or data_registry.get_equipment(item_id)
    return obj.name_for(lang) if obj else item_id


def gear_label(lang: str, gear: dict | None, storage=None) -> str:
    if not gear:
        return t(lang, "GEAR_NONE")
    item = data_registry.get_equipment(gear.get("id", ""))
    if not item:
        return gear.get("id", "?")
    out = f"{item.name_for(lang)} ({t(lang, TIER_KEYS.get(item.tier, 'TIER_mortal'))})"
    dur = int(gear.get("dur", 100))
    if dur < 100:
        out += f" ✚{dur}%"
    return out


def catalyst_text(lang: str, user: dict, now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    parts = []
    stone = user["cultivation"].get("active_stone")
    if stone:
        parts.append(f"🪨 +{int(SPIRIT_STONES[stone]['boost'] * 100)}%")
    for buff in user.get("buffs", []):
        until = parse_iso(buff.get("until"))
        if until and until > now:
            parts.append(f"🩸 +{int(float(buff['boost']) * 100)}%")
    if user.get("progress", {}).get("root_ancient_used"):
        parts.append("🌿 +25%")
    method = METHODS.get(user["cultivation"].get("active_method_id", ""))
    if method and method["tech_mult"] > 1.0:
        parts.append(f"🌀 ×{method['tech_mult']}")
    return " + ".join(parts) if parts else t(lang, "PROF_NONE")


def status_line(lang: str, user: dict, now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    status = get_status(user)
    lines = []
    if status is UserStatus.MEDITATING:
        started = parse_iso(user["cultivation"].get("meditation_started_at"))
        mins = int((now - started).total_seconds() // 60) if started else 0
        lines.append(t(lang, "STATUS_MEDITATING_T", minutes=mins))
    if status is UserStatus.IN_COMBAT and user["combat"].get("session"):
        e = user["combat"]["session"]["enemy"]
        lines.append(t(lang, "STATUS_IN_COMBAT", enemy=enemy_name(e, lang)))
    if in_seclusion(user, now):
        finish = parse_iso(user["cultivation"].get("seclusion_finish_time"))
        mins = max(1, int((finish - now).total_seconds() // 60) + 1)
        lines.append(t(lang, "STATUS_SECLUSION", minutes=mins))
    if meridians_sealed(user, now):
        until = parse_iso(user["cultivation"]["meridian_sealed_until"])
        hours = max(1, int((until - now).total_seconds() // 3600) + 1)
        lines.append(t(lang, "STATUS_MERIDIAN_SEALED", hours=hours))
    deb = active_debuff(user, now)
    if deb:
        exp = parse_iso(deb.get("expires_at"))
        mins = max(1, int((exp - now).total_seconds() // 60)) if exp else 0
        lines.append(t(lang, "STATUS_INJURED", minutes=mins))
    if paralysis_active(user, now):
        until = parse_iso(user["combat"].get("paralysis_until"))
        mins = max(1, int((until - now).total_seconds() // 60)) if until else 0
        lines.append(t(lang, "STATUS_PARALYSIS", minutes=mins))
    return "\n".join(lines)


# ── destiny scroll (Part 3 §3.1) ─────────────────────────────────────────────

def profile_text(storage, user: dict, world_boost: float = 1.0,
                 now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    lang = user["account"]["language"]
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis = user["stats"]["visible"]

    dao_key = DAO_PATHS[cul["dao_path"]]["key"] if cul.get("dao_path") else None
    align_key = "ALIGN_ORTHODOX_NAME" if cul["alignment"] == "orthodox" else "ALIGN_DEMONIC_NAME"
    path_text = (t(lang, dao_key) if dao_key else t(lang, "PROF_NONE"))
    if dao_key:
        path_text += f" ({t(lang, align_key)})"

    cost = stage_cost(user)
    frac = cul["qi_current"] / cost if cost else 1.0
    rate = afk_hourly_rate(user, world_boost=world_boost, now=now)

    zone = storage.get_zone(user["location"]["current_zone_id"]) if storage else None
    zone_key = zone["key"] if zone else ZONES[user["location"]["current_zone_id"]]["key"]
    sect_id = user["location"].get("sect_id")
    sect_text = t(lang, "SECT_NONE")
    if sect_id:
        sect = storage.get_sect(sect_id) if storage else None
        if sect:
            sect_text = t(lang, sect["key"]) if sect.get("key") else sect.get("name", sect_id)

    deck = [x for x in user["combat"]["loadout"] if x]
    deck_names = " · ".join(_tech_name(tid, lang) for tid in deck[:3]) or t(lang, "PROF_NONE")
    if len(deck) > 3:
        deck_names += " …"

    sl = status_line(lang, user, now)
    return t(lang, "MENU_TITLE",
             name=user["account"]["username"],
             realm=t(lang, REALM_NAMES[realm]),
             stage=realm_stage_label(lang, realm, stage),
             path=path_text,
             qi_bar=_bar(frac),
             qi=cul["qi_current"], cap=cost,
             hp_bar=_bar(vis["physique_hp"] / vis["max_hp"] if vis["max_hp"] else 1),
             hp=f"{vis['physique_hp']} / {vis['max_hp']}",
             rate=round(rate),
             catalyst=catalyst_text(lang, user, now),
             mantra=f"{t(lang, METHODS.get(cul.get('active_method_id', ''), METHODS['method_breath_mortal'])['key'])} (×{METHODS.get(cul.get('active_method_id', ''), {'tech_mult': 1.0})['tech_mult']})",
             weapon=gear_label(lang, user["equipment"].get("weapon")),
             robe=gear_label(lang, user["equipment"].get("robe")),
             deck=deck_names,
             location=t(lang, zone_key),
             vein=user["location"].get("vein_density", 1.0),
             sect=sect_text,
             status_block=("\n" + sl) if sl else "")


def _tech_name(tech_id: str, lang: str) -> str:
    tech = data_registry.get_technique(tech_id)
    if not tech:
        return tech_id
    return tech.name_en if lang == "en" and tech.name_en else tech.name


def deep_profile_text(lang: str, user: dict) -> str:
    """Hidden-attribute matrix with reveal conditions (Part 3 §3.2)."""
    from .engine.models import combat_profile
    hidden = user["stats"]["hidden"]
    vis = user["stats"]["visible"]
    prof = combat_profile(user)
    rows = []

    def locked(key: str) -> str:
        return t(lang, "STAT_LOCKED", need=t(lang, key))

    if hidden.get("karmic_luck_revealed"):
        rows.append(t(lang, "HIDDEN_LUCK", value=hidden["karmic_luck"]))
    else:
        rows.append(locked("LUCK_NEED"))
    if hidden.get("dao_affinity_revealed"):
        rows.append(t(lang, "HIDDEN_CHARISMA", value=hidden["dao_affinity_charisma"]))
    else:
        rows.append(locked("CHARISMA_NEED"))
    risk = prof["qi_deviation_risk"]
    if hidden.get("dao_heart_revealed") or risk > 15:
        rows.append(t(lang, "STAT_DAOHEART", value=hidden["dao_heart_stability"],
                      risk=round(risk, 1)))
    else:
        rows.append(locked("DAOHEART_NEED"))
    corruption = hidden["demonic_corruption"]
    if corruption > 0:
        rows.append(t(lang, "HIDDEN_CORRUPTION", value=corruption))
        if corruption >= 30:
            rows.append(t(lang, "HIDDEN_AURA"))
    rows.append("───────")
    rows.append(t(lang, "STAT_BLOCK",
                  atk=f"{prof['spiritual_atk']} / {prof['physical_atk']}",
                  pdef=prof["physical_def"], sdef=prof["spiritual_def"],
                  spd=prof["meridian_speed"], sense=vis["spiritual_sense"],
                  wins=user["combat"]["wins"], losses=user["combat"]["losses"],
                  deaths=user["progress"].get("deaths", 0)))
    return t(lang, "DEEP_TITLE", rows="\n".join(rows))


# ── bag (Part 3 §5) ──────────────────────────────────────────────────────────

def bag_text(lang: str, user: dict, tab: str = "gear") -> str:
    inv = user["inventory"]
    used, cap = item_count(user), item_capacity(user)
    full_note = t(lang, "BAG_FULL") if used >= cap else ""
    body = ""
    if tab == "gear":
        lines = []
        for slot in EQUIP_SLOTS:
            gear = user["equipment"].get(slot)
            if gear:
                item = data_registry.get_equipment(gear.get("id", ""))
                nm = item.name_for(lang) if item else gear.get("id", "?")
                dur = int(gear.get("dur", 100))
                bar = _bar(dur / 100)
                lines.append(t(lang, "BAG_EQUIPPED", slot=t(lang, f"SLOT_{slot}"),
                               item=nm, dur=dur, bar=bar))
            else:
                lines.append(f"▫️ {t(lang, f'SLOT_{slot}')}")
        body = "\n".join(lines) or t(lang, "BAG_EMPTY")
    elif tab == "consumables":
        lines = []
        for iid, qty in sorted(inv.get("items", {}).items()):
            c = data_registry.get_consumable(iid)
            if c and c.category in ("pill", "herb", "disposable_weapon", "relic"):
                lines.append(f"• {c.name_for(lang)} ×{qty}")
        body = "\n".join(lines) or t(lang, "BAG_EMPTY")
    else:  # materials
        lines = []
        for iid, qty in sorted(inv.get("items", {}).items()):
            c = data_registry.get_consumable(iid)
            if c and c.category == "material":
                lines.append(f"• {c.name_for(lang)} ×{qty}")
        for gid in inv.get("gear", {}):
            g = data_registry.get_equipment(gid)
            if g:
                lines.append(f"• {g.name_for(lang)} ({t(lang, TIER_KEYS.get(g.tier, 'TIER_mortal'))})")
        body = "\n".join(lines) or t(lang, "BAG_EMPTY")
    stones = inv["spirit_stones"]
    return t(lang, "BAG_TITLE",
             used=used, cap=cap, full=full_note,
             low=stones.get("low", 0), mid=stones.get("mid", 0),
             high=stones.get("high", 0), heavenly=stones.get("heavenly", 0),
             tab=t(lang, f"BAG_TAB_{tab.upper()}"), body=body)


def bag_item_text(lang: str, user: dict, kind: str, item_id: str) -> str:
    item = (data_registry.get_equipment(item_id) if kind == "gear"
            else data_registry.get_consumable(item_id))
    if not item:
        return t(lang, "ERR_UNKNOWN")
    qty = (1 if kind == "gear" else items_mod.count_item(user, item_id))
    desc = _item_effect_line(lang, item)
    price = getattr(item, "price_stones", None)
    sell = getattr(item, "sell_price", 0)
    cat = getattr(item, "category", "misc").upper()
    category = t(lang, f"ITEM_CAT_{cat}")
    if category == f"ITEM_CAT_{cat}":
        category = t(lang, "ITEM_CAT_MISC")
    return t(lang, "ITEM_CARD", name=item.name_for(lang), qty=qty,
             category=category, effect=desc,
             price=t(lang, "ITEM_PRICE", value=int(price)) if price else "",
             sell=t(lang, "ITEM_SELL", value=int(sell)) if sell else "")


def _item_effect_line(lang: str, item) -> str:
    bits = []
    for field, key in (("spiritual_atk_bonus", "EFF_SPIRIT_ATK"),
                       ("physical_atk_bonus", "EFF_PHYS_ATK"),
                       ("physical_def_bonus", "EFF_PHYS_DEF"),
                       ("spiritual_def_bonus", "EFF_SPIRIT_DEF"),
                       ("hp_bonus", "EFF_HP"),
                       ("speed_bonus", "EFF_SPEED"),
                       ("capacity_bonus", "EFF_CAPACITY")):
        v = getattr(item, field, 0)
        if v:
            bits.append(t(lang, key, value=int(v)))
    crit = getattr(item, "crit_rate_bonus", 0)
    if crit:
        bits.append(t(lang, "EFF_CRIT", value=round(float(crit), 1)))
    trib = getattr(item, "tribulation_mitigation_percent", 0)
    if trib:
        bits.append(t(lang, "EFF_TRIB", value=int(trib)))
    val = getattr(item, "value", 0)
    fx = getattr(item, "fixed_damage", 0)
    action = getattr(item, "action", "")
    if action and not bits:
        bits.append(t(lang, f"ITEM_ACTION_{action.upper()}"))
    if val and action != "breakthrough_bonus":
        bits.append(t(lang, "ITEM_ACTION_VALUE", value=val))
    if fx:
        bits.append(t(lang, "ITEM_ACTION_VALUE", value=fx))
    special = getattr(item, "special_effect", None)
    if special:
        bits.append(t(lang, f"EFF_SPECIAL_{special.upper()}", _fallback=special))
    return " · ".join(bits) or t(lang, "PROF_NONE")


# ── martial hall (Part 3 §4) ─────────────────────────────────────────────────

def martial_text(lang: str, user: dict, max_slots_val: int | None = None) -> str:
    from .engine.models import max_slots as _ms
    cap = max_slots_val or _ms(user)
    cul = user["cultivation"]
    method_id = cul.get("active_method_id", "method_breath_mortal")
    m = METHODS.get(method_id, METHODS["method_breath_mortal"])
    mantra = t(lang, m["key"])

    loadout = user["combat"]["loadout"]
    slots = []
    for i in range(cap):
        tid = loadout[i] if i < len(loadout) else None
        if tid:
            tech = data_registry.get_technique(tid)
            art = data_registry.get_martial_art(data_registry.tech_owner_art.get(tid, ""))
            art_nm = art.name_for(lang) if art else "?"
            slots.append(t(lang, "DECK_SLOT_FILLED", idx=i + 1,
                           skill=_tech_name(tid, lang), qi=tech.qi_cost if tech else "?",
                           art=art_nm))
        else:
            slots.append(t(lang, "DECK_SLOT_EMPTY", idx=i + 1))
    arts = []
    for art_id in user["inventory"].get("arts", []):
        art = data_registry.get_martial_art(art_id)
        if art:
            arts.append(f"• {art.name_for(lang)} — {t(lang, 'ART_TECHS', n=len(art.techniques))}")
    return t(lang, "MARTIAL_TITLE", mantra=mantra, mult=m["tech_mult"],
             cap=cap, slots="\n".join(slots), arts="\n".join(arts) or t(lang, "ARTS_NONE"),
             hint=t(lang, "DECK_HINT", cap=cap))


def deck_picker_text(lang: str, user: dict, slot_idx: int) -> str:
    lines = []
    for tid in items_mod.owned_techniques(user):
        if tid in [x for x in user["combat"]["loadout"] if x]:
            continue
        tech = data_registry.get_technique(tid)
        if not tech:
            continue
        nm = _tech_name(tid, lang)
        lines.append(f"• {nm} · {tech.qi_cost}⚡️ · ×{tech.base_damage_multiplier}")
    return t(lang, "PICKER_TITLE", slot=slot_idx + 1,
             body="\n".join(lines) or t(lang, "PICKER_EMPTY"))


# ── shop ─────────────────────────────────────────────────────────────────────

def shop_row_label(lang: str, entry: dict) -> str:
    price = entry["price"]
    kind = entry["kind"]
    ref = entry["ref"]
    if kind == "art":
        art = data_registry.get_martial_art(ref)
        label = f"📜 {art.name_for(lang)}"
    elif kind == "method":
        label = f"🌀 {t(lang, METHODS[ref]['key'])}"
    elif kind == "equip":
        eq = data_registry.get_equipment(ref)
        label = f"🛡 {eq.name_for(lang)}"
    else:
        c = data_registry.get_consumable(ref)
        icon = {"pill": "💊", "herb": "🌿", "disposable_weapon": "📜", "relic": "🪆"}.get(c.category, "•")
        label = f"{icon} {c.name_for(lang)}"
    return t(lang, "BTN_BUY", item=label, price=price)


def shop_text(lang: str, user: dict) -> str:
    return t(lang, "SHOP_TITLE", stones=user["inventory"]["spirit_stones"].get("low", 0))


# ── map & sect (kept) ────────────────────────────────────────────────────────

def map_text(lang: str, user: dict, storage) -> str:
    zone_id = user["location"]["current_zone_id"]
    zone = storage.get_zone(zone_id)
    return t(lang, "MAP_TITLE",
             zone=t(lang, zone["key"]) if zone else zone_id,
             vein=user["location"].get("vein_density", 1.0))


def zone_text(lang: str, zone: dict, storage, viewer_id: int | None) -> str:
    if zone.get("owner"):
        if zone["owner_kind"] == "user" and zone["owner"] == viewer_id:
            owner = t(lang, "OWNER_YOU")
        elif zone["owner_kind"] == "sect":
            sect = storage.get_sect(zone["owner"])
            owner = (t(lang, sect["key"]) if sect and sect.get("key") else zone["owner"]) if sect else "?"
        else:
            owner = f"#{zone['owner']}"
    else:
        owner = t(lang, "OWNER_NONE")
    beasts = [b.name_for(lang) for b in data_registry.beasts_for_guard(zone["guard_level"])]
    return t(lang, "ZONE_INFO",
             zone=t(lang, zone["key"]),
             vein=zone["vein_density"],
             realm=t(lang, REALM_NAMES[zone["min_realm"]]),
             owner=owner,
             guard=f"⚔️ Lv.{zone['guard_level']}",
             beasts="، ".join(beasts) if beasts else "—")


def sect_text(lang: str, user: dict, storage) -> str:
    sect_id = user["location"].get("sect_id")
    if not sect_id:
        sect_name = t(lang, "SECT_NONE")
    else:
        sect = storage.get_sect(sect_id)
        sect_name = (t(lang, sect["key"]) if sect and sect.get("key")
                     else t(lang, "SECT_PLAYER", name=(sect or {}).get("name", "")))
    return t(lang, "SECT_TITLE", sect=sect_name, info="")


def dao_text(lang: str, user: dict) -> str:
    cul = user["cultivation"]
    if cul.get("dao_path"):
        current = t(lang, "DAO_ALREADY", dao=t(lang, DAO_PATHS[cul["dao_path"]]["key"]))
    else:
        current = ""
    return t(lang, "DAO_CHOICE_TITLE", current=current)


def admin_text(lang: str, storage, boost_label: str) -> str:
    return t(lang, "ADMIN_TITLE",
             status=t(lang, "ADMIN_STATUS_ONLINE"),
             users=storage.count_users(),
             meditating=storage.count_meditating(),
             boost=boost_label)


# ── combat view (Part 2 §6.1) ────────────────────────────────────────────────

def combat_view_text(lang: str, user: dict, session: dict, round_lines: list[str]) -> str:
    e = session["enemy"]
    p = session["player"]
    enemy = enemy_name(e, lang)
    ehp = _bar(e["hp"] / e["max_hp"] if e["max_hp"] else 0)
    php = _bar(p["hp"] / p["max_hp"] if p["max_hp"] else 0)
    qib = _bar(p["qi"] / p["max_qi"] if p["max_qi"] else 0)
    statuses = []
    for side, key in (("e", "COMBAT_ENEMY_STATUS"), ("p", "COMBAT_SELF_STATUS")):
        sts = session["statuses"][side]
        if sts:
            names = " · ".join(f"◈ {s['kind']} ({s['turns']})" for s in sts)
            statuses.append(t(lang, key, list=names))
    icon = {"hunt": "🐺", "conquest": "🏴", "rival": "🗡"}.get(session["kind"], "⚔️")
    return t(lang, "COMBAT_VIEW",
             icon=icon, enemy=enemy, rnd=session["round"],
             you=t(lang, "COMBAT_YOU"),
             hp_bar=php, hp=f"{int(p['hp'])}/{int(p['max_hp'])}",
             qi_bar=qib, qi=f"{int(p['qi'])}/{int(p['max_qi'])}",
             ehp_bar=ehp, ehp=f"{int(e['hp'])}/{int(e['max_hp'])}",
             erealm=t(lang, "COMBAT_GUARD_LEVEL", n=e.get("guard_level", 1)),
             log="\n".join(round_lines) if round_lines else t(lang, "COMBAT_OPENING"),
             statuses=("\n".join(statuses) + "\n") if statuses else "",
             shield=(t(lang, "COMBAT_SHIELD_LEFT", value=session["shield"]) + "\n")
             if session.get("shield") else "")


def outcome_text(lang: str, user: dict, outcome: str, session: dict) -> str:
    """Post-battle screen text for victory / flee / miracle / death."""
    reward = session.get("reward") or {}
    if outcome == "VICTORY":
        extra = ""
        if reward:
            drops = reward.get("drops") or []
            extra = "\n" + t(lang, "COMBAT_REWARD", stones=reward.get("stones", 0),
                             qi=reward.get("qi", 0))
            if drops:
                extra += "\n" + t(lang, "COMBAT_LOOT", item_list=" / ".join(drops),
                                   sep="،" if lang == "fa" else ", ")
        if session.get("kind") == "conquest":
            extra += "\n" + t(lang, "COMBAT_PLANT_FLAG")
        return t(lang, "COMBAT_WIN_TITLE") + extra
    if outcome == "FLED":
        return t(lang, "COMBAT_FLED")
    if outcome == "MIRACLE_ESCAPE":
        return t(lang, "MIRACLE_REPORT", hours=3)
    if outcome == "TRUE_DEATH":
        cul = user["cultivation"]
        return t(lang, "DEATH_REPORT", minutes=45, realm=t(lang, REALM_NAMES[cul["current_realm_index"]]))
    return t(lang, "ERR_UNKNOWN")

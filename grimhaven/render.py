"""Screen renderers — shared by the Telegram bot and the web demo console.

Every function returns plain localized text (Telegram-safe, no HTML parsing)
built from the locale tables, following the UI mockups of doc chapter 8.
"""
from __future__ import annotations

import datetime as dt

from .engine import items as items_mod
from .engine.constants import (
    DAO_PATHS,
    EQUIP_SLOTS,
    HERBS,
    METHODS,
    PAVILION_PRICES,
    PILLS,
    REALM_NAMES,
    REALM_STAGES,
    SPIRIT_STONES,
    TECHNIQUES,
    TIER_KEYS,
    ZONES,
)
from .engine.models import (
    afk_hourly_rate,
    in_seclusion,
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


def gear_label(lang: str, gear: dict | None, storage=None) -> str:
    if not gear:
        return t(lang, "GEAR_NONE")
    name_key = items_mod.GEAR_NAME_KEYS.get(gear.get("id") or gear.get("gear_id", ""), None)
    name = t(lang, name_key) if name_key else (gear.get("id") or "?")
    tier = t(lang, TIER_KEYS.get(gear.get("tier", "mortal"), "TIER_mortal"))
    return f"{name} ({tier})"


def catalyst_text(lang: str, user: dict, now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    parts = []
    stone = user["cultivation"].get("active_stone")
    if stone:
        parts.append(f"🪨 {t(lang, 'BTN_USE_STONE').split('🪨')[-1].strip()} +{int(SPIRIT_STONES[stone]['boost']*100)}%")
    for buff in user.get("buffs", []):
        until = parse_iso(buff.get("until"))
        if until and until > now:
            parts.append(f"🩸 +{int(float(buff['boost'])*100)}%")
    method = METHODS.get(user["cultivation"].get("active_method_id", ""))
    if method and method["tech_mult"] > 1.0:
        parts.append(f"🌀 ×{method['tech_mult']}")
    return " + ".join(parts) if parts else t(lang, "PROF_NONE")


def hidden_block(lang: str, user: dict) -> str:
    hidden = user["stats"]["hidden"]
    lines = []
    if hidden.get("karmic_luck_revealed"):
        lines.append(t(lang, "HIDDEN_LUCK", value=hidden["karmic_luck"]))
    if hidden.get("dao_affinity_revealed"):
        lines.append(t(lang, "HIDDEN_CHARISMA", value=hidden["dao_affinity_charisma"]))
    if hidden.get("dao_heart_revealed"):
        pass  # dao heart is shown in the visible block already
    corruption = hidden["demonic_corruption"]
    if corruption >= 30:
        lines.append(t(lang, "HIDDEN_CORRUPTION", value=corruption))
    if not lines:
        lines.append(t(lang, "HIDDEN_LOCKED"))
    return "\n".join(lines)


def profile_text(storage, user: dict, world_boost: float = 1.0,
                 now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    lang = user["account"]["language"]
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis, hidden = user["stats"]["visible"], user["stats"]["hidden"]

    dao_key = DAO_PATHS[cul["dao_path"]]["key"] if cul.get("dao_path") else None
    align_key = "ALIGN_ORTHODOX_NAME" if cul["alignment"] == "orthodox" else "ALIGN_DEMONIC_NAME"
    path_text = t(lang, dao_key) if dao_key else t(lang, "PROF_NONE")
    path_text += f" ({t(lang, align_key)})" if dao_key else ""

    cost = stage_cost(user)
    frac = cul["qi_current"] / cost if cost else 1.0
    rate = afk_hourly_rate(user, world_boost=world_boost, now=now)
    if cul["meditating"]:
        status = t(lang, "STATUS_MEDITATING")
    else:
        status = ""
    if in_seclusion(user, now):
        finish = parse_iso(cul["seclusion_finish_time"])
        mins = max(1, int((finish - now).total_seconds() // 60) + 1)
        status = (status + "\n" if status else "") + t(lang, "STATUS_SECLUSION", minutes=mins)
    if meridians_sealed(user, now):
        until = parse_iso(cul["meridian_sealed_until"])
        hours = max(1, int((until - now).total_seconds() // 3600) + 1)
        status = (status + "\n" if status else "") + t(lang, "STATUS_MERIDIAN_SEALED", hours=hours)

    zone = storage.get_zone(cul and user["location"]["current_zone_id"]) if storage else None
    zone_key = zone["key"] if zone else ZONES[user["location"]["current_zone_id"]]["key"]
    sect_id = user["location"].get("sect_id")
    sect_text = t(lang, "SECT_NONE")
    if sect_id:
        sect = storage.get_sect(sect_id) if storage else None
        if sect:
            sect_text = t(lang, sect["key"]) if sect.get("key") else sect.get("name", sect_id)

    daoheart_risk = max(0, int((100 - hidden["dao_heart_stability"]) / 4))
    return t(lang, "MENU_TITLE",
             name=user["account"]["username"],
             realm=t(lang, REALM_NAMES[realm]),
             stage=realm_stage_label(lang, realm, stage),
             path=path_text,
             alignment=t(lang, align_key),
             bar=_bar(frac),
             qi=cul["qi_current"], cap=cost,
             rate=round(rate),
             catalyst=catalyst_text(lang, user, now),
             hp=f"{vis['physique_hp']} / {vis['max_hp']}",
             sense=vis["spiritual_sense"],
             circ=vis["circulation_velocity"],
             daoheart=t(lang, "PROF_DAOHEART_STABLE", risk=daoheart_risk),
             hidden_block=hidden_block(lang, user),
             weapon=gear_label(lang, user["equipment"].get("weapon")),
             robe=gear_label(lang, user["equipment"].get("robe")),
             accessory=gear_label(lang, user["equipment"].get("accessory")),
             location=t(lang, zone_key),
             vein=user["location"].get("vein_density", 1.0),
             sect=sect_text,
             status_block=status)


def backpack_text(lang: str, user: dict) -> str:
    inv = user["inventory"]
    stones = inv["spirit_stones"]
    pills = ", ".join(f"{t(lang, PILLS[p]['key'])} ×{n}" for p, n in inv.get("pills", {}).items() if n > 0) \
        or t(lang, "PROF_NONE")
    herbs = []
    for h, n in inv.get("herbs", {}).items():
        if n > 0 and h in HERBS:
            herbs.append(f"{t(lang, HERBS[h]['key'])} ×{n}")
    herbs_text = ", ".join(herbs) or t(lang, "PROF_NONE")
    if inv.get("herbs", {}).get("root_used"):
        herbs_text += " ✔"
    gear = ", ".join(gear_label(lang, {"id": g, "tier": d["tier"]})
                     for g, d in inv.get("gear", {}).items()) or t(lang, "PROF_NONE")
    equipped = "\n".join(
        t(lang, "EQUIP_LINE",
          slot=t(lang, f"SLOT_{slot}"),
          item=gear_label(lang, user["equipment"].get(slot)))
        for slot in EQUIP_SLOTS)
    return t(lang, "BACKPACK_TITLE",
             low=stones.get("low", 0), mid=stones.get("mid", 0),
             high=stones.get("high", 0), heavenly=stones.get("heavenly", 0),
             pills=pills, herbs=herbs_text, gear=gear, equipped=equipped)


def shop_text(lang: str, user: dict) -> str:
    return t(lang, "SHOP_TITLE", stones=user["inventory"]["spirit_stones"].get("low", 0))


def shop_button_label(lang: str, shop_id: str) -> str:
    item = items_mod.SHOP_CATALOG[shop_id]
    if item["kind"] == "stone":
        item_name = f"🪨 {item['grade']}"
    elif item["kind"] == "pill":
        item_name = f"💊 {t(lang, PILLS[item['pill']]['key'])}"
    elif item["kind"] == "herb":
        item_name = f"🌿 {t(lang, HERBS[item['herb']]['key'])}"
    elif item["kind"] == "method":
        item_name = f"🌀 {t(lang, METHODS[item['method']]['key'])}"
    elif item["kind"] == "technique":
        item_name = f"⚔️ {t(lang, TECHNIQUES[item['tech']]['key'])}"
    elif item["kind"] == "gear":
        item_name = f"🛡 {t(lang, items_mod.GEAR_NAME_KEYS.get(item['gear_id'], item['gear_id']))}"
    else:
        item_name = f"🪆 {t(lang, 'ITEM_DOLL_SUBSTITUTE')}"
    return t(lang, "BTN_BUY", item=item_name, price=PAVILION_PRICES[shop_id])


def martial_text(lang: str, user: dict) -> str:
    inv = user["inventory"]
    method_id = user["cultivation"].get("active_method_id", "method_breath_mortal")
    methods = "\n".join(
        f"{'▶️' if m == method_id else '•'} {t(lang, METHODS[m]['key'])} (×{METHODS[m]['tech_mult']})"
        for m in inv.get("methods", []))
    techs = "\n".join(f"• {t(lang, TECHNIQUES[x]['key'])}" for x in inv.get("techniques", []))
    loadout = []
    for i, tech in enumerate(user["combat"]["loadout"], 1):
        label = t(lang, TECHNIQUES[tech]["key"]) if tech else t(lang, "PROF_NONE")
        loadout.append(f"[{i}] {label}")
    return t(lang, "MARTIAL_TITLE",
             method=t(lang, METHODS.get(method_id, METHODS["method_breath_mortal"])["key"]),
             mult=METHODS.get(method_id, {"tech_mult": 1.0})["tech_mult"],
             methods=methods, techniques=techs, loadout="\n".join(loadout))


def map_text(lang: str, user: dict, storage) -> str:
    zone_id = user["location"]["current_zone_id"]
    zone = storage.get_zone(zone_id)
    return t(lang, "MAP_TITLE",
             zone=t(lang, zone["key"]) if zone else zone_id,
             vein=user["location"].get("vein_density", 1.0))


def zone_text(lang: str, zone: dict, storage, viewer_id: int | None) -> str:
    kind, sect_key = None, None
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
    return t(lang, "ZONE_INFO",
             zone=t(lang, zone["key"]),
             vein=zone["vein_density"],
             realm=t(lang, REALM_NAMES[zone["min_realm"]]),
             owner=owner,
             guard=f"⚔️ Lv.{zone['guard_level']}")


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


def battle_log_text(lang: str, result: dict) -> str:
    lines = []
    for entry in result["log"]:
        who = "🧑" if entry["actor"] == "player" else "👹"
        tech = entry.get("tech")
        name = t(lang, TECHNIQUES[tech]["key"]) if tech else ""
        crit = " 💢" if entry.get("crit") else ""
        guard = " 🛡" if entry.get("guard") else ""
        lines.append(f"{who} {name} → {entry['damage']}{crit}{guard}".rstrip())
    if result["won"]:
        outcome = "🏆 VICTORY"
    else:
        outcome = "💀 DEFEAT"
    return t(lang, "BATTLE_REPORT", log="\n".join(lines[-14:]), result=outcome)

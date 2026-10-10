"""Screen renderers v2 — exact templates from docs/mainPrompt2 (parts 1–6).

Every screen the dock can reach lives here: the destiny-scroll HUD with its
two modal sub-views, the paginated bag + item identity cards, the world map
and zone hub cards, the message-by-message battle HUD and Loot Scroll, the
meditation hub with its chronicle report, the martial pavilion deck builder,
the liberated Spirit Pavilion, the sect gate and the dramatic tribulation
cards. All strings are locale-keyed; numbers render as Persian digits for
the fa locale through ``t()``.
"""
from __future__ import annotations

import datetime as dt

from .core.combat_engine import enemy_name, enemy_proto
from .core.data_loader import data_registry
from .core.state_machine import (UserStatus, active_debuff, get_status,
                                 paralysis_active)
from .engine import items as items_mod
from .engine.constants import (
    DAO_PATHS,
    EQUIP_SLOTS,
    REALM_NAMES,
    REALM_STAGES,
    SPIRIT_STONES,
    TIER_KEYS,
)
from .engine.models import (
    active_buffs,
    as_float,
    afk_hourly_rate,
    comprehension,
    crit_chance,
    in_seclusion,
    item_capacity,
    item_count,
    lifespan_years,
    meridians_opened,
    meridians_sealed,
    parse_iso,
    qi_purity,
    stage_cost,
    utcnow,
)
from .localization import t

BAR_WIDTH = 10
DIV = "━━━━━━━━━━━━━━━━━━━━━"


def _bar(fraction: float) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * BAR_WIDTH))
    return "▰" * filled + "▱" * (BAR_WIDTH - filled)


def bar(cur, mx) -> str:
    """10-segment spec bar from raw values."""
    return _bar(cur / mx if mx else 0.0)


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
    return t(lang, ("STAGE_EARLY", "STAGE_MID", "STAGE_LATE", "STAGE_PEAK")[min(stage, 3)])


def target_label(lang: str, realm: int, stage: int) -> str:
    return f"{t(lang, REALM_NAMES[realm])} [{realm_stage_label(lang, realm, stage)}]"


def item_name(item_id: str, lang: str) -> str:
    return data_registry.item_name(item_id, lang)


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
    for buff in active_buffs(user, now):
        rate_mult = buff.get("rate_mult")
        if rate_mult is not None and as_float(rate_mult, 1.0) < 1.0:
            parts.append(f"😈 ×{as_float(rate_mult, 1.0):g}")
            continue
        parts.append(f"🩸 +{int(as_float(buff.get('boost'), 0.0) * 100)}%")
    if user.get("progress", {}).get("root_ancient_used"):
        parts.append("🌿 +25%")
    m = data_registry.get_method(user["cultivation"].get("active_method_id") or "")
    if m and m.qi_mult > 1.0:
        parts.append(f"🌀 ×{m.qi_mult}")
    return " + ".join(parts) if parts else t(lang, "PROF_NONE")


def status_line(lang: str, user: dict, now: dt.datetime | None = None) -> str:
    now = now or utcnow()
    status = get_status(user)
    lines = []
    if status is UserStatus.IN_COMBAT and user["combat"].get("session"):
        e = user["combat"]["session"]["enemy"]
        lines.append(t(lang, "STATUS_IN_COMBAT", enemy=enemy_name(e, lang)))
    if meridians_sealed(user, now):
        until = parse_iso(user["cultivation"]["meridian_sealed_until"])
        hours = max(1, int((until - now).total_seconds() // 3600) + 1) if until else 0
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


def _sect_name(lang: str, user: dict, storage) -> str:
    sect_id = user.get("location", {}).get("sect_id")
    if not sect_id:
        return ""
    sect = storage.get_sect(sect_id) if storage else None
    if sect:
        return t(lang, sect["key"]) if sect.get("key") else sect.get("name", sect_id)
    return sect_id


# ═══════════════════════════════ profile HUD (P1 §4) ══════════════════════════

def hud_text(storage, user: dict, world_boost: float = 1.0,
             now: dt.datetime | None = None) -> str:
    """The destiny scroll — spec P4.1 template, no empty placeholders."""
    now = now or utcnow()
    lang = user["account"]["language"]
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis = user["stats"]["visible"]
    from .engine.models import combat_profile
    prof = combat_profile(user, now)
    loc = user["location"]
    zdef = data_registry.get_zone(loc.get("current_zone_id", ""))
    density = loc.get("vein_density") or loc.get("density") or (zdef.density if zdef else 1.0)
    rate = afk_hourly_rate(user, world_boost=world_boost, now=now)

    # equipped block: active slots only — never render an empty line
    eq_lines = []
    for icon, slot, key in (("🗡️", "weapon", "HUD_EQ_WEAPON"),
                            ("🥋", "robe", "HUD_EQ_ROBE"),
                            ("💍", "ring", "HUD_EQ_RING"),
                            ("🧿", "accessory", "HUD_EQ_ACCESSORY"),
                            ("🐾", "companion", "HUD_EQ_COMPANION")):
        g = user["equipment"].get(slot)
        if g:
            eq_lines.append(f"{icon} {t(lang, key)}: {gear_label(lang, g)}")
    sect = _sect_name(lang, user, storage)
    align_key = ("ALIGN_ORTHODOX_NAME" if cul["alignment"] == "orthodox" else "ALIGN_DEMONIC_NAME")
    dao_key = DAO_PATHS[cul["dao_path"]]["key"] if cul.get("dao_path") else None

    out = [t(lang, "HUD_TITLE", name=user["account"]["username"])]
    out.append(t(lang, "HUD_RANK", realm=t(lang, REALM_NAMES[realm]),
                 layer=stage + 1, dao=(t(lang, dao_key) + f" ({t(lang, align_key)})") if dao_key else ""))
    out.append(t(lang, "HUD_QI", qi_bar=bar(cul["qi_current"], cul["qi_capacity"]),
                 qi=cul["qi_current"], cap=cul["qi_capacity"],
                 pct=int(round(100 * cul["qi_current"] / max(1, cul["qi_capacity"])))))
    out.append(t(lang, "HUD_RATE", rate=round(rate), density=density,
                 location=(zdef.name_for(lang) if zdef else loc.get("name", ""))))
    out.append("")
    out.append(t(lang, "HUD_HP", hp_bar=bar(vis["physique_hp"], vis["max_hp"]),
                 hp=vis["physique_hp"], max_hp=vis["max_hp"]))
    out.append(t(lang, "HUD_ATK", atk=prof["spiritual_atk"]))
    out.append(t(lang, "HUD_DEF", pdef=prof["physical_def"], sdef=prof["spiritual_def"]))
    out.append(t(lang, "HUD_SPEED", speed=prof["meridian_speed"]))
    out.extend(eq_lines)
    out.append(t(lang, "HUD_SECT", sect=sect or t(lang, "HUD_NO_SECT")))
    sl = status_line(lang, user, now)
    if sl:
        out.append(DIV)
        out.append(sl)
    return "\n".join(out)


def meridians_text(lang: str, user: dict) -> str:
    """Hidden meridians & dao modal (P1 §4.2)."""
    from .engine.models import combat_profile
    hidden = user["stats"]["hidden"]
    prof = combat_profile(user)
    cul = user["cultivation"]
    align = t(lang, {"orthodox": "ALIGN_LIGHT", "demonic": "ALIGN_DARK"}.get(cul["alignment"], "ALIGN_NEUTRAL"))
    lines = [
        t(lang, "MER_TITLE"),
        t(lang, "MER_SENSE", sense=prof["divine_sense"]),
        t(lang, "MER_CRIT_LINE", crit=round(crit_chance(user), 1), comp=comprehension(user)),
        t(lang, "MER_DAO", value=hidden["dao_heart_stability"]),
        t(lang, "MER_DEV", risk=round(prof["qi_deviation_risk"], 1)),
        t(lang, "MER_FORTUNE", fortune=hidden["karmic_luck"]),
        t(lang, "MER_PURITY", purity=qi_purity(user)),
        t(lang, "MER_MERIDIANS", opened=meridians_opened(user)),
        t(lang, "MER_LIFESPAN", years=lifespan_years(user)),
        "",
        t(lang, "MER_ALIGN", align=align, karma=hidden["karmic_luck"]),
    ]
    return "\n".join(lines)


def karma_text(lang: str, user: dict) -> str:
    """Ethical standing & dao milestones (P1 §4.3)."""
    hidden = user["stats"]["hidden"]
    cul = user["cultivation"]
    alignment = cul["alignment"]
    align_key = {"orthodox": "ALIGN_ORTHODOX_NAME", "demonic": "ALIGN_DEMONIC_NAME"}.get(
        alignment, "ALIGN_NEUTRAL_NAME")
    dao_key = DAO_PATHS[cul["dao_path"]]["key"] if cul.get("dao_path") else None
    prog = user["progress"]
    lines = [
        t(lang, "KARMA_TITLE"),
        t(lang, "KARMA_ALIGN", align=t(lang, align_key)),
        t(lang, "KARMA_POINTS", points=hidden["karmic_luck"]),
        t(lang, "KARMA_CORRUPT", index=hidden["demonic_corruption"]),
        "",
        t(lang, "KARMA_RECORD", wins=user["combat"]["wins"], losses=user["combat"]["losses"],
          deaths=prog.get("deaths", 0), miracles=prog.get("miracle_escapes", 0)),
        "",
        t(lang, "KARMA_PATH",
          path=t(lang, dao_key) if dao_key else t(lang, "KARMA_PATH_OPEN"),
          realm=t(lang, REALM_NAMES[cul["current_realm_index"]]),
          attempts=prog["breakthrough_attempts"], successes=prog["breakthrough_successes"]),
    ]
    if hidden["demonic_corruption"] >= 30:
        lines.append(t(lang, "HIDDEN_AURA"))
    return "\n".join(lines)


# legacy API kept for admin/other screens
def profile_text(storage, user: dict, world_boost: float = 1.0,
                 now: dt.datetime | None = None) -> str:
    return hud_text(storage, user, world_boost=world_boost, now=now)


def deep_profile_text(lang: str, user: dict) -> str:
    return meridians_text(lang, user)


# ═══════════════════════════════════ bag (P2) ═════════════════════════════════

BAG_TABS = ("gear", "consumables", "materials")
ITEMS_PER_PAGE = 5


def _bag_rows(lang: str, user: dict, tab: str) -> list[tuple[str, str]]:
    """[(item_id, row_label)] for a tab, equipped gear excluded from spare rows."""
    rows: list[tuple[str, str]] = []
    inv = user["inventory"]
    if tab == "gear":
        for gid, g in sorted((inv.get("gear") or {}).items()):
            if any(gg and gg.get("id") == gid and gd == g for gg, gd in
                   ((None, None),)):  # placeholder; identity handled below
                pass
            item = data_registry.get_equipment(gid)
            if not item:
                continue
            equipped_slot = next((s for s, gg in (user["equipment"].items() or {})
                                  if gg and gg.get("id") == gid), None)
            dur = int((g or {}).get("dur", 100)) if isinstance(g, dict) else 100
            icon = "⚔️" if item.slot == "weapon" else "🥋" if item.slot == "robe" else "💍" if item.slot == "ring" else "🧿" if item.slot == "accessory" else "🐾"
            label = f"{icon} {item.name_for(lang)}"
            if dur < 100:
                label += f" ✚{dur}%"
            if equipped_slot:
                label += " ✅"
            rows.append((gid, label))
    else:
        for iid, qty in sorted((inv.get("items") or {}).items()):
            if qty <= 0:
                continue
            item = data_registry.get_consumable(iid)
            if not item:
                continue
            is_mat = item.category in ("material", "herb")
            if tab == "materials" and not is_mat:
                continue
            if tab == "consumables" and is_mat:
                continue
            icon = {"material": "⛏️", "herb": "🌿", "pill": "💊", "talisman": "📜",
                    "disposable_weapon": "📜", "relic": "🔮", "stone": "🪨"}.get(item.category, "💊")
            if tab == "consumables" and item.category in ("talisman", "disposable_weapon"):
                icon = "📜"
            rows.append((iid, f"{icon} {item.name_for(lang)} (×{qty})"))
    return rows


def bag_text(lang: str, user: dict, tab: str = "gear", page: int = 1) -> str:
    """Paginated bag screen (P2): header + tab rows + 5-item page."""
    rows = _bag_rows(lang, user, tab)
    pages = max(1, (len(rows) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, pages))
    from .engine.models import item_count
    used, cap = item_count(user), item_capacity(user)
    stones = user["inventory"]["spirit_stones"]
    tab_labels = {
        "gear": ("⚔️", t(lang, "BAG_TAB_GEAR")),
        "consumables": ("💊", t(lang, "BAG_TAB_PILLS")),
        "materials": ("🌿", t(lang, "BAG_TAB_MATS")),
    }
    lines = [t(lang, "BAG_TITLE", used=used, cap=cap,
               low=stones.get("low", 0), mid=stones.get("mid", 0), high=stones.get("high", 0))]
    lines.append(" | ".join((f"🔸 {ic} {nm}" if k == tab else f"{ic} {nm}")
                            for k, (ic, nm) in ((t_, tab_labels[t_]) for t_ in BAG_TABS)))
    lines.append(DIV)
    if tab == "gear":
        worn = [(s, g) for s, g in (user["equipment"].items() or {}) if g]
        if worn:
            lines.append(t(lang, "BAG_WORN"))
            for slot, g in worn:
                lines.append(f"• {slot_label(lang, slot)}: {gear_label(lang, g)}")
            lines.append(DIV)
    chunk = rows[(page - 1) * ITEMS_PER_PAGE: page * ITEMS_PER_PAGE]
    if not chunk:
        empty_key = {"gear": "BAG_EMPTY_GEAR", "consumables": "BAG_EMPTY_PILLS",
                     "materials": "BAG_EMPTY_MATS"}[tab]
        lines.append(t(lang, empty_key))
    for _iid, label in chunk:
        lines.append(f"▪️ {label}")
    lines.append(DIV)
    lines.append(t(lang, "BAG_PAGE", page=page, pages=pages))
    return "\n".join(lines)


def slot_label(lang: str, slot: str) -> str:
    return t(lang, f"SLOT_{slot.upper()}")


def bag_inspect_text(lang: str, user: dict, item_id: str) -> str:
    """🔍 item identity card (P2): name, quantity, lore, effects, sell value."""
    gear = data_registry.get_equipment(item_id)
    item = data_registry.get_consumable(item_id)
    obj = gear or item
    name = obj.name_for(lang) if obj else item_id
    icon = "⚔️" if (gear and gear.slot == "weapon") else "🛡️" if gear else (
        {"pill": "💊", "herb": "🌿", "material": "⛏️", "talisman": "📜",
         "disposable_weapon": "📜", "relic": "🔮"}.get(getattr(item, "category", ""), "🎒"))
    lines = [t(lang, "INSPECT_TITLE", icon=icon, name=name,
               tier=t(lang, TIER_KEYS.get(getattr(obj, "tier", "mortal") if gear else "mortal", "TIER_mortal")) if gear else "").replace("  ", " ")]
    qty = ""
    if gear:
        owned = (user["inventory"].get("gear") or {}).get(item_id)
        equipped_slot = next((s for s, g in (user["equipment"].items() or {}) if g and g.get("id") == item_id), None)
        lines.append(t(lang, "INSPECT_OWNED", owned=1 if owned else 0))
        if equipped_slot:
            lines.append(t(lang, "INSPECT_EQUIPPED", slot=slot_label(lang, equipped_slot)))
    else:
        count = items_mod.count_item(user, item_id)
        lines.append(t(lang, "INSPECT_QTY", qty=count))
    desc = ""
    if item:
        desc = item.description_en if (lang == "en" and getattr(item, "description_en", None)) \
            else getattr(obj, "description", "")
    elif gear:
        desc = getattr(gear, "description", "") or ""
        if not desc and lang == "en":
            desc = getattr(gear, "description_en", "") or ""
    if desc:
        lines.append(t(lang, "INSPECT_LORE", lore=desc))
    effects = _item_effect_lines(lang, obj)
    if effects:
        lines.append(t(lang, "INSPECT_EFFECTS"))
        lines.extend(effects)
    sell = int(getattr(obj, "sell_price", 0) or 0)
    if sell:
        lines.append(t(lang, "INSPECT_SELL", value=sell))
    return "\n".join(lines)


def _item_effect_lines(lang: str, obj) -> list[str]:
    out = []
    if obj is None:
        return out
    fields = (("spiritual_atk_bonus", "EFF_SP_ATK"), ("physical_atk_bonus", "EFF_PHYS_ATK"),
              ("physical_def_bonus", "EFF_PDEF"), ("spiritual_def_bonus", "EFF_SDEF"),
              ("hp_bonus", "EFF_HP"), ("speed_bonus", "EFF_SPEED"),
              ("capacity_bonus", "EFF_CAPACITY"), ("crit_rate_bonus", "EFF_CRIT"),
              ("tribulation_mitigation_percent", "EFF_TRIB_MIT"))
    for field, key in fields:
        v = getattr(obj, field, 0) or 0
        if v:
            if field == "crit_rate_bonus":
                v = int(float(v) * 100)
            out.append(f"• {t(lang, key)}: +{v}")
    special = getattr(obj, "special_effect", "") or ""
    if special:
        # gear specials are stored as internal keys (leech_qi_percent_3, stun_10, …)
        # and localized via EFF_SPECIAL_<KEY>
        out.append(f"• {t(lang, 'EFF_SPECIAL_' + special.upper())}")
    action = getattr(obj, "action", "")
    if action in ("heal", "restore_hp"):
        out.append(f"• {t(lang, 'EFF_HEAL')}: +{getattr(obj, 'value', 0)}")
    elif action in ("restore_qi",):
        out.append(f"• {t(lang, 'EFF_QI')}: +{getattr(obj, 'value', 0)}")
    elif action == "breakthrough_bonus":
        out.append(f"• {t(lang, 'EFF_PILL_BONUS')}: +{getattr(obj, 'value', 0)}%")
    elif action == "instant_damage":
        out.append(f"• {t(lang, 'EFF_ITEM_DMG')}: {getattr(obj, 'fixed_damage', 0)}")
    elif action == "shield":
        out.append(f"• {t(lang, 'EFF_SHIELD')}: {getattr(obj, 'shield_value', 0)}")
    elif action == "enemy_debuff":
        out.append(f"• {t(lang, 'EFF_DEBUFF')}: −{getattr(obj, 'atk_cut_percent', 20)}% ({getattr(obj, 'turns', 3)}⏳)")
    return out


# legacy signature used by older screens/tests
def bag_item_text(lang: str, user: dict, kind: str, item_id: str) -> str:
    return bag_inspect_text(lang, user, item_id)


# ═══════════════════════════════ world map (P3) ═══════════════════════════════

def map_text(lang: str, user: dict, storage=None) -> str:
    loc = user["location"]
    zdef = data_registry.get_zone(loc.get("current_zone_id", ""))
    cur = zdef.name_for(lang) if zdef else loc.get("name", "")
    density = loc.get("vein_density") or loc.get("density") or (zdef.density if zdef else 1.0)
    return (f"{t(lang, 'MAP_TITLE')}\n\n"
            f"{t(lang, 'MAP_HERE', loc=cur, density=density)}\n"
            f"{t(lang, 'MAP_SUB')}\n\n"
            f"{DIV}")


def zone_hub_text(lang: str, user: dict, zone_id: str) -> str:
    z = data_registry.get_zone(zone_id)
    if not z:
        return t(lang, "ERR_UNKNOWN")
    enemies = data_registry.enemies_by_zone_list(zone_id)
    beast_names = "، ".join(e.name_for(lang) for e in enemies[:4]) if enemies else z.beasts_for(lang)
    settled = user["location"].get("current_zone_id") == zone_id
    lines = [
        t(lang, "ZONEH_TITLE", name=z.name_for(lang)),
        t(lang, "ZONEH_DANGER", danger=z.danger_for(lang), rec=z.rec_for(lang)),
        "",
        t(lang, "ZONEH_ATMO"),
        t(lang, "ZONEH_LORE", lore=z.lore_for(lang)),
        "",
        t(lang, "ZONEH_RES"),
        t(lang, "ZONEH_DENSITY", density=z.density),
        t(lang, "ZONEH_BEASTS", beasts=beast_names),
        t(lang, "ZONEH_MATERIALS", materials=z.materials_for(lang)),
        DIV,
        t(lang, "ZONEH_PROMPT"),
    ]
    if settled:
        lines.insert(1, t(lang, "ZONEH_SETTLED"))
    return "\n".join(lines)


# legacy compat
def zone_text(lang: str, zone: dict, storage, viewer_id: int | None) -> str:
    z = data_registry.get_zone(zone.get("zone_id", ""))
    if not z:
        return t(lang, "ERR_UNKNOWN")
    owner_txt = t(lang, "OWNER_NONE")
    if zone.get("owner"):
        if zone.get("owner_kind") == "sect":
            sect = storage.get_sect(zone["owner"]) if storage else None
            owner_txt = (t(lang, sect["key"]) if sect and sect.get("key") else (sect or {}).get("name", "")) if sect else "—"
        elif zone.get("owner_kind") == "user" and zone["owner"] == viewer_id:
            owner_txt = t(lang, "OWNER_YOU")
        else:
            owner_txt = f"tg:{zone['owner']}"
    return t(lang, "ZONE_INFO_OLD", zone=z.name_for(lang), density=z.density,
             realm=t(lang, REALM_NAMES[z.min_realm]), owner=owner_txt,
             beasts="، ".join(e.name_for(lang) for e in data_registry.enemies_by_zone_list(z.zone_id)))


# ═══════════════════════════════ combat (P4) ══════════════════════════════════

def combat_hud_text(lang: str, user: dict, session: dict,
                    narrative_player: str = "", narrative_enemy: str = "") -> str:
    """The per-round battle HUD — spec P4 §2 template, verbatim layout."""
    e = session["enemy"]
    p = session["player"]
    proto = enemy_proto(e)
    e_name = enemy_name(e, lang)
    e_tier = proto.tier_for(lang) if proto else t(lang, "ENEMY_TIER_WILD")
    e_icon = (proto.icon if proto else "🐺")
    e_status = _status_summary(lang, session["statuses"]["e"]) or t(lang, "STATUS_ENRAGED")
    cul = user["cultivation"]
    p_realm = t(lang, REALM_NAMES[cul["current_realm_index"]])
    log = session.get("log") or {}
    narr_p = narrative_player or log.get("p") or ""
    narr_e = narrative_enemy or log.get("e") or ""
    out = [
        t(lang, "COMBAT_TITLE", enemy=e_name, rnd=session["round"]),
        "",
        f"{e_icon} {e_name} [{e_tier}]",
        t(lang, "COMBAT_EHP", ehp_bar=bar(e["hp"], e["max_hp"]), hp=e["hp"], max=e["max_hp"]),
        t(lang, "COMBAT_ESTATUS", status=e_status),
        "",
        "⚡ VS ⚡",
        "",
        t(lang, "COMBAT_PNAME", name=user["account"]["username"], realm=p_realm),
        t(lang, "COMBAT_PHP", php_bar=bar(p["hp"], p["max_hp"]), hp=p["hp"], max=p["max_hp"]),
        t(lang, "COMBAT_PQI", qi_bar=bar(p["qi"], p["max_qi"]), qi=p["qi"], qi_max=p["max_qi"]),
    ]
    if narr_p or narr_e:
        out.append(DIV)
        out.append(t(lang, "COMBAT_LOG_HDR"))
        out.append(f"🔹 {narr_p or t(lang, 'COMBAT_LOG_NONE')}")
        out.append(f"🔸 {narr_e or t(lang, 'COMBAT_LOG_NONE')}")
    out.append(DIV)
    out.append(t(lang, "COMBAT_PROMPT"))
    return "\n".join(out)


# older signature — the round message keeps the same builder
def combat_view_text(lang: str, user: dict, session: dict, round_lines: list[str]) -> str:
    return combat_hud_text(lang, user, session)


def _status_summary(lang: str, statuses: list[dict]) -> str:
    if not statuses:
        return ""
    parts = []
    for st in statuses:
        icon = {"burn": "🔥", "poison": "☠️", "bleed": "🩸", "stun": "💫",
                "slow": "🐌", "atk_buff": "🗡️", "fortify": "🛡️", "regen": "🌿"}.get(st["kind"], "🌀")
        parts.append(f"{icon} {t(lang, 'STATUS_' + st['kind'].upper())} ({st['turns']}⏳)")
    return " · ".join(parts)


def loot_scroll_text(lang: str, user: dict, session: dict) -> str:
    """The clean Loot Scroll — spec P4 §5.2."""
    e = session["enemy"]
    reward = session.get("reward") or {}
    drops = reward.get("drops") or []
    e_name = enemy_name(e, lang)
    loot_str = "، ".join(drops) if drops else t(lang, "LOOT_NONE")
    return (
        f"{t(lang, 'VICTORY_TITLE', enemy=e_name)}\n\n"
        f"{t(lang, 'VICTORY_FLAVOR')}\n\n"
        f"{t(lang, 'VICTORY_GAINS')}\n"
        f"🔹 {t(lang, 'VICTORY_QI', qi=reward.get('qi', 0))}\n"
        f"🔹 {t(lang, 'VICTORY_STONES', stones=reward.get('stones', 0))}\n"
        f"🔹 {t(lang, 'VICTORY_MATS', items=loot_str)}\n"
        f"{DIV}"
    )


def flee_card_text(lang: str, user: dict, session: dict) -> str:
    return t(lang, "FLEE_CARD", enemy=enemy_name(session["enemy"], lang))


def outcome_text(lang: str, user: dict, outcome: str, session: dict) -> str:
    """Cards for non-victory terminations (flee / miracle / true death)."""
    if outcome == "FLED":
        return flee_card_text(lang, user, session)
    if outcome == "MIRACLE_ESCAPE":
        return t(lang, "MIRACLE_CARD")
    if outcome == "TRUE_DEATH":
        rep = _death_summary(user)
        return t(lang, "DEATH_CARD", **rep)
    return t(lang, "ERR_UNKNOWN")


def _death_summary(user: dict) -> dict:
    from .core.state_machine import PARALYSIS_MINUTES
    return {"minutes": PARALYSIS_MINUTES}


# ══════════════════════════ meditation hub & chronicle (P6) ══════════════════

def meditate_hub_text(lang: str, user: dict, world_boost: float = 1.0,
                      now: dt.datetime | None = None) -> str:
    from .engine.cultivation import CultivationEngine
    s = CultivationEngine.hub_state(user, now=now)
    rate = afk_hourly_rate(user, world_boost=world_boost, now=now or utcnow())
    out = [
        t(lang, "HUB_TITLE"),
        t(lang, "HUB_LOCATION", loc=s["location_name"], density=s["density"]),
        t(lang, "HUB_MANTRA", mantra=s["mantra_name"] or t(lang, "HUB_MANTRA_NONE"), mult=s["mantra_mult"]),
        t(lang, "HUB_QI_HDR"),
        t(lang, "HUB_QI_BAR", qi_bar=bar(s["qi"], s["max_qi"]), qi=s["qi"], max=s["max_qi"], pct=s["pct"]),
        t(lang, "HUB_RATE", rate=round(rate)),
        t(lang, "HUB_ELAPSED", hours=s["elapsed_hours"], minutes=s["elapsed_minutes"]),
        t(lang, "HUB_PENDING", qi=s["unclaimed"]),
        DIV,
        t(lang, "HUB_PROMPT"),
    ]
    return "\n".join(out)


def chronicle_text(lang: str, report: str) -> str:
    return report  # engine-rendered chronicle keeps its own template


def catalyst_menu_text(lang: str, user: dict) -> str:
    stones = user["inventory"]["spirit_stones"]
    lines = [t(lang, "CAT_TITLE")]
    for grade, key in (("low", "STONE_LOW"), ("mid", "STONE_MID"), ("high", "STONE_HIGH")):
        sdef = SPIRIT_STONES.get(grade, {})
        if sdef:
            lines.append(t(lang, "CAT_ROW", icon=sdef.get("icon", "🪨"),
                           name=t(lang, key), qty=stones.get(grade, 0),
                           boost=int(sdef.get("boost", 0) * 100)))
    lines.append(DIV)
    lines.append(t(lang, "CAT_PROMPT"))
    return "\n".join(lines)


# ═══════════════════════ martial pavilion & deck builder (P5) ═════════════════

def martial_text(lang: str, user: dict, max_slots_val: int | None = None) -> str:
    from .engine.models import max_slots as max_slots_fn
    from .engine.models import gear_specials
    cap = max_slots_val or max_slots_fn(user)
    cul = user["cultivation"]
    m = data_registry.get_method(cul.get("active_method_id") or "")
    loadout = user["combat"]["loadout"]
    equipped = [x for x in loadout if x]
    slot_lines = []
    for idx in range(cap):
        slot_no = idx + 1
        if idx < len(loadout) and loadout[idx]:
            tech = data_registry.get_technique(loadout[idx])
            if tech:
                slot_lines.append(t(lang, "MTL_SLOT", n=slot_no, name=tech.name if lang != "en" else (tech.name_en or tech.name),
                                    qi=tech.qi_cost, element=_element_label(lang, tech)))
            else:
                slot_lines.append(t(lang, "MTL_SLOT_RAW", n=slot_no, tid=loadout[idx]))
        else:
            slot_lines.append(t(lang, "MTL_SLOT_EMPTY", n=slot_no))
    available = []
    for art_id in (user["inventory"].get("arts") or []):
        art = data_registry.get_martial_art(art_id)
        if not art:
            continue
        for tech in art.techniques:
            if tech.id not in equipped:
                available.append(t(lang, "MTL_AVAIL", name=tech.name if lang != "en" else (tech.name_en or tech.name),
                                   art=art.name_for(lang)))
    avail_txt = "\n".join(available[:5]) if available else t(lang, "MTL_AVAIL_NONE")
    lines = [
        t(lang, "MTL_TITLE"),
        t(lang, "MTL_MANTRA", mantra=(m.name_for(lang) if m else t(lang, "HUB_MANTRA_NONE")),
          mult=(m.qi_mult if m else 1.0)),
        t(lang, "MTL_CAPACITY", used=len(equipped), cap=cap),
        DIV,
        t(lang, "MTL_LOADOUT_HDR"),
    ]
    lines.extend(slot_lines)
    for extra in range(cap, 6):
        lines.append(t(lang, "MTL_SLOT_LOCKED", n=extra + 1))
    lines.append(t(lang, "MTL_STOCK_HDR"))
    lines.append(avail_txt)
    lines.append(DIV)
    lines.append(t(lang, "MTL_PROMPT"))
    return "\n".join(lines)


def _element_label(lang: str, tech) -> str:
    art_id = data_registry.tech_owner_art.get(tech.id)
    art = data_registry.get_martial_art(art_id or "")
    el = ""
    if art:
        el = art.element_en if (lang == "en" and art.element_en) else art.element
    return el or t(lang, "MTL_ELEMENT_ANY")


def mantra_menu_text(lang: str, user: dict) -> str:
    from .engine.models import recompute_visible_stats
    active = user["cultivation"].get("active_method_id")
    lines = [t(lang, "MANTRA_TITLE"), ""]
    for mid, m in data_registry.methods.items():
        owned = mid in set(user["inventory"].get("methods") or [])
        icon = "✅" if mid == active else ("📜" if owned else "🔒")
        lines.append(f"{icon} {m.name_for(lang)} — ×{m.qi_mult} ({m.tier})")
        if m.lore_for(lang) and (owned or mid == active):
            lines.append(f"   «{m.lore_for(lang)}»")
    lines.append(DIV)
    lines.append(t(lang, "MANTRA_PROMPT"))
    return "\n".join(lines)


def dao_prompt_text(lang: str, user: dict) -> str:
    lines = [t(lang, "DAO_TITLE"), "", t(lang, "DAO_PROMPT")]
    for dao, spec in DAO_PATHS.items():
        lines.append(f"• {t(lang, spec['key'])} — {t(lang, 'DAO_' + dao.upper() + '_DESC')}")
    lines.append(DIV)
    return "\n".join(lines)


def deck_picker_text(lang: str, user: dict, slot_idx: int) -> str:
    return t(lang, "MTL_PICK_TITLE", n=slot_idx + 1)


# ════════════════════════════ spirit pavilion / shop (P5) ═════════════════════

def shop_text(lang: str, user: dict) -> str:
    stones = user["inventory"]["spirit_stones"]
    return "\n".join([
        t(lang, "SHOP_TITLE"),
        t(lang, "SHOP_BALANCE", low=stones.get("low", 0), mid=stones.get("mid", 0),
          high=stones.get("high", 0)),
        t(lang, "SHOP_FREE_BANNER"),
        DIV,
        t(lang, "SHOP_PROMPT"),
    ])


ARTS_PER_PAGE = 6


def arts_catalog_text(lang: str, user: dict, page: int = 1) -> str:
    arts = list(data_registry.martial_arts.values())
    arts.sort(key=lambda a: (a.required_realm, a.art_id))
    pages = max(1, (len(arts) + ARTS_PER_PAGE - 1) // ARTS_PER_PAGE)
    page = max(1, min(page, pages))
    chunk = arts[(page - 1) * ARTS_PER_PAGE: page * ARTS_PER_PAGE]
    owned = set(user["inventory"].get("arts") or [])
    lines = [t(lang, "SHOP_ARTS_TITLE", n=len(arts))]
    for a in chunk:
        mark = "✅" if a.art_id in owned else "📜"
        el = a.element_en if (lang == "en" and a.element_en) else a.element
        lines.append(f"{mark} {a.name_for(lang)} ({el})")
    lines.append(DIV)
    lines.append(t(lang, "SHOP_PAGE", page=page, pages=pages))
    return "\n".join(lines)


def art_inspect_text(lang: str, user: dict, art_id: str) -> str:
    art = data_registry.get_martial_art(art_id)
    if not art:
        return t(lang, "ERR_UNKNOWN")
    el = art.element_en if (lang == "en" and art.element_en) else art.element
    learned = art_id in set(user["inventory"].get("arts") or [])
    lines = [
        t(lang, "ARTCARD_TITLE", name=art.name_for(lang)),
        t(lang, "ARTCARD_ELEMENT", element=el),
        t(lang, "ARTCARD_SUBS"),
    ]
    for i, tech in enumerate(art.techniques[:3], start=1):
        nm = tech.name if lang != "en" else (tech.name_en or tech.name)
        lines.append(t(lang, "ARTCARD_TECH", n=i, name=nm, qi=tech.qi_cost, cd=tech.cooldown))
    lines.append(DIV)
    lines.append(t(lang, "ARTCARD_PRICE", free=learned))
    return "\n".join(lines)


def shop_booth_text(lang: str, user: dict, booth: str) -> str:
    """Free testing booths (pills / gear / talismans)."""
    title = {"pills": "SHOP_PILLS_TITLE", "gear": "SHOP_GEAR_TITLE",
             "talismans": "SHOP_TALI_TITLE"}[booth]
    lines = [t(lang, title), t(lang, "SHOP_FREE_NOTE",
                       cap=items_mod.BOOTH_CAP_PER_CLAIM), DIV]
    entries = items_mod.booth_entries(user, booth)
    for e in entries:
        icon = {"pills": "💊", "gear": "⚔️", "talismans": "📜"}[booth]
        lines.append(f"{icon} {e['name']} — {t(lang, 'SHOP_FREE_PRICE')}")
    if not entries:
        lines.append(t(lang, "SHOP_EMPTY"))
    lines.append(DIV)
    lines.append(t(lang, "SHOP_BOOTH_PROMPT"))
    return "\n".join(lines)


# ════════════════════════════════ sect gate (P5) ══════════════════════════════

def sect_gate_text(lang: str) -> str:
    return "\n".join([
        t(lang, "SECT_GATE_TITLE"),
        "",
        t(lang, "SECT_GATE_FOG"),
        t(lang, "SECT_GATE_ECHO"),
        f"«{t(lang, 'SECT_GATE_VOICE')}»",
        DIV,
        t(lang, "SECT_GATE_STATUS"),
    ])


def sect_text(lang: str, user: dict, storage) -> str:
    sect_id = user.get("location", {}).get("sect_id")
    if not sect_id:
        return "\n".join([t(lang, "SECT_DASH_TITLE"), "", t(lang, "SECT_DASH_NONE"), DIV,
                          t(lang, "SECT_DASH_PROMPT")])
    sect = storage.get_sect(sect_id) if storage else None
    name = (t(lang, sect["key"]) if sect and sect.get("key") else (sect or {}).get("name", sect_id)) if sect else sect_id
    contribution = user.get("sect", {}).get("contribution", 0) if isinstance(user.get("sect"), dict) else 0
    return "\n".join([t(lang, "SECT_DASH_TITLE"), "",
                      t(lang, "SECT_DASH_MEMBER", sect=name, rank=t(lang, "SECT_RANK_OUTER"),
                        contrib=contribution),
                      DIV, t(lang, "SECT_DASH_PROMPT")])


# ════════════════════════ breakthrough / tribulation (P6) ═════════════════════

def breakthrough_prep_text(lang: str, user: dict, prep: dict) -> str:
    tgt_realm, tgt_stage = prep["target_realm"], prep["target_stage"]
    target = target_label(lang, tgt_realm, tgt_stage)
    return "\n".join([
        t(lang, "BT_PREP_TITLE"),
        t(lang, "BT_PREP_TARGET", target=target),
        t(lang, "BT_PREP_RATE", rate=prep["rate"]),
        t(lang, "BT_PREP_DEV", risk=prep["qi_deviation_risk"]),
        t(lang, "BT_PREP_DAO", value=prep["dao_heart"]),
        "",
        t(lang, "BT_PREP_WARN_HDR"),
        t(lang, "BT_PREP_WARN"),
        DIV,
        t(lang, "BT_PREP_PROMPT"),
    ])


def breakthrough_win_text(lang: str, user: dict, res: dict) -> str:
    cul = user["cultivation"]
    lines = [
        t(lang, "BT_WIN_TITLE"),
        "",
        t(lang, "BT_WIN_FLAVOR"),
        t(lang, "BT_WIN_RANK", rank=target_label(lang, res["new_realm"], res["new_stage"])),
        "",
        t(lang, "BT_WIN_HDR"),
        t(lang, "BT_WIN_CAP", cap=res["qi_capacity"]),
        t(lang, "BT_WIN_HP", hp=res.get("bonus_hp", 0)),
        t(lang, "BT_WIN_ATK", atk=res.get("bonus_atk", 0)),
        t(lang, "BT_WIN_LIFE", years=res.get("lifespan_increase", 0)),
    ]
    if res.get("lightning"):
        lines.append(t(lang, "BT_WIN_LIGHTNING", bolt=res["lightning"]))
    if res.get("slot_unlocked"):
        lines.append(t(lang, "BT_WIN_SLOT"))
    lines.append(DIV)
    return "\n".join(lines)


def breakthrough_fail_text(lang: str, res: dict) -> str:
    return "\n".join([
        t(lang, "BT_FAIL_TITLE"),
        "",
        t(lang, "BT_FAIL_FLAVOR"),
        t(lang, "BT_FAIL_HDR"),
        t(lang, "BT_FAIL_QI"),
        t(lang, "BT_FAIL_HP", hp=res.get("remaining_hp", 1), max=res.get("max_hp", 1)),
        t(lang, "BT_FAIL_DEBUFF", minutes=res.get("debuff_minutes", 120)),
        DIV,
    ])


# ══════════════════════════ onboarding & panel (P1) ═══════════════════════════

def start_text(lang: str) -> str:
    return t(lang, "START_NARRATIVE")


DOCK_LANDED = "DOCK_LANDED"
PANEL_RESTORED = "PANEL_RESTORED"


# ═══════════════════════════════ admin (unchanged) ════════════════════════════

def admin_text(lang: str, storage, boost_label: str) -> str:
    return t(lang, "ADMIN_TEXT", boost=boost_label)

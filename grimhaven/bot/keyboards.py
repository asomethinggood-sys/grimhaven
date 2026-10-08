"""Keyboard builders — inline boards plus the Part-3 persistent reply dock.

Telegram caps callback_data at 64 bytes, so every payload stays short:
`<root>:<sub>:<id>` with data-driven ids.
"""
from __future__ import annotations

import datetime as dt

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from ..core.combat_engine import enemy_name
from ..core.data_loader import data_registry
from ..localization import t


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


# ── persistent reply keyboard (Part 3 §2.1) ──────────────────────────────────
REPLY_ROWS = {
    "fa": [
        ["🧘 مدیتیشن و تهذیب", "⚡️ شکست سد"],
        ["🗺 نقشه و شکار", "🎒 کوله‌پشتی"],
        ["📜 لوح سرنوشت", "🏛 پاویون"],
        ["⚔️ هنرهای رزمی", "⛩ فرقه"],
    ],
    "en": [
        ["🧘 Meditate", "⚡️ Breakthrough"],
        ["🗺 Map & Hunt", "🎒 Bag"],
        ["📜 Profile", "🏛 Pavilion"],
        ["⚔️ Skills", "⛩ Sect"],
    ],
}

REPLY_TO_ACTION = {
    "🧘 مدیتیشن و تهذیب": "meditate", "⚡️ شکست سد": "breakthrough",
    "🗺 نقشه و شکار": "map", "🎒 کوله‌پشتی": "bag",
    "📜 لوح سرنوشت": "menu", "🏛 پاویون": "shop",
    "⚔️ هنرهای رزمی": "martial", "⛩ فرقه": "sect",
    "🧘 Meditate": "meditate", "⚡️ Breakthrough": "breakthrough",
    "🗺 Map & Hunt": "map", "🎒 Bag": "bag",
    "📜 Profile": "menu", "🏛 Pavilion": "shop",
    "⚔️ Skills": "martial", "⛩ Sect": "sect",
}


def reply_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=REPLY_ROWS.get(lang, REPLY_ROWS["fa"]),
                               resize_keyboard=True, is_persistent=True)


# ── main menu ────────────────────────────────────────────────────────────────

def main_menu(lang: str, user: dict) -> InlineKeyboardMarkup:
    from ..engine.models import in_seclusion
    from ..core.state_machine import get_status, UserStatus
    now = dt.datetime.now(dt.timezone.utc)
    status = get_status(user)
    rows: list[list[InlineKeyboardButton]] = []
    if status is UserStatus.MEDITATING:
        rows.append([InlineKeyboardButton(t(lang, "BTN_CLAIM_STOP"), callback_data="stop_meditate")])
    elif status is UserStatus.IN_COMBAT and user["combat"].get("session"):
        rows.append([InlineKeyboardButton(t(lang, "BTN_RETURN_TO_BATTLE"), callback_data="combat:view")])
    elif status is UserStatus.SECLUSION and in_seclusion(user, now):
        rows.append([InlineKeyboardButton(t(lang, "BTN_CHECK_TRIBULATION"), callback_data="check_tribulation")])
    else:
        rows.append([InlineKeyboardButton(t(lang, "BTN_MEDITATE"), callback_data="meditate"),
                     InlineKeyboardButton(t(lang, "BTN_BREAKTHROUGH"), callback_data="breakthrough")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_BAG"), callback_data="bag:tab:gear"),
                 InlineKeyboardButton(t(lang, "BTN_MARTIAL"), callback_data="martial")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_MAP"), callback_data="map"),
                 InlineKeyboardButton(t(lang, "BTN_SHOP"), callback_data="shop")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_SECT"), callback_data="sect"),
                 InlineKeyboardButton(t(lang, "BTN_DEEP"), callback_data="deep")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_LANG_TOGGLE"), callback_data="language")])
    return kb(rows)


def back_to_menu(lang: str) -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(t(lang, "BTN_MAIN_MENU"), callback_data="menu")]])


def back_row(lang: str, back: str = "menu") -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(t(lang, "BACK"), callback_data=back)]])


# ── combat keyboard (Part 2 §6.2) ────────────────────────────────────────────

def combat_kb(lang: str, user: dict, session: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    loadout = [x for x in user["combat"]["loadout"] if x]

    tech_buttons = []
    for tech_id in loadout:
        tech = data_registry.get_technique(tech_id)
        if not tech:
            continue
        cd = session["cooldowns"].get(tech_id, 0)
        name = (tech.name_en if lang == "en" and tech.name_en else tech.name)
        if len(name) > 18:
            name = name[:17] + "…"
        if cd > 0:
            tech_buttons.append(InlineKeyboardButton(f"⏳ {name} ({cd})", callback_data="combat:noop"))
        elif session["player"]["qi"] < tech.qi_cost:
            tech_buttons.append(InlineKeyboardButton(f"🈵 {name}", callback_data="combat:no_qi"))
        else:
            tech_buttons.append(InlineKeyboardButton(f"⚡️ {name} ·{tech.qi_cost}",
                                                      callback_data=f"combat:tech:{tech_id}"))
    for i in range(0, len(tech_buttons), 2):
        rows.append(tech_buttons[i:i + 2])

    # auxiliary: disposable talismans & battle pills
    aux = []
    for iid, qty in sorted(user["inventory"].get("items", {}).items()):
        item = data_registry.get_consumable(iid)
        if not item or qty <= 0 or item.category not in ("disposable_weapon", "relic"):
            continue
        if item.action in ("instant_damage", "shield", "enemy_debuff"):
            nm = item.name_en if lang == "en" and item.name_en else item.name
            if len(nm) > 14:
                nm = nm[:13] + "…"
            aux.append(InlineKeyboardButton(f"📜 {nm} ×{qty}", callback_data=f"combat:item:{iid}"))
        if len(aux) == 2:
            break
    heal_pills = [(iid, q) for iid, q in user["inventory"].get("items", {}).items()
                  if (data_registry.get_consumable(iid) and
                      data_registry.get_consumable(iid).action in ("heal_hp", "heal_and_cure", "combat_damage_buff"))]
    for iid, qty in heal_pills[:1]:
        item = data_registry.get_consumable(iid)
        nm = item.name_en if lang == "en" and item.name_en else item.name
        if len(nm) > 14:
            nm = nm[:13] + "…"
        aux.append(InlineKeyboardButton(f"💊 {nm} ×{qty}", callback_data=f"combat:item:{iid}"))
    if aux:
        rows.extend([aux[i:i + 2] for i in range(0, len(aux), 2)])

    rows.append([InlineKeyboardButton(t(lang, "BTN_COMBAT_BASIC"), callback_data="combat:basic"),
                 InlineKeyboardButton(t(lang, "BTN_COMBAT_FLEE"), callback_data="combat:flee")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_COMBAT_SIM"), callback_data="combat:simulate")])
    return kb(rows)


def victory_kb(lang: str, user: dict, session: dict) -> InlineKeyboardMarkup | None:
    if session.get("kind") == "rival":
        return kb([[InlineKeyboardButton(t(lang, "BTN_MERCY"), callback_data="mercy"),
                    InlineKeyboardButton(t(lang, "BTN_PLUNDER"), callback_data="plunder")],
                   [InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")]])
    return None


# ── bag tabs (Part 3 §5.2/5.3) ───────────────────────────────────────────────

def bag_kb(lang: str, user: dict, tab: str) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(("🔸 " if tab == "gear" else "⚪ ") + t(lang, "BAG_TAB_GEAR"), callback_data="bag:tab:gear"),
        InlineKeyboardButton(("🔸 " if tab == "consumables" else "⚪ ") + t(lang, "BAG_TAB_CONSUMABLES"), callback_data="bag:tab:consumables"),
        InlineKeyboardButton(("🔸 " if tab == "materials" else "⚪ ") + t(lang, "BAG_TAB_MATERIALS"), callback_data="bag:tab:materials"),
    ]]
    inv = user["inventory"]
    if tab == "gear":
        item_row = []
        for iid in list(inv.get("items", {}))[:4]:
            item_row.append(InlineKeyboardButton(f"💊 {iid[:10]}…", callback_data=f"bag:item:item:{iid}"))
        rows.extend([item_row[i:i + 2] for i in range(0, len(item_row), 2)])
        gear_row = [InlineKeyboardButton(f"🎽 {gid[:14]}", callback_data=f"bag:item:gear:{gid}")
                    for gid in list(inv.get("gear", {}))[:4]]
        rows.extend([gear_row[i:i + 2] for i in range(0, len(gear_row), 2)])
        for slot, gear in user["equipment"].items():
            if gear and slot != "natal":
                rows.append([InlineKeyboardButton(t(lang, "BTN_UNEQUIP", slot=t(lang, f"SLOT_{slot}")),
                                                  callback_data=f"bag:unequip:{slot}")])
    elif tab in ("consumables", "materials"):
        wanted = ("pill", "herb", "disposable_weapon", "relic") if tab == "consumables" else ("material",)
        items = []
        for iid, qty in sorted(inv.get("items", {}).items()):
            c = data_registry.get_consumable(iid)
            if c and (c.category in wanted or (tab == "materials" and data_registry.get_equipment(iid))):
                items.append((iid, qty, c))
        for chunk_start in range(0, min(len(items), 8), 2):
            rows.append([InlineKeyboardButton(f"{nm.name_for(lang)} ×{q}", callback_data=f"bag:item:item:{iid}")
                         for iid, q, nm in items[chunk_start:chunk_start + 2]])
    rows.append([InlineKeyboardButton(t(lang, "BTN_MAIN_MENU"), callback_data="menu")])
    return kb(rows)


def item_kb(lang: str, user: dict, kind: str, item_id: str, equipped: bool = False) -> InlineKeyboardMarkup:
    rows = []
    item = (data_registry.get_equipment(item_id) if kind == "gear"
            else data_registry.get_consumable(item_id))
    if kind == "gear" and not equipped:
        rows.append([InlineKeyboardButton(t(lang, "BTN_EQUIP"), callback_data=f"bag:equip:{item_id}")])
    if kind == "gear":
        price = getattr(item, "sell_price", 0) if not equipped else 0
        row = []
        if price:
            row.append(InlineKeyboardButton(t(lang, "BTN_SELL", price=price), callback_data=f"bag:sell:gear:{item_id}"))
        row.append(InlineKeyboardButton(t(lang, "BTN_REPAIR"), callback_data=f"bag:repair:{item_id}"))
        for slot, gear in user["equipment"].items():
            if gear and gear.get("id") == item_id and slot != "natal":
                row.append(InlineKeyboardButton(t(lang, "BTN_UNEQUIP", slot=t(lang, f"SLOT_{slot}")),
                                                callback_data=f"bag:unequip:{slot}"))
        rows.append(row)
    else:
        action = getattr(item, "action", "")
        if action not in ("sell_only", "auto_revive") and getattr(item, "category", "") in ("pill", "herb", "relic"):
            rows.append([InlineKeyboardButton(t(lang, "BTN_USE"), callback_data=f"bag:use:{item_id}")])
        if action in ("instant_damage", "shield", "enemy_debuff"):
            rows.append([InlineKeyboardButton(t(lang, "BTN_COMBAT_ONLY"), callback_data="bag:tab:consumables")])
        sell = getattr(item, "sell_price", 0)
        if sell:
            rows.append([InlineKeyboardButton(t(lang, "BTN_SELL", price=sell), callback_data=f"bag:sell:item:{item_id}")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="bag:back")])
    return kb(rows)


# ── martial hall ─────────────────────────────────────────────────────────────

def martial_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    from ..engine.constants import METHODS
    from ..engine.models import max_slots as _ms
    rows = []
    cap = _ms(user)
    loadout = user["combat"]["loadout"]
    deck_row = []
    for i in range(cap):
        tid = loadout[i] if i < len(loadout) else None
        label = f"[{i + 1}] " + ("✖" if tid else "➕")
        if tid:
            tech = data_registry.get_technique(tid)
            nm = tech.name_en if (lang == "en" and tech and tech.name_en) else (tech.name if tech else tid)
            label = f"[{i + 1}] {(nm[:12] + '…') if len(nm) > 13 else nm}"
        deck_row.append(InlineKeyboardButton(label, callback_data=f"martial:slot:{i}"))
    for i in range(0, len(deck_row), 2):
        rows.append(deck_row[i:i + 2])
    method_row = [InlineKeyboardButton(f"🌀 {t(lang, METHODS[m]['key'])} ×{METHODS[m]['tech_mult']}",
                                       callback_data=f"method:{m}")
                  for m in user["inventory"].get("methods", []) if m in METHODS]
    rows.extend([method_row[i:i + 2] for i in range(0, len(method_row), 2)])
    rows.append([InlineKeyboardButton(t(lang, "BTN_MAIN_MENU"), callback_data="menu")])
    return kb(rows)


def deck_picker_kb(lang: str, user: dict, slot_idx: int) -> InlineKeyboardMarkup:
    rows = []
    used = [x for x in user["combat"]["loadout"] if x]
    buttons = []
    for tid in items_mod_owned(user):
        if tid in used:
            continue
        tech = data_registry.get_technique(tid)
        if not tech:
            continue
        nm = tech.name_en if lang == "en" and tech.name_en else tech.name
        if len(nm) > 15:
            nm = nm[:14] + "…"
        buttons.append(InlineKeyboardButton(f"⚔️ {nm} ·{tech.qi_cost}⚡", callback_data=f"martial:equip:{slot_idx}:{tid}"))
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    cur = user["combat"]["loadout"][slot_idx] if slot_idx < len(user["combat"]["loadout"]) else None
    if cur:
        rows.append([InlineKeyboardButton(t(lang, "BTN_DECK_CLEAR"), callback_data=f"martial:unequip:{slot_idx}")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="martial")])
    return kb(rows)


def items_mod_owned(user: dict) -> list[str]:
    from ..engine import items as items_mod
    return items_mod.owned_techniques(user)


# ── map / zone / sect / shop / dao ───────────────────────────────────────────

def shop_kb(lang: str, user: dict, page: int = 0) -> InlineKeyboardMarkup:
    from ..engine import items as items_mod
    from ..render import shop_row_label
    entries = items_mod.shop_entries(user)
    per_page = 12
    chunk = entries[page * per_page:(page + 1) * per_page]
    buttons = [InlineKeyboardButton(shop_row_label(lang, e)[:56], callback_data=f"buy:{e['shop_id']}")
               for e in chunk]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"shop:{page - 1}"))
    if (page + 1) * per_page < len(entries):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"shop:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def map_kb(lang: str, storage, current_zone: str) -> InlineKeyboardMarkup:
    from ..engine.constants import ZONES
    rows = []
    for zone_id in ZONES:
        zone = storage.get_zone(zone_id)
        key = zone["key"] if zone else ZONES[zone_id]["key"]
        mark = "📍 " if zone_id == current_zone else ""
        rows.append([InlineKeyboardButton(f"{mark}{t(lang, key)}", callback_data=f"zone:{zone_id}")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def zone_kb(lang: str, zone: dict, user: dict) -> InlineKeyboardMarkup:
    from ..core.state_machine import get_status, UserStatus
    rows = [[InlineKeyboardButton(t(lang, "BTN_TRAVEL"), callback_data=f"travel:{zone['zone_id']}")]]
    is_owner = zone.get("owner_kind") == "user" and zone.get("owner") == user["user_id"]
    status = get_status(user)
    if status not in (UserStatus.MEDITATING, UserStatus.SECLUSION):
        rows.append([InlineKeyboardButton(t(lang, "BTN_HUNT"), callback_data=f"hunt:{zone['zone_id']}")])
        if not is_owner:
            rows.append([InlineKeyboardButton(t(lang, "BTN_CONQUER"), callback_data=f"conquer:{zone['zone_id']}")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="map")])
    return kb(rows)


def sect_kb(lang: str, user: dict, storage) -> InlineKeyboardMarkup:
    from ..engine.constants import NPC_SECTS
    rows = []
    if user["location"].get("sect_id"):
        rows.append([InlineKeyboardButton(t(lang, "BTN_LEAVE_SECT"), callback_data="leave_sect")])
    else:
        for sect_id, sect in NPC_SECTS.items():
            rows.append([InlineKeyboardButton(t(lang, "BTN_JOIN_SECT", sect=t(lang, sect["key"])),
                                              callback_data=f"join:{sect_id}")])
        rows.append([InlineKeyboardButton(t(lang, "BTN_FOUND_SECT"), callback_data="found_sect")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def dao_kb(lang: str, chosen: bool) -> InlineKeyboardMarkup:
    from ..engine.constants import DAO_PATHS
    rows = []
    for dao, spec in DAO_PATHS.items():
        aligns = ("demonic",) if dao == "blood" else ("orthodox", "demonic")
        row = [InlineKeyboardButton(
            t(lang, spec["key"]) + (" 🕊" if a == "orthodox" else " 😈"),
            callback_data=f"dao:{dao}:{a}") for a in aligns]
        rows.append(row)
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def sacrifice_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(t(lang, "SACRIFICE_BLOOD"), callback_data="sacrifice:blood")],
        [InlineKeyboardButton(t(lang, "SACRIFICE_DEVOUR"), callback_data="sacrifice:devour")],
        [InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")],
    ])


def breakthrough_confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[
        InlineKeyboardButton(t(lang, "BTN_CONFIRM"), callback_data="breakthrough_do"),
        InlineKeyboardButton(t(lang, "BTN_CANCEL"), callback_data="menu"),
    ]])


def language_kb() -> InlineKeyboardMarkup:
    return kb([[
        InlineKeyboardButton(t("fa", "BTN_LANG_FA"), callback_data="setlang:fa"),
        InlineKeyboardButton(t("en", "BTN_LANG_EN"), callback_data="setlang:en"),
    ]])


def deep_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")]])


def admin_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [InlineKeyboardButton(t(lang, "ADMIN_BROADCAST"), callback_data="admin:usage:broadcast")],
        [InlineKeyboardButton(t(lang, "ADMIN_DROPS"), callback_data="admin:drops")],
        [InlineKeyboardButton(t(lang, "ADMIN_INSPECT"), callback_data="admin:usage:inspect")],
        [InlineKeyboardButton(t(lang, "ADMIN_REALM"), callback_data="admin:usage:realm")],
        [InlineKeyboardButton(t(lang, "ADMIN_JUDGE"), callback_data="admin:usage:judge")],
        [InlineKeyboardButton(t(lang, "ADMIN_WORLD"), callback_data="admin:usage:boost")],
        [InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")],
    ])


def mercy_plunder_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[
        InlineKeyboardButton(t(lang, "BTN_MERCY"), callback_data="mercy"),
        InlineKeyboardButton(t(lang, "BTN_PLUNDER"), callback_data="plunder"),
    ]])

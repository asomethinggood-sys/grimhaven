"""Inline-keyboard builders (doc chapter 8 mockups → Telegram buttons)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..localization import t


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def main_menu(lang: str, meditating: bool, seclusion: bool) -> InlineKeyboardMarkup:
    meditate = (InlineKeyboardButton(t(lang, "BTN_STOP_MEDITATE"), callback_data="stop_meditate")
                if meditating else
                InlineKeyboardButton(t(lang, "BTN_MEDITATE"), callback_data="meditate"))
    row1 = [meditate]
    if seclusion:
        row1.append(InlineKeyboardButton(t(lang, "BTN_CHECK_TRIBULATION"), callback_data="check_tribulation"))
    else:
        row1.append(InlineKeyboardButton(t(lang, "BTN_BREAKTHROUGH"), callback_data="breakthrough"))
    return kb([
        row1,
        [InlineKeyboardButton(t(lang, "BTN_BACKPACK"), callback_data="backpack"),
         InlineKeyboardButton(t(lang, "BTN_MARTIAL"), callback_data="martial")],
        [InlineKeyboardButton(t(lang, "BTN_MAP"), callback_data="map"),
         InlineKeyboardButton(t(lang, "BTN_SHOP"), callback_data="shop")],
        [InlineKeyboardButton(t(lang, "BTN_SECT"), callback_data="sect"),
         InlineKeyboardButton(t(lang, "BTN_DAO"), callback_data="dao")],
        [InlineKeyboardButton(t(lang, "BTN_LANG_TOGGLE"), callback_data="language")],
    ])


def back_to_menu(lang: str) -> InlineKeyboardMarkup:
    return kb([[InlineKeyboardButton(t(lang, "BTN_MAIN_MENU"), callback_data="menu")]])


def backpack_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    stones = user["inventory"]["spirit_stones"]
    pills = user["inventory"].get("pills", {})
    herbs = user["inventory"].get("herbs", {})
    rows = []
    stone_row = [
        InlineKeyboardButton(f"🪨 {g} ×{stones.get(g, 0)}", callback_data=f"stone:{g}")
        for g in ("low", "mid", "high", "heavenly") if stones.get(g, 0) > 0
    ]
    if stone_row:
        rows.append(stone_row)
    pill_row = [InlineKeyboardButton(f"💊 {t(lang, key)}", callback_data=f"pill:{key}")
                for key in pills if pills.get(key, 0) > 0]
    if pill_row:
        rows.append(pill_row)
    herb_row = [InlineKeyboardButton(f"🌿 {t(lang, key)}", callback_data=f"herb:{key}")
                for key in ("root_ancient", "lotus_seven") if herbs.get(key, 0) > 0]
    if herb_row:
        rows.append(herb_row)
    gear_row = [InlineKeyboardButton(f"⚙️ {t(lang, 'BTN_EQUIP')}: {gid[-12:]}", callback_data=f"equip:{gid}")
                for gid in list(user["inventory"].get("gear", {}))[:6]
                if gid != "doll_substitute"]
    rows.extend([gear_row[i:i + 2] for i in range(0, len(gear_row), 2)])
    unequip_row = [InlineKeyboardButton(f"✖️ {t(lang, f'SLOT_{slot}')}", callback_data=f"unequip:{slot}")
                   for slot, gear in user["equipment"].items() if gear and slot != "natal"]
    rows.extend([unequip_row[i:i + 2] for i in range(0, len(unequip_row), 2)])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def shop_kb(lang: str) -> InlineKeyboardMarkup:
    from ..engine.constants import PAVILION_PRICES
    from ..render import shop_button_label
    ids = list(PAVILION_PRICES)
    buttons = [InlineKeyboardButton(shop_button_label(lang, sid), callback_data=f"buy:{sid}")
               for sid in ids]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="menu")])
    return kb(rows)


def martial_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    from ..engine.constants import METHODS
    rows = []
    method_row = [InlineKeyboardButton(f"🌀 {t(lang, METHODS[m]['key'])}", callback_data=f"method:{m}")
                  for m in user["inventory"].get("methods", [])]
    rows.extend([method_row[i:i + 2] for i in range(0, len(method_row), 2)])
    loadout_row = []
    for i, tech in enumerate(user["combat"]["loadout"], 1):
        label = f"[{i}] ✖️" if tech else f"[{i}] —"
        loadout_row.append(InlineKeyboardButton(label, callback_data=f"loadout:{i}:clear" if tech else f"loadout:{i}:none"))
    rows.append(loadout_row)
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
    rows = [[InlineKeyboardButton(t(lang, "BTN_TRAVEL"), callback_data=f"travel:{zone['zone_id']}")]]
    is_owner = zone.get("owner_kind") == "user" and zone.get("owner") == user["user_id"]
    if not is_owner:
        rows.append([InlineKeyboardButton(t(lang, "BTN_CONQUER"), callback_data=f"conquer:{zone['zone_id']}")])
    rows.append([InlineKeyboardButton(t(lang, "BTN_FIGHT_MONSTER"), callback_data=f"hunt:{zone['zone_id']}")])
    rows.append([InlineKeyboardButton(t(lang, "BACK"), callback_data="map")])
    return kb(rows)


def sect_kb(lang: str, user: dict, storage) -> InlineKeyboardMarkup:
    from ..engine.constants import NPC_SECTS
    rows = []
    current = user["location"].get("sect_id")
    for sect_id, sect in NPC_SECTS.items():
        mark = "🏠 " if sect_id == current else ""
        rows.append([InlineKeyboardButton(
            f"{mark}{t(lang, sect['key'])} — {t(lang, 'ALIGN_ORTHODOX_NAME' if sect['alignment'] == 'orthodox' else 'ALIGN_DEMONIC_NAME')}",
            callback_data=f"join:{sect_id}")])
    if current:
        rows.append([InlineKeyboardButton(t(lang, "BTN_LEAVE_SECT"), callback_data="leave_sect")])
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

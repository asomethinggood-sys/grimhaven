"""Keyboard builders v2 — persistent reply dock plus every inline board.

The bottom dock is a PERSISTENT ReplyKeyboardMarkup (is_persistent, resizable,
never one-time). Nothing in the inline keyboards duplicates it: inline boards
carry only contextual actions. Telegram caps callback_data at 64 bytes, so
payloads stay terse: `<ns>:<verb>[:<arg>[:<page>[:<round>]]]]`.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from ..core.data_loader import data_registry
from ..localization import Locale, t

NOOP = "noop"


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def _btn(text: str, data: str = NOOP) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ── persistent reply dock (P1 §1) ────────────────────────────────────────────
DOCK_ROWS = {
    "fa": [
        ["🧘 مدیتیشن و تهذیب", "⚡ اقدام به شکست سد"],
        ["🗺 نقشه و شکار", "🎒 کوله‌پشتی و گنجینه"],
        ["📜 لوح سرنوشت (پروفایل)", "🏛 پاویون تجارت"],
        ["⛩ فرقه", "⚙️ تنظیمات"],
    ],
    "en": [
        ["🧘 Meditation & Cultivation", "⚡ Attempt Breakthrough"],
        ["🗺 Map & Hunt", "🎒 Bag & Treasures"],
        ["📜 Destiny Scroll (Profile)", "🏛 Spirit Pavilion"],
        ["⛩ Sect", "⚙️ Settings"],
    ],
}

# dock label → callback namespace string (dispatched identically to inline data)
REPLY_TO_ACTION = {
    "🧘 مدیتیشن و تهذیب": "cultivate:view:hub",
    "⚡ اقدام به شکست سد": "breakthrough:view:prep",
    "🗺 نقشه و شکار": "map:view:world",
    "🎒 کوله‌پشتی و گنجینه": "bag:tab:gear:1",
    "📜 لوح سرنوشت (پروفایل)": "profile:view:main",
    "🏛 پاویون تجارت": "shop:view:hub",
    "⛩ فرقه": "sect:view:main",
    "⚙️ تنظیمات": "settings:view:main",
    "🧘 Meditation & Cultivation": "cultivate:view:hub",
    "⚡ Attempt Breakthrough": "breakthrough:view:prep",
    "🗺 Map & Hunt": "map:view:world",
    "🎒 Bag & Treasures": "bag:tab:gear:1",
    "📜 Destiny Scroll (Profile)": "profile:view:main",
    "🏛 Spirit Pavilion": "shop:view:hub",
    "⛩ Sect": "sect:view:main",
    "⚙️ Settings": "settings:view:main",
}


def dock_reply_kb(lang: str) -> ReplyKeyboardMarkup:
    """The 8-button dock — persistent, resized, never one-shot (P1 §1.1)."""
    return ReplyKeyboardMarkup(keyboard=DOCK_ROWS.get(lang, DOCK_ROWS["fa"]),
                               is_persistent=True, resize_keyboard=True,
                               one_time_keyboard=False, input_field_placeholder=None)


def start_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_OPEN_SCROLL"), "profile:view:main")]])


def language_kb() -> InlineKeyboardMarkup:
    return kb([[_btn(t("fa", "BTN_LANG_FA"), "setlang:fa"),
                _btn(t("en", "BTN_LANG_EN"), "setlang:en")]])


def settings_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "SET_MARTIAL"), "martial:view:main")],
        [_btn(t(lang, "SET_HELP"), "settings:help")],
        [_btn(t(lang, "SET_PANEL"), "settings:panel_tip")],
        [_btn("🇮🇷 فارسی", "setlang:fa"), _btn("🇺🇸 English", "setlang:en")],
        [_btn(t(lang, "BACK_PROFILE"), "profile:view:main")],
    ])


# ── profile HUD & modals ─────────────────────────────────────────────────────

def profile_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "BTN_MERIDIANS"), "profile:view:meridians")],
        [_btn(t(lang, "BTN_KARMA"), "profile:view:karma")],
    ])


def back_profile_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BACK_PROFILE"), "profile:view:main")]])


# ── bag (P2) ─────────────────────────────────────────────────────────────────

BAG_TABS = (("gear", "⚔️", "BAG_TAB_GEAR"),
            ("consumables", "💊", "BAG_TAB_PILLS"),
            ("materials", "🌿", "BAG_TAB_MATS"))


def bag_tab_row(lang: str, tab: str) -> list[InlineKeyboardButton]:
    return [_btn((("🔸 " if active == tab else "") + f"{icon} {t(lang, key)}"),
                 f"bag:tab:{active}:1")
            for active, icon, key in BAG_TABS]


def _bag_items_page(lang: str, user: dict, tab: str, page: int):
    from ..render import _bag_rows, ITEMS_PER_PAGE
    rows = _bag_rows(lang, user, tab)
    pages = max(1, (len(rows) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, pages))
    chunk = rows[(page - 1) * ITEMS_PER_PAGE: page * ITEMS_PER_PAGE]
    return chunk, page, pages


def bag_kb(lang: str, user: dict, tab: str, page: int) -> InlineKeyboardMarkup:
    chunk, page, pages = _bag_items_page(lang, user, tab, page)
    rows = [bag_tab_row(lang, tab)]
    btns = [_btn(f"🔍 {label[:42]}", f"bag:inspect:{tab}:{page}:{iid}") for iid, label in chunk]
    rows.extend([btns[i:i + 1] for i in range(0, len(btns))])
    nav = []
    if page > 1:
        nav.append(_btn(t(lang, "PAGE_PREV"), f"bag:tab:{tab}:{page - 1}"))
    nav.append(_btn(t(lang, "PAGE_INDICATOR", page=page, pages=pages)))
    if page < pages:
        nav.append(_btn(t(lang, "PAGE_NEXT"), f"bag:tab:{tab}:{page + 1}"))
    rows.append(nav)
    return kb(rows)


def item_kb(lang: str, user: dict, tab: str, page: int, item_id: str) -> InlineKeyboardMarkup:
    """Identity-card actions (P2): consume / assign-to-battle / equip / sell / back."""
    from ..engine import items as items_mod
    gear = data_registry.get_equipment(item_id)
    item = data_registry.get_consumable(item_id)
    rows: list[list[InlineKeyboardButton]] = []
    equipped_slot = next((s for s, g in (user["equipment"].items() or {})
                          if g and g.get("id") == item_id), None)
    if gear:
        if not equipped_slot:
            rows.append([_btn(t(lang, "BTN_EQUIP"), f"bag:action:equip:{item_id}")])
        else:
            rows.append([_btn(t(lang, "BTN_UNEQUIP2"), f"bag:action:unequip:{equipped_slot}")])
    else:
        action = getattr(item, "action", "") if item else ""
        if item and action not in ("sell_only",) and item.category in ("pill", "herb", "relic"):
            rows.append([_btn(t(lang, "BTN_SWALLOW"), f"bag:action:consume:{item_id}")])
        if item and (item.category in ("disposable_weapon", "talisman", "relic")
                     or action in ("instant_damage", "shield", "enemy_debuff", "heal_hp")):
            rows.append([_btn(t(lang, "BTN_ASSIGN_BATTLE"), f"bag:action:assign_battle:{item_id}")])
    sell = int(getattr(gear or item, "sell_price", 0) or 0)
    if sell and not equipped_slot:
        rows.append([_btn(t(lang, "BTN_SELL2", price=sell), f"bag:action:sell:{item_id}")])
    rows.append([_btn(t(lang, "BTN_BACK_LIST"), f"bag:tab:{tab}:{page}")])
    return kb(rows)


# ── world map (P3) ───────────────────────────────────────────────────────────

def map_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    """2-column zone grid with danger badges and Qi densities."""
    cur = user["location"].get("current_zone_id")
    zones = data_registry.zones_ordered()
    rows, row = [], []
    for z in zones:
        mark = "📍" if z.zone_id == cur else ""
        density = Locale.num(lang, float(z.density))
        label = f"{z.icon} {mark}{z.name_for(lang)} ({density}×)"
        row.append(_btn(label[:60], f"map:zone:inspect:{z.zone_id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn(t(lang, "MAP_BACK_PROFILE"), "profile:view:main")])
    return kb(rows)


def zone_hub_kb(lang: str, user: dict, zone_id: str) -> InlineKeyboardMarkup:
    settled = user["location"].get("current_zone_id") == zone_id
    settle = (_btn(t(lang, "ZONE_SETTLED"), NOOP) if settled
              else _btn(t(lang, "ZONE_SETTLE"), f"map:action:settle:{zone_id}"))
    return kb([
        [settle],
        [_btn(t(lang, "ZONE_HUNT"), f"map:action:hunt:{zone_id}"),
         _btn(t(lang, "ZONE_GATHER"), f"map:action:gather:{zone_id}")],
        [_btn(t(lang, "ZONE_BACK_MAP"), "map:view:world")],
    ])


# ── combat (P4) ──────────────────────────────────────────────────────────────

def battle_kb(lang: str, user: dict, session: dict) -> InlineKeyboardMarkup:
    rnd = session["round"]
    rows: list[list[InlineKeyboardButton]] = []
    loadout = [x for x in user["combat"]["loadout"] if x]
    tech_btns = []
    for tid in loadout:
        tech = data_registry.get_technique(tid)
        if not tech:
            continue
        nm = tech.name_en if (lang == "en" and tech.name_en) else tech.name
        if len(nm) > 16:
            nm = nm[:15] + "…"
        cd = session["cooldowns"].get(tid, 0)
        if cd > 0:
            tech_btns.append(_btn(f"⏳ {nm} ({Locale.num(lang, cd)} {t(lang, 'ROUNDS')})", NOOP))
        elif session["player"]["qi"] < tech.qi_cost:
            tech_btns.append(_btn(f"🚫 {nm} ({t(lang, 'LOW_QI')})", NOOP))
        else:
            tech_btns.append(_btn(f"⚡ {nm} ({Locale.num(lang, tech.qi_cost)} {t(lang, 'QI_UNIT')})",
                                  f"combat:act:tech:{tid}:{rnd}"))
    rows.extend([tech_btns[i:i + 2] for i in range(0, len(tech_btns), 2)])
    rows.append([
        _btn(t(lang, "BTN_MERIDIAN_STRIKE"), f"combat:act:basic:none:{rnd}"),
        _btn(t(lang, "BTN_FLEE"), f"combat:act:flee:none:{rnd}")])
    aux = battle_item_buttons(lang, user, rnd)
    if aux:
        rows.append(aux[:2])
    rows.append([_btn(t(lang, "BTN_SIM"), f"combat:act:sim:none:{rnd}")])
    return kb(rows)


def battle_item_buttons(lang: str, user: dict, rnd: int = 1) -> list[InlineKeyboardButton]:
    from ..engine import items as items_mod
    from ..core.combat_engine import battle_item_sources
    rnd_src = battle_item_sources(user)
    btns = []
    for iid in rnd_src:
        item = data_registry.get_consumable(iid)
        if not item:
            continue
        qty = items_mod.count_item(user, iid)
        if qty <= 0:
            continue
        nm = item.name_en if (lang == "en" and item.name_en) else item.name
        if len(nm) > 16:
            nm = nm[:15] + "…"
        icon = "📜" if item.category in ("disposable_weapon", "talisman", "relic") else "💊"
        btns.append(_btn(f"{icon} {nm} (×{Locale.num(lang, qty)})", f"combat:act:item:{iid}:{rnd}"))
    return btns


def loot_scroll_kb(lang: str, zone_id: str | None) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_COLLECT_SETTLE"),
                     f"map:zone:inspect:{zone_id or 'zone_valley_mortals'}")]])


def flee_kb(lang: str, zone_id: str | None) -> InlineKeyboardMarkup:
    data = f"map:zone:inspect:{zone_id}" if zone_id else "map:view:world"
    return kb([[_btn(t(lang, "BTN_BACK_TO_REGION"), data)]])


def mercy_plunder_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_MERCY"), "combat:rival:mercy"),
                _btn(t(lang, "BTN_PLUNDER"), "combat:rival:plunder")]])


def recovery_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_RETURN_MEDITATE"), "cultivate:view:hub")]])


# ── meditation hub (P6) ──────────────────────────────────────────────────────

def meditate_hub_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "BTN_CLAIM_CHRONICLE"), "cultivate:action:claim")],
        [_btn(t(lang, "BTN_SWITCH_MANTRA"), "cultivate:mantra:select"),
         _btn(t(lang, "BTN_CATALYST"), "cultivate:catalyst:menu")],
        [_btn(t(lang, "BACK_PROFILE"), "profile:view:main")],
    ])


def chronicle_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_BACK_MEDITATE"), "cultivate:view:hub")]])


def catalyst_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    rows = []
    for grade in ("low", "mid", "high", "heavenly"):
        rows.append([_btn(t(lang, f"STONE_{grade.upper()}"), f"cultivate:catalyst:stone:{grade}")])
    rows.append([_btn(t(lang, "BACK"), "cultivate:view:hub")])
    return kb(rows)


def mantra_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    """Owned/available mantras (shared by hub + pavilion)."""
    active = user["cultivation"].get("active_method_id")
    owned = set(user["inventory"].get("methods") or [])
    rows = []
    for mid, m in data_registry.methods.items():
        if mid not in owned and m.tier != "mortal":
            continue
        nm = m.name_for(lang)
        icon = "✅" if mid == active else "🌀"
        rows.append([_btn(f"{icon} {nm} (×{Locale.num(lang, m.qi_mult)})", f"martial:mantra:set:{mid}")])
    rows.append([_btn(t(lang, "BACK"), "martial:mantra:back")])
    return kb(rows)


# ── martial pavilion (P5 §2) ─────────────────────────────────────────────────

def martial_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    from ..engine.models import max_slots as _ms
    cap = _ms(user)
    rows = [[_btn(t(lang, "BTN_SWITCH_MANTRA"), "martial:mantra:menu")]]
    btns = []
    for i in range(6):
        if i < cap:
            tid = user["combat"]["loadout"][i] if i < len(user["combat"]["loadout"]) else None
            label = f"🔄 [{Locale.num(lang, i + 1)}] "
            if tid:
                tech = data_registry.get_technique(tid)
                nm = (tech.name_en if (lang == "en" and tech and tech.name_en) else tech.name) if tech else tid
                label += nm[:14] + ("…" if len(nm) > 14 else "")
            else:
                label += t(lang, "SLOT_FREE")
            btns.append(_btn(label, f"martial:slot:select:{i}"))
        else:
            btns.append(_btn(f"🔒 [{Locale.num(lang, i + 1)}] {t(lang, 'SLOT_LOCKED')}", NOOP))
    rows.extend([btns[i:i + 3] for i in range(0, len(btns), 3)])
    rows.append([_btn(t(lang, "BACK_PROFILE"), "profile:view:main")])
    return kb(rows)


def slot_picker_kb(lang: str, user: dict, slot_idx: int) -> InlineKeyboardMarkup:
    from ..engine import items as items_mod
    equipped = [x for x in user["combat"]["loadout"] if x]
    rows = []
    for tid in items_mod.owned_techniques(user):
        tech = data_registry.get_technique(tid)
        if not tech:
            continue
        nm = tech.name_en if (lang == "en" and tech.name_en) else tech.name
        mark = "✅ " if tid in equipped else "⚡ "
        rows.append([_btn(f"{mark}{nm[:20]} ({Locale.num(lang, tech.qi_cost)} {t(lang, 'QI_UNIT')})",
                          f"martial:slot:equip:{slot_idx}:{tid}")])
    rows.append([_btn(t(lang, "BTN_CLEAR_SLOT"), f"martial:slot:clear:{slot_idx}")])
    rows.append([_btn(t(lang, "BTN_CANCEL_BACK"), "martial:view:main")])
    return kb(rows)


# ── spirit pavilion / shop (P5 §4) ───────────────────────────────────────────

def shop_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "SHOP_ARTS_ROW"), "shop:tab:arts:1")],
        [_btn(t(lang, "SHOP_PILLS_ROW"), "shop:tab:pills:1"),
         _btn(t(lang, "SHOP_GEAR_ROW"), "shop:tab:gear:1")],
        [_btn(t(lang, "SHOP_TALI_ROW"), "shop:tab:talismans:1"),
         _btn(t(lang, "BACK_PROFILE"), "profile:view:main")],
    ])


def arts_kb(lang: str, user: dict, page: int) -> InlineKeyboardMarkup:
    from ..render import ARTS_PER_PAGE
    arts = sorted(data_registry.martial_arts.values(), key=lambda a: (a.required_realm, a.art_id))
    pages = max(1, (len(arts) + ARTS_PER_PAGE - 1) // ARTS_PER_PAGE)
    page = max(1, min(page, pages))
    owned = set(user["inventory"].get("arts") or [])
    rows = []
    for a in arts[(page - 1) * ARTS_PER_PAGE: page * ARTS_PER_PAGE]:
        mark = "✅" if a.art_id in owned else "📜"
        el = a.element_en if (lang == "en" and a.element_en) else a.element
        rows.append([_btn(f"{mark} {a.name_for(lang)[:30]} ({el})", f"shop:art:inspect:{a.art_id}:{page}")])
    nav = []
    if page > 1:
        nav.append(_btn(t(lang, "PAGE_PREV"), f"shop:tab:arts:{page - 1}"))
    nav.append(_btn(t(lang, "PAGE_INDICATOR", page=page, pages=pages)))
    if page < pages:
        nav.append(_btn(t(lang, "PAGE_NEXT"), f"shop:tab:arts:{page + 1}"))
    rows.append(nav)
    rows.append([_btn(t(lang, "BACK_SHOP_HUB"), "shop:view:hub")])
    return kb(rows)


def art_card_kb(lang: str, user: dict, art_id: str, page: int) -> InlineKeyboardMarkup:
    learned = art_id in set(user["inventory"].get("arts") or [])
    if learned:
        return kb([
            [_btn(t(lang, "BTN_TO_HALL"), "martial:view:main")],
            [_btn(t(lang, "BTN_BACK_ARTS"), f"shop:tab:arts:{page}")],
        ])
    return kb([
        [_btn(t(lang, "BTN_CLAIM_FREE"), f"shop:art:claim_free:{art_id}:{page}")],
        [_btn(t(lang, "BTN_BACK_ARTS"), f"shop:tab:arts:{page}")],
    ])


def booth_kb(lang: str, user: dict, booth: str, page: int = 1) -> InlineKeyboardMarkup:
    from ..engine import items as items_mod
    entries = items_mod.booth_entries(user, booth)
    rows = []
    for e in entries[:10]:
        icon = {"pills": "💊", "gear": "⚔️", "talismans": "📜"}[booth]
        rows.append([_btn(f"{icon} {e['name'][:26]}", f"shop:booth:claim:{booth}:{e['item_id']}")])
    rows.append([_btn(t(lang, "BACK_SHOP_HUB"), "shop:view:hub")])
    return kb(rows)


# ── sect gate / dashboard ────────────────────────────────────────────────────

def sect_gate_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BACK_PROFILE"), "profile:view:main")]])


def sect_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    from ..engine.constants import NPC_SECTS
    rows = []
    if user["location"].get("sect_id"):
        rows.append([_btn(t(lang, "BTN_LEAVE_SECT"), "sect:action:leave")])
    else:
        for sect_id, sect in NPC_SECTS.items():
            rows.append([_btn(t(lang, "BTN_JOIN_SECT", sect=t(lang, sect["key"])),
                              f"sect:action:join:{sect_id}")])
        if user["cultivation"]["current_realm_index"] >= 6:
            rows.append([_btn(t(lang, "BTN_FOUND_SECT"), "sect:action:found")])
    rows.append([_btn(t(lang, "BACK_PROFILE"), "profile:view:main")])
    return kb(rows)


# ── breakthrough (P6 §3–4) ───────────────────────────────────────────────────

def bt_prep_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "BTN_WRATH"), "breakthrough:action:confirm")],
        [_btn(t(lang, "BTN_TAKE_PILL"), "breakthrough:pill:select")],
        [_btn(t(lang, "BTN_WITHDRAW"), "profile:view:main")],
    ])


def bt_pill_kb(lang: str, user: dict) -> InlineKeyboardMarkup:
    rows = []
    from ..engine import items as items_mod
    for iid, qty in sorted((user["inventory"].get("items") or {}).items()):
        item = data_registry.get_consumable(iid)
        if not item or qty <= 0 or item.action != "breakthrough_bonus":
            continue
        nm = item.name_en if (lang == "en" and item.name_en) else item.name
        rows.append([_btn(f"💊 {nm[:22]} (×{Locale.num(lang, qty)})", f"breakthrough:pill:use:{iid}")])
    if user["cultivation"].get("active_pill"):
        rows.append([_btn(t(lang, "BTN_DROP_PILL"), "breakthrough:pill:drop")])
    rows.append([_btn(t(lang, "BACK"), "breakthrough:view:prep")])
    return kb(rows)


def bt_win_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_RECORD_RANK"), "profile:view:main")]])


def bt_fail_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([[_btn(t(lang, "BTN_REPAIR_BAG"), "bag:tab:consumables:1"),
                _btn(t(lang, "BTN_RESETTLE_HEAL"), "cultivate:view:hub")]])


# ── legacy adjuncts (dao / sacrifice / admin) ────────────────────────────────

def dao_kb(lang: str, chosen: bool) -> InlineKeyboardMarkup:
    from ..engine.constants import DAO_PATHS
    rows = []
    for dao, spec in DAO_PATHS.items():
        aligns = ("demonic",) if dao == "blood" else ("orthodox", "demonic")
        rows.append([_btn(t(lang, spec["key"]) + (" 🕊" if a == "orthodox" else " 😈"),
                          f"dao:pick:{dao}:{a}") for a in aligns])
    rows.append([_btn(t(lang, "BACK_PROFILE"), "profile:view:main")])
    return kb(rows)


def sacrifice_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "SACRIFICE_BLOOD"), "sect:sacrifice:blood")],
        [_btn(t(lang, "SACRIFICE_DEVOUR"), "sect:sacrifice:devour")],
        [_btn(t(lang, "BACK"), "sect:view:main")],
    ])


def admin_kb(lang: str) -> InlineKeyboardMarkup:
    return kb([
        [_btn(t(lang, "ADMIN_BROADCAST"), "admin:usage:broadcast")],
        [_btn(t(lang, "ADMIN_DROPS"), "admin:drops")],
        [_btn(t(lang, "ADMIN_INSPECT"), "admin:usage:inspect")],
        [_btn(t(lang, "ADMIN_REALM"), "admin:usage:realm")],
        [_btn(t(lang, "ADMIN_JUDGE"), "admin:usage:judge")],
        [_btn(t(lang, "ADMIN_WORLD"), "admin:usage:boost")],
        [_btn(t(lang, "BACK"), "profile:view:main")],
    ])

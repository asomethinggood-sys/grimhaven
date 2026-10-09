"""Game data tables — faithful to the Master Design Document (docs/master_prompt_fa.txt).

Every numeric value here is taken from the design doc: realm costs, timers,
penalties, catalyst boosts, tiers, zones. Locale keys referenced here live in
locales/locale_fa.json and locales/locale_en.json.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Chapter 2 — Realms, stages, Qi costs, seclusion timers
# ─────────────────────────────────────────────────────────────────────────────
# Each realm: ordered list of stage costs (Qi needed in the Dantian to break
# through that stage).  qi_capacity of a player equals the cost of the stage
# they are working on (a full Dantian == ready for breakthrough).

REALM_STAGES: dict[int, list[int]] = {
    # 1. تصفیه چی — 9 پیوسته لایه
    1: [500, 1_000, 2_000, 3_500, 5_500, 8_000, 11_000, 15_000, 20_000],
    # 2. پی‌ریزی بنیاد — Early / Mid / Late / Peak
    2: [60_000, 60_000, 60_000, 60_000],
    # 3. هسته طلایی
    3: [300_000, 300_000, 300_000, 300_000],
    # 4. روح نوزاد
    4: [1_500_000, 1_500_000, 1_500_000, 1_500_000],
    # 5. انشعاب روح — 3 برش فانی + اوج
    5: [6_000_000, 6_000_000, 6_000_000, 6_000_000],
    # 6. پژواک خلاء
    6: [25_000_000, 25_000_000, 25_000_000, 25_000_000],
    # 7. صعود به مصیبت — 9 مصیبت صاعقه
    7: [60_000_000] * 9,
    # 8. نامیرای حقیقی
    8: [250_000_000, 250_000_000, 250_000_000, 250_000_000],
    # 9. حاکم دائو — سقف قدرت (ادغام کیهان: هزینه نمادین بی‌نهایت)
    9: [10**15],
}

REALM_NAMES: dict[int, str] = {  # locale keys
    1: "REALM_1_NAME",
    2: "REALM_2_NAME",
    3: "REALM_3_NAME",
    4: "REALM_4_NAME",
    5: "REALM_5_NAME",
    6: "REALM_6_NAME",
    7: "REALM_7_NAME",
    8: "REALM_8_NAME",
    9: "REALM_9_NAME",
}

MAX_REALM = 9

# §2.2 — Closed-door seclusion durations, in MINUTES.
SECLUSION_MINUTES_REALM1 = {  # by layer (1-indexed)
    1: 15, 2: 15, 3: 15,
    4: 45, 5: 45, 6: 45,
    7: 120, 8: 120, 9: 120,
}
SECLUSION_MINUTES_REALM = {
    2: 6 * 60,       # 6 hours
    3: 24 * 60,      # 24 hours
    4: 48 * 60,      # 48 hours
    5: 72 * 60, 6: 72 * 60, 7: 72 * 60, 8: 72 * 60, 9: 72 * 60,
}

# Chapter 3 — breakthrough maths (doc §3.1 + §10.2 reference code)
BASE_CHANCE_LAYER = 50.0     # ordinary layer/stage within a realm
BASE_CHANCE_REALM_JUMP = 25.0  # jump between major realms
REALM_PENALTIES: dict[int, float] = {1: 0, 2: 10, 3: 20, 4: 35, 5: 50, 6: 65, 7: 80, 8: 90}
SUCCESS_CLAMP_MIN, SUCCESS_CLAMP_MAX = 5.0, 95.0
FAILURE_WEIGHTS = {"MINOR": 60, "DEVIATION": 30, "ANNIHILATION": 10}
MIRACLE_LUCK_COST = 15       # karmic luck burned by a miracle salvation

# §2.3 — ability spikes on entering a realm: (hp_mult, qi_cap_mult)
REALM_ENTRY_SPIKES: dict[int, tuple[float, float]] = {
    2: (3.0, 4.0),    # ×3 HP, ×4 Qi capacity, +50% physical resist
    3: (5.0, 8.0),    # ×5 HP, ×8 Qi, immunity to Qi-Condensation attacks
    4: (8.0, 15.0),   # ×8 HP, ×15 Qi, emergency soul bar
}

# §5 — AFK base Qi rates per realm (doc §10.2 reference code, extended to 9)
BASE_RATES: dict[int, float] = {
    1: 100, 2: 300, 3: 800, 4: 2_000, 5: 5_000, 6: 12_000, 7: 30_000,
    8: 60_000, 9: 100_000,
}

TECH_MULT_RANGE = (1.0, 10.0)   # doc §5
VEIN_DENSITY_RANGE = (1.0, 5.0)  # doc §5
LUCK_BONUS_FACTOR = 0.002        # Luck × 0.002

# ─────────────────────────────────────────────────────────────────────────────
# Chapter 3/5 — Catalysts
# ─────────────────────────────────────────────────────────────────────────────
# Spirit stones: boost %, consumption per hour (fractional ok)
SPIRIT_STONES: dict[str, dict[str, float]] = {
    "low":      {"boost": 0.15, "per_hour": 20.0},
    "mid":      {"boost": 0.45, "per_hour": 5.0},
    "high":     {"boost": 1.20, "per_hour": 0.5},
    "heavenly": {"boost": 3.00, "per_hour": 0.0},  # no decay
}

HERBS: dict[str, dict] = {
    "root_ancient":  {"key": "ITEM_ROOT_ANCIENT",  "effect": "circulation_perm",  "boost": 0.25},
    "lotus_seven":   {"key": "ITEM_LOTUS_SEVEN",   "effect": "core_stability",    "boost": 0.20},
}

# Demonic sacrifices (Demonic path only) — doc §5.1.4
SACRIFICES: dict[str, dict] = {
    "blood":  {"boost": 1.00, "hours": 12, "corruption": 5,  "karma": -10, "key": "SACRIFICE_BLOOD"},
    "devour": {"boost": 5.00, "hours": 6,  "corruption": 15, "karma": -20, "key": "SACRIFICE_DEVOUR"},
}

# ─────────────────────────────────────────────────────────────────────────────
# Chapter 4 — stats & reveal conditions
# ─────────────────────────────────────────────────────────────────────────────
CORRUPTION_VISIBLE_AT = 30          # purple aura reveal threshold
LUCK_REVEAL_REALM = 4               # via Nascent Soul or Star-Observing method
CHARISMA_REVEAL_REALM = 4           # via Nascent Soul or quest chain
DAOHEART_REVEAL_REALM = 5           # Spirit Severing+

# ─────────────────────────────────────────────────────────────────────────────
# Chapter 6 — alignments & the five Dao paths
# ─────────────────────────────────────────────────────────────────────────────
ALIGNMENTS = ("orthodox", "demonic")

DAO_PATHS: dict[str, dict] = {
    "sword":    {"key": "DAO_SWORD",    "atk": 1.30, "def": 0.85, "crit": 0.25, "hp": 1.00},
    "alchemy":  {"key": "DAO_ALCHEMY",  "atk": 1.00, "def": 1.00, "crit": 0.05, "hp": 1.10},
    "body":     {"key": "DAO_BODY",     "atk": 1.05, "def": 1.35, "crit": 0.05, "hp": 1.50},
    "elements": {"key": "DAO_ELEMENTS", "atk": 1.15, "def": 1.10, "crit": 0.10, "hp": 1.10},
    "blood":    {"key": "DAO_BLOOD",    "atk": 1.25, "def": 0.95, "crit": 0.15, "hp": 1.05},
}
DAO_CHOICE_REALM, DAO_CHOICE_STAGE = 1, 8  # end of layer 9 → before Foundation
BLOOD_DAO_QI_STEAL = 0.10                  # steal 10% of victim's stored Qi
DEMONIC_AFK_MULT_CAP = 3.0                 # demonic AFK up to ×3

# ─────────────────────────────────────────────────────────────────────────────
# Chapter 7 — item tiers, equipment slots, techniques
# ─────────────────────────────────────────────────────────────────────────────
ITEM_TIERS = ("mortal", "earth", "heaven", "spirit", "saint", "ancient", "divine")
TIER_KEYS = {t: f"TIER_{t.upper()}" for t in ITEM_TIERS}
TIER_POWER = {t: i for i, t in enumerate(ITEM_TIERS)}   # 0..6
TIER_STAT_MULT = {t: 1.0 + i * 0.75 for i, t in enumerate(ITEM_TIERS)}

EQUIP_SLOTS = ("weapon", "robe", "ring", "accessory", "companion", "natal")

# Internal cultivation methods — tech multiplier 1.0 .. 10.0 (doc §7.3)
METHODS: dict[str, dict] = {
    "method_breath_mortal":   {"tier": "mortal", "tech_mult": 1.0, "key": "METHOD_BREATH_MORTAL"},
    "method_sky_cleaving":    {"tier": "earth",  "tech_mult": 1.5, "key": "METHOD_SKY_CLEAVING"},
    "method_jade_purity":     {"tier": "heaven", "tech_mult": 2.5, "key": "METHOD_JADE_PURITY"},
    "method_dragon_vein":     {"tier": "spirit", "tech_mult": 4.0, "key": "METHOD_DRAGON_VEIN"},
    "method_star_field":      {"tier": "saint",  "tech_mult": 6.5, "key": "METHOD_STAR_FIELD"},
    "method_chaos_origin":    {"tier": "ancient","tech_mult": 8.5, "key": "METHOD_CHAOS_ORIGIN"},
    "method_heaven_dao":      {"tier": "divine", "tech_mult": 10.0, "key": "METHOD_HEAVEN_DAO"},
}

# Active combat techniques — 4 loadout slots (doc §7.3)
TECHNIQUES: dict[str, dict] = {
    "basic_strike":   {"slot": 1, "power": 1.0, "qi_cost_pct": 0.0,  "key": "TECH_BASIC_STRIKE"},
    "iron_guard":     {"slot": 2, "power": 0.0, "qi_cost_pct": 0.05, "key": "TECH_IRON_GUARD", "guard": True},
    "mist_step":      {"slot": 2, "power": 0.4, "qi_cost_pct": 0.05, "key": "TECH_MIST_STEP",  "evade": True},
    "element_burst":  {"slot": 3, "power": 1.8, "qi_cost_pct": 0.12, "key": "TECH_ELEMENT_BURST"},
    "sword_rain":     {"slot": 3, "power": 2.2, "qi_cost_pct": 0.18, "key": "TECH_SWORD_RAIN"},
    "ult_heaven_split": {"slot": 4, "power": 4.5, "qi_cost_pct": 0.50, "key": "TECH_ULT_HEAVEN_SPLIT"},
    "ult_blood_moon":   {"slot": 4, "power": 4.0, "qi_cost_pct": 0.50, "key": "TECH_ULT_BLOOD_MOON", "lifesteal": 0.5},
}

# ─────────────────────────────────────────────────────────────────────────────
# Chapters 1/5 — world zones & spirit veins
# ─────────────────────────────────────────────────────────────────────────────
def _zones_bridge() -> dict[str, dict]:
    """Legacy-shape view over data/zones.json (the dataset is the source of truth)."""
    import json as _json
    from pathlib import Path as _P
    path = _P(__file__).resolve().parent.parent.parent / "data" / "zones.json"
    raw = _json.loads(path.read_text(encoding="utf-8"))
    return {zid: {"key": z["key"], "vein_density": float(z["density"]),
                  "min_realm": int(z["required_realm_tier"]), "guard": int(z["guard"]),
                  "icon": z["icon"], "name": z["name"], "name_en": z["name_en"]}
            for zid, z in raw.items()}


ZONES: dict[str, dict] = _zones_bridge()
DEFAULT_ZONE = "zone_valley_mortals"

# NPC sects joinable from Foundation Establishment (doc §2.3)
NPC_SECTS: dict[str, dict] = {
    "sect_azure_cloud":  {"key": "SECT_AZURE_CLOUD",  "alignment": "orthodox"},
    "sect_crimson_fire": {"key": "SECT_CRIMSON_FIRE", "alignment": "orthodox"},
    "sect_moon_shadow":  {"key": "SECT_MOON_SHADOW",  "alignment": "demonic"},
}
SECT_FOUND_REALM = 6  # found own sect as Sect Master at Void Refinement (doc §2.3)

# PvP-flavoured karma economy (doc §3.3.4)
KARMA_MODIFIERS = {
    "help_disciple": +2,
    "mercy_in_battle": +1,
    "save_mortal": +5,
    "plunder": -5,
    "blood_sacrifice": -10,
    "miracle_escape": -15,  # direct karma burn — see MIRACLE_LUCK_COST
}

# Lucky encounters while AFK (doc §3.3.2)
LUCKY_ENCOUNT_THRESHOLD = 70
AMBUSH_THRESHOLD = 20
LUCKY_ENCOUNT_CHANCE = 0.05

# Tribulation lightning damage variance (doc §3.3.3)
TRIBULATION_DAMAGE_RANGE = (10_000, 30_000)

# Shop (Pavilion) prices in low-grade spirit stones
PAVILION_PRICES: dict[str, int] = {
    "stone_mid": 8,
    "stone_high": 60,
    "pill_foundation": 40,
    "pill_guardian": 40,
    "pill_spirit": 120,
    "root_ancient": 200,
    "lotus_seven": 260,
    "method_jade_purity": 500,
    "method_dragon_vein": 2_000,
    "method_star_field": 8_000,
    "method_chaos_origin": 30_000,
    "tech_sword_rain": 300,
    "tech_ult_heaven_split": 1_500,
    "tech_ult_blood_moon": 1_500,
    "weapon_earth": 150,
    "weapon_heaven": 900,
    "robe_earth": 120,
    "ring_spirit": 700,
    "accessory_karma": 1_000,
    "companion_sun_orb": 2_500,
    "doll_substitute": 10_000,
}

PILLS: dict[str, dict] = {
    "pill_foundation": {"key": "ITEM_PILL_FOUNDATION", "bonus": 20.0},
    "pill_guardian":   {"key": "ITEM_PILL_GUARDIAN",   "bonus": 20.0},
    "pill_spirit":     {"key": "ITEM_PILL_SPIRIT",     "bonus": 30.0},
}

STARTING_ITEMS = {
    "spirit_stones": {"low": 30, "mid": 0, "high": 0, "heavenly": 0},
    "herbs": {},
    "pills": {"pill_guardian": 1},
    "methods": ["method_breath_mortal", "method_sky_cleaving"],
    "techniques": ["basic_strike", "iron_guard", "element_burst"],
    "gear": {},  # gear_id -> {"slot","tier","key"}
}

#!/usr/bin/env python3
"""Build data/*.json — the modular content datasets for Grimhaven.

Sources (uploaded to the 'TELEGRAM GAME' Google Drive folder, mirrored in google_drive/):
  * dataoffline_events.json  → 50 idle-adventure events (FA canonical text kept verbatim, EN added)
  * datamartial_arts.json    → 30 manuals / 90 techniques (flavor kept verbatim, combat stats + EN added)
plus authored tables: equipment.json, consumables.json, enemies.json.

The generated JSON is committed to the repo; this script only needs re-running when the
source content changes.  Run from the repo root:  python scripts/build_data.py
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "google_drive"
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)


def _h(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


# ─────────────────────────────────────────────────────────────────────────────
# 1) OFFLINE EVENTS — EN translations + item-name → item_id mapping
# ─────────────────────────────────────────────────────────────────────────────
EVENT_EN = {
    1: "While meditating under the waterfall, a Qi-devouring viper struck — you cleaved it in a single flash of the blade.",
    2: "A mysterious traveler brushed past you and slipped a low-grade spirit pill into your robe.",
    3: "The sky thundered without warning and your focus shattered; your Dao Heart wavered by 2%.",
    4: "The mountain breeze carried the scent of spirit blossoms; your lungs filled with cool, clean Qi.",
    5: "A playful spirit monkey dropped from the canopy, tossed two spirit stones at your feet, and vanished into the branches.",
    6: "A stray lightning spark bit through your flesh; your muscles went numb for a moment.",
    7: "You heard the wind's whisper — for an instant you perceived the deeper current of Qi through your meridians.",
    8: "An iron-feathered eagle tried to ambush you; a burst of Qi sent it fleeing.",
    9: "In the rock fissure behind you, you unearthed the root of a wild ten-year ginseng.",
    10: "A demonic cultivator spied you from afar, but turned away when he saw the steadiness of your aura.",
    11: "Dawn frost crystallized on your lashes; your body's resistance to the bone-deep cold was quietly tested.",
    12: "The ancient temple bell tolled across the valley; your troubled mind settled into perfect stillness.",
    13: "A luminous crystal beetle landed on your knee; you coaxed it gently toward the flowers with a thread of Qi.",
    14: "The midday sun synced with your breathing; your absorption rate doubled for a while.",
    15: "A distant memory from your mortal days surfaced; one tear fell, and your resolve hardened like tempered steel.",
    16: "A three-tailed spirit fox passed by, gazed deep into your Dantian, and dissolved into the mist.",
    17: "Venom wasps swarmed around your head; you ignited your spiritual aura and burned them all to ash.",
    18: "A cascade of gravel struck your shoulder — your bronze-skin armor shattered it to dust.",
    19: "Blood hung in the air; among the remains of two slain spirit beasts you found a cracked beast core.",
    20: "The drip of the cave aligned with your heartbeat; your lower Dantian warmed with a pleasant heat.",
    21: "A mortal woodcutter glimpsed your aura, bowed in awe, and left a basket of wild mushrooms as an offering.",
    22: "A hot wind from the south set fire-grain Qi spinning through your meridians.",
    23: "A knot in your right meridian burst open; your hands feel lighter and your output flows freer.",
    24: "A rain of limpid Qi fell, washing the dust of the world from your robes.",
    25: "A snake coiled in the brambles struck at your ankle; a flick of two fingers crushed its skull.",
    26: "Inner demons whispered of shortcuts; you recited the orthodox mantra and dispersed them.",
    27: "The crack of a rotting branch broke the silence — your divine sense instantly spread to a hundred meters.",
    28: "A silver fish leapt from clear water; its twist and turn inspired a lighter footwork.",
    29: "The black cold of midnight bit to the bone; you burned stored Qi just to keep warm.",
    30: "Ancient tracks near your camp reminded you: in this world you are still very small.",
    31: "The smell of wet earth and cut grass scrubbed the lethargy of endless practice from your soul.",
    32: "In the stream bed you found a rusted dagger — the relic of some unknown cultivator from centuries past.",
    33: "Bloodthirsty mosquitoes descended; a spiritual shock dropped their swarm in a heap.",
    34: "A brown scorpion lunged from under a stone; you split it with the edge of your boot.",
    35: "The full moon rose; night-Yin merged with your breath cycle, and a current of pristine cold flowed through you.",
    36: "A distant avalanche shook the cliffs; for one second the umbilical of your concentration tore.",
    37: "Among the roots you found a rotted cloth pouch holding a few mortal-dynasty copper coins.",
    38: "A phosphorescent bird perched above you and sang another world away; your weariness lifted.",
    39: "A vein of toxic gas hissed from a fissure; you moved your dwelling three leagues downwind.",
    40: "A wandering wraith clawed at your mind; you incinerated it with golden light from your Dantian.",
    41: "You drank from the spirit spring; your throat turned clear as crystal.",
    42: "A colossal brown bear scented your Qi and fled in terror — you smiled at your own power.",
    43: "A crimson twilight aurora injected peerless Yang energy into the meridians of your body.",
    44: "The Qi in your Dantian reached a roaring pitch; the wall of your current layer felt thinner.",
    45: "A storm tore your hat away; you watched the endless majesty of nature and the endless road of the Dao.",
    46: "A glass frog sang in the pond; its resonance vibrated through the meridians of your inner ear.",
    47: "A golden leaf from an ancient tree landed on your palm and melted into a mote of pure Qi.",
    48: "A bandit blind to the world reached for your purse; a flick of your finger launched him ten meters back.",
    49: "In the uncanny hush of midnight your pulse aligned with the cosmos; perception honed to a razor.",
    50: "Faint incense and woodsmoke drifted up the valley; deep peace covered your weary body and mind.",
}

ITEM_BY_FA_NAME = {
    "قرص روح درجه پایین": "pill_spirit_gather_low",
    "جینسنگ وحشی": "herb_wild_ginseng",
    "هسته نیمه‌جانور": "mat_beast_core_broken",
    "قارچ کوهی": "herb_mountain_mushroom",
    "خنجر کهنه معنوی": "wpn_rusty_spirit_dagger",
}

TYPE_ICON = {"combat": "⚔️", "encounter": "👣", "hazard": "⚡️", "nature": "🍃", "insight": "💡"}


def build_offline_events() -> None:
    raw = json.loads((SRC / "dataoffline_events.json").read_text(encoding="utf-8"))
    assert len(raw) == 50, f"expected 50 events, got {len(raw)}"
    out = []
    for ev in raw:
        eff = dict(ev.get("effect") or {})
        item_name = eff.pop("item", None)
        if item_name:
            eff["item_id"] = ITEM_BY_FA_NAME[item_name]
            eff["item_name_fa"] = item_name
        out.append({
            "id": ev["id"],
            "type": ev["type"],
            "icon": TYPE_ICON[ev["type"]],
            "text": ev["text"],
            "text_en": EVENT_EN[ev["id"]],
            "effect": eff,
        })
    (OUT / "offline_events.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"offline_events.json: {len(out)} events")


# ─────────────────────────────────────────────────────────────────────────────
# 2) MARTIAL ARTS — required realm, price, multipliers, status effects, EN
# ─────────────────────────────────────────────────────────────────────────────
# art_id → (required_realm, price_low_stones, name_en, element_en, family)
ARTS = {
    "art_moonlight_sword":          (1, 220, "Moonlight Nightwalker Sword", "Metal & Yin Light", "sword"),
    "art_crimson_thunder_fist":     (2, 900, "Crimson Thunder Fist", "Fire & Lightning", "fist"),
    "art_autumn_gale_blade":        (1, 180, "Autumn Gale Blade", "Wind & Wither", "blade"),
    "art_abyssal_dragon_spear":     (2, 980, "Abyssal Dragon Spear", "Deep Water & Darkness", "spear"),
    "art_blazing_lotus_palm":       (1, 240, "Blazing Lotus Palm", "Fire & Wood", "palm"),
    "art_glacial_wolf_claw":        (1, 260, "Glacial Wolf Claw", "Ice & Beast", "claw"),
    "art_trembling_willow_whip":    (1, 160, "Trembling Willow Whip", "Wood & Flexibility", "whip"),
    "art_mountain_axe":             (1, 280, "Mountain-Splitting Axe", "Heavy Earth", "axe"),
    "art_shadow_sword":             (2, 950, "Deceitful Shadow Sword", "Darkness & Illusion", "shadow"),
    "art_sky_feather_bow":          (1, 250, "Storm-Eagle Feather Bow", "Wind & Speed", "bow"),
    "art_taiji_yin_yang":           (1, 300, "Taiji Yin-Yang Fist", "Duality of Yin & Yang", "taiji"),
    "art_drunken_blood_blade":      (3, 2600, "Drunken Blood Blade", "Blood & Disorder", "blood"),
    "art_golden_thunder_spear":     (3, 2800, "Golden Thunder Spear", "Metal & Lightning", "spear"),
    "art_ghost_step":               (2, 880, "Wandering Ghost Step", "Darkness & Wind", "shadow"),
    "art_solar_eagle_claw":         (2, 940, "Solar Eagle Claw", "Fire & Yang", "claw"),
    "art_bone_frost_needle":        (2, 960, "Bone-Frost Rain Needle", "Water & Deep Ice", "needle"),
    "art_centipede_poison_palm":    (3, 2700, "Centipede Venom Palm", "Poison & Earth", "palm"),
    "art_seven_stars_sword":        (4, 6400, "Seven Heavens Starsword", "Starlight & Space", "sword"),
    "art_roaring_quake_mace":       (4, 6200, "Roaring Quake Mace", "Earth & Sound", "mace"),
    "art_sandstorm_blade":          (2, 920, "Sandstorm Blade", "Earth & Wind", "blade"),
    "art_soul_chime_flute":         (3, 2500, "Soul-Charm Bone Flute", "Sound & Spirit", "sonic"),
    "art_phoenix_ash_sword":        (5, 15500, "Phoenix Ash Sword", "Undying Flame", "sword"),
    "art_ocean_mist_palm":          (2, 900, "Ocean Heavy-Mist Palm", "Water & Brine Fog", "palm"),
    "art_iron_rhino_fist":          (1, 200, "Iron Rhino Fist", "Metal & Earth Body", "fist"),
    "art_meteor_finger":            (1, 290, "Meteor-Sundering Finger", "Fire & Piercing Metal", "finger"),
    "art_blood_lotus_saber":        (3, 2650, "Blood Lotus Saber", "Blood & Demonic Rot", "blood"),
    "art_abyssal_serpent_spear":    (5, 15000, "Abyssal Serpent Spear", "Water & Corrosive Venom", "spear"),
    "art_spring_breeze_fan":        (1, 210, "Cherry-Breeze Fan", "Wood & Gentle Wind", "fan"),
    "art_phantom_bat_claw":         (2, 930, "Phantom Bat Claw", "Darkness & Sound", "blood"),
    "art_heavenly_tribulation_sword": (7, 62000, "Heavenly Tribulation Judgement", "Pure Lightning & Cosmic Law", "sword"),
}

TECH_EN = {  # technique id → english name
    "moon_slash": "Crescent Silver Beam", "lunar_mist": "Moon Dust Waltz", "zenith_eclipse": "Total Sword Eclipse",
    "thunder_strike": "Crimson Thunder Slam", "plasma_wave": "Dantian Plasma Wave", "hell_thunderstorm": "Ember Hell Thunderstorm",
    "falling_leaf_cut": "Falling Leaf Sever", "whirlwind_vortex": "Withered Leaf Vortex", "autumn_desolation": "Dirge of Yellow Frost",
    "abyss_thrust": "Blackwater Breach", "dragon_coil": "Dark Dragon Coil", "depths_maelstrom": "Maelstrom of the Silent Deep",
    "lotus_touch": "Petal Ignition", "root_ignition": "Kindled Root Veins", "thousand_petal_blast": "Ember Thousand-Petal Blast",
    "frost_swipe": "Snowstorm Talon Rake", "howling_blizzard": "Wolfhowl in the Fog", "pack_pounce": "Polar Night Hunt",
    "snap_lash": "Green Switch Crack", "entangling_bough": "Capturing Ivy Coils", "willow_dance_fury": "Stormdance of the Willows",
    "heavy_cleave": "Earthen Heavy Cleave", "ground_fissure": "Sudden Rock Fissure", "mountain_collapse": "Collapse of the Ancient Peak",
    "phantom_feint": "Phantom Feint", "umbra_cloak": "Glide of the Umbral Cloak", "endless_nightmare": "Blade-Nightmare Without End",
    "feather_arrow": "Windfletch Arrow", "triple_talon_shot": "Triple Talon Flight", "storm_barrage": "Iron Feather Barrage",
    "yin_deflection": "Flow of Soft Yin", "yang_burst": "Burst of Hard Yang", "harmony_wheel": "Cosmic Wheel of Balance",
    "stagger_cut": "Stumbling Swig Cut", "blood_toast": "Crimson Drinker's Toast", "frenzy_carnival": "Carnival of Red Frenzy",
    "golden_pierce": "Adamantine Piercer", "thunder_discharge": "Heaven's Discharge", "heaven_judgment_lance": "Lance of Cosmic Judgement",
    "vanishing_strike": "Blow from Nowhere", "afterimage_dance": "Dance of Eternal Afterimages", "soul_reaper_stride": "Reaper's Abyssal Stride",
    "sun_grip": "Noontide Scorching Grip", "solar_flare_dive": "Dive from the Solar Crown", "supernova_talon": "Supernova Talon Burst",
    "needle_shot": "Crystal Needle Volley", "marrow_chill": "Bone-Marrow Freeze", "thousand_frost_needles": "Thousand Icicle Needles",
    "venom_strike": "Marsh Green Fang", "corrosive_mist": "Acid Rot Mist", "heart_decay_palm": "Viscera Decay Palm",
    "star_flash": "Polaris Starflash", "big_dipper_slash": "Big Dipper Constellation Cut", "astral_collapse": "Meteorite Collapse",
    "mace_bash": "Stoneboulder Bash", "sonic_tremor": "Eardrum Quake Resonance", "earthshaker_slam": "Earthshatter Concussion",
    "sand_slash": "Grit Raw Sever", "blinding_dune": "Blinding Dune Veil", "desert_coffin": "Coffin of the Sinkhole",
    "mournful_tone": "Autumn's Sorrowful Air", "dissonant_screech": "Mind-Rending Screech", "requiem_hymn": "Hymn of Parting Souls",
    "cinder_edge": "Blazing Cinder Blade", "wing_sweep_flame": "Firebird Wing Sweep", "rebirth_inferno": "Rising from Ashes Inferno",
    "damp_slap": "Saltwave Smack", "obscuring_vapor": "Blinding Brine Shroud", "tsunami_thrust": "Onslaught of Roiling Waves",
    "horn_strike": "Adamant Horn Gore", "unbreakable_stance": "Bronze-Skin Steadfastness", "stampede_charge": "Herd of Rhino Stomps",
    "finger_poke": "Astral Pointing", "beam_piercer": "Meteor Laser Piercer", "falling_star_core": "Star-Core Descent",
    "blood_petal_cut": "Carnelian Petal Sever", "crimson_bloom": "Bloom of the Red Bud", "blood_sea_cleave": "Cleave of the Blood Sea",
    "viper_jab": "Blue Viper Fang-Jab", "constricting_tide": "Coils of the Squeezing Tide", "hydra_venom_thrust": "Nine-Head Venom Thrust",
    "petal_gust": "Spring Petal Gust", "soothing_trap": "Rapture of Spring Scents", "blooming_tempest": "Tempest of the Enchanted Garden",
    "sonar_scratch": "Night-Echo Talon Rake", "wing_blade_glide": "Soundless Wing-Blade Glide", "vampiric_screech": "Essence-Draining Shriek",
    "law_decree": "Decree of Heavenly Law", "tribulation_bolt": "Ninth Layer Tribulation Bolt", "heavenly_execution": "Blade of Cosmic Execution",
}

# family → (scaling_stat, hit verbs normal, crit verb, graze verb)
FAMILY = {
    "sword":  ("spiritual_atk", "slices", "carves to the bone", "glances"),
    "blade":  ("spiritual_atk", "slices", "cleaves open", "grazes"),
    "fist":   ("physical_atk",  "hammers", "pulverizes", "bounces off"),
    "palm":   ("spiritual_atk", "sends qi through", "scorches", "ripples off"),
    "claw":   ("physical_atk",  "rakes", "rending open", "scores"),
    "whip":   ("physical_atk",  "lashes", "flays", "snaps against"),
    "axe":    ("physical_atk",  "cleaves", "splits", "thuds against"),
    "mace":   ("physical_atk",  "batters", "shatters", "drums against"),
    "spear":  ("physical_atk",  "skewers", "pins through", "sparks off"),
    "bow":    ("physical_atk",  "punches through", "spikes home", "ricochets from"),
    "needle": ("spiritual_atk", "pierces", "transfixes", "ticks off"),
    "finger": ("spiritual_atk", "bores into", "drills through", "flecks"),
    "shadow": ("spiritual_atk", "ghosts past", "unmakes", "dissipates against"),
    "blood":  ("spiritual_atk", "drinks from", "empties", "sprays past"),
    "taiji":  ("spiritual_atk", "redirects", "hurls", "fizzles against"),
    "sonic":  ("spiritual_atk", "vibrates", "ruptures", "wavering off"),
    "fan":    ("spiritual_atk", "fans", "unmans", "drifts across"),
}

# tier index 0/1/2 → base damage multiplier
TIER_MULT = [(1.15, 1.25, 1.35), (1.6, 1.75, 1.9), (2.4, 2.6, 2.9)]


def _status_for(art_id: str, idx: int):
    """Status-infliction table — each family carries a signature effect."""
    fam = ARTS[art_id][4]
    heavy = idx == 2
    mid = idx == 1
    # burn families
    if art_id in ("art_crimson_thunder_fist", "art_blazing_lotus_palm", "art_phoenix_ash_sword", "art_meteor_finger"):
        ch = 0.25 if not mid else (0.45 if not heavy else 0.7)
        return {"kind": "burn", "chance": ch, "turns": 2 if not heavy else 3, "dot_pct": 0.035 if not heavy else 0.06}
    if art_id in ("art_centipede_poison_palm", "art_abyssal_serpent_spear"):
        ch = 0.35 if mid else (0.55 if heavy else 0.2)
        return {"kind": "poison", "chance": ch, "turns": 3 if not heavy else 4, "dot_pct": 0.03 if not heavy else 0.045}
    if art_id in ("art_golden_thunder_spear", "art_heavenly_tribulation_sword"):
        ch = 0.15 if mid else (0.3 if heavy else 0.08)
        return {"kind": "stun", "chance": ch, "turns": 1}
    if art_id in ("art_glacial_wolf_claw", "art_bone_frost_needle", "art_ocean_mist_palm", "art_autumn_gale_blade"):
        ch = 0.45 if mid else (0.6 if heavy else 0.25)
        return {"kind": "slow", "chance": ch, "turns": 2, "speed_pct": -0.25}
    if art_id in ("art_mountain_axe", "art_roaring_quake_mace", "art_sandstorm_blade"):
        ch = 0.4 if mid else (0.55 if heavy else 0.2)
        return {"kind": "armor_break", "chance": ch, "turns": 2, "def_pct": -0.25}
    if art_id == "art_soul_chime_flute":
        ch = 0.2 if mid else (0.32 if heavy else 0.1)
        return {"kind": "confuse", "chance": ch, "turns": 1}
    if art_id == "art_shadow_sword" and mid:
        return {"kind": "blind", "chance": 0.5, "turns": 1, "target": "self"} if False else \
               {"kind": "evade", "chance": 0.6, "turns": 1, "target": "self"}
    if art_id == "art_ghost_step" and (mid or heavy):
        return {"kind": "evade", "chance": 0.45 if mid else 0.75, "turns": 1, "target": "self"}
    if art_id == "art_taiji_yin_yang" and idx == 0:
        return {"kind": "guard", "chance": 0.9, "turns": 1, "mitigation": 0.5, "target": "self"}
    if art_id == "art_taiji_yin_yang" and heavy:
        return {"kind": "regen", "chance": 1.0, "turns": 2, "heal_pct": 0.06, "target": "self"}
    if art_id == "art_iron_rhino_fist" and mid:
        return {"kind": "fortify", "chance": 0.85, "turns": 2, "def_pct": 0.5, "target": "self"}
    if art_id == "art_spring_breeze_fan" and mid:
        return {"kind": "regen", "chance": 0.9, "turns": 2, "heal_pct": 0.05, "target": "self"}
    if art_id == "art_drunken_blood_blade" and heavy:
        return {"kind": "bleed", "chance": 0.6, "turns": 3, "dot_pct": 0.04}
    return None


LIFESTEAL = {  # art_id → fraction of damage recovered as HP (per tier idx)
    "art_drunken_blood_blade": (0.15, 0.2, 0.35),
    "art_blood_lotus_saber": (0.12, 0.2, 0.3),
    "art_phantom_bat_claw": (0.15, 0.22, 0.4),
}

ELEMENT_EN = {  # element display tweaks if translation missing
}

EN_TEMPLATES = {
    "normal": "{verb1} — {tech} {hit} {target} for {damage} damage.",
    "crit":   "Critical! {tech} detonates a vital point; {verb2} — {damage} damage tears through {target}!",
    "grazed": "{target} slips the edge of {tech}; {hit_past} — only {damage} damage gets in.",
}
VERB1 = {
    "sword": ("A cold arc of light", "Moonlit steel flashes", "The blade sings"),
    "blade": ("A dry wind curls the cut", "Leaves scatter along the edge", "The flat hums"),
    "fist": ("Knuckles crack like a whip-crack", "The ground cracks under your step", "Heat blooms off the guard"),
    "palm": ("A luminous palm opens", "Qi pours like molten glass", "The palm presses softly —"),
    "claw": ("Fur bristles into steel", "A low growl rises", "Cold talons arc"),
    "whip": ("The tip whips flat", "Willow limbs blur", "The lash cracks"),
    "axe": ("Shoulders coil, axe falls", "Chips fly from the haft", "The earth shudders"),
    "mace": ("Iron greets stone", "The air thumps", "A boulder swings"),
    "spear": ("The haft hums mid-thrust", "A point finds the seam", "Water beads off the shaft"),
    "bow": ("The string thrums once", "Feathers sing downwind", "Three breaths, one draw"),
    "needle": ("Fingertips flick", "Crystal points catch light", "A rain of slivers"),
    "finger": ("One finger becomes a falling star", "The air scorches at the tip", "A mote of fire walks"),
    "shadow": ("The figure is not where you struck", "Darkness thickens", "A blade arrives from the wrong side"),
    "blood": ("The cup is drained to the dregs", "Your veins keep the rhythm", "Scarlet steam rises"),
    "taiji": ("The circle closes", "Soft overcomes hard", "Two fishes wheel"),
    "sonic": ("A reed-thin note bends the air", "The melody sharpens", "Harmonics crawl under the skin"),
    "fan": ("Petals ride the breeze", "A gentle gust — too gentle", "Scent before steel"),
}
VERB2 = {
    "sword": ("the moon itself seems to fall", "silver drowns the field"), "blade": ("wither follows the edge", "the cut rots the light"),
    "fist": ("thunder walks the marrow", "red lightning arcs"), "palm": ("flame floods the meridians", "a lotus blooms in the chest"),
    "claw": ("frost splits the hide", "the pack descends"), "whip": ("stripes open in sequence", "the willow lashes"),
    "axe": ("timber and bone part", "the mountainside gives"), "mace": ("a fault line answers", "the ears go quiet"),
    "spear": ("the abyss answers", "tide-crush through the guard"), "bow": ("the storm lands at once", "no arrow is seen twice"),
    "needle": ("ice seeds the wounds", "a thousand points ring"), "finger": ("a star touches down", "the air is a smoking line"),
    "shadow": ("the nightmare stays", "the cloak folds over the soul"), "blood": ("the cup overflows red", "the drunk blade remembers"),
    "taiji": ("yin and yang snap shut", "the wheel grinds"), "sonic": ("the soul misses a step", "silence is louder"),
    "fan": ("the garden closes", "spring burns cold"),
}


def _grazed_verb(hit: str) -> str:
    return {"slices": "it grazes", "hammers": "it skids off", "cleaves": "it skips", "rakes": "it only scores",
            "pierces": "it clatters", "lashes": "it snaps wide"}.get(hit, "it glances")


def build_martial_arts() -> None:
    raw = json.loads((SRC / "datamartial_arts.json").read_text(encoding="utf-8"))
    assert len(raw) == 30
    out = []
    for art in raw:
        art_id = art["art_id"]
        req, price, en_name, en_element, fam = ARTS[art_id]
        rng = random.Random(_h(art_id))
        techs = []
        for idx, t in enumerate(art["techniques"]):
            base = TIER_MULT[idx % 3][0] if idx < 3 else 2.5
            mult = round(base + rng.choice((0.0, 0.05, 0.1)), 2)
            scaling = FAMILY[fam][0]
            if idx == 2 and rng.random() < 0.3:
                mult = round(mult + 0.1, 2)
            st = _status_for(art_id, idx)
            tech = {
                "id": t["id"],
                "name": t["name"],
                "name_en": TECH_EN[t["id"]],
                "qi_cost": t["qi_cost"],
                "cooldown": t["cooldown"],
                "base_damage_multiplier": mult,
                "scaling_stat": scaling,
                "status_inflict": st,
                "descriptions": t["descriptions"],
            }
            if art_id in LIFESTEAL:
                tech["lifesteal_pct"] = LIFESTEAL[art_id][idx]
            # EN flavor — same {target}/{damage} placeholders, 3 tiers
            hit = FAMILY[fam][1]
            v1 = rng.choice(VERB1[fam])
            v2 = rng.choice(VERB2[fam])
            tech["descriptions_en"] = {
                "normal": f"{v1} — {tech['name_en']} {hit} {{target}} for {{damage}} damage.",
                "crit": f"Critical! {tech['name_en']} detonates a vital point; {v2} — {{damage}} damage tears through {{target}}!",
                "grazed": f"{{target}} slips the edge of {tech['name_en']}; {_grazed_verb(hit)} — only {{damage}} damage gets in.",
            }
            techs.append(tech)
        out.append({
            "art_id": art_id,
            "name": art["name"],
            "name_en": en_name,
            "element": art["element"],
            "element_en": en_element,
            "family": fam,
            "required_realm": req,
            "price_stones": price,
            "techniques": techs,
        })
    (OUT / "martial_arts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(a["techniques"]) for a in out)
    print(f"martial_arts.json: {len(out)} arts / {n} techniques")


# ─────────────────────────────────────────────────────────────────────────────
# 3) EQUIPMENT  (schema of Part-1 §3.3, extended with name_en, capacity, slot…)
# ─────────────────────────────────────────────────────────────────────────────
def e(item_id, name, name_en, slot, tier, **kw):
    d = {"item_id": item_id, "name": name, "name_en": name_en, "slot": slot, "tier": tier}
    d.update({k: v for k, v in kw.items()})
    return d


EQUIPMENT = [
    # weapons
    e("wpn_rusty_spirit_dagger", "خنجر کهنه معنوی", "Rusted Spirit Dagger", "weapon", "mortal",
      spiritual_atk_bonus=6, sell_price=25, price_stones=90),
    e("wpn_iron_leaf", "شمشیر برگ آهن", "Iron-Leaf Sword", "weapon", "mortal",
      spiritual_atk_bonus=12, sell_price=30, price_stones=110),
    e("wpn_green_tide", "تیغ جزر سبز", "Green Tide Blade", "weapon", "earth",
      spiritual_atk_bonus=24, physical_atk_bonus=24, crit_rate_bonus=4.0, sell_price=70, price_stones=150),
    e("wpn_verdant_rain", "شمشیر باران سبز", "Verdant Rain Sword", "weapon", "heaven",
      spiritual_atk_bonus=45, crit_rate_bonus=8.0, special_effect="leech_qi_percent_3",
      sell_price=380, price_stones=900),
    e("wpn_thunder_awl", "مثقاله رعد", "Thunder Awl Spear", "weapon", "spirit",
      spiritual_atk_bonus=70, physical_atk_bonus=70, crit_rate_bonus=6.0, special_effect="stun_10",
      sell_price=900, price_stones=2200),
    e("wpn_dawnbreaker", "پنحه سپیده‌دم", "Dawnbreaker Greatsword", "weapon", "saint",
      spiritual_atk_bonus=120, physical_atk_bonus=120, crit_rate_bonus=10.0, special_effect="burn_15",
      sell_price=1800, price_stones=4800),
    # robes
    e("robe_hemp", "ردای کتان ساده", "Plain Hemp Robe", "robe", "mortal", physical_def_bonus=8, sell_price=20, price_stones=60),
    e("robe_jade_wind", "ردای یشم بادبان", "Jade-Sail Robe", "robe", "earth",
      physical_def_bonus=30, spiritual_def_bonus=40, tribulation_mitigation_percent=15, sell_price=55, price_stones=120),
    e("robe_cloud_step", "ردای گام ابر", "Cloudstep Robe", "robe", "heaven",
      physical_def_bonus=60, spiritual_def_bonus=75, speed_bonus=6, tribulation_mitigation_percent=10, sell_price=320, price_stones=800),
    e("robe_asura_ash", "ردای خاکستر اَصورا", "Asura Ash Robe", "robe", "spirit",
      physical_def_bonus=110, spiritual_def_bonus=130, hp_bonus=400, tribulation_mitigation_percent=20, sell_price=1000, price_stones=2600),
    # rings (spatial capacity)
    e("ring_canvas", "انگشتر فضای ساده", "Canvas Spatial Ring", "ring", "mortal", capacity_bonus=16, sell_price=25, price_stones=90),
    e("ring_bronze_gourd", "انگشتر کدوی برنزی", "Bronze Gourd Ring", "ring", "earth", capacity_bonus=30, sell_price=80, price_stones=240),
    e("ring_mind_jade", "انگشتر یشم گسترش ذهن", "Mind-Expansion Jade Ring", "ring", "spirit",
      capacity_bonus=60, special_effect="slot_bonus_1", sell_price=900, price_stones=2400),
    e("ring_mountains", "انگشتر کوهستان آویزان", "Hanging-Mountain Ring", "ring", "heaven", capacity_bonus=48, sell_price=420, price_stones=1100),
    # accessories
    e("acc_karma_cleansing", "تعویذه پاکسازی کارما", "Karma-Cleansing Pendant", "accessory", "spirit",
      spiritual_def_bonus=40, special_effect="reveal_luck", sell_price=450, price_stones=1000),
    e("acc_thunder_pearl", "مروارید رعد", "Thunder Pearl", "accessory", "heaven", crit_rate_bonus=6.0, speed_bonus=5, sell_price=300, price_stones=750),
    # companions
    e("cmp_spirit_crane", "کرانه روحی اهلی", "Tamed Spirit Crane", "companion", "earth",
      speed_bonus=8, hp_bonus=80, sell_price=120, price_stones=380),
    e("cmp_sun_orb", "گوی خورشید خواب", "Sun-Orb Spirit", "companion", "saint",
      spiritual_atk_bonus=60, hp_bonus=600, special_effect="regen_3", sell_price=1200, price_stones=2500),
    # natal artifacts
    e("nat_ancient_bell", "زنگ کهن جان‌بسته", "Ancient Soul-Bound Bell", "natal", "divine",
      spiritual_atk_bonus=90, spiritual_def_bonus=120, crit_rate_bonus=5.0, tribulation_mitigation_percent=25,
      special_effect="stabilize_5", price_stones=9000, sell_price=3000),
]


def build_equipment() -> None:
    for it in EQUIPMENT:
        it.setdefault("name_fa", it["name"])
    (OUT / "equipment.json").write_text(json.dumps(EQUIPMENT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"equipment.json: {len(EQUIPMENT)} items")


# ─────────────────────────────────────────────────────────────────────────────
# 4) CONSUMABLES
# ─────────────────────────────────────────────────────────────────────────────
def c(item_id, name, name_en, category, action, **kw):
    d = {"item_id": item_id, "name": name, "name_en": name_en, "category": category, "action": action}
    d.update(kw)
    return d


CONSUMABLES = [
    # pills
    c("pill_guardian", "قرص حافظ", "Guardian Pill", "pill", "breakthrough_bonus", value=20, price_stones=40, sell_price=15),
    c("pill_foundation", "قرص پی‌ریزی", "Foundation Pill", "pill", "breakthrough_bonus", value=20, price_stones=40, sell_price=15),
    c("pill_spirit", "قرص روح", "Spirit Pill", "pill", "breakthrough_bonus", value=30, price_stones=120, sell_price=45),
    c("pill_spirit_gather_low", "قرص جمع‌آوری روح", "Low-Grade Spirit-Gathering Pill", "pill", "restore_qi", value=180, price_stones=30, sell_price=10),
    c("pill_vitality_refine", "قرص ترمیم مریدین", "Meridian Restoration Pill", "pill", "heal_and_cure", value=150, price_stones=220, sell_price=80),
    c("pill_marrow_wash", "قرص شست‌وشوی مغز استخوان", "Marrow-Wash Pill", "pill", "heal_hp", value=420, price_stones=600, sell_price=200),
    c("pill_ten_thousand_swords", "قرص ده هزار شمشیر", "Ten-Thousand-Swords Pill", "pill", "combat_damage_buff", value=30, price_stones=500, sell_price=180),
    # herbs
    c("root_ancient", "ریشه جینسنگ کهن", "Ancient Ginseng Root", "herb", "permanent_qi_rate", value=25, price_stones=200, sell_price=0),
    c("lotus_seven", "نیلوفر هفت‌رنگ", "Seven-Coloured Lotus", "herb", "core_stability", value=20, price_stones=260, sell_price=90),
    c("herb_wild_ginseng", "جینسنگ وحشی", "Wild Ginseng", "herb", "restore_qi", value=90, sell_price=12),
    c("herb_mountain_mushroom", "قارچ کوهی", "Mountain Mushroom", "herb", "heal_hp", value=40, sell_price=4),
    # materials
    c("mat_beast_core_broken", "هسته نیمه‌جانور", "Cracked Beast Core", "material", "sell_only", value=0, sell_price=30),
    c("mat_wolf_fang", "نیشگرگ یخی", "Glacial Wolf Fang", "material", "sell_only", value=0, sell_price=25),
    c("mat_serpent_scale", "فلس افعی اسیدی", "Acid Viper Scale", "material", "sell_only", value=0, sell_price=40),
    c("mat_thunder_wood", "چوب صاعقه‌دیده", "Storm-Struck Thunderwood", "material", "sell_only", value=0, sell_price=90),
    # disposable talismans (usable mid-combat)
    c("talisman_gale_blade", "طلسم تیغ باد", "Gale-Blade Talisman", "disposable_weapon", "instant_damage",
      fixed_damage=38, bypass_defense_percent=20, price_stones=15, sell_price=5),
    c("talisman_crimson_thunder", "طلسم رعد سرخ", "Crimson Thunder Talisman", "disposable_weapon", "instant_damage",
      fixed_damage=75, stun_chance_percent=30, bypass_defense_percent=50, burn_on_use=True, price_stones=45, sell_price=18),
    c("talisman_iron_wall", "طلسم دیوار آهنین", "Iron-Wall Talisman", "disposable_weapon", "shield",
      shield_value=70, price_stones=25, sell_price=9),
    c("talisman_seal_flame", "طلسم مهار شعله", "Flame-Seal Talisman", "disposable_weapon", "enemy_debuff",
      atk_cut_percent=25, turns=3, price_stones=60, sell_price=22),
    # revives
    c("doll_substitute", "عروسک جایگزین مرگ", "Substitute Death Doll", "relic", "auto_revive",
      price_stones=10000, sell_price=3500),
    c("elixir_soul_anchor", "اکسیر لنگر جان", "Soul-Anchor Elixir", "relic", "cure_paralysis",
      price_stones=900, sell_price=300),
]


def build_consumables() -> None:
    for it in CONSUMABLES:
        it.setdefault("price_stones", None)
        it["name_fa"] = it.pop("name")
        it["name"] = it["name_fa"]
    (OUT / "consumables.json").write_text(json.dumps(CONSUMABLES, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"consumables.json: {len(CONSUMABLES)} items")


# ─────────────────────────────────────────────────────────────────────────────
# 5) ENEMIES — sector guardians, spirit beasts, loot tables
# ─────────────────────────────────────────────────────────────────────────────
def m(key, name_fa, name_en, guard, hp, atk, dfn, sdef, spd, loot, special=None):
    d = {"enemy_id": key, "name": name_fa, "name_en": name_en, "guard_level": guard,
         "hp": hp, "atk": atk, "def": dfn, "sdef": sdef, "speed": spd, "loot": loot}
    if special:
        d["special"] = special
    return d


ENEMIES = [
    m("wild_boar", "گورچ جنگل", "Bristled Forest Boar", 1, 60, 9, 6, 4, 9,
      {"stones": (1, 2), "qi": (20, 45), "drop": {"mat_wolf_fang": 0.08}}),
    m("hungry_wolf", "گرگ گرسنه", "Famished Grey Wolf", 1, 75, 11, 5, 4, 12,
      {"stones": (1, 2), "qi": (22, 50), "drop": {"mat_wolf_fang": 0.15, "talisman_gale_blade": 0.05}}),
    m("rock_serpent", "مار صخره‌ای", "Rock-Back Serpent", 2, 150, 19, 14, 10, 13,
      {"stones": (2, 4), "qi": (50, 110), "drop": {"mat_serpent_scale": 0.18}}),
    m("mist_panther", "پلنگ مه", "Mist-Stalking Panther", 2, 130, 24, 9, 12, 19,
      {"stones": (2, 5), "qi": (55, 120), "drop": {"talisman_gale_blade": 0.12, "herb_wild_ginseng": 0.1}}),
    m("blood_croc", "تمساح خون", "Blood Marsh Crocodile", 3, 320, 40, 26, 18, 12,
      {"stones": (3, 8), "qi": (130, 260), "drop": {"mat_serpent_scale": 0.25, "talisman_crimson_thunder": 0.12}}),
    m("marsh_wraith", "ارواح باتلاق", "Marsh Wraith", 3, 240, 52, 12, 40, 22,
      {"stones": (4, 9), "qi": (150, 300), "drop": {"talisman_seal_flame": 0.2}}),
    m("spring_serpent_king", "شاه‌مار چشمه بهشتی", "Spring Serpent King", 4, 620, 78, 44, 40, 20,
      {"stones": (8, 16), "qi": (350, 700), "drop": {"talisman_crimson_thunder": 0.3, "mat_thunder_wood": 0.25}}),
    m("jade_guardian", "نگهبان یشم", "Jade Statue Guardian", 4, 880, 66, 70, 46, 11,
      {"stones": (9, 18), "qi": (380, 720), "drop": {"pill_marrow_wash": 0.15}}),
    m("thunder_roc", "رعد طائر صاعقه", "Thunderclap Roc", 7, 2600, 260, 130, 120, 44,
      {"stones": (30, 60), "qi": (2500, 4500), "drop": {"mat_thunder_wood": 0.5, "acc_thunder_pearl": 0.06}}),
    m("storm_qilin", "قیلین طوفان", "Storm-Qilin Sovereign", 7, 4200, 310, 180, 160, 38,
      {"stones": (45, 90), "qi": (3200, 6000), "drop": {"wpn_thunder_awl": 0.1, "nat_ancient_bell": 0.02}}),
]

GUARDIANS = [
    m("guardian_1", "سپاهدار دهانه دره", "Gorge Wardensprite", 1, 150, 16, 10, 8, 12,
      {"stones": (2, 5), "qi": (40, 90), "drop": {}}),
    m("guardian_2", "سپاهدار بامبو", "Bamboo Grove Sentinel", 2, 420, 38, 26, 20, 16,
      {"stones": (5, 11), "qi": (150, 320), "drop": {"talisman_gale_blade": 0.25}}),
    m("guardian_3", "نگهبان باتلاق خون", "Blood Marsh Watcher", 3, 980, 74, 52, 40, 20,
      {"stones": (10, 22), "qi": (420, 820), "drop": {"talisman_crimson_thunder": 0.3}}),
    m("guardian_4", "نگهبان چشمه بهشت", "Heaven-Spring Warden", 4, 2200, 140, 96, 84, 24,
      {"stones": (18, 40), "qi": (1000, 2200), "drop": {"pill_marrow_wash": 0.3}}),
    m("guardian_5", "فرمانده قله مه", "Misty-Peak Commander", 5, 4600, 250, 160, 140, 28,
      {"stones": (30, 60), "qi": (2600, 5200), "drop": {"robe_cloud_step": 0.12}}),
    m("guardian_6", "کیشور رگه آسمانی", "Sky-Vein Kshantaka", 6, 9800, 430, 280, 250, 33,
      {"stones": (48, 95), "qi": (6000, 12000), "drop": {"ring_mind_jade": 0.08}}),
    m("guardian_7", "فرشته داوری فلات رعد", "Plateau Judgement Herald", 7, 21000, 760, 480, 430, 40,
      {"stones": (80, 150), "qi": (14000, 28000), "drop": {"wpn_dawnbreaker": 0.1, "elixir_soul_anchor": 0.25}}),
]

RIVAL = {  # roaming cultivators — the mercy/plunder duel
    "enemy_id": "rival_cultivator", "name": "مرید رقیب سرگردان", "name_en": "Wandering Rival Disciple",
    "guard_level": 0, "hp": 220, "atk": 30, "def": 18, "sdef": 16, "speed": 15,
    "loot": {"stones": (0, 0), "qi": (0, 0), "drop": {}},
}


def build_enemies() -> None:
    data = {"beasts": ENEMIES, "guardians": GUARDIANS, "rival": RIVAL}
    (OUT / "enemies.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"enemies.json: {len(ENEMIES)} beasts, {len(GUARDIANS)} guardians, rival duel")


if __name__ == "__main__":
    build_offline_events()
    build_martial_arts()
    build_equipment()
    build_consumables()
    build_enemies()
    print("done →", OUT)

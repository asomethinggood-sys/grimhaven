"""UI-overhaul regressions: FSM guards, bootstrap contract, data consistency,
locale coverage and an early-game balance probe."""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

import pytest

from grimhaven.core import combat_engine as ce
from grimhaven.core.data_loader import DATA_DIR, bootstrap
from grimhaven.core.middleware import callback_blocked
from grimhaven.engine import items
from grimhaven.engine.models import ensure_v2, new_user_doc, utcnow

ROOT = Path(__file__).resolve().parent.parent
LOCALES = {lang: json.loads((ROOT / "locales" / f"locale_{lang}.json").read_text(encoding="utf-8"))
           for lang in ("fa", "en")}


@pytest.fixture(scope="module", autouse=True)
def _registry():
    bootstrap()


def doc(**status):
    u = ensure_v2(new_user_doc(42, "hero", "en"))
    u.update(status)
    return u


# ── bootstrap contract ───────────────────────────────────────────────────────

def test_new_user_bootstrap_stats():
    u = new_user_doc(1, "newbie", "fa")
    hidden = u["stats"]["hidden"]
    assert hidden["karmic_luck"] == 50
    assert hidden["dao_affinity_charisma"] == 30
    assert hidden["dao_heart_stability"] == 70
    assert hidden["demonic_corruption"] == 0
    assert 1 <= len(u["combat"]["loadout"]) <= 6  # starter arts auto-fit the deck
    assert u["inventory"]["spirit_stones"] == {"low": 30, "mid": 0, "high": 0, "heavenly": 0}
    assert not u["combat"]["session"]


def test_ring_capacity_is_bounded():
    from grimhaven.engine.models import add_item, ring_has_room
    u = doc()
    cids = [cid for cid, c in bootstrap().consumables.items() if c.category != "relic"]
    added = 0
    for cid in cids:
        if add_item(u, cid):
            added += 1
    assert added == 12  # base ring capacity
    leftover = [cid for cid in cids if cid not in u["inventory"]["items"]]
    assert leftover and not ring_has_room(u, leftover[0])
    assert ring_has_room(u, cids[0])  # already-owned stacks always have room


# ── FSM guard matrix (Part 3 §2) ─────────────────────────────────────────────

def test_guard_blocks_matrix():
    m = doc(status="meditating")
    m["cultivation"]["meditating"] = True
    m["cultivation"]["meditation_started_at"] = utcnow().isoformat()
    assert callback_blocked(m, "travel:zone_misty_peak") == "GUARD_MEDITATING"
    assert callback_blocked(m, "hunt:zone_mortal_valley") == "GUARD_MEDITATING"
    assert callback_blocked(m, "stop_meditate") is None
    assert callback_blocked(m, "menu") is None
    assert callback_blocked(m, "bag:use:pill_spirit") is None  # pill-mid-trance is allowed
    assert callback_blocked(m, "bag:equip:wpn_iron_leaf") == "GUARD_MEDITATING"

    combat = doc(status="in_combat")
    combat["combat"]["session"] = {"round": 1, "battle_id": "x", "updated_at": utcnow().isoformat(),
                                   "finished": False, "statuses": {"p": {}, "e": {}},
                                   "cooldowns": {"p": {}, "e": {}}, "shield": 0,
                                   "enemy": {"hp": 1, "max_hp": 2, "atk": 1, "def": 1, "sdef": 1, "speed": 1},
                                   "player": {"hp": 1, "max_hp": 2, "qi": 0, "max_qi": 1}}
    assert callback_blocked(combat, "menu") == "GUARD_COMBAT"
    assert callback_blocked(combat, "combat:basic") is None
    assert callback_blocked(combat, "combat:item:pill_spirit") is None

    injured = doc(status="heavily_injured")
    injured["combat"]["injury"] = {"debuff_id": "severe_meridian_injury",
                                   "expires_at": (utcnow() + dt.timedelta(hours=2)).isoformat()}
    assert callback_blocked(injured, "breakthrough_do") == "GUARD_INJURED"
    assert callback_blocked(injured, "hunt:zone_blood_marsh") is None
    assert callback_blocked(injured, "bag") is None

    para = doc(status="heavily_injured")
    para["combat"]["paralysis_until"] = (utcnow() + dt.timedelta(minutes=30)).isoformat()
    para["combat"]["injury"] = {"debuff_id": "severe_meridian_injury",
                                "expires_at": (utcnow() + dt.timedelta(hours=2)).isoformat()}
    assert callback_blocked(para, "meditate") == "GUARD_PARALYSIS"
    assert callback_blocked(para, "bag:tab:consumables") is None


def test_seclusion_blocks_claim_actions():
    s = doc(status="seclusion_tribulation")
    s["cultivation"]["seclusion_finish_time"] = (utcnow() + dt.timedelta(minutes=20)).isoformat()
    assert callback_blocked(s, "hunt:zone_blood_marsh") == "GUARD_SECLUSION"
    assert callback_blocked(s, "bag:tab:gear") is None  # read-only viewing stays open
    assert callback_blocked(s, "bag:equip:x") == "GUARD_SECLUSION"
    assert callback_blocked(s, "check_tribulation") is None
    assert callback_blocked(s, "breakthrough") is None
    assert callback_blocked(s, "stop_meditate") is None


# ── data registry consistency ────────────────────────────────────────────────

def test_moon_slash_matches_martial_arts_json():
    raw = json.loads((DATA_DIR / "martial_arts.json").read_text(encoding="utf-8"))
    arts = raw if isinstance(raw, list) else raw.get("arts", raw.get("martial_arts", []))
    moon = next(a for a in arts if a["art_id"] == "art_moonlight_sword")
    tech = next(t_ for t_ in moon["techniques"] if t_["id"] == "moon_slash")
    reg = bootstrap().get_technique("moon_slash")
    assert reg.id == tech["id"]
    assert reg.name_en == tech["name_en"]
    assert reg.descriptions_en.normal == tech["descriptions_en"]["normal"]
    # English descriptions must be real prose, not a fallback of the Persian text
    assert re.search(r"[a-z]{4,}", reg.descriptions_en.normal)


# ── locale coverage: every t(lang, "KEY") in the codebase must exist ────────

SOURCE_DIRS = (ROOT / "grimhaven").rglob("*.py")
KEY_RE = re.compile(r"""t\(\s*[\w.\[\]"' ]+\s*,\s*"([A-Z][A-Z0-9_]{2,})" """)


def test_locale_tables_complete_and_symmetric():
    fa, en = LOCALES["fa"], LOCALES["en"]
    assert set(fa) == set(en), f"asymmetric: fa-only={set(fa)-set(en)} en-only={set(en)-set(fa)}"
    used: set[str] = set()
    for path in SOURCE_DIRS:
        src = path.read_text(encoding="utf-8")
        used |= set(KEY_RE.findall(src))
        used |= set(re.findall(r'"(BTN_[A-Z_]+|GUARD_[A-Z_]+|STATUS_[A-Z_]+|COMBAT_[A-Z_]+|USE_[A-Z_]+|ADVENTURE_[A-Z_]+)"', src))
    missing = {k for k in used if k not in fa}
    assert not missing, f"locale keys referenced but missing: {sorted(missing)}"
    # dynamic engine statuses rendered through t(lang, status) must exist too
    for status in ("FULL_HP", "QI_FULL", "WRONG_REALM", "ALREADY_USED", "NOT_INJURED",
                   "NOT_USABLE", "UNKNOWN_ITEM", "NOT_OWNED"):
        assert status in LOCALES["fa"]


def test_locale_format_placeholders_match():
    for key, fa_tpl in LOCALES["fa"].items():
        en_tpl = LOCALES["en"][key]
        f = set(re.findall(r"\{(\w+)\}", fa_tpl))
        e = set(re.findall(r"\{(\w+)\}", en_tpl))
        assert f == e, f"{key}: fa{sorted(f)} != en{sorted(e)}"


# ── balance probe (spec: realm-1 starter vs a wild boar) ─────────────────────

def test_early_game_balance_sane():
    wins = 0
    n = 60
    for i in range(n):
        u = ensure_v2(new_user_doc(100 + i, f"p{i}", "en"))
        enemy = ce.make_beast("wild_boar", random.Random(i))
        ce.start_session(u, enemy, "hunt")
        outcome, _summary, _snap = ce.simulate(u, rng=random.Random(1000 + i))
        wins += outcome == ce.OUT_VICTORY
    rate = wins / n
    assert 0.35 <= rate <= 0.95, f"starter vs wild boar win-rate {rate:.0%} out of band"


# ── victory commits rewards and clears the session ──────────────────────────

def test_victory_persists_loot_and_closes_session():
    u = ensure_v2(new_user_doc(7, "rich", "en"))
    u["stats"]["visible"]["physique_hp"] = 9999
    u["stats"]["visible"]["max_hp"] = 9999
    enemy = ce.make_beast("rock_serpent", random.Random(3))
    enemy["hp"] = enemy["max_hp"] = 1  # guaranteed win
    ce.start_session(u, enemy, "hunt")
    outcome, _summary, snap = ce.simulate(u, rng=random.Random(4))
    assert outcome == ce.OUT_VICTORY
    assert u["combat"]["session"] is None
    assert u["combat"]["wins"] == 1
    total_stones = sum(u["inventory"]["spirit_stones"].values())
    assert total_stones >= 11  # 10 base + loot
    assert u["status"] != "in_combat"

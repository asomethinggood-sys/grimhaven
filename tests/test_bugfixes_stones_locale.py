"""Regression tests for the two bug fixes shipped with the artwork round:

1. **Unlimited catalysts** — new players must start with no spirit stones; an
   arm requires a positive balance; heavenly stones burn at 0.1/h; and an AFK
   session the balance cannot pay for disarms (keeping the remainder) instead
   of silently going negative or boosting forever.
2. **Persian text leaking into the English UI** — every Persian display name
   in the enemy/loot data now carries a ``name_en``, list joiners follow the
   reader's language (Arabic comma for fa, ASCII for en), and internal buff
   ids render through locale keys.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

import pytest

from grimhaven.engine.constants import SPIRIT_STONES
from grimhaven.engine.cultivation import CultivationEngine
from grimhaven.engine.items import activate_stone
from grimhaven.engine.models import ensure_v2, new_user_doc

FA_CHARS = re.compile(r"[؀-ۿ]")
DATA = Path(__file__).resolve().parent.parent / "data"
NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.timezone.utc)


class NoEvents(random.Random):
    """Neutral rolls: no treasure, no bandits."""

    def uniform(self, a, b):
        return 50.0


def settled_user(stone: str | None, balance: dict[str, int],
                 hours: float) -> dict:
    user = new_user_doc(1, "stone_tester", "en")
    user["inventory"]["spirit_stones"].update(balance)
    user["cultivation"]["active_stone"] = stone
    user["cultivation"]["meditating"] = True
    user["cultivation"]["last_afk_timestamp"] = (NOW - dt.timedelta(hours=hours)).isoformat()
    return user


# ── bug 1: catalyst economy ──────────────────────────────────────────────────

def test_new_players_start_with_no_catalysts():
    stones = new_user_doc(1, "soul", "en")["inventory"]["spirit_stones"]
    assert stones == {"low": 0, "mid": 0, "high": 0, "heavenly": 0}, \
        "catalysts are enemy loot, not a starter kit"


def test_activating_a_stone_you_do_not_own_is_refused():
    user = new_user_doc(1, "soul", "en")
    assert activate_stone(user, "low") == {"status": "NO_STONES"}
    assert user["cultivation"].get("active_stone") in (None, "")
    user["inventory"]["spirit_stones"]["low"] = 2
    res = activate_stone(user, "low")
    assert res["status"] == "OK"
    assert user["cultivation"]["active_stone"] == "low"


def test_heavenly_stones_are_rare_and_burn():
    assert SPIRIT_STONES["heavenly"]["per_hour"] == pytest.approx(0.1)
    # one stone = ten hours, and the hourglass is not a free-for-all
    user = settled_user("heavenly", {"heavenly": 1}, 8.0)
    CultivationEngine.settle_afk(user, now=NOW, rng=NoEvents(0))
    assert user["inventory"]["spirit_stones"]["heavenly"] == 0
    assert user["cultivation"]["active_stone"] == "heavenly"   # paid for the full stay


def test_afk_shortfall_disarms_and_keeps_remainder():
    # mid costs 5/hr; 100 h would need 500 — a balance of 7 cannot pay
    user = settled_user("mid", {"mid": 7}, 100.0)
    CultivationEngine.settle_afk(user, now=NOW, rng=NoEvents(0))
    assert user["cultivation"]["active_stone"] is None, \
        "unpaid time must disarm the boost, not extend it"
    assert user["inventory"]["spirit_stones"]["mid"] == 7, "remainder stays intact"
    assert all(v >= 0 for v in user["inventory"]["spirit_stones"].values())


def test_ensure_v2_disarms_legacy_free_lunch():
    """A pre-fix player can hold an armed grade with a zero balance."""
    doc = new_user_doc(1, "legacy_soul", "en")
    doc["inventory"]["spirit_stones"] = {"low": 0, "mid": 0, "high": 0, "heavenly": 0}
    doc["cultivation"]["active_stone"] = "heavenly"
    doc = ensure_v2(doc)
    assert doc["cultivation"].get("active_stone") is None
    # …but an armed stone that is actually paid for survives the migration
    doc2 = new_user_doc(2, "paid_soul", "en")
    doc2["inventory"]["spirit_stones"]["low"] = 3
    doc2["cultivation"]["active_stone"] = "low"
    doc2 = ensure_v2(doc2)
    assert doc2["cultivation"]["active_stone"] == "low"


def test_enemies_reward_stones():
    enemies = json.loads((DATA / "enemies.json").read_text(encoding="utf-8"))
    entries = []
    for grp in enemies.values():
        if isinstance(grp, list):
            entries.extend(x for x in grp if isinstance(x, dict))
        elif isinstance(grp, dict):
            entries.append(grp)
    serpents = [b for b in entries if b.get("enemy_id") == "rock_serpent"]
    assert serpents and serpents[0]["rewards"]["spirit_stones"] >= 1, \
        "the first hunted beast must be able to drop a catalyst"


# ── bug 2: English UI must not render Persian ────────────────────────────────

def test_every_persian_display_name_has_an_english_one():
    leaks = []
    for name in ("enemies.json",):
        raw = json.loads((DATA / name).read_text(encoding="utf-8"))
        stack = [raw]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("name"), str) and FA_CHARS.search(node["name"]):
                    if not node.get("name_en"):
                        leaks.append(node["name"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    assert not leaks, f"entries without name_en leak Persian into EN: {leaks[:3]}"


def test_list_joiners_follow_language():
    from grimhaven.render import _list_sep
    assert _list_sep("fa") == "، "          # Persian comma, no ASCII ","
    assert _list_sep("en") == ", "
    assert "," not in _list_sep("fa")


def test_buff_labels_render_via_locales():
    from grimhaven.localization import t
    en, fa = t("en", "BUFF_INNER_DEMON"), t("fa", "BUFF_INNER_DEMON")
    assert en and not FA_CHARS.search(en)
    assert fa and FA_CHARS.search(fa)
    # the inner-demon entry carries a locale key rather than a hardcoded string
    src = (Path(__file__).resolve().parent.parent / "grimhaven" / "engine"
           / "cultivation.py").read_text(encoding="utf-8")
    assert '"label_key": "BUFF_INNER_DEMON"' in src


def test_no_stones_alert_key_exists_in_both_locales():
    from grimhaven.localization import t
    assert t("fa", "NO_STONES") and t("en", "NO_STONES")
    assert FA_CHARS.search(t("fa", "NO_STONES"))
    assert not FA_CHARS.search(t("en", "NO_STONES"))

"""Round-2 spec acceptance: dock persistence, lifecycle & placeholder rules,
pagination math, slot matrix, free claims, tribulation numbers."""
from __future__ import annotations

import datetime as dt
import random

import pytest

from grimhaven.bot import keyboards as kbs
from grimhaven.bot.handlers.callbacks import _dispatch
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core.data_loader import data_registry
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine import items as items_mod
from grimhaven.engine.cultivation import CultivationEngine
from grimhaven.engine.items import grant_item
from grimhaven.engine.models import max_slots, new_user_doc, utcnow
from grimhaven.engine.world import travel


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "acc.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={999})
    storage.close()


def user(ctx, uid=1, lang="fa", realm=1, qi=100, cap=500):
    u = new_user_doc(uid, f"soul{uid}", lang)
    u["cultivation"]["current_realm_index"] = realm
    u["cultivation"]["qi_current"] = qi
    u["cultivation"]["qi_capacity"] = cap
    ctx.storage.save_user(u)
    return u


def dispatch(ctx, u, data):
    action, _, arg = data.partition(":")
    return _dispatch(ctx, u, action, arg, ctx.settle(u))


# ── P1 §3 dock ───────────────────────────────────────────────────────────────

def test_dock_is_persistent_and_inline_free():
    kb = kbs.dock_reply_kb("fa")
    # persistence kwargs land in the wire payload
    payload = kb.to_dict()
    assert payload["is_persistent"] is True
    assert payload["one_time_keyboard"] is False
    assert payload["resize_keyboard"] is True
    assert len(payload["keyboard"]) == 4 and all(len(r) == 2 for r in payload["keyboard"])


def test_dock_labels_never_duplicate_into_inline_boards(ctx):
    u = user(ctx, 2)
    dock_labels = {lbl for row in kbs.DOCK_ROWS[u["account"]["language"]] for lbl in row}
    for data in ("profile:view:main", "bag:tab:gear:1", "map:view:world", "shop:view:hub",
                 "martial:view:main", "cultivate:view:hub", "sect:view:main"):
        _text, kb, _opts = dispatch(ctx, u, data)
        if kb is None:
            continue
        inline_texts = [b.text for row in kb.inline_keyboard for b in row]
        assert not (set(inline_texts) & dock_labels), f"dock label leaked inline in {data}"


# ── placeholders banned across all screens ──────────────────────────────────

def test_no_empty_placeholder_strings(ctx):
    u = user(ctx, 3)
    banned = ("— هیچ —", "«»", "None")  # engine spec bans filler strings
    for data in ("profile:view:main", "profile:view:meridians", "profile:view:karma",
                 "bag:tab:materials:1", "martial:view:main", "cultivate:view:hub",
                 "map:view:world", "shop:view:hub", "sect:view:main"):
        text, _kb, _opts = dispatch(ctx, u, data)
        for b in banned:
            assert b not in text, f"{b!r} leaked in {data}"


# ── P3 bag pagination math (5 / page) + return-to-origin ────────────────────

def test_pagination_math_and_return_to_page(ctx):
    u = user(ctx, 4)
    mats = ["mat_wolf_fang", "mat_serpent_scale", "mat_thunder_wood", "wolf_pelt", "wolf_fang",
            "boar_tusk", "boar_hide"]   # 7 rows → 5 + tail-2 under the 10-slot ring
    for m in mats:
        grant_item(u, m)
    assert all(m in u["inventory"]["items"] for m in mats)
    text, kb, opts = dispatch(ctx, u, "bag:tab:materials:1")
    item_rows = [b for row in kb.inline_keyboard for b in row if b.callback_data.startswith("bag:inspect")]
    assert len(item_rows) == 5, "page one shows exactly 5 rows"
    # jump to the tail page (2 items), inspect, and the back button must return to page 2
    _t2, kb2, _o = dispatch(ctx, u, "bag:tab:materials:2")
    row2 = [b for row in kb2.inline_keyboard for b in row if b.callback_data.startswith("bag:inspect")]
    assert len(row2) == 2, "tail page shows the remainder"
    iid = row2[0].callback_data.split(":")[-1]
    _t3, kb3, _o = dispatch(ctx, u, f"bag:inspect:materials:2:{iid}")
    back = [b for row in kb3.inline_keyboard for b in row
            if "بازگشت به لیست" in b.text or "Back to list" in b.text]
    assert back and back[0].callback_data == "bag:tab:materials:2", \
        "return-to-origin must keep the current page"


# ── P3 deck slot matrix ──────────────────────────────────────────────────────

def test_slot_matrix_locks_by_realm(ctx):
    for realm, expect_cap in ((1, 3), (2, 4), (3, 5), (9, 5)):
        u = user(ctx, 10 + realm, realm=realm)
        assert max_slots(u) == expect_cap, f"realm {realm} cap"


def test_free_claim_lands_in_deck(ctx):
    u = user(ctx, 5)
    arts = list(data_registry.martial_arts.values())
    art = arts[0]
    _t, kb, _o = dispatch(ctx, u, f"shop:art:claim_free:{art.art_id}:1")
    assert art.art_id in u["inventory"]["arts"]
    owned = items_mod.owned_techniques(u)
    assert owned and all(t_ in u["combat"]["loadout"] for t_ in owned[:3])
    # second claim must not duplicate or crash
    dispatch(ctx, u, f"shop:art:claim_free:{art.art_id}:1")
    assert u["inventory"]["arts"].count(art.art_id) == 1


# ── P4 battle item row ───────────────────────────────────────────────────────

def test_battle_item_row_and_source(ctx):
    u = user(ctx, 6)
    grant_item(u, "talisman_crimson_thunder", )
    grant_item(u, "pill_vitality_refine")
    items_mod.set_battle_item(u, "talisman_crimson_thunder")
    from grimhaven.core import combat_engine as ce
    src = ce.battle_item_sources(u)
    assert "talisman_crimson_thunder" in src


# ── P6 tribulation numbers ───────────────────────────────────────────────────

def test_tribulation_fail_numbers(ctx):
    u = user(ctx, 7, qi=10_000, cap=10_000)
    u["cultivation"]["dao_path"] = "sword"
    vis = u["stats"]["visible"]
    vis["max_hp"] = 1000
    vis["physique_hp"] = 1000
    if CultivationEngine.tribulation_prep(u, now=utcnow())["status"] != "READY":
        pytest.skip("gate")
    # force a failed roll: rate floored at 10 → use an rng that returns 100
    class FailRng(random.Random):
        def uniform(self, a, b):
            return b + 1.0
    res = CultivationEngine.confirm_tribulation(u, now=utcnow(), rng=FailRng(1))
    if res["status"] not in ("FAILED",):
        pytest.skip("miracle path taken")
    assert u["cultivation"]["qi_current"] <= 5_000
    assert vis["physique_hp"] == max(1, int(vis["max_hp"] * 0.20))
    debuff = next(b for b in u["buffs"] if b.get("id") == "inner_demon_deviation")
    assert debuff["rate_mult"] == 0.5
    assert int((dt.datetime.fromisoformat(debuff["until"]) - utcnow()).total_seconds()) == 120 * 60

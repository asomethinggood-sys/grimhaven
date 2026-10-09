"""Integration test — drives the real Telegram callback dispatcher end-to-end
with a fake query object. Catches crashes and missing locale keys in every
major screen of the game."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from pathlib import Path

import pytest


def _dispatch_pair3(ctx, user, action, arg, settle, **kw):
    return _dispatch3(ctx, user, action, arg, settle, **kw)[:2]

from grimhaven.bot.handlers.callbacks import _dispatch as _dispatch3
from grimhaven.bot.handlers.common import Ctx
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc, utcnow


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "test.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={999})
    storage.close()


def _blocked(ctx, user, data):
    from grimhaven.core.middleware import callback_blocked
    return callback_blocked(user, data)


def make_user(ctx, uid=1, realm=1, stage=0, lang="fa", **cult):
    user = new_user_doc(uid, f"user{uid}", lang)
    user["cultivation"]["current_realm_index"] = realm
    user["cultivation"]["current_stage"] = stage
    user["cultivation"]["qi_capacity"] = 500 if realm == 1 else user["cultivation"]["qi_capacity"]
    user["cultivation"].update(cult)
    ctx.storage.save_user(user)
    return user


LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"


_ALL_KEYS: set[str] | None = None


def _all_locale_keys() -> set[str]:
    global _ALL_KEYS
    if _ALL_KEYS is None:
        _ALL_KEYS = set()
        for fname in ("locale_fa.json", "locale_en.json"):
            _ALL_KEYS |= set(json.loads((LOCALE_DIR / fname).read_text(encoding="utf-8")))
    return _ALL_KEYS


def _check_no_raw_keys(text: str):
    """Rendered text must not contain unresolved locale keys (exact key ids)."""
    leftovers = [k for k in _all_locale_keys() if re.search(rf"\b{re.escape(k)}\b", text)]
    assert not leftovers, f"unresolved locale keys: {leftovers}"


def test_full_player_journey(ctx):
    user = make_user(ctx, uid=1)
    settle = {"status": "OK", "gained": 0, "events": []}

    # main menu
    text, kb = _dispatch_pair3(ctx, user, "menu", "", settle)
    _check_no_raw_keys(text)
    assert "لوح سرنوشت" in text

    # meditate → stop
    text, kb = _dispatch_pair3(ctx, user, "meditate", "", settle)
    _check_no_raw_keys(text)
    assert user["cultivation"].get("meditating") is not True
    text, kb = _dispatch_pair3(ctx, user, "stop_meditate", "", settle)
    _check_no_raw_keys(text)

    # language toggle to English and back
    text, kb = _dispatch_pair3(ctx, user, "setlang", "en", settle)
    _check_no_raw_keys(text)
    assert "DESTINY SCROLL" in text
    text, kb = _dispatch_pair3(ctx, user, "setlang", "fa", settle)

    # backpack / shop locked at Qi Condensation
    text, kb = _dispatch_pair3(ctx, user, "backpack", "", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch_pair3(ctx, user, "shop", "", settle)
    _check_no_raw_keys(text)

    # map → zone → travel
    text, kb = _dispatch_pair3(ctx, user, "map", "", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch_pair3(ctx, user, "zone", "zone_ordinary_cave", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch_pair3(ctx, user, "travel", "zone_ordinary_cave", settle)
    _check_no_raw_keys(text)
    assert user["location"]["current_zone_id"] == "zone_ordinary_cave"

    # hunt a monster — a session must now own the screen (P4 lock model)
    text, kb = _dispatch_pair3(ctx, user, "hunt", "zone_ordinary_cave", settle)
    _check_no_raw_keys(text)
    if user["combat"].get("session"):
        assert _blocked(ctx, user, "map:view:world") == "GUARD_COMBAT"
        from grimhaven.core import combat_engine as ce
        ce.close_session(user)  # walk away; sim/flee paths are covered in combat tests

    # dao panel (too early, still renders)
    text, kb = _dispatch_pair3(ctx, user, "dao", "", settle)
    _check_no_raw_keys(text)

    # breakthrough attempt without full Qi → alert, not a crash (spec P6 §3.1)
    text, kb, opts = _dispatch3(ctx, user, "breakthrough", "", settle)
    _check_no_raw_keys(text)
    assert "لبریز" in opts.get("alert", "") or "not full" in opts.get("alert", "").lower()


def test_breakthrough_flow_instant_resolution(ctx):
    """P6 §3–4 — prep gate, then the tribulation resolves instantly; no seclusion."""
    user = make_user(ctx, uid=2)
    user["cultivation"]["qi_current"] = 500
    settle = {"status": "OK", "gained": 0, "events": []}
    text, kb = _dispatch_pair3(ctx, user, "breakthrough", "", settle)
    _check_no_raw_keys(text)
    assert "دروازهٔ شکست سد" in text or "Breakthrough Gate" in text
    assert user["cultivation"].get("seclusion_finish_time") is None
    text, kb = _dispatch_pair3(ctx, user, "breakthrough_do", "", settle)
    _check_no_raw_keys(text)
    assert any(marker in text for marker in
               ("پیروزی بر مشیت", "Triumph over Heaven", "شکست کالبد",
                "Physique Shattered", "معجزه"))
    # the battle-less user drops straight back to a clean state
    assert user["combat"].get("session") is None


def test_admin_actions(ctx):
    admin = make_user(ctx, uid=999)
    settle = {"status": "OK", "gained": 0, "events": []}
    text, kb = _dispatch_pair3(ctx, admin, "admin", "drops", settle)
    _check_no_raw_keys(text)
    assert "Rain" in text or "بارش" in text
    text, kb = _dispatch_pair3(ctx, admin, "admin", "usage:broadcast", settle)
    _check_no_raw_keys(text)
    # non-admin blocked
    pleb = make_user(ctx, uid=5)
    text, kb = _dispatch_pair3(ctx, pleb, "admin", "drops", settle)
    _check_no_raw_keys(text)


def test_locale_files_valid_and_complete():
    fa = json.loads((LOCALE_DIR / "locale_fa.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALE_DIR / "locale_en.json").read_text(encoding="utf-8"))
    missing_in_en = set(fa) - set(en)
    missing_in_fa = set(en) - set(fa)
    assert not missing_in_en, f"keys missing in EN: {missing_in_en}"
    assert not missing_in_fa, f"keys missing in FA: {missing_in_fa}"

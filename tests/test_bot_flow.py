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

from grimhaven.bot.handlers.callbacks import _dispatch
from grimhaven.bot.handlers.common import Ctx
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc, utcnow


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "test.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={999})
    storage.close()


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
    text, kb = _dispatch(ctx, user, "menu", "", settle)
    _check_no_raw_keys(text)
    assert "لوح سرنوشت" in text

    # meditate → stop
    text, kb = _dispatch(ctx, user, "meditate", "", settle)
    _check_no_raw_keys(text)
    assert user["cultivation"]["meditating"] is True
    text, kb = _dispatch(ctx, user, "stop_meditate", "", settle)
    _check_no_raw_keys(text)

    # language toggle to English and back
    text, kb = _dispatch(ctx, user, "setlang", "en", settle)
    _check_no_raw_keys(text)
    assert "DESTINY SCROLL" in text
    text, kb = _dispatch(ctx, user, "setlang", "fa", settle)

    # backpack / shop locked at Qi Condensation
    text, kb = _dispatch(ctx, user, "backpack", "", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch(ctx, user, "shop", "", settle)
    _check_no_raw_keys(text)

    # map → zone → travel
    text, kb = _dispatch(ctx, user, "map", "", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch(ctx, user, "zone", "zone_common_cave", settle)
    _check_no_raw_keys(text)
    text, kb = _dispatch(ctx, user, "travel", "zone_common_cave", settle)
    _check_no_raw_keys(text)
    assert user["location"]["current_zone_id"] == "zone_common_cave"

    # hunt a monster
    text, kb = _dispatch(ctx, user, "hunt", "zone_common_cave", settle)
    _check_no_raw_keys(text)

    # dao panel (too early, still renders)
    text, kb = _dispatch(ctx, user, "dao", "", settle)
    _check_no_raw_keys(text)

    # breakthrough attempt without Qi → error message, not a crash
    text, kb = _dispatch(ctx, user, "breakthrough", "", settle)
    _check_no_raw_keys(text)
    assert "کافی نیست" in text or "Insufficient" in text


def test_breakthrough_flow_with_full_dantian(ctx):
    user = make_user(ctx, uid=2)
    user["cultivation"]["qi_current"] = 500
    settle = {"status": "OK", "gained": 0, "events": []}
    text, kb = _dispatch(ctx, user, "breakthrough", "", settle)
    _check_no_raw_keys(text)
    assert user["cultivation"]["seclusion_finish_time"] is None  # confirmation screen
    text, kb = _dispatch(ctx, user, "breakthrough_do", "", settle)
    _check_no_raw_keys(text)
    assert user["cultivation"]["seclusion_finish_time"] is not None
    # checking too early → still in seclusion
    text, kb = _dispatch(ctx, user, "check_tribulation", "", settle)
    _check_no_raw_keys(text)
    # force seclusion to end, then resolve — every outcome branch must render
    past = utcnow() - dt.timedelta(hours=1)
    user["cultivation"]["seclusion_finish_time"] = past.isoformat()
    ctx.save(user)
    text, kb = _dispatch(ctx, user, "check_tribulation", "", settle)
    _check_no_raw_keys(text)
    # second check: no active seclusion → back to the destiny scroll
    text, kb = _dispatch(ctx, user, "check_tribulation", "", settle)
    _check_no_raw_keys(text)


def test_admin_actions(ctx):
    admin = make_user(ctx, uid=999)
    settle = {"status": "OK", "gained": 0, "events": []}
    text, kb = _dispatch(ctx, admin, "admin", "drops", settle)
    _check_no_raw_keys(text)
    assert "Rain" in text or "بارش" in text
    text, kb = _dispatch(ctx, admin, "admin", "usage:broadcast", settle)
    _check_no_raw_keys(text)
    # non-admin blocked
    pleb = make_user(ctx, uid=5)
    text, kb = _dispatch(ctx, pleb, "admin", "drops", settle)
    _check_no_raw_keys(text)


def test_locale_files_valid_and_complete():
    fa = json.loads((LOCALE_DIR / "locale_fa.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALE_DIR / "locale_en.json").read_text(encoding="utf-8"))
    missing_in_en = set(fa) - set(en)
    missing_in_fa = set(en) - set(fa)
    assert not missing_in_en, f"keys missing in EN: {missing_in_en}"
    assert not missing_in_fa, f"keys missing in FA: {missing_in_fa}"

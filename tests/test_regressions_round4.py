"""Round-4 regression sweep — the bugs found after the "great shift":

* partial / hand-corrupted player documents crashed mid-update, leaving the
  player's tap completely unanswered (ensure_v2 now backfills everything);
* `/inspect` died on a Python-level kwarg collision (``lang=`` vs ``t(lang, …)``);
* sect join/found alerts rendered a raw ``{sect}`` placeholder;
* the Dao-choice alert rendered the raw locale key (``DAO_SWORD`` …);
* a crash anywhere in the command/callback pipeline produced NO reply at all
  (both PTB entry points now always answer);
* ``deploy/gha_state.sh save`` POSTed to a non-existent public REST endpoint, so
  the game's only database was never persisted between Actions runs (it now
  speaks the @actions/artifact v2 Twirp protocol).
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path

import pytest

from grimhaven.bot.handlers import callbacks as cb
from grimhaven.bot.handlers import admin as admin_h
from grimhaven.bot.handlers import commands as cmd_h
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core.data_loader import bootstrap as bootstrap_data
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import ensure_v2, new_user_doc, utcnow
from grimhaven.localization import Locale, locales, t

REPO = Path(__file__).resolve().parent.parent

ACTIONS = [
    "profile:view:main", "profile:view:meridians", "profile:view:karma",
    "cultivate:view:hub", "cultivate:action:claim", "cultivate:catalyst:menu",
    "breakthrough:view:prep", "breakthrough:pill:select",
    "map:view:world", "map:zone:inspect:zone_valley_mortals",
    "map:action:hunt:zone_valley_mortals", "map:action:gather:zone_valley_mortals",
    "bag:tab:gear:1", "martial:view:main", "martial:mantra:menu",
    "shop:view:hub", "sect:view:main", "settings:view:main", "setlang:en",
]


@pytest.fixture()
def ctx(tmp_path):
    storage = Storage(tmp_path / "r4.db")
    bootstrap_world(storage)
    bootstrap_data()
    yield Ctx(storage, admin_ids={999})
    storage.close()


def _dispatch_all(ctx, user):
    for act in ACTIONS:
        a0, _, a1 = act.partition(":")
        text, kb, opts = cb._dispatch(ctx, user, a0, a1, {"gained": 0, "events": []},
                                      now=utcnow())
        assert isinstance(text, str) and text
        ctx.save(user)


# ── 1. document hardening ────────────────────────────────────────────────────

def test_ensure_v2_backfills_every_engine_field():
    bare = {"_id": "tg_user_2", "user_id": 2, "account": {"username": "bare", "language": "en"}}
    doc = ensure_v2(copy.deepcopy(bare))
    assert doc["cultivation"]["qi_capacity"] > 0
    assert doc["stats"]["visible"]["circulation_velocity"] == 10
    assert doc["stats"]["visible"]["spiritual_sense"] == 10
    assert doc["stats"]["hidden"]["karmic_luck"] == 50
    assert doc["stats"]["hidden"]["dao_heart_stability"] == 70
    assert doc["inventory"]["spirit_stones"] == {"low": 0, "mid": 0, "high": 0, "heavenly": 0}
    assert doc["buffs"] == []
    assert doc["equipment"]["weapon"] is None and doc["equipment"]["natal"] is None
    assert doc["combat"]["loadout"]
    assert doc["location"]["current_zone_id"] == "zone_valley_mortals"
    assert doc["progress"]["deaths"] == 0


def test_partial_and_bare_docs_survive_every_screen(ctx):
    for raw in (
        {"_id": "tg_user_1", "user_id": 1,
         "account": {"username": "min", "language": "fa"},
         "cultivation": {"current_realm_index": 1, "current_stage": 0,
                         "qi_current": 10, "qi_capacity": 500},
         "inventory": {}, "stats": {"visible": {}, "hidden": {}},
         "combat": {}, "location": {"current_zone_id": "zone_valley_mortals"}},
        {"_id": "tg_user_2", "user_id": 2, "account": {"username": "bare", "language": "en"}},
    ):
        doc = ensure_v2(copy.deepcopy(raw))
        ctx.storage.save_user(doc)
        user = ctx.storage.get_user(doc["user_id"])
        _dispatch_all(ctx, user)
        # the un-wrapped settle path (handlers call it before dispatch) too
        ctx.settle(user)
        ctx.save(user)


# ── 2. the two placeholder / kwarg bugs ──────────────────────────────────────

def test_sect_join_and_found_alerts_carry_localized_name(ctx):
    user = new_user_doc(11, "sectling", "en")
    user["cultivation"]["current_realm_index"] = 2
    ctx.storage.save_user(user)
    user = ctx.storage.get_user(11)
    _text, _kb, opts = cb._dispatch(ctx, user, "sect:action", "join:sect_azure_cloud",
                                    {"gained": 0, "events": []}, now=utcnow())
    alert = opts.get("alert", "")
    assert "{sect}" not in alert and alert
    assert "Azure" in alert or "Cloud" in alert or "⛩" in alert
    ctx.save(user)

    user = ctx.storage.get_user(11)
    user["cultivation"]["current_realm_index"] = 6
    ctx.storage.save_user(user)
    user = ctx.storage.get_user(11)
    _text, _kb, opts = cb._dispatch(ctx, user, "sect:action", "found",
                                    {"gained": 0, "events": []}, now=utcnow())
    alert = opts.get("alert", "")
    assert "{sect}" not in alert and alert
    ctx.save(user)


def test_dao_alert_carries_localized_path_name(ctx):
    user = new_user_doc(12, "daoling", "fa")
    user["cultivation"]["current_realm_index"] = 1
    user["cultivation"]["current_stage"] = 8
    ctx.storage.save_user(user)
    user = ctx.storage.get_user(12)
    _text, _kb, opts = cb._dispatch(ctx, user, "dao:pick", "sword:orthodox",
                                    {"gained": 0, "events": []}, now=utcnow())
    alert = opts.get("alert", "")
    assert "DAO_" not in alert and alert
    assert "شمشیر" in alert or "剑" in alert or "⚔" in alert or "☯" in alert
    ctx.save(user)


def test_locale_placeholder_sets_are_symmetric():
    fa = json.loads((REPO / "locales/locale_fa.json").read_text(encoding="utf-8"))
    en = json.loads((REPO / "locales/locale_en.json").read_text(encoding="utf-8"))
    for key in set(fa) | set(en):
        pf = set(re.findall(r"\{(\w+)\}", fa.get(key, "")))
        pe = set(re.findall(r"\{(\w+)\}", en.get(key, "")))
        assert pf == pe, f"placeholder drift for {key}: fa={sorted(pf)} en={sorted(pe)}"


def test_no_t_call_passes_lang_or_key_as_format_kwarg():
    """``t(lang, key, lang=…)`` is a Python-level TypeError; scan the tree."""
    offenders = []
    for path in (REPO / "grimhaven").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\bt\(", src):
            i, depth, j = m.end(), 1, m.end()
            while j < len(src) and depth:
                c = src[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                elif c in "\"'":
                    q = c
                    j += 1
                    while j < len(src) and src[j] != q:
                        j += 2 if src[j] == "\\" else 1
                j += 1
            call = src[i:j - 1]
            for kw in ("lang", "key"):
                if re.search(rf"\b{kw}\s*=(?!=)", call):
                    offenders.append(f"{path}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, f"t() kwarg collisions: {offenders}"


# ── 3. /inspect must render, never TypeError ─────────────────────────────────

from tests.telegram_fakes import (FakeBot, FakeContext as _Holder,  # faithful fakes
                                 FakeUpdate as _Upd)


def test_admin_inspect_renders_full_report(ctx):
    target = new_user_doc(555, "inspected", "fa")
    ctx.storage.save_user(target)
    admin = new_user_doc(999, "boss", "en")
    ctx.storage.save_user(admin)
    holder = _Holder(ctx)
    holder.args = ["555"]
    upd = _Upd(text='/inspect 555', user_id=999)
    asyncio.run(admin_h.cmd_inspect(upd, holder))
    assert upd.message.replies, "/inspect produced no reply"
    report = upd.message.replies[0]
    assert "inspected" in report and "{lang}" not in report and "{" not in report


def test_admin_inspect_unknown_user_replies(ctx):
    holder = _Holder(ctx)
    holder.args = ["424242"]
    upd = _Upd(text='/inspect 555', user_id=999)
    asyncio.run(admin_h.cmd_inspect(upd, holder))
    assert upd.message.replies


# ── 4. the async layer always answers, even when the engine explodes ─────────

def test_run_safety_net_answers_when_dispatch_crashes(ctx, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("engine on fire")

    # commands.py imports _dispatch by name — patch it where it is used
    monkeypatch.setattr(cmd_h, "_dispatch", boom)
    user = new_user_doc(70, "victim", "fa")
    ctx.storage.save_user(user)
    holder = _Holder(ctx)
    upd = _Upd(text='⚙️ تنظیمات', user_id=70)
    asyncio.run(cmd_h._run(upd, holder, "profile:view:main"))
    assert upd.message.replies, "a crashed dispatch must still answer the player"
    assert "ERR_UNKNOWN" not in upd.message.replies[0]  # localized, not the raw key


def test_on_callback_safety_net_answers_when_settle_crashes(ctx, monkeypatch):
    """A crash OUTSIDE the inner render guard (settle, migration, …) must still
    answer the tap instead of leaving the player staring at a dead button."""
    def boom(*a, **kw):
        raise RuntimeError("settle on fire")

    monkeypatch.setattr(Ctx, "settle", boom)
    user = new_user_doc(71, "victim2", "fa")
    ctx.storage.save_user(user)

    upd = _Upd(query_data="profile:view:main", user_id=71)
    holder = _Holder(ctx)
    q = upd.callback_query
    asyncio.run(cb.on_callback(upd, holder))
    assert q.answered, "a crashed callback must still be answered"
    assert q.answered[0][1] is True  # show_alert popup so the player sees it


# ── 5. the world-state saver speaks the real artifact protocol ───────────────

def test_gha_state_saves_through_the_artifact_twirp_protocol():
    # the Twirp client was extracted into a module (round 5) so the protocol is
    # unit-testable; gha_state.sh must still drive it
    script = (REPO / "deploy/gha_state.sh").read_text(encoding="utf-8")
    client = (REPO / "deploy/gha_artifact.py").read_text(encoding="utf-8")
    both = script + client
    # the dead public-REST creation POST (404 for every historical run) is gone
    assert "expiration_days=1" not in both
    assert "gha_artifact.py" in script  # save/verify delegate to the module
    assert "CreateArtifact" in client
    assert "FinalizeArtifact" in client
    assert "x-ms-blob-type" in client
    assert "Actions.Results" in client
    assert "ACTIONS_RUNTIME_TOKEN" in client and "ACTIONS_RESULTS_URL" in client
    assert "twirp/github.actions.results.api.v1.ArtifactService" in client


def test_locale_module_exposes_mapping_variant():
    assert locales.t_map("en", "INSPECT_REPORT", {"lang": "fa"})  # {lang} placeholder
    assert t("en", "HUD_TITLE", name="x")

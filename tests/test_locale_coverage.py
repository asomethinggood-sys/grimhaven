"""Locale coverage gate: no screen may ask for a key the tables do not hold.

`t()` never raises on a missing key — it prints the key itself, so a screen that
asks for `USE_PASSIVE` without a locale row shows the player the literal string
"USE_PASSIVE". This module walks the live router (every reachable payload, both
languages) with `Locale.t_map` instrumented and fails on the first key that
comes back as its own name, then keeps the dead-key census honest.

The census itself lives in `scripts/locale_audit.py` (run it with `--dead`).
"""
from __future__ import annotations

import random

import pytest

from grimhaven.bot.handlers.callbacks import _canon, _dispatch
from grimhaven.bot.handlers.common import Ctx
from grimhaven.core import combat_engine as ce
from grimhaven.core.data_loader import bootstrap, data_registry
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine import items as items_mod
from grimhaven.engine.models import (ensure_v2, new_user_doc, recompute_visible_stats,
                                     utcnow)
from grimhaven.localization import Locale, locales as live_locale

ROOTS = (
    "profile:view:main", "profile:view:meridians", "profile:view:karma",
    "cultivate:view:hub", "cultivate:view:catalyst", "cultivate:view:seclusion",
    "bag:tab:gear:1", "bag:tab:consumables:1", "bag:tab:materials:1",
    "martial:view:main", "martial:view:deck", "martial:view:mantras",
    "shop:view:hub", "shop:tab:arts:1", "shop:tab:pills:1", "shop:tab:gear:1",
    "shop:tab:talismans:1", "sect:view:main", "breakthrough:view:prep",
    "settings:view:main", "dao:view", "map:view:world", "menu",
    "admin:view:panel", "unknown:payload:shape",
)


class Recorder:
    """Instruments `Locale.t_map` and remembers every key that came back raw."""

    def __init__(self, target: Locale):
        self.target = target                 # the live module-level instance
        self.requests: set[tuple[str, str]] = set()
        self.missing: set[str] = set()
        self._orig = Locale.t_map            # plain function: (self, lang, key, mapping)

    def wrapper(self, lang, key, mapping):   # bound → called as instance.t_map(...)
        text = self._orig(self.target, lang, key, mapping)
        self.requests.add((lang, key))
        if text == key:
            self.missing.add(key)
        return text


@pytest.fixture()
def ctx(tmp_path):
    bootstrap()
    storage = Storage(tmp_path / "locale.db")
    bootstrap_world(storage)
    yield Ctx(storage, admin_ids={7})
    storage.close()


def _user(uid: int, lang: str, realm: int = 4) -> dict:
    user = ensure_v2(new_user_doc(uid, f"soul{uid}", lang))
    user["cultivation"].update({"current_realm_index": realm, "current_stage": 1})
    user["cultivation"]["qi_current"] = user["cultivation"]["qi_capacity"] = 250_000
    user["inventory"]["spirit_stones"].update({"low": 9000, "mid": 40, "high": 6, "heavenly": 1})
    for iid in list(data_registry.consumables)[:24]:
        items_mod.add_item(user, iid, 2)
    for iid in list(data_registry.equipment)[:8]:
        items_mod.add_item(user, iid, 1, is_gear=True)
    for art_id in list(data_registry.martial_arts)[:3]:
        if art_id not in user["inventory"]["arts"]:
            user["inventory"]["arts"].append(art_id)
    recompute_visible_stats(user)
    return user


def _walk(ctx, rec: Recorder, user: dict, roots=ROOTS, depth: int = 2) -> int:
    """BFS over the payload graph; returns the number of tapped payloads."""
    seen: set[str] = set()
    frontier = list(roots)
    taps = 0
    for _ in range(depth):
        nxt: list[str] = []
        for payload in frontier:
            raw = _canon(payload, user)
            if raw in seen:
                continue
            seen.add(raw)
            action, _, arg = raw.partition(":")
            try:
                text, kb, opts = _dispatch(ctx, user, action, arg, {}, now=utcnow())
            except Exception as exc:            # a crash is another module's test
                pytest.fail(f"dispatch {payload!r} raised {exc!r}")
            taps += 1
            for opt in ("answer", "alert"):
                value = (opts or {}).get(opt)
                if isinstance(value, str) and value in (None, ""):
                    continue
            if kb is not None:
                for row in kb.inline_keyboard:
                    for btn in row:
                        if btn.callback_data and btn.callback_data != "noop":
                            nxt.append(btn.callback_data)
        frontier = nxt
    return taps


def _force_terminal_cards(ctx, rec: Recorder) -> None:
    """Victory / death / rival cards are the screens with the most strings."""
    import grimhaven.bot.handlers.callbacks as cb

    class NoMiracle(random.Random):
        def uniform(self, a, b):
            return 99.9 if (a == 0 and b == 100) else (a + b) / 2

        def randint(self, a, b):
            return a

    def fight(uid: int, lang: str):
        user = _user(uid, lang)
        ce.start_session(user, ce.make_beast("valley_wolf", random.Random(2)), "hunt",
                         "zone_valley_mortals")
        return user, user["combat"]["session"]

    for lang in ("fa", "en"):
        user, session = fight(90, lang)
        session["enemy"]["hp"] = 1
        _dispatch(ctx, user, "combat", f"act:basic:none:{session['round']}", {}, now=utcnow())
        if user["combat"].get("session"):       # the win did not land on this roll
            session = user["combat"]["session"]
            session["enemy"]["hp"] = 1
            _dispatch(ctx, user, "combat", f"act:basic:none:{session['round']}", {},
                      now=utcnow())

    # a real death (miracle escape disabled) and the flee/rival verdict buttons
    original = cb.random.Random
    cb.random.Random = lambda *a, **k: NoMiracle(1)
    try:
        for lang in ("fa", "en"):
            user, session = fight(91, lang)
            session["player"]["hp"] = 1
            user["cultivation"]["qi_current"] = 0
            user["stats"]["visible"]["physique_hp"] = 1
            _dispatch(ctx, user, "combat", f"act:basic:none:{session['round']}", {},
                      now=utcnow())
            _dispatch(ctx, user, "combat", f"act:flee:none:{session['round']}", {},
                      now=utcnow())
        for payload in ("combat:rival:mercy", "combat:rival:plunder"):
            user = _user(92, "fa")
            ce.start_session(user, ce.make_beast("valley_wolf", random.Random(2)), "rival",
                             "zone_valley_mortals")
            _dispatch(ctx, user, *payload.partition(":")[::2], {}, now=utcnow())
    finally:
        cb.random.Random = original


def test_no_reachable_screen_asks_for_a_missing_locale_key(ctx, monkeypatch):
    for lang, uid in (("fa", 81), ("en", 82)):
        rec = Recorder(live_locale)
        monkeypatch.setattr(Locale, "t_map", rec.wrapper)
        user = _user(uid, lang)
        taps = _walk(ctx, rec, user)
        _force_terminal_cards(ctx, rec)
        assert taps >= 40, f"{lang}: the walk only tapped {taps} payloads — is it still wired?"
        assert rec.requests, "no key was requested at all — the instrumentation is broken"
        assert not rec.missing, f"missing locale keys ({lang}): {sorted(rec.missing)}"
        monkeypatch.undo()


def test_locale_census_passes_and_finds_no_dead_keys():
    """`scripts/locale_audit.py` must report missing=0, asymmetric=0, dead=0."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, str(root / "scripts" / "locale_audit.py"),
                           "--quiet"], capture_output=True, text=True, cwd=root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = subprocess.run([sys.executable, str(root / "scripts" / "locale_audit.py")],
                         capture_output=True, text=True, cwd=root).stdout
    dead = [line for line in out.splitlines() if line.startswith("dead (defined")]
    assert dead and dead[0].endswith(": 0"), out

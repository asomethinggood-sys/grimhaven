"""Engine tests — every assertion is anchored to the Master Design Document."""
from __future__ import annotations

import datetime as dt
import random

import pytest

from grimhaven.engine.constants import (
    BASE_RATES,
    REALM_PENALTIES,
    REALM_STAGES,
    SPIRIT_STONES,
)
from grimhaven.engine.cultivation import CultivationEngine
from grimhaven.engine.models import afk_hourly_rate, new_user_doc, utcnow

NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.timezone.utc)


def fresh_user(**hidden) -> dict:
    user = new_user_doc(1, "tester", "fa")
    user["stats"]["hidden"].update(hidden)
    return user


# ── chapter 2: realm economics ───────────────────────────────────────────────

def test_realm_costs_match_doc():
    assert REALM_STAGES[1] == [500, 1000, 2000, 3500, 5500, 8000, 11000, 15000, 20000]
    assert sum(REALM_STAGES[1]) == 66_500
    assert all(c == 60_000 for c in REALM_STAGES[2]) and sum(REALM_STAGES[2]) == 240_000
    assert sum(REALM_STAGES[3]) == 1_200_000
    assert sum(REALM_STAGES[4]) == 6_000_000
    assert sum(REALM_STAGES[5]) == 24_000_000
    assert sum(REALM_STAGES[6]) == 100_000_000
    assert len(REALM_STAGES[7]) == 9 and sum(REALM_STAGES[7]) == 540_000_000
    assert sum(REALM_STAGES[8]) == 1_000_000_000


def test_base_rates_and_penalties_match_doc():
    assert BASE_RATES[1] == 100
    assert REALM_PENALTIES == {1: 0, 2: 10, 3: 20, 4: 35, 5: 50, 6: 65, 7: 80, 8: 90}


# ── chapter 5: AFK formula ───────────────────────────────────────────────────

def test_afk_base_rate_no_buffs():
    user = fresh_user(karmic_luck=0)
    # 100 Qi/hr base × tech 1.0 × vein 1.0 × (1+0 luck)
    assert afk_hourly_rate(user, now=NOW) == pytest.approx(100.0)


class NoEvents(random.Random):
    """Neutral rolls: no lucky treasure, no bandit ambush."""

    def uniform(self, a, b):
        return 50.0


def test_afk_settle_accumulates_and_caps():
    user = fresh_user(karmic_luck=0)
    user["cultivation"]["meditating"] = True
    user["cultivation"]["last_afk_timestamp"] = (NOW - dt.timedelta(hours=3)).isoformat()
    res = CultivationEngine.settle_afk(user, now=NOW, rng=NoEvents(0))
    assert res["gained"] == 300
    assert user["cultivation"]["qi_current"] == 300
    # cap at dantian capacity (500 for layer 1)
    user["cultivation"]["last_afk_timestamp"] = (NOW - dt.timedelta(hours=50)).isoformat()
    res = CultivationEngine.settle_afk(user, now=NOW, rng=NoEvents(0))
    assert user["cultivation"]["qi_current"] == 500
    assert res["overflow"] == 4800  # 5000 gained, only 200 of room left


def test_spirit_stone_boost_and_consumption():
    user = fresh_user(karmic_luck=0)
    user["cultivation"]["active_stone"] = "mid"  # +45%, 5/hr
    user["inventory"]["spirit_stones"]["mid"] = 10
    rate = afk_hourly_rate(user, now=NOW)
    assert rate == pytest.approx(145.0)
    user["cultivation"]["last_afk_timestamp"] = (NOW - dt.timedelta(hours=2)).isoformat()
    CultivationEngine.settle_afk(user, now=NOW, rng=random.Random(3))
    assert user["inventory"]["spirit_stones"]["mid"] == 0  # 10 - 5*2


def test_lucky_encounter_for_high_luck():
    user = fresh_user(karmic_luck=90)
    user["cultivation"]["last_afk_timestamp"] = (NOW - dt.timedelta(hours=20)).isoformat()
    rng = random.Random(1)
    before = user["inventory"]["spirit_stones"]["low"]
    CultivationEngine.settle_afk(user, now=NOW, rng=rng)
    assert user["inventory"]["spirit_stones"]["low"] > before
    assert user["progress"]["encounters_found"] >= 1


# ── chapter 3: breakthrough ──────────────────────────────────────────────────

def test_breakthrough_requires_full_dantian():
    user = fresh_user()
    res = CultivationEngine.begin_breakthrough(user, now=NOW)
    assert res["status"] == "QI_NOT_ENOUGH"


def test_breakthrough_starts_seclusion_and_burns_qi():
    user = fresh_user()
    user["cultivation"]["qi_current"] = 500
    res = CultivationEngine.begin_breakthrough(user, now=NOW)
    assert res["status"] == "SECLUSION_STARTED"
    assert res["minutes"] == 15  # layers 1-3 => 15 minutes (doc §2.2)
    assert user["cultivation"]["qi_current"] == 0
    # blocked while in seclusion
    assert CultivationEngine.begin_breakthrough(user, now=NOW)["status"] == "ALREADY_IN_SECLUSION"


def test_success_formula_bounds():
    user = fresh_user(dao_heart_stability=100, karmic_luck=100, demonic_corruption=0)
    rate = CultivationEngine.success_rate(user)
    # base 50 (layer) + 30 + 20 (guardian pill not set here) … stays clamped ≤95
    assert 5.0 <= rate <= 95.0
    corrupt = fresh_user(dao_heart_stability=0, karmic_luck=0, demonic_corruption=100)
    assert CultivationEngine.success_rate(corrupt) >= 5.0  # clamped floor


def test_miracle_salvation_burns_luck():
    user = fresh_user(karmic_luck=100)
    user["cultivation"]["qi_current"] = 500
    CultivationEngine.begin_breakthrough(user, now=NOW)
    user["cultivation"]["seclusion_finish_time"] = (NOW - dt.timedelta(minutes=1)).isoformat()

    class Rigged(random.Random):
        pct_roll = 0

        def uniform(self, a, b):
            if b - a < 2000:  # a 0-100 percent roll
                Rigged.pct_roll += 1
                # 1st % roll = main breakthrough roll → fail; 2nd = miracle → win
                return 99.9 if Rigged.pct_roll == 1 else 0.0
            return (a + b) / 2  # lightning variance

    Rigged.pct_roll = 0
    res = CultivationEngine.resolve_breakthrough(user, now=NOW, rng=Rigged(0))
    assert res["status"] == "MIRACLE_SAVED"
    assert user["stats"]["hidden"]["karmic_luck"] == 85  # −15 karma burn
    assert user["cultivation"]["current_stage"] == 1  # advanced anyway


def test_failure_weights_distribution():
    """Over many rolls failures split ~60/30/10 (doc §3.2)."""
    counts = {"MINOR": 0, "DEVIATION": 0, "ANNIHILATION": 0}
    rng = random.Random(42)
    for i in range(3000):
        user = fresh_user(karmic_luck=0, dao_heart_stability=0)
        user["cultivation"]["current_realm_index"] = 2  # realm penalty 10
        user["cultivation"]["current_stage"] = 1
        user["cultivation"]["qi_capacity"] = REALM_STAGES[2][1]
        user["cultivation"]["seclusion_finish_time"] = (NOW - dt.timedelta(minutes=1)).isoformat()
        user["cultivation"]["seclusion_target"] = {"realm": 2, "stage": 2}

        class AlwaysFail(random.Random):
            def __init__(self, inner):
                super().__init__(0)
                self.inner = inner

            def uniform(self, a, b):
                return 99.99

            def choices(self, pop, weights=None, k=1):
                return self.inner.choices(pop, weights=weights, k=k)

        res = CultivationEngine.resolve_breakthrough(user, now=NOW, rng=AlwaysFail(rng))
        assert res["status"] == "FAILED"
        counts[res["type"]] += 1
    total = sum(counts.values())
    assert 0.55 < counts["MINOR"] / total < 0.65
    assert 0.25 < counts["DEVIATION"] / total < 0.35
    assert 0.06 < counts["ANNIHILATION"] / total < 0.14


def test_minor_failure_burns_qi_and_seals():
    user = fresh_user(karmic_luck=0)
    user["cultivation"]["qi_current"] = 1000
    detail = CultivationEngine._apply_failure(user, "MINOR", random.Random(0))
    assert detail["qi_burned"] == 300
    assert user["cultivation"]["meridian_sealed_until"] is not None


def test_annihilation_demands_golden_core_or_higher():
    user = fresh_user(karmic_luck=0)
    user["cultivation"]["current_realm_index"] = 2
    detail = CultivationEngine._apply_failure(user, "ANNIHILATION", random.Random(0))
    # degrades to deviation below Golden Core
    assert "stones_lost" in detail

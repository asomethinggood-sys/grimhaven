"""Cultivation engine — AFK settlement, seclusion, breakthrough & tribulation.

Implements chapters 2, 3 and 5 of the Master Design Document, including the
reference `CultivationEngine` semantics from §10.2.
"""
from __future__ import annotations

import datetime as dt
import random

from .constants import (
    BASE_CHANCE_LAYER,
    BASE_CHANCE_REALM_JUMP,
    FAILURE_WEIGHTS,
    MAX_REALM,
    MIRACLE_LUCK_COST,
    PILLS,
    REALM_PENALTIES,
    REALM_STAGES,
    SECLUSION_MINUTES_REALM,
    SECLUSION_MINUTES_REALM1,
    SPIRIT_STONES,
    SUCCESS_CLAMP_MAX,
    SUCCESS_CLAMP_MIN,
    TRIBULATION_DAMAGE_RANGE,
)
from .models import tribulation_mitigation
from .models import (
    afk_hourly_rate,
    iso,
    meridians_sealed,
    parse_iso,
    recompute_visible_stats,
    utcnow,
)
from ..core.state_machine import UserStatus, set_status, paralysis_active, is_injured


class CultivationEngine:
    """Stateless engine: every method mutates the user document in place and
    returns a result dict with locale message codes for the UI layer."""

    # ── meditation / AFK ────────────────────────────────────────────────────
    @staticmethod
    def start_meditation(user: dict, now: dt.datetime | None = None) -> dict:
        now = now or utcnow()
        cul = user["cultivation"]
        if cul["meditating"]:
            return {"status": "ALREADY_MEDITATING"}
        if CultivationEngine._seclusion_remaining(user, now):
            return {"status": "IN_SECLUSION"}
        if paralysis_active(user, now) or user["combat"].get("session"):
            return {"status": "BLOCKED"}
        set_status(user, UserStatus.MEDITATING)
        cul["meditation_started_at"] = iso(now)
        cul["last_afk_timestamp"] = iso(now)
        return {"status": "OK"}

    @staticmethod
    def stop_meditation(user: dict, now: dt.datetime | None = None) -> dict:
        now = now or utcnow()
        cul = user["cultivation"]
        if not cul["meditating"]:
            return {"status": "NOT_MEDITATING"}
        result = CultivationEngine.settle_afk(user, now=now)
        set_status(user, UserStatus.IDLE)
        cul["meditation_started_at"] = None
        result["status"] = "STOPPED"
        return result

    @staticmethod
    def _seclusion_remaining(user: dict, now: dt.datetime) -> bool:
        finish = parse_iso(user["cultivation"].get("seclusion_finish_time"))
        return bool(finish and finish > now)

    @staticmethod
    def settle_afk(user: dict, now: dt.datetime | None = None,
                   world_boost: float = 1.0, rng: random.Random | None = None) -> dict:
        """Accrue Qi since `last_afk_timestamp`. Consumes spirit stones,
        fires lucky encounters / ambushes, caps at the Dantian capacity."""
        now = now or utcnow()
        rng = rng or random
        cul = user["cultivation"]
        last = parse_iso(cul.get("last_afk_timestamp")) or now
        elapsed_hours = max(0.0, (now - last).total_seconds() / 3600.0)
        cul["last_afk_timestamp"] = iso(now)
        sealed = meridians_sealed(user, now)  # sealed ⇒ 50% yield (doc §3.2)
        if elapsed_hours <= 0:
            return {"status": "OK", "gained": 0, "events": []}

        # spirit stone consumption while meditating
        consumed_stones: dict[str, float] = {}
        stone = cul.get("active_stone")
        if stone and stone in SPIRIT_STONES:
            need = SPIRIT_STONES[stone]["per_hour"] * elapsed_hours
            have = user["inventory"]["spirit_stones"].get(stone, 0)
            if stone == "heavenly":
                consumed_stones[stone] = 0.0
            elif have >= need:
                user["inventory"]["spirit_stones"][stone] = int(have - need)
                consumed_stones[stone] = need
            else:  # ran out part-way: partial boost handled simply — disable
                user["inventory"]["spirit_stones"][stone] = 0
                cul["active_stone"] = None

        rate = afk_hourly_rate(user, world_boost=world_boost, now=now)
        gained = int(rate * elapsed_hours)

        cap = cul["qi_capacity"]
        room = max(0, cap - cul["qi_current"])
        added = min(gained, room)
        cul["qi_current"] += added
        user["progress"]["qi_total_accumulated"] += added

        events: list[dict] = []
        # lucky encounters per full hour of absence (doc §3.3.2)
        luck = user["stats"]["hidden"]["karmic_luck"]
        full_hours = int(elapsed_hours)
        for _ in range(min(full_hours, 24)):  # cap event rolls at 24 per settle
            roll = rng.uniform(0, 100)
            if luck > 70 and roll < 5:
                stones = rng.randint(1, 5)
                user["inventory"]["spirit_stones"]["low"] += stones
                user["progress"]["encounters_found"] += 1
                events.append({"type": "TREASURE", "stones": stones})
            elif luck < 20 and roll < 5:
                stolen = int(cul["qi_current"] * 0.05)
                cul["qi_current"] -= stolen
                user["progress"]["encounters_found"] += 1
                events.append({"type": "AMBUSH", "stolen": stolen})
        return {
            "status": "SEALED" if sealed else "OK",
            "gained": added,
            "overflow": gained - added,
            "elapsed_hours": round(elapsed_hours, 3),
            "rate": round(rate, 1),
            "consumed_stones": consumed_stones,
            "events": events,
        }

    # ── breakthrough: seclusion → tribulation ──────────────────────────────
    @staticmethod
    def seclusion_minutes(user: dict) -> int:
        cul = user["cultivation"]
        realm = cul["current_realm_index"]
        stage = cul["current_stage"]
        if realm == 1:
            return SECLUSION_MINUTES_REALM1[stage + 1]
        return SECLUSION_MINUTES_REALM.get(realm, 72 * 60)

    @staticmethod
    def begin_breakthrough(user: dict, now: dt.datetime | None = None) -> dict:
        """Validate conditions, burn the stage's Qi, lock down and start the
        closed-door seclusion timer (doc §2.2)."""
        now = now or utcnow()
        cul = user["cultivation"]
        if CultivationEngine._seclusion_remaining(user, now):
            return {"status": "ALREADY_IN_SECLUSION"}
        if meridians_sealed(user, now):
            return {"status": "MERIDIANS_SEALED"}
        if user["combat"].get("session"):
            return {"status": "IN_COMBAT"}
        if paralysis_active(user, now) or is_injured(user, now):
            return {"status": "INJURED"}
        realm = cul["current_realm_index"]
        stage = cul["current_stage"]
        stages = REALM_STAGES[realm]
        cost = stages[min(stage, len(stages) - 1)]
        if realm >= MAX_REALM and stage >= len(stages) - 1:
            return {"status": "AT_DAO_SOVEREIGN"}
        if cul["qi_current"] < cost:
            return {"status": "QI_NOT_ENOUGH", "need": cost, "have": cul["qi_current"]}
        # Dao choice gate: cannot enter Foundation without choosing a Dao path
        if realm == 1 and stage == len(stages) - 1 and not cul.get("dao_path"):
            return {"status": "CHOOSE_DAO_FIRST"}

        cul["qi_current"] -= cost
        minutes = CultivationEngine.seclusion_minutes(user)
        target_stage = stage + 1
        target_realm = realm
        if target_stage >= len(stages):
            target_realm, target_stage = realm + 1, 0
        cul["seclusion_finish_time"] = iso(now + dt.timedelta(minutes=minutes))
        cul["seclusion_target"] = {"realm": target_realm, "stage": target_stage}
        set_status(user, UserStatus.SECLUSION)
        user["progress"]["breakthrough_attempts"] += 1
        return {"status": "SECLUSION_STARTED", "minutes": minutes,
                "target_realm": target_realm, "target_stage": target_stage}

    @staticmethod
    def success_rate(user: dict) -> float:
        """Doc §3.1 formula (clamped 5–95%)."""
        cul = user["cultivation"]
        hidden = user["stats"]["hidden"]
        realm = cul["current_realm_index"]
        stages = REALM_STAGES[realm]
        is_realm_jump = cul["current_stage"] >= len(stages) - 1
        base = BASE_CHANCE_REALM_JUMP if is_realm_jump else BASE_CHANCE_LAYER
        dao_heart_bonus = hidden["dao_heart_stability"] * 0.3
        pill_bonus = 0.0
        from ..core.data_loader import data_registry
        active_pill = cul.get("active_pill")
        if active_pill:
            item = data_registry.get_consumable(active_pill)
            if item and item.action == "breakthrough_bonus":
                pill_bonus = float(item.value)
        if user["inventory"].get("items", {}).get("lotus_seven") and realm == 3:
            pill_bonus += 20.0  # seven-colour lotus stabilises the Golden Core
        luck_bonus = hidden["karmic_luck"] * 0.2
        corruption_penalty = hidden["demonic_corruption"] * 0.5
        penalty = REALM_PENALTIES.get(realm, 10)
        if cul["alignment"] == "demonic":
            penalty += 5.0  # black lightning & karmic fire make it deadlier
        rate = base + dao_heart_bonus + pill_bonus + luck_bonus - corruption_penalty - penalty
        return max(SUCCESS_CLAMP_MIN, min(SUCCESS_CLAMP_MAX, rate))

    @staticmethod
    def resolve_breakthrough(user: dict, now: dt.datetime | None = None,
                             rng: random.Random | None = None) -> dict:
        """Run when seclusion ends — the tribulation roll of doc §3 & §10.2."""
        now = now or utcnow()
        rng = rng or random
        cul = user["cultivation"]
        finish = parse_iso(cul.get("seclusion_finish_time"))
        if not finish:
            return {"status": "NO_SECLUSION"}
        if finish > now:
            return {"status": "STILL_IN_SECLUSION",
                    "remaining_minutes": int((finish - now).total_seconds() // 60) + 1}
        target = cul.get("seclusion_target") or {}
        cul["seclusion_finish_time"] = None

        rate = CultivationEngine.success_rate(user)
        hidden = user["stats"]["hidden"]
        roll = rng.uniform(0, 100)
        consumed_pill = cul.get("active_pill")
        cul["active_pill"] = None
        if user["inventory"].get("items", {}).get("lotus_seven") and \
                cul["current_realm_index"] == 3:
            user["inventory"]["items"].pop("lotus_seven", None)

        lightning = int(rng.uniform(*TRIBULATION_DAMAGE_RANGE))
        if cul["alignment"] == "demonic":
            lightning = int(lightning * 1.5)  # karmic fire amplifies tribulation
        mitigation = tribulation_mitigation(user)
        if mitigation:
            lightning = max(1, int(lightning * (1 - mitigation)))
        set_status(user, UserStatus.IDLE)

        if roll <= rate:
            CultivationEngine._apply_advance(user, target)
            return {"status": "SUCCESS", "rate": round(rate, 1),
                    "lightning": lightning, "consumed_pill": consumed_pill}

        # Miracle roll — karma can deflect the lightning (doc §3.3.1)
        luck_roll = rng.uniform(0, 100)
        if luck_roll <= hidden["karmic_luck"]:
            hidden["karmic_luck"] = max(0, hidden["karmic_luck"] - MIRACLE_LUCK_COST)
            CultivationEngine._apply_advance(user, target)
            return {"status": "MIRACLE_SAVED", "rate": round(rate, 1),
                    "lightning": lightning}

        failure = rng.choices(list(FAILURE_WEIGHTS), weights=list(FAILURE_WEIGHTS.values()))[0]
        detail = CultivationEngine._apply_failure(user, failure, rng)
        return {"status": "FAILED", "type": failure, "rate": round(rate, 1),
                "lightning": lightning, **detail}

    @staticmethod
    def _apply_advance(user: dict, target: dict) -> None:
        cul = user["cultivation"]
        new_realm = target.get("realm", cul["current_realm_index"])
        new_stage = target.get("stage", 0)
        entered_new_realm = new_realm > cul["current_realm_index"]
        cul["current_realm_index"] = new_realm
        cul["current_stage"] = new_stage
        stages = REALM_STAGES[new_realm]
        cul["qi_capacity"] = stages[min(new_stage, len(stages) - 1)]
        user["progress"]["breakthrough_successes"] += 1
        user["stats"]["hidden"]["dao_heart_stability"] = min(
            100, user["stats"]["hidden"]["dao_heart_stability"] + 2)
        if entered_new_realm:
            from .constants import REALM_ENTRY_SPIKES
            spike = REALM_ENTRY_SPIKES.get(new_realm)
            if spike:
                vis = user["stats"]["visible"]
                vis["max_hp"] = int(vis["max_hp"] * spike[0]) or 100
                vis["physique_hp"] = vis["max_hp"]
        recompute_visible_stats(user)
        CultivationEngine._reveal_stats_if_due(user)

    @staticmethod
    def _apply_failure(user: dict, failure: str, rng: random.Random) -> dict:
        cul = user["cultivation"]
        hidden = user["stats"]["hidden"]
        detail: dict = {}
        if failure == "MINOR":
            burned = int(cul["qi_current"] * 0.30)
            cul["qi_current"] -= burned
            cul["meridian_sealed_until"] = iso(utcnow() + dt.timedelta(hours=6))
            user["progress"]["failures_minor"] += 1
            detail = {"qi_burned": burned, "sealed_hours": 6}
        elif failure == "DEVIATION":
            # drop one sub-stage; madness burns 10% of spirit stones carried
            if cul["current_stage"] > 0:
                cul["current_stage"] -= 1
            else:  # first stage of the realm: fall to last stage of previous realm
                if cul["current_realm_index"] > 1:
                    cul["current_realm_index"] -= 1
                    prev_stages = REALM_STAGES[cul["current_realm_index"]]
                    cul["current_stage"] = len(prev_stages) - 1
            stones = user["inventory"]["spirit_stones"]
            lost = {g: int(q * 0.10) for g, q in stones.items()}
            for g, q in lost.items():
                stones[g] -= q
            cul["qi_capacity"] = REALM_STAGES[cul["current_realm_index"]][cul["current_stage"]]
            cul["qi_current"] = min(cul["qi_current"], cul["qi_capacity"])
            hidden["dao_heart_stability"] = max(0, hidden["dao_heart_stability"] - 5)
            user["progress"]["failures_deviation"] += 1
            recompute_visible_stats(user)
            detail = {"stones_lost": lost}
        else:  # ANNIHILATION — Golden Core+ only, else degrades to deviation
            if cul["current_realm_index"] < 3:
                return CultivationEngine._apply_failure(user, "DEVIATION", rng)
            has_defense = any(
                user["equipment"].get(slot) for slot in ("robe", "companion")
            )
            has_doll = user["inventory"].get("gear", {}).get("doll_substitute") or \
                user["inventory"].get("doll_substitute")
            if has_doll:
                if isinstance(user["inventory"].get("gear"), dict):
                    user["inventory"]["gear"].pop("doll_substitute", None)
                user["progress"]["failures_minor"] += 1
                return {"saved_by_doll": True}
            # fall to layer 1 of the previous realm; equipped artifact shatters
            prev_realm = cul["current_realm_index"] - 1
            cul["current_realm_index"] = prev_realm
            cul["current_stage"] = 0
            cul["qi_capacity"] = REALM_STAGES[prev_realm][0]
            cul["qi_current"] = min(cul["qi_current"], cul["qi_capacity"])
            shattered = None
            if not has_defense:
                for slot in ("weapon", "accessory", "ring"):
                    if user["equipment"].get(slot):
                        shattered = user["equipment"].pop(slot)
                        break
            vis = user["stats"]["visible"]
            vis["physique_hp"] = max(1, vis["max_hp"] // 4)
            user["progress"]["failures_annihilation"] += 1
            recompute_visible_stats(user)
            detail = {"shattered": shattered}
        return detail

    @staticmethod
    def _reveal_stats_if_due(user: dict) -> None:
        """Hidden-stat reveal conditions (doc §4.2)."""
        realm = user["cultivation"]["current_realm_index"]
        hidden = user["stats"]["hidden"]
        from .constants import CHARISMA_REVEAL_REALM, DAOHEART_REVEAL_REALM, LUCK_REVEAL_REALM
        knows_starwatch = user["cultivation"].get("active_method_id") == "method_star_field"
        has_eye = (user["equipment"].get("accessory") or {}).get("id") == "acc_karma_cleansing"
        if realm >= LUCK_REVEAL_REALM or knows_starwatch or has_eye:
            hidden["karmic_luck_revealed"] = True
        if realm >= CHARISMA_REVEAL_REALM:
            hidden["dao_affinity_revealed"] = True
        if realm >= DAOHEART_REVEAL_REALM:
            hidden["dao_heart_revealed"] = True

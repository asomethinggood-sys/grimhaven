"""Part 3 §1 — Offline Cultivation Adventure Engine.

Turns a bare `+500 Qi` claim into an atmospheric chronicle: 1–4 procedural
events sampled from data/offline_events.json by elapsed time, spread over
simulated HH:MM timestamps, each mutating the save atomically.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

from ..core.data_loader import OfflineEvent, data_registry
from ..engine.models import add_item, parse_iso, ring_has_room, stage_cost
from ..localization import t


class OfflineAdventureEngine:

    @staticmethod
    def calculate_event_count(elapsed_hours: float) -> int:
        if elapsed_hours < 1.0:
            return 1
        if elapsed_hours < 4.0:
            return 2
        if elapsed_hours < 8.0:
            return 3
        return 4

    @staticmethod
    def sample_events(count: int, rng: random.Random,
                      pool: list[OfflineEvent] | None = None) -> list[OfflineEvent]:
        events = pool if pool is not None else data_registry.get_all_events()
        return rng.sample(events, min(count, len(events)))

    @staticmethod
    def generate_simulated_timeline(start: dt.datetime, end: dt.datetime,
                                    count: int) -> list[dt.datetime]:
        total_seconds = max(120.0, (end - start).total_seconds())
        slice_len = total_seconds / max(1, count)
        stamps = []
        for i in range(count):
            offset = random.uniform(0.1, 0.9) * slice_len if count > 1 else random.uniform(0.25, 0.75) * slice_len
            stamps.append(start + dt.timedelta(seconds=i * slice_len + offset))
        return sorted(stamps)

    @classmethod
    def process_meditation_claim(cls, user: dict, now: dt.datetime,
                                 base_report: dict, world_boost: float = 1.0,
                                 rng: random.Random | None = None,
                                 start: dt.datetime | None = None) -> tuple[str, dict]:
        """Roll idle-adventure events for the meditation just claimed, apply the
        effects to the user document and render the bilingual chronicle.

        `base_report` is the settlement result from CultivationEngine (qi/rate/time).
        """
        rng = rng or random
        lang = user["account"]["language"]
        cul = user["cultivation"]
        start = start or parse_iso(cul.get("meditation_started_at")) \
            or parse_iso(cul.get("last_afk_timestamp")) or now
        end = now
        elapsed = max(0.0, (end - start).total_seconds() / 3600.0)

        count = cls.calculate_event_count(elapsed)
        events = cls.sample_events(count, rng)
        timestamps = cls.generate_simulated_timeline(start, end, len(events))

        hidden = user["stats"]["hidden"]
        luck = hidden["karmic_luck"]

        bonus_qi = 0
        bonus_stones = 0
        dao_delta = 0
        hp_delta = 0
        loot: list[str] = []
        lost_to_ring = 0
        log_lines: list[str] = []

        for evt, ts in zip(events, timestamps):
            eff = evt.effect
            multiplier = 1.0
            flavor_note = ""
            # karmic windfall: high-luck cultivators sometimes double the harvest
            if evt.type in ("insight", "nature", "combat") and luck > 70 and rng.uniform(0, 100) < 8:
                multiplier = 2.0
                flavor_note = " " + t(lang, "ADVENTURE_LUCKY")
            if eff.bonus_qi:
                bonus_qi += int(eff.bonus_qi * multiplier)
            if eff.spirit_stones:
                bonus_stones += int(eff.spirit_stones * multiplier)
            if eff.dao_heart:
                dao_delta += int(eff.dao_heart * multiplier)
            if eff.hp:
                hp_delta += int(eff.hp * multiplier)
            item_label = ""
            if eff.item_id:
                item = data_registry.get_consumable(eff.item_id) or data_registry.get_equipment(eff.item_id)
                if item and ring_has_room(user, eff.item_id):
                    add_item(user, eff.item_id)
                    item_label = t(lang, "ADVENTURE_ITEM", item=item.name_for(lang))
                    user["progress"]["encounters_found"] = user["progress"].get("encounters_found", 0) + 1
                else:
                    lost_to_ring += 1
                    item_label = " " + t(lang, "ADVENTURE_RING_FULL")
            text = evt.text_for(lang)
            stamp = ts.strftime("%H:%M")
            log_lines.append(t(lang, "ADVENTURE_LINE", stamp=stamp, icon=evt.icon,
                               text=text, note=(flavor_note + item_label).strip()))

        # apply the harvest atomically to the document
        gained_details: dict[str, Any] = {"events": [e.id for e in events]}
        cap = stage_cost(user)
        if bonus_qi:
            room = max(0, cul["qi_capacity"] - cul["qi_current"])
            added = min(max(0, bonus_qi), room)
            cul["qi_current"] += added
            user["progress"]["qi_total_accumulated"] += added
            gained_details["bonus_qi"] = added
        if bonus_stones:
            user["inventory"]["spirit_stones"]["low"] += bonus_stones
            gained_details["bonus_stones"] = bonus_stones
        if dao_delta:
            hidden["dao_heart_stability"] = max(0, min(100, hidden["dao_heart_stability"] + dao_delta))
            gained_details["dao_delta"] = dao_delta
        if hp_delta:
            vis = user["stats"]["visible"]
            vis["physique_hp"] = max(1, min(vis["max_hp"], vis["physique_hp"] + hp_delta))
            gained_details["hp_delta"] = hp_delta
        if events:
            user["progress"]["adventures"] = user["progress"].get("adventures", 0) + 1
        gained_details.update({"lost_to_ring": lost_to_ring,
                              "event_count": len(events)})

        hours = int((end - start).total_seconds() // 3600)
        minutes = int((end - start).total_seconds() % 3600 // 60)
        report = cls._format_report(lang, hours, minutes, log_lines, base_report, gained_details, loot)
        return report, gained_details

    @staticmethod
    def _format_report(lang: str, hours: int, minutes: int, log_lines: list[str],
                       base_report: dict, gained: dict, loot: list[str]) -> str:
        qi_total = int(base_report.get("gained", 0)) + int(gained.get("bonus_qi", 0))
        stones = int(base_report.get("stones_found", 0)) + int(gained.get("bonus_stones", 0))
        dao = gained.get("dao_delta", 0)
        hp = gained.get("hp_delta", 0)
        overflow = int(base_report.get("overflow", 0))
        body = "\n".join(log_lines) if log_lines else t(lang, "ADVENTURE_QUIET")
        extras = []
        if dao:
            extras.append(t(lang, "ADVENTURE_DAO", value=("+" if dao > 0 else "") + str(dao)))
        if hp:
            extras.append(t(lang, "ADVENTURE_HP", value=("+" if hp > 0 else "") + str(hp)))
        if gained.get("lost_to_ring"):
            extras.append(t(lang, "ADVENTURE_RING_FULL_NOTE", n=gained["lost_to_ring"]))
        if overflow:
            extras.append(t(lang, "AFK_OVERFLOW", qi=overflow))
        return t(lang, "ADVENTURE_REPORT",
                 hours=hours, minutes=minutes, qi=qi_total, stones=stones,
                 rate=round(base_report.get("rate", 0)),
                 body=body, extras="\n".join(extras) if extras else "",
                 sealed=t(lang, "AFK_SEALED") if base_report.get("status") == "SEALED" else "")

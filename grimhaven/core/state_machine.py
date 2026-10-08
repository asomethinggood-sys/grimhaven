"""UserStatus FSM — the single source of truth for what a player may do.

Part 1 of the overhaul (state-machine concurrency guard). The user document
carries `status` at the top level; the legacy `cultivation.meditating` flag is
kept in sync by `set_status()` so older code paths keep working.
"""
from __future__ import annotations

import datetime as dt
import enum
from typing import Any


class UserStatus(str, enum.Enum):
    IDLE = "idle"                            # free to roam, fight, shop
    MEDITATING = "meditating"                # locked in cave, generating Qi
    IN_COMBAT = "in_combat"                  # locked in a live battle session
    SECLUSION = "seclusion_tribulation"      # locked in breakthrough seclusion
    HEAVILY_INJURED = "heavily_injured"      # recovery debuff state


VALID = {s.value for s in UserStatus}

# severe-meridian-injury debuff duration after a miracle escape (hours)
INJURY_HOURS = 3
# coma lock after a true death (minutes)
PARALYSIS_MINUTES = 45
# AFK-yield multiplier while injured / during the paralysis coma
INJURY_QI_MULT = 0.5


def get_status(user: dict) -> UserStatus:
    raw = user.get("status")
    if not raw:  # legacy docs without the field — derive
        cul = user.get("cultivation", {})
        if cul.get("meditating"):
            return UserStatus.MEDITATING
        if user.get("combat", {}).get("session"):
            return UserStatus.IN_COMBAT
        return UserStatus.IDLE
    try:
        return UserStatus(raw)
    except ValueError:
        return UserStatus.IDLE


def set_status(user: dict, status: UserStatus) -> None:
    user["status"] = status.value
    user.setdefault("cultivation", {})["meditating"] = status is UserStatus.MEDITATING


# ── debuffs & locks ───────────────────────────────────────────────────────────

def active_debuff(user: dict, now: dt.datetime | None = None) -> dict | None:
    """Severe-meridian-injury debuff while it is still running (auto-expires)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    deb = user.get("combat", {}).get("injury")
    if not deb:
        return None
    exp = deb.get("expires_at")
    try:
        if exp and dt.datetime.fromisoformat(exp) <= now:
            user["combat"]["injury"] = None
            return None
    except (TypeError, ValueError):
        return None
    return deb


def paralysis_active(user: dict, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    until = user.get("combat", {}).get("paralysis_until")
    if not until:
        return False
    try:
        return dt.datetime.fromisoformat(until) > now
    except (TypeError, ValueError):
        return False


def is_injured(user: dict, now: dt.datetime | None = None) -> bool:
    return get_status(user) is UserStatus.HEAVILY_INJURED and (
        active_debuff(user, now) is not None or paralysis_active(user, now)
    )


def qi_rate_multiplier(user: dict, now: dt.datetime | None = None) -> float:
    deb = active_debuff(user, now)
    if deb:
        return float(deb.get("qi_rate_multiplier", INJURY_QI_MULT))
    if paralysis_active(user, now):
        return 0.25  # comatose body barely draws breath
    return 1.0


def combat_penalties(user: dict, now: dt.datetime | None = None) -> tuple[float, float]:
    """(speed_mult, def_mult) from the injury debuff."""
    deb = active_debuff(user, now)
    if not deb:
        return 1.0, 1.0
    return (1.0 - float(deb.get("combat_speed_penalty", 0.0)),
            1.0 - float(deb.get("combat_def_penalty", 0.0)))

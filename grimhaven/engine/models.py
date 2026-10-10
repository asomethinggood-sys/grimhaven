"""User document model — mirrors the MongoDB/JSONB schema of design doc §10.1,
extended by the 3-part overhaul: FSM status, battle sessions, injury debuffs,
spatial-ring capacity, and data-driven combat stats.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from .constants import (
    BASE_RATES,
    DEMONIC_AFK_MULT_CAP,
    EQUIP_SLOTS,
    MAX_REALM,
    METHODS,
    REALM_ENTRY_SPIKES,
    REALM_STAGES,
)

UTC = dt.timezone.utc


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(ts: dt.datetime | None = None) -> str:
    return (ts or utcnow()).isoformat()


def parse_iso(value: Any) -> dt.datetime | None:
    """Parse a persisted timestamp, whatever shape it arrived in.

    Documents cross several schema revisions and an operator may have edited one
    by hand, so a timestamp can legitimately turn out to be ``None``, a string,
    a bare epoch number or even a ``datetime``.  Only ``ValueError`` used to be
    caught, so an int made every caller explode: ``settle_afk``, ``afk_hourly_rate``,
    the injury lock and the world boost all sit on this one function, and a crash
    in any of them took down *every* screen for that player.
    """
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def as_iso(value: Any) -> str | None:
    """Coerce any timestamp-ish value into the ISO string the schema stores."""
    parsed = parse_iso(value)
    return parsed.isoformat() if parsed is not None else None


# ── defensive accessors used by ensure_v2 ─────────────────────────────────────

def as_dict(value: Any, *, changes: list[str] | None = None,
            field: str = "") -> dict:
    """A mapping is required; anything else is replaced by an empty one."""
    if isinstance(value, dict):
        return value
    if changes is not None and value is not None and field:
        changes.append(f"{field}:{type(value).__name__}→object")
    return {}


def as_list(value: Any, *, changes: list[str] | None = None,
            field: str = "") -> list:
    if isinstance(value, list):
        return value
    if changes is not None and value is not None and field:
        changes.append(f"{field}:{type(value).__name__}→array")
    return []


_DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def as_int(value: Any, default: int = 0, minimum: int | None = None,
            maximum: int | None = None) -> int:
    """Best-effort integer.  Persian/Arabic digits and ``1،234`` groupings are
    accepted because the FA locale writes them back into documents through
    ``Locale.num``, and a display artifact must never become a crash."""
    out: Any = None
    if isinstance(value, bool):
        out = int(value)
    elif isinstance(value, (int, float)):
        out = value
    elif isinstance(value, str):
        text = value.translate(_DIGIT_FOLD).replace("،", "").replace(",", "").strip()
        try:
            out = float(text)
        except ValueError:
            out = None
    if out is None:
        out = default
    try:
        out = int(out)
    except (TypeError, ValueError, OverflowError):
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def as_float(value: Any, default: float = 0.0, minimum: float | None = None,
             maximum: float | None = None) -> float:
    out: Any = None
    if isinstance(value, bool):
        out = float(value)
    elif isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        text = value.translate(_DIGIT_FOLD).replace("،", "").replace(",", "").strip()
        try:
            out = float(text)
        except ValueError:
            out = None
    if out is None:
        out = default
    if out != out or out in (float("inf"), float("-inf")):      # NaN / infinity
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "✔")
    return bool(value)


def as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


# ── new schema ───────────────────────────────────────────────────────────────
BASE_RING_CAPACITY = 12

START_INVENTORY = {
    "spirit_stones": {"low": 30, "mid": 0, "high": 0, "heavenly": 0},
    "methods": ["method_breath_mortal", "method_sky_cleaving"],
    "arts": ["art_moonlight_sword"],          # every mortal starts on the moonlight path
    "items": {"pill_guardian": 1, "talisman_gale_blade": 2},
    "gear": {},                               # gear_id -> {"dur": 100}
}


def new_user_doc(user_id: int, username: str = "", language: str = "fa") -> dict:
    """Brand-new cultivator: Qi Condensation, layer 1, empty Dantian."""
    doc = {
        "_id": f"tg_user_{user_id}",
        "user_id": user_id,
        "status": "idle",
        "account": {
            "username": username or f"cultivator_{user_id}",
            "language": language,
            "is_banned": False,
            "registered_at": iso(),
            "is_admin": False,
        },
        "cultivation": {
            "current_realm_index": 1,
            "current_stage": 0,
            "qi_current": 0,
            "qi_capacity": REALM_STAGES[1][0],
            "dao_path": None,
            "alignment": "orthodox",
            "alignment_locked": False,
            "active_method_id": "method_breath_mortal",
            "active_stone": None,
            "active_pill": None,
            "meditating": False,
            "meditation_started_at": None,
            "seclusion_finish_time": None,
            "seclusion_target": None,
            "meridian_sealed_until": None,
            "last_afk_timestamp": iso(),
        },
        "stats": {
            "visible": {
                "physique_hp": 100,
                "max_hp": 100,
                "spiritual_sense": 10,
                "circulation_velocity": 10,
            },
            "hidden": {
                "karmic_luck": 50,
                "karmic_luck_revealed": False,
                "dao_affinity_charisma": 30,
                "dao_affinity_revealed": False,
                "dao_heart_stability": 70,
                "demonic_corruption": 0,
            },
        },
        "inventory": copy.deepcopy(START_INVENTORY),
        "equipment": {slot: None for slot in EQUIP_SLOTS},
        "combat": {
            # deck builder: technique ids, grown up to max_slots (3 → 6)
            "loadout": ["moon_slash", "lunar_mist", "zenith_eclipse"],
            "session": None,
            "injury": None,           # {debuff_id, expires_at, ...}
            "paralysis_until": None,  # coma lock after a true death
            "wins": 0,
            "losses": 0,
            "item_slots": [],          # battle keyboard row-4 consumables (max 2)
        },
        "location": {
            "current_zone_id": "zone_valley_mortals",
            "zone_id": "zone_valley_mortals",
            "vein_density": 1.0,
            "density": 1.0,
            "name": "دره فانی‌ها",
            "name_en": "Valley of Mortals",
            "since": iso(),
            "sect_id": None,
        },
        "ui": {
            "active_menu_message_id": None,   # single-window lifecycle (P1)
            "bag_tab": "gear",
            "bag_page": 1,
            "map_page": 1,
            "shop_page": 1,
        },
        "buffs": [],
        "progress": {
            "qi_total_accumulated": 0,
            "breakthrough_attempts": 0,
            "breakthrough_successes": 0,
            "failures_minor": 0,
            "failures_deviation": 0,
            "failures_annihilation": 0,
            "encounters_found": 0,
            "root_ancient_used": False,
            "deaths": 0,
            "miracle_escapes": 0,
            "adventures": 0,
            "last_gather_at": None,
        },
    }
    return doc


#: every status value the FSM knows; anything else is repaired to "idle"
_VALID_STATUS = {"idle", "meditating", "in_combat", "seclusion_tribulation",
                 "heavily_injured"}
_STONE_GRADES = ("low", "mid", "high", "heavenly")
_LANGS = ("fa", "en")
_ALIGNMENTS = ("orthodox", "demonic")


def ensure_v2(doc: dict, changes: list[str] | None = None) -> dict:
    """Idempotent, *validating* migration for every stored document.

    Two jobs, in this order:

    1. **repair** anything the schema cannot hold — a scalar where an object
       belongs, a list of strings where the buff engine expects objects, a bare
       epoch number where an ISO timestamp belongs, an out-of-range realm index;
    2. **backfill** every field the engines and renderers touch, so a partial
       document can never raise mid-update.

    Before this existed the "repair" half was absent: ``doc.setdefault(...)``
    happily returns the scalar it found, and the first ``.get`` on it raised
    ``AttributeError`` inside the *guard* or the *settlement* step — which the
    handlers turned into ERR_UNKNOWN on every single button, for that player,
    forever, because the malformed row was never rewritten.  ``changes`` lets a
    caller persist (and log) exactly what was repaired.
    """
    if not isinstance(doc, dict):
        raise TypeError(f"user document must be an object, got {type(doc).__name__}")
    from ..core.data_loader import data_registry   # zone/technique validation
    log = changes if changes is not None else []

    def note(msg: str) -> None:
        if msg not in log:
            log.append(msg)

    def obj(field: str, *, container: dict | None = None) -> dict:
        box = doc if container is None else container
        value = box.get(field)
        if isinstance(value, dict):
            return value
        if value is not None:
            note(f"{field}:{type(value).__name__}→object")
        box[field] = {}
        return box[field]

    def arr(field: str, *, container: dict) -> list:
        value = container.get(field)
        if isinstance(value, list):
            return value
        if value is not None:
            note(f"{field}:{type(value).__name__}→array")
        container[field] = []
        return container[field]

    def keep(box: dict, key: str, value: Any, *, label: str | None = None) -> None:
        """Write a repaired scalar and report it when the bytes really changed.

        Silent scalar repairs are the second-worst thing a migration can do: the
        document looks fine in memory, nothing is written back, and the next tap
        reads the same junk.  ``ui.active_menu_message_id = "77"`` is the case
        that mattered — the root-window delete needs a real integer id.
        """
        raw = box.get(key)
        box[key] = value
        if raw is not None and raw != value:
            note(f"{label or key}:{raw!r}→{value!r}")

    def iso_field(box: dict, key: str, *, label: str | None = None,
                  fallback: str | None = None) -> None:
        """Normalize one timestamp — and *report* it when the stored bytes were
        not already that exact string.

        A bare epoch number (or a ``Z``-suffixed offset) parses fine, so the old
        code silently accepted it and moved on. But renderers and the admin HUD
        read the raw stored value, and an unreported repair is never written back:
        the row stays broken for the next tap. Anything that changes the bytes
        must therefore land in ``changes``.
        """
        raw = box.get(key)
        value = as_iso(raw)
        label = label or key
        if value is None:
            if raw is not None:
                note(f"{label} was not a timestamp")
            value = fallback
        box[key] = value
        if raw is not None and value is not None and raw != value:
            note(f"{label} normalized to {value!r}")

    # ── status ──
    raw_status = doc.get("status")
    if not isinstance(raw_status, str) or raw_status not in _VALID_STATUS:
        if raw_status is not None:
            note(f"status:{raw_status!r}→idle")
        doc["status"] = "idle"

    # ── account ──
    acc = obj("account")
    acc["username"] = as_str(acc.get("username"), f"cultivator_{doc.get('user_id', 0)}")
    if acc.get("language") not in _LANGS:
        if acc.get("language") is not None:
            note(f"account.language:{acc.get('language')!r}→fa")
        acc["language"] = "fa"
    acc["is_banned"] = as_bool(acc.get("is_banned"))
    acc["is_admin"] = as_bool(acc.get("is_admin"))
    iso_field(acc, "registered_at", label="account.registered_at", fallback=iso())

    # ── cultivation ──
    cul = obj("cultivation")
    realm = as_int(cul.get("current_realm_index"), 1, minimum=1, maximum=MAX_REALM)
    if cul.get("current_realm_index") != realm:
        note(f"cultivation.current_realm_index:{cul.get('current_realm_index')!r}→{realm}")
    cul["current_realm_index"] = realm
    layers = len(REALM_STAGES[realm])
    stage = as_int(cul.get("current_stage"), 0, minimum=0, maximum=layers - 1)
    if cul.get("current_stage") != stage:
        note(f"cultivation.current_stage:{cul.get('current_stage')!r}→{stage}")
    cul["current_stage"] = stage
    capacity = as_int(cul.get("qi_capacity"), 0, minimum=1)
    if capacity <= 0:
        capacity = REALM_STAGES[realm][stage] or 1
        note("cultivation.qi_capacity→realm table")
    cul["qi_capacity"] = capacity
    qi = as_int(cul.get("qi_current"), 0, minimum=0)
    if qi != as_int(cul.get("qi_current"), -1):
        note("cultivation.qi_current coerced")
    cul["qi_current"] = min(qi, capacity)
    cul["alignment"] = cul.get("alignment") if cul.get("alignment") in _ALIGNMENTS \
        else "orthodox"
    if not isinstance(cul.get("alignment_locked"), bool):
        cul["alignment_locked"] = as_bool(cul.get("alignment_locked"))
    cul["dao_path"] = cul.get("dao_path") if isinstance(cul.get("dao_path"), str) else None
    for key, default in (("active_method_id", "method_breath_mortal"),
                         ("active_pill", None), ("active_stone", None)):
        value = cul.get(key)
        if value is not None and not isinstance(value, str):
            note(f"cultivation.{key} coerced")
            value = None
        if value is None and default is not None and not cul.get(key):
            value = default
        if key == "active_stone" and value not in _STONE_GRADES:
            value = None
        cul[key] = value
    if not cul.get("active_method_id"):
        cul["active_method_id"] = "method_breath_mortal"
    cul["meditating"] = as_bool(cul.get("meditating"))
    for key in ("meditation_started_at", "seclusion_finish_time",
                "meridian_sealed_until", "last_afk_timestamp"):
        iso_field(cul, key, label=f"cultivation.{key}")
    cul["last_afk_timestamp"] = cul["last_afk_timestamp"] or iso()
    if cul.get("seclusion_target") is not None:
        cul["seclusion_target"] = as_int(cul.get("seclusion_target"), 0, minimum=0)
    if cul.get("current_realm_index") >= MAX_REALM and not cul.get("seclusion_target"):
        cul["seclusion_target"] = None

    # ── stats ──
    stats = obj("stats")
    vis = obj("visible", container=stats)
    hid = obj("hidden", container=stats)
    vis["max_hp"] = as_int(vis.get("max_hp"), 100, minimum=1)
    vis["physique_hp"] = as_int(vis.get("physique_hp"), vis["max_hp"], minimum=1,
                                maximum=vis["max_hp"])
    vis["spiritual_sense"] = as_int(vis.get("spiritual_sense"), 10, minimum=0)
    vis["circulation_velocity"] = as_int(vis.get("circulation_velocity"), 10, minimum=0)
    hid["karmic_luck"] = as_int(hid.get("karmic_luck"), 50, minimum=0, maximum=100)
    hid["dao_affinity_charisma"] = as_int(hid.get("dao_affinity_charisma"), 30,
                                          minimum=0, maximum=100)
    hid["dao_heart_stability"] = as_int(hid.get("dao_heart_stability"), 70,
                                       minimum=0, maximum=100)
    hid["demonic_corruption"] = as_int(hid.get("demonic_corruption"), 0,
                                       minimum=0, maximum=100)
    for key in ("karmic_luck_revealed", "dao_affinity_revealed"):
        hid[key] = as_bool(hid.get(key))

    # ── buffs: a list of objects, nothing else ──
    # Every consumer (afk_hourly_rate, hub_state, catalyst_text, the settlement
    # sweep) does ``buff.get(...)``; one bare string in this list used to crash
    # the *idle accrual* step, i.e. every screen at once.
    raw_buffs = doc.get("buffs")
    if isinstance(raw_buffs, dict):
        entries = [raw_buffs]                    # single buff written as an object
        note("buffs:object→array")
    elif isinstance(raw_buffs, list):
        entries = raw_buffs
    else:
        entries = []
        if raw_buffs is not None:
            note(f"buffs:{type(raw_buffs).__name__}→array")
    buffs: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            note(f"buffs: dropped non-object entry ({type(entry).__name__})")
            continue
        # every entry — whatever shape it arrived in — goes through the validator,
        # and a buff that cannot expire is *removed*, not kept as an empty stub:
        # a stub is re-noticed on every load, so the row is rewritten forever.
        buff = _ensure_buff(entry, note)
        if buff:
            buffs.append(buff)
    doc["buffs"] = buffs

    # ── inventory ──
    inv = obj("inventory")
    # setdefault semantics preserved from the old migration: a document that
    # never owned the starter art still gets it (the default deck depends on it)
    if "arts" not in inv:
        inv["arts"] = ["art_moonlight_sword"]
    if "methods" not in inv:
        inv["methods"] = ["method_breath_mortal", "method_sky_cleaving"]
    inv["arts"] = [x for x in arr("arts", container=inv) if isinstance(x, str)]
    inv["methods"] = [x for x in arr("methods", container=inv) if isinstance(x, str)]
    if not inv["methods"]:
        inv["methods"] = ["method_breath_mortal"]
    gear = obj("gear", container=inv)
    for gid, g in list(gear.items()):
        if not isinstance(g, dict):
            gear[gid] = {"dur": 100}
            note(f"inventory.gear.{gid}: scalar → dur object")
            continue
        g["dur"] = as_int(g.get("dur"), 100, minimum=0, maximum=100)
    items = obj("items", container=inv)
    for iid, qty in list(items.items()):
        n = as_int(qty, 0, minimum=0)
        if n != qty:
            note(f"inventory.items.{iid}: {qty!r}→{n}")
        if n <= 0:
            items.pop(iid, None)
        else:
            items[iid] = n
    stones = obj("spirit_stones", container=inv)
    for grade in _STONE_GRADES:
        n = as_int(stones.get(grade), 0, minimum=0)
        if stones.get(grade) != n:
            note(f"inventory.spirit_stones.{grade} coerced")
        stones[grade] = n
    # merge legacy pills/herbs dicts into the unified item ledger
    for pid, qty in (as_dict(inv.get("pills")) or {}).items():
        n = as_int(qty, 0, minimum=0)
        if n and pid not in items:
            items[pid] = n
    for hid_key, qty in (as_dict(inv.get("herbs")) or {}).items():
        if hid_key == "root_used":
            if as_bool(qty):
                obj("progress")["root_ancient_used"] = True
            continue
        n = as_int(qty, 0, minimum=0)
        if n and hid_key not in items:
            items[hid_key] = n
    for legacy in ("pills", "herbs", "techniques"):
        inv.pop(legacy, None)

    # ── equipment ──
    eq = obj("equipment")
    for slot in EQUIP_SLOTS:
        piece = eq.get(slot)
        if piece is None:
            eq[slot] = None
        elif isinstance(piece, dict):
            piece["dur"] = as_int(piece.get("dur"), 100, minimum=0, maximum=100)
        else:
            eq[slot] = None
            note(f"equipment.{slot}: non-object piece dropped")

    # ── combat ──
    cmb = obj("combat")
    session = cmb.get("session")
    if session is not None and not isinstance(session, dict):
        cmb["session"] = None
        note("combat.session: scalar → null")
    elif isinstance(session, dict):
        session["round"] = as_int(session.get("round"), 1, minimum=1)
        session["turn_lock"] = as_int(session.get("turn_lock"), session["round"] - 1)
        session["finished"] = as_bool(session.get("finished"))
        for side in ("player", "enemy"):
            if session.get(side) is not None and not isinstance(session[side], dict):
                session[side] = {}
                note(f"combat.session.{side}: scalar → object")
    injury = cmb.get("injury")
    if injury is not None and not isinstance(injury, dict):
        cmb["injury"] = None
        note("combat.injury: scalar → null")
    elif isinstance(injury, dict):
        iso_field(injury, "expires_at", label="combat.injury.expires_at")
    iso_field(cmb, "paralysis_until", label="combat.paralysis_until")
    cmb["wins"] = as_int(cmb.get("wins"), 0, minimum=0)
    cmb["losses"] = as_int(cmb.get("losses"), 0, minimum=0)
    loadout = [x for x in arr("loadout", container=cmb) if isinstance(x, str)]
    # drop legacy placeholder techniques that no longer exist in the data set —
    # but only when the data set is actually loaded, otherwise "unknown" means
    # nothing and every player's loadout would be erased
    content_ready = data_registry.is_loaded()
    kept = ([tid for tid in loadout if tid in data_registry.techniques_index][:6]
            if content_ready else loadout[:6])
    if kept != loadout:
        note("combat.loadout: unknown technique ids dropped")
    cmb["loadout"] = kept
    if not kept and "art_moonlight_sword" in inv.get("arts", []):
        cmb["loadout"] = ["moon_slash", "lunar_mist", "zenith_eclipse"]
    item_slots = [x for x in arr("item_slots", container=cmb) if isinstance(x, str)]
    cmb["item_slots"] = item_slots[:2]

    # ── progress ──
    prog = obj("progress")
    for key, default in (("qi_total_accumulated", 0), ("breakthrough_attempts", 0),
                         ("breakthrough_successes", 0), ("failures_minor", 0),
                         ("failures_deviation", 0), ("failures_annihilation", 0),
                         ("encounters_found", 0), ("deaths", 0),
                         ("miracle_escapes", 0), ("adventures", 0)):
        prog[key] = as_int(prog.get(key), default, minimum=0)
    prog["root_ancient_used"] = as_bool(prog.get("root_ancient_used"))
    iso_field(prog, "last_gather_at", label="progress.last_gather_at")

    # ── spec P3: zone renames → data/zones.json ids (one-time alias migration)
    _ZONE_ALIASES = {"zone_mortal_valley": "zone_valley_mortals",
                     "zone_common_cave": "zone_ordinary_cave",
                     "zone_misty_peak": "zone_mist_peak",
                     "zone_heaven_spring": "zone_heavenly_spring"}
    loc = obj("location")
    zid = as_str(loc.get("current_zone_id")) or as_str(loc.get("zone_id")) \
        or "zone_valley_mortals"
    zid = _ZONE_ALIASES.get(zid, zid)
    zdef = data_registry.get_zone(zid) or data_registry.get_zone("zone_valley_mortals")
    if content_ready and not data_registry.get_zone(zid):
        note(f"location.current_zone_id:{zid} → {zdef.zone_id if zdef else zid}")
        zid = zdef.zone_id if zdef else "zone_valley_mortals"
    loc["current_zone_id"] = loc["zone_id"] = zid
    if zdef:
        loc.setdefault("name", zdef.name)
        loc.setdefault("name_en", zdef.name_en)
        # vein_density is the LIVE field (travel/conquest update it); the legacy
        # density mirror must follow it — never let a stale value win.
        vd_raw = loc.get("vein_density")
        if vd_raw is None:
            vd_raw = loc.get("density")
        vd = as_float(vd_raw, float(zdef.density), minimum=0.1, maximum=10.0)
        if vd != vd_raw:
            note("location.vein_density coerced to a number")
        loc["vein_density"] = vd
        loc["density"] = vd
    iso_field(loc, "since", label="location.since", fallback=iso())
    loc["sect_id"] = as_str(loc.get("sect_id")) or None

    # ── ui ──
    ui = obj("ui")
    anchor = ui.get("active_menu_message_id")
    if anchor in (None, ""):
        keep(ui, "active_menu_message_id", None)
    elif as_int(anchor, 0) <= 0:
        note(f"ui.active_menu_message_id:{anchor!r} dropped")
        keep(ui, "active_menu_message_id", None)
    else:
        keep(ui, "active_menu_message_id", as_int(anchor, 0))
    if ui.get("bag_tab") not in ("gear", "consumables", "materials"):
        keep(ui, "bag_tab", "gear")
    for key in ("bag_page", "map_page", "shop_page"):
        keep(ui, key, as_int(ui.get(key), 1, minimum=1))
    for key in ("mantra_back",):
        if ui.get(key) is not None and not isinstance(ui.get(key), str):
            ui[key] = None

    # ── derived consistency ──
    if cul["meditating"] and doc["status"] == "idle":
        doc["status"] = "meditating"
    if doc["status"] == "in_combat" and not isinstance(cmb.get("session"), dict):
        doc["status"] = "idle"
        note("combat.session gone but status said in_combat → idle")
    if doc["status"] == "meditating" and not cul["meditating"]:
        doc["status"] = "idle"
    doc["_id"] = as_str(doc.get("_id"), f"tg_user_{doc.get('user_id', 0)}")
    return doc


def _ensure_buff(buff: dict, note) -> dict:
    """Validate one buff object; the return value is always safe to ``.get``."""
    out: dict[str, Any] = {}
    for key in ("id", "key", "kind"):
        if isinstance(buff.get(key), str):
            out[key] = buff[key]
    raw_until = buff.get("until")
    until = as_iso(raw_until)
    if raw_until is not None and until is None:
        note(f"buff {buff.get('id') or buff.get('key')}: bad 'until' dropped")
    elif until is not None and raw_until != until:
        # a bare epoch number is readable here but not in the HUD — rewrite the row
        note(f"buff {buff.get('id') or buff.get('key') or buff.get('kind')}: "
             f"'until' normalized to {until!r}")
    if until is None:
        note("buff without a usable 'until' dropped (cannot expire)")
        return {}
    out["until"] = until
    if buff.get("boost") is not None:
        out["boost"] = as_float(buff.get("boost"), 0.0, minimum=-1.0, maximum=10.0)
    if buff.get("rate_mult") is not None:
        out["rate_mult"] = as_float(buff.get("rate_mult"), 1.0, minimum=0.05, maximum=10.0)
    return out


def active_buffs(user: dict, now: dt.datetime | None = None,
                 *, kinds: tuple[str, ...] | None = None) -> list[dict]:
    """The live, *validated* buffs on a document.

    Every engine/renderer that touches ``user["buffs"]`` must go through this:
    a hand-edited or stale document may hold a string, a null or a buff without
    an ``until``, and ``.get`` on those used to abort the whole tap.
    """
    now = now or utcnow()
    out: list[dict] = []
    raw = user.get("buffs") if isinstance(user, dict) else None
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        until = parse_iso(entry.get("until"))
        if until is None or until <= now:
            continue
        if kinds and str(entry.get("kind") or "") not in kinds:
            continue
        out.append(entry)
    return out


def buff_rate_multiplier(user: dict, now: dt.datetime | None = None) -> float:
    """Product of the active ``rate_mult`` debuffs (1.0 when none are live)."""
    mult = 1.0
    for buff in active_buffs(user, now):
        if buff.get("rate_mult") is None:
            continue
        mult *= as_float(buff.get("rate_mult"), 1.0, minimum=0.0, maximum=10.0)
    return mult


def buff_catalyst(user: dict, now: dt.datetime | None = None) -> float:
    """Summed ``boost`` of the live buffs, the form the AFK formula wants."""
    total = 0.0
    for buff in active_buffs(user, now):
        if buff.get("rate_mult") is not None and buff.get("boost") is None:
            continue
        total += as_float(buff.get("boost"), 0.0)
    return total



# ── derived stats ────────────────────────────────────────────────────────────

LIFESPANS: dict[int, int] = {1: 80, 2: 120, 3: 200, 4: 350, 5: 600, 6: 1000, 7: 1800}


def meridians_opened(user: dict) -> int:
    """8 mortal meridians; realms + stages unseal them."""
    cul = user["cultivation"]
    return min(8, cul["current_realm_index"] + cul["current_stage"] // 3)


def lifespan_years(user: dict) -> int:
    cul = user["cultivation"]
    base = LIFESPANS.get(cul["current_realm_index"], 80)
    bonus = int(user["stats"]["hidden"].get("dao_heart_stability", 50) / 25)
    return base + bonus * 5


def qi_purity(user: dict) -> int:
    """0–100 refinement of the circulating Qi (active mantra drives it)."""
    from ..core.data_loader import data_registry
    cul = user["cultivation"]
    m = data_registry.get_method(cul.get("active_method_id") or "")
    mult = m.qi_mult if m else 1.0
    return max(1, min(100, int(40 + mult * 12 + cul["current_realm_index"] * 3)))


def crit_chance(user: dict) -> float:
    """Percent crit chance — same formula the combat resolver uses."""
    prof = combat_profile(user)
    return float(max(5.0, min(60.0, prof["divine_sense"] * 0.5 + prof["crit_bonus"] * 100)))


def comprehension(user: dict) -> int:
    vis = user["stats"]["visible"]
    hidden = user["stats"]["hidden"]
    return int(vis.get("spiritual_sense", 10) * 1.5 + hidden.get("dao_heart_stability", 50) * 0.2
               + user["cultivation"]["current_realm_index"] * 3)



def stage_cost(user: dict) -> int:
    cul = user["cultivation"]
    realm = cul["current_realm_index"]
    stage = cul["current_stage"]
    stages = REALM_STAGES[realm]
    return stages[min(stage, len(stages) - 1)]


def hp_multiplier(user: dict) -> float:
    realm = user["cultivation"]["current_realm_index"]
    mult = 1.0
    for r in range(2, realm + 1):
        mult *= REALM_ENTRY_SPIKES.get(r, (1.0, 1.0))[0]
    return mult


def gear_durability(gear: dict | None) -> float:
    if not gear:
        return 0.0
    return max(0.1, min(1.0, float(gear.get("dur", 100)) / 100.0))


def gear_bonus(user: dict, field: str) -> float:
    """Sum a numeric field across equipped items from data/equipment.json."""
    from ..core.data_loader import data_registry
    total = 0.0
    for slot, gear in (user.get("equipment") or {}).items():
        if not gear:
            continue
        item = data_registry.get_equipment(gear.get("id", ""))
        if not item:
            continue
        total += float(getattr(item, field, 0) or 0) * gear_durability(gear)
    return total


def gear_specials(user: dict) -> set[str]:
    from ..core.data_loader import data_registry
    out = set()
    for slot, gear in (user.get("equipment") or {}).items():
        if not gear:
            continue
        item = data_registry.get_equipment(gear.get("id", ""))
        if item and item.special_effect:
            out.add(item.special_effect)
    return out


def recompute_visible_stats(user: dict) -> None:
    """Recompute HP / sense / circulation from realm, stage, Dao and gear."""
    realm = user["cultivation"]["current_realm_index"]
    stage = user["cultivation"]["current_stage"]
    base_hp = 100.0 * hp_multiplier(user) * (1.0 + 0.10 * stage)
    dao = user["cultivation"].get("dao_path")
    if dao == "body":
        base_hp *= 1.5
    elif dao:
        from .constants import DAO_PATHS
        base_hp *= DAO_PATHS[dao]["hp"]
    gear_bonus_hp = 0.0
    for slot, gear in (user.get("equipment") or {}).items():
        if gear and slot in ("robe", "companion", "natal"):
            from .constants import TIER_POWER
            gear_bonus_hp += 0.05 * (1 + TIER_POWER[gear.get("tier", "mortal")])
    max_hp = int(base_hp * (1.0 + gear_bonus_hp)) + int(gear_bonus(user, "hp_bonus"))
    vis = user["stats"]["visible"]
    vis["max_hp"] = max(1, max_hp)
    vis["physique_hp"] = min(vis.get("physique_hp", vis["max_hp"]), vis["max_hp"]) or vis["max_hp"]
    vis["spiritual_sense"] = int(10 * (1.5 ** (realm - 1)) * (1 + 0.05 * stage))
    method = METHODS.get(user["cultivation"].get("active_method_id", ""), {})
    circ = int(10 + realm * 5 + stage + method.get("tech_mult", 1.0))
    circ += int(gear_bonus(user, "speed_bonus"))
    vis["circulation_velocity"] = circ


def afk_hourly_rate(user: dict, world_boost: float = 1.0,
                    now: dt.datetime | None = None) -> float:
    """Doc §5 master formula, plus the injury debuff multiplier."""
    now = now or utcnow()
    cul = user["cultivation"]
    rate_mult = 1.0
    realm = cul["current_realm_index"]
    base = BASE_RATES.get(realm, 100)
    from ..core.data_loader import data_registry
    _m = data_registry.get_method(cul.get("active_method_id", ""))
    tech = _m.qi_mult if _m else METHODS.get(cul.get("active_method_id", ""), {}).get("tech_mult", 1.0)
    # timed debuffs (e.g. inner-demon deviation) — active_buffs() ignores
    # malformed entries and already-expired records instead of raising
    rate_mult *= buff_rate_multiplier(user, now)

    catalyst = 0.0
    stone = cul.get("active_stone")
    if stone:
        from .constants import SPIRIT_STONES
        catalyst += as_float(SPIRIT_STONES[stone]["boost"], 0.0)
    if user.get("progress", {}).get("root_ancient_used"):
        catalyst += 0.25  # ancient ginseng permanently widens the channels
    catalyst += buff_catalyst(user, now)
    if cul["alignment"] == "demonic":
        catalyst = min(1.0 + catalyst, DEMONIC_AFK_MULT_CAP) - 1.0

    vein = as_float(user["location"].get("vein_density", 1.0), 1.0, minimum=0.1)
    luck_bonus = as_float(user["stats"]["hidden"].get("karmic_luck", 50), 50) * 0.002
    rate = base * tech * (1.0 + catalyst) * vein * rate_mult
    rate *= (1.0 + luck_bonus) * world_boost
    if _meridians_sealed(user, now):
        rate *= 0.5
    from ..core.state_machine import qi_rate_multiplier
    rate *= qi_rate_multiplier(user, now)
    return rate


def _meridians_sealed(user: dict, now: dt.datetime | None = None) -> bool:
    until = parse_iso(user["cultivation"].get("meridian_sealed_until"))
    return bool(until and until > (now or utcnow()))


def meridians_sealed(user: dict, now: dt.datetime | None = None) -> bool:
    return _meridians_sealed(user, now)


def in_seclusion(user: dict, now: dt.datetime | None = None) -> bool:
    finish = parse_iso(user["cultivation"].get("seclusion_finish_time"))
    return bool(finish and finish > (now or utcnow()))


def is_max_realm_final(user: dict) -> bool:
    cul = user["cultivation"]
    return cul["current_realm_index"] >= MAX_REALM and cul["current_stage"] >= len(REALM_STAGES[MAX_REALM]) - 1


# ── spatial ring capacity ───────────────────────────────────────────────────

def item_capacity(user: dict) -> int:
    cap = BASE_RING_CAPACITY + int(gear_bonus(user, "capacity_bonus"))
    return cap


def item_count(user: dict) -> int:
    inv = user["inventory"]
    return len([1 for q in inv.get("items", {}).values() if q > 0]) + len(inv.get("gear", {}))


def ring_has_room(user: dict, item_id: str) -> bool:
    if item_id in user["inventory"].get("items", {}) or item_id in user["inventory"].get("gear", {}):
        return True  # stacks are free once slotted
    return item_count(user) < item_capacity(user)


def add_item(user: dict, item_id: str, qty: int = 1, *, is_gear: bool = False) -> bool:
    """Deposit one item; returns False when the spatial ring is full."""
    inv = user["inventory"]
    if is_gear or (item_id in _gear_ids()):
        if item_id in inv["gear"]:
            inv["gear"][item_id] = {"dur": 100}
            return True
        if not ring_has_room(user, item_id):
            return False
        inv["gear"][item_id] = {"dur": 100}
        return True
    if item_id not in inv["items"] and not ring_has_room(user, item_id):
        return False
    inv["items"][item_id] = inv["items"].get(item_id, 0) + qty
    return True


def _gear_ids() -> set[str]:
    from ..core.data_loader import data_registry
    return set(data_registry.equipment.keys())


def consume_item(user: dict, item_id: str, qty: int = 1) -> bool:
    inv = user["inventory"]
    if inv["items"].get(item_id, 0) >= qty:
        inv["items"][item_id] -= qty
        if inv["items"][item_id] <= 0:
            inv["items"].pop(item_id, None)
        return True
    return False


# ── combat profile (Part 2 §1) ───────────────────────────────────────────────

def max_slots(user: dict) -> int:
    """Action deck capacity: 3 base, +1 Foundation, +1 Golden Core, +1 mind ring."""
    realm = user["cultivation"]["current_realm_index"]
    slots = 3
    if realm >= 2:
        slots += 1
    if realm >= 3:
        slots += 1
    if "slot_bonus_1" in gear_specials(user):
        slots += 1
    return min(slots, 6)


def combat_profile(user: dict, now: dt.datetime | None = None) -> dict:
    """Full attribute set that governs the Part-2 battle formulas."""
    from ..core.data_loader import data_registry
    cul = user["cultivation"]
    realm, stage = cul["current_realm_index"], cul["current_stage"]
    vis = user["stats"]["visible"]
    hidden = user["stats"]["hidden"]

    dao = cul.get("dao_path")
    atk_mult = 1.0
    crit_extra = 0.05
    if dao:
        from .constants import DAO_PATHS
        atk_mult *= DAO_PATHS[dao]["atk"]
        crit_extra += DAO_PATHS[dao]["crit"]

    spiritual_atk = int((8 * (1.7 ** (realm - 1)) + stage * 3) * atk_mult)
    physical_atk = int(spiritual_atk * (1.15 if dao == "body" else 0.95))
    spiritual_atk += int(gear_bonus(user, "spiritual_atk_bonus"))
    physical_atk += int(gear_bonus(user, "physical_atk_bonus"))

    physical_def = int(4 * (1.55 ** (realm - 1))) + int(gear_bonus(user, "physical_def_bonus"))
    spiritual_def = int(3 * (1.55 ** (realm - 1))) + int(gear_bonus(user, "spiritual_def_bonus"))
    if realm >= 3:
        physical_def = int(physical_def * 1.25)  # Golden Core: mortal blows shrug off
        spiritual_def = int(spiritual_def * 1.25)
    if dao == "body":
        physical_def = int(physical_def * 1.3)

    # injury penalties
    from ..core.state_machine import combat_penalties
    spd_mult, def_mult = combat_penalties(user, now)
    speed = int((vis["circulation_velocity"] + realm * 2) * spd_mult)
    physical_def = int(physical_def * def_mult)
    spiritual_def = int(spiritual_def * def_mult)

    divine_sense = vis["spiritual_sense"]
    weapon_crit = gear_bonus(user, "crit_rate_bonus")

    qi_dev_risk = max(0.0, (100 - hidden["dao_heart_stability"]) / 4.0)
    if "stabilize_5" in gear_specials(user):
        qi_dev_risk = max(0.0, qi_dev_risk - 5.0)

    return {
        "spiritual_atk": max(1, spiritual_atk),
        "physical_atk": max(1, physical_atk),
        "physical_def": max(0, physical_def),
        "spiritual_def": max(0, spiritual_def),
        "meridian_speed": max(1, speed),
        "divine_sense": divine_sense,
        "karmic_luck": hidden["karmic_luck"],
        "qi_deviation_risk": qi_dev_risk,
        "crit_bonus": weapon_crit,
        "hp": vis["physique_hp"],
        "max_hp": vis["max_hp"],
        "qi": int(cul["qi_current"]),
        "max_qi": int(cul["qi_capacity"]),
    }


def tribulation_mitigation(user: dict) -> float:
    """Sum of tribulation_mitigation_percent across gear (0..0.6 cap)."""
    return min(0.60, gear_bonus(user, "tribulation_mitigation_percent") / 100.0)

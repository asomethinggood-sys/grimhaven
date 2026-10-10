"""Round 7 — fault injection: every failure must land in the right layer and be
findable from one log line.

Each test breaks *one* dependency (poisoned row, impossible timestamp, a vault
that refuses to read or write, Telegram that refuses to deliver or edit) and
asserts three things:

1. which **stage** was running when it broke,
2. which **category** the taxonomy gave it,
3. what the **player** was actually told.

The log assertion is what makes a screenshot diagnosable: one line carrying
``incident=`` that ties the text on the player's screen to the traceback below it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3

import pytest

from grimhaven.bot.handlers import callbacks as cb
from grimhaven.bot.handlers import commands as cmd_h
from grimhaven.bot.handlers.common import Ctx
from grimhaven.bot.handlers.reporting import log_line
from grimhaven.core.middleware import guard_update
from grimhaven.core.data_loader import bootstrap as bootstrap_data
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import ensure_v2, new_user_doc, parse_iso, utcnow
from grimhaven.errors import (REPORTED, Category, ContentError, Notice,
                              StorageError, TelegramError, classify)
from grimhaven.localization import t
from tests.telegram_fakes import FakeBot, FakeContext, FakeUpdate


UID = 4242

# the validators rewrite stored ids against the shipped data sets
bootstrap_data()

def _tguser(uid: int):
    return type("U", (), {"id": uid, "username": f"soul{uid}", "first_name": "S"})()


#: how a state-guard refusal reaches the reporter (middleware returns a locale
#: key, the handler wraps it): an expected outcome, never an incident
GuardNotice = type("GuardNotice", (Notice,), {"category": Category.GUARD})


def _storage(tmp_path) -> Storage:
    storage = Storage(tmp_path / "fault.db")
    bootstrap_world(storage)
    return storage


# ── A. the taxonomy itself ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (StorageError("vault offline"), Category.STORAGE),
        (sqlite3.DatabaseError("database disk image is malformed"), Category.STORAGE),
        (TelegramError("telegram sendMessage failed"), Category.TELEGRAM),
        (Notice("busy", user_key="GUARD_COMBAT"), Category.DOMAIN),
        (GuardNotice("locked"), Category.GUARD),
        (ContentError("missing zone"), Category.CONTENT),
        (ValueError("boom"), Category.INTERNAL),
        (KeyError("attack"), Category.INTERNAL),
    ],
)
def test_classify_routes_each_failure_to_its_layer(exc, expected):
    assert classify(exc) is expected


def test_expected_outcomes_are_not_incidents():
    """A guard refusal must not be treated as a defect: no incident id, no
    traceback.  A real fault in any other layer must be."""
    assert Category.GUARD not in REPORTED and Category.DOMAIN not in REPORTED
    for cat in (Category.STORAGE, Category.TELEGRAM, Category.CONTENT,
                Category.INTERNAL):
        assert cat in REPORTED


def test_one_incident_reference_appears_in_exactly_one_log_line(caplog):
    from grimhaven.bot.handlers import reporting
    ref = "A1B2C3"
    line = log_line(stage="telegram-render", path="callback",
                    category=Category.TELEGRAM, ref=ref, update_id=9,
                    user_id=UID, action="map:view:world")
    with caplog.at_level(logging.WARNING, logger="grimhaven.bot"):
        logging.getLogger("grimhaven.bot.handlers.reporting").warning(line)
    hits = [r.getMessage() for r in caplog.records if ref in r.getMessage()]
    assert len(hits) == 1
    # incident + path + stage + category + who + what, all on that one line
    for token in ("failure path=callback", "stage=telegram-render",
                  "category=telegram", f"incident={ref}", "update_id=9",
                  f"user_id={UID}", "action='map:view:world'"):
        assert token in hits[0]
    assert "token" not in hits[0].lower()     # never the bot token, never the doc
    assert reporting.render_failed(Exception("Message is not modified")) is False


# ── B. malformed nested data must self-repair AND persist the repair ────────

#: every top-level section the schema stores as an object
MAPPING_SECTIONS = ["account", "cultivation", "combat", "inventory",
                    "equipment", "progress", "ui", "stats", "location"]


@pytest.mark.parametrize("section", MAPPING_SECTIONS)
def test_ensure_v2_repairs_a_scalar_section(section):
    """``setdefault`` returns the scalar it found — the origin of "a problem for
    every command". A scalar where an object belongs must be replaced **and**
    reported, so the caller can persist the fix."""
    doc = new_user_doc(UID, "soul", "fa")
    doc[section] = "left-over-scalar"
    changes: list[str] = []
    ensure_v2(doc, changes)
    assert isinstance(doc[section], dict), f"{section} must come back as an object"
    assert any(ch.startswith(f"{section}:") for ch in changes), \
        f"the repair of {section} must be reported: {changes}"


@pytest.mark.parametrize("broken", [
    {"combat": 5}, {"combat": {"session": "abc"}}, {"combat": {"injury": 12}},
    {"inventory": None}, {"inventory": {"gear": "rusty"}},
    {"inventory": {"items": {"gold": "many"}}},
    {"inventory": {"spirit_stones": {"low": -5}}},
    {"ui": [1, 2]}, {"ui": {"active_menu_message_id": "77"}},
    {"stats": 7}, {"progress": "done"}, {"location": ()},
    {"location": {"zone_id": "zone_does_not_exist", "vein_density": "abc"}},
    {"equipment": {"boots": []}},
    {"cultivation": {"qi_current": "lots", "stage": "3",
                     "current_realm_index": 999}},
    {"buffs": ["x", {"rate": "no"}, {"kind": "sect", "until": "yesterday"}]},
    {"buffs": {"kind": "sect", "until": 1700000000}},
    {"status": 42}, {"account": {"language": "klingon"}},
])
def test_ensure_v2_survives_any_nonsense(broken):
    """Whatever a hand edit, a half-written row or an old release left behind,
    migration may not raise: a raising migration is an unbreakable lock-out."""
    doc = new_user_doc(UID, "soul", "fa")
    doc.update(broken)
    changes: list[str] = []
    out = ensure_v2(doc, changes)
    assert isinstance(out, dict)
    for section in MAPPING_SECTIONS:
        assert isinstance(out[section], dict), f"{section} left malformed"
    assert isinstance(out["buffs"], list)
    assert all(isinstance(b, dict) for b in out["buffs"])
    replaced = [k for k in broken if k in MAPPING_SECTIONS and not isinstance(broken[k], dict)]
    if replaced:
        assert changes, "a repair that is not reported is a repair that is not saved"
    # a migration that keeps "fixing" the same document forever is a bug: the
    # second pass must find nothing left to change
    again: list[str] = []
    ensure_v2(out, again)
    assert again == [], f"ensure_v2 is not a fixed point: {again}"


@pytest.mark.parametrize("field", ["active_menu_message_id", "bag_page", "map_page"])
def test_a_string_id_is_rewritten_and_reported(tmp_path, field):
    """The root-window delete passes this value to Telegram, so a "77" that only
    gets fixed in memory means the next screen still breaks on the same row."""
    storage = _storage(tmp_path)
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"][field] = "77"
    storage.save_user(doc)
    ctx = Ctx(storage, admin_ids=set())
    user, _ = ctx.get_or_create_user(_tguser(UID))
    assert user["ui"][field] == 77
    assert storage.get_user(UID)["ui"][field] == 77, "the repair must be on disk"
    storage.close()


# ── C. timestamps that cannot exist ──────────────────────────────────────────

@pytest.mark.parametrize("value", [
    None, "", "   ", "not-a-date", 0, 1, 1700000000, 1700000000.5,
    "1700000000", "2026-13-45T99:99:99", "2026-02-30T00:00:00",
    dt.datetime(2026, 1, 1),  # naive datetime
])
def test_parse_iso_never_raises_and_never_returns_a_naive_datetime(value):
    out = parse_iso(value)
    assert out is None or out.tzinfo is not None
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        assert out is not None, "a unix epoch is a legitimate timestamp"
    if isinstance(value, str) and not value[:2].isdigit():
        assert out is None, "garbage must read as 'no timestamp', not as 1970"
    if isinstance(value, dt.datetime):
        assert out is not None


@pytest.mark.asyncio
async def test_an_impossible_afk_timestamp_cannot_wipe_or_inflate_progress(tmp_path):
    """A clock jump used to turn into a negative/absurd accrual. The engine must
    clamp it, and the tap must still render."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["cultivation"]["last_afk_timestamp"] = "the-day-the-sun-exploded"
    doc["cultivation"]["qi_current"] = 10
    storage.save_user(doc)

    bot = FakeBot()
    update = FakeUpdate(text="🧘 مدیتیشن و تهذیب", user_id=UID, bot=bot)
    await guard_update(storage.get_user, cmd_h.on_reply_button)(update, FakeContext(ctx, bot))
    delivered = (bot.sent[-1][1] if bot.sent else update.message.replies[-1])
    assert delivered.startswith(t("fa", "HUB_TITLE")), "the hub must still render"
    assert "ERR_UNKNOWN" not in delivered and t("fa", "ERR_UNKNOWN") not in delivered
    saved = storage.get_user(UID)
    ts_ok = parse_iso(saved["cultivation"]["last_afk_timestamp"]) is not None
    assert ts_ok, "the unusable timestamp must be replaced with a usable one"
    assert saved["cultivation"]["qi_current"] >= 10, "a bad clock cannot delete qi"
    storage.close()


def test_epoch_and_tz_offsets_survive_the_json_round_trip(tmp_path):
    """A buff written by an admin tool as a bare epoch number must be readable as
    ISO after the trip through SQLite — and an expired one must not resurrect."""
    from grimhaven.engine.models import active_buffs
    storage = _storage(tmp_path)
    doc = new_user_doc(UID, "soul", "fa")
    future = utcnow() + dt.timedelta(hours=3)
    doc["buffs"] = [
        {"kind": "sect_boon", "rate_mult": 2.0, "until": future.timestamp()},
        {"kind": "inner_demon", "rate_mult": 0.5, "until": 1700000000},
    ]
    storage.save_user(doc)
    back = storage.get_user(UID)
    live = active_buffs(back)
    assert len(live) == 1, f"only the future buff may be live: {live}"
    assert parse_iso(live[0]["until"]) is not None
    assert abs((parse_iso(live[0]["until"]) - future).total_seconds()) < 5
    # storage keeps whatever was written; the repair happens on load, and then
    # has to be persisted for the HUD (which renders the raw value) to be sane
    ctx = Ctx(storage, admin_ids=set())
    repaired, _ = ctx.get_or_create_user(_tguser(UID))
    assert isinstance(repaired["buffs"][1]["until"], str)
    assert repaired["buffs"][1]["until"].startswith("2023-11"), \
        "the epoch buff must be normalized to ISO, not left as a bare number"
    assert storage.get_user(UID)["buffs"][1]["until"].startswith("2023-11")
    storage.close()


def test_karmic_luck_and_vein_density_coerce_instead_of_raising():
    """models.py used to multiply a stored string ("karmic_luck * 0.002") and the
    zone maths died taking the panel with it."""
    from grimhaven.engine.cultivation import CultivationEngine
    doc = new_user_doc(UID, "soul", "fa")
    doc["stats"]["hidden"]["karmic_luck"] = "abc"
    doc["location"]["vein_density"] = "abc"
    doc["cultivation"]["qi_current"] = "lots"
    ensure_v2(doc)
    assert isinstance(doc["stats"]["hidden"]["karmic_luck"], (int, float))
    assert isinstance(doc["location"]["vein_density"], (int, float))
    assert isinstance(doc["cultivation"]["qi_current"], (int, float))
    CultivationEngine.settle_afk(doc)          # must not raise
    doc["stats"]["hidden"]["karmic_luck"] = -50
    ensure_v2(doc)
    assert doc["stats"]["hidden"]["karmic_luck"] == 0, "out-of-range must clamp, not pass through"


# ── D. the vault: a read or a write that fails must be a *storage* incident ──

@pytest.mark.asyncio
async def test_unreadable_row_is_quarantined_not_silently_reset(tmp_path, caplog):
    """The row is evidence. If we forget it, the next save overwrites the only
    trace of what the player's document used to contain."""
    storage = _storage(tmp_path)
    with storage._lock, storage._conn:
        storage._conn.execute("INSERT INTO users(user_id, doc, updated_at) "
                              "VALUES(?, 'not json at all', datetime('now'))", (UID,))
    with caplog.at_level(logging.DEBUG, logger="grimhaven.db.storage"):
        assert storage.get_user(UID) is None
    text = caplog.text
    assert "quarantining unreadable player document" in text
    assert f"user_id={UID}" in text
    row = storage._read("SELECT raw, reason FROM corrupt_users WHERE user_id=?",
                        (UID,))[0]
    assert row["raw"].startswith("not json"), "the bytes must be kept"
    assert "not a JSON object" in row["reason"]
    storage.close()


@pytest.mark.asyncio
async def test_read_fault_is_reported_as_storage_with_a_reference(tmp_path, caplog):
    """SQLite dying mid-read is not the player's fault and is not "unknown error"."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    storage.save_user(doc)
    original = storage._read

    def broken(sql, args=()):
        if "users" in sql:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return original(sql, args)

    storage._read = broken
    bot = FakeBot()
    update = FakeUpdate(text="🧘 مدیتیشن و تهذیب", user_id=UID, bot=bot)
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await guard_update(storage.get_user, cmd_h.on_reply_button)(
            update, FakeContext(ctx, bot))
    told = update.message.replies[-1]
    assert t("fa", "ERR_STORAGE") in told, "the player must be told about the vault"
    assert "خطای ناشناخته" not in told, "a database outage must not read as unknown"
    ref = told.split("کد پیگیری:")[-1].strip().splitlines()[0]
    assert len(ref) == 6, f"expected the incident reference, got {told!r}"
    line = [r.getMessage() for r in caplog.records if f"incident={ref}" in r.getMessage()]
    assert len(line) == 1, "exactly one log line carries this incident"
    assert "stage=load-user" in line[0] and "category=storage" in line[0]
    exc_lines = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("malformed" in r.getMessage() for r in exc_lines), "traceback context"
    storage.close()


@pytest.mark.asyncio
async def test_write_fault_after_a_rendered_screen_is_still_reported(tmp_path, caplog):
    """The game state cannot be persisted → the player must hear about it, and the
    log must say the persist stage, so the incident is not mistaken for a crash."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    storage.save_user(doc)
    original = storage.save_user

    calls = {"n": 0}

    def broken(user):
        calls["n"] += 1
        if calls["n"] == 1:
            original(user)          # the game result lands (stage "persist")
            return
        raise StorageError("world database write failed (save_user): disk I/O error")

    ctx.save = broken               # the *anchor* persist is the one that fails
    bot = FakeBot()
    update = FakeUpdate(query_data="cultivate:action:claim", user_id=UID,
                        message_id=500, bot=bot)
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await cb.on_callback(update, FakeContext(ctx, bot))
    q = update.callback_query
    assert q.edits, "the screen the player tapped for must still be rendered"
    assert q.answered and q.answered[-1][1] is True, "the toast must carry the notice"
    assert t("fa", "ERR_STORAGE") in (q.answered[-1][0] or "")
    line = [r.getMessage() for r in caplog.records if "failure path=callback" in r.getMessage()]
    assert line and "stage=persist-render-state" in line[0] and "category=storage" in line[0]
    storage.close()


@pytest.mark.asyncio
async def test_a_non_serializable_document_is_a_defect_not_a_player_error(tmp_path, caplog):
    storage = _storage(tmp_path)
    doc = new_user_doc(UID, "soul", "fa")
    doc["progress"]["witness"] = {1, 2, 3}            # a set: engines must never write one
    with pytest.raises(StorageError) as exc:
        storage.save_user(doc)
    assert classify(exc.value) is Category.STORAGE
    assert "not serializable" in str(exc.value)
    storage.close()


# ── E. Telegram: delivery is a separate layer from the game ──────────────────

@pytest.mark.asyncio
async def test_a_screen_telegram_refuses_is_a_delivery_incident(tmp_path, caplog):
    """The game state was already saved; what failed was *delivery*. The player
    must read "telegram rejected this screen", not "unknown error", and the log
    must name the render stage, not dispatch."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = None
    storage.save_user(doc)

    bot = FakeBot()
    bot.fail_send = True                             # every send is rejected
    update = FakeUpdate(text="🧘 مدیتیشن و تهذیب", user_id=UID, bot=bot)
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await guard_update(storage.get_user, cmd_h.on_reply_button)(
            update, FakeContext(ctx, bot))
    told = update.message.replies[-1]
    assert t("fa", "ERR_DELIVERY") in told, told
    assert "خطای ناشناخته" not in told
    line = [r.getMessage() for r in caplog.records if "failure path=message" in r.getMessage()]
    assert line and "stage=telegram-render" in line[0]
    assert "category=telegram" in line[0]
    # progress the tap was supposed to make is still there: only delivery broke
    assert isinstance(storage.get_user(UID)["cultivation"], dict)
    storage.close()


@pytest.mark.asyncio
async def test_a_stale_anchor_is_replaced_instead_of_killing_the_tap(tmp_path, caplog):
    """``message to edit not found`` means the window is gone, not that the game
    broke. The screen must be sent fresh and the anchor must move to it."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = 123456      # long deleted
    storage.save_user(doc)

    bot = FakeBot()
    update = FakeUpdate(query_data="cultivate:view:hub", user_id=UID,
                        message_id=123456, bot=bot)
    update.callback_query.message.deleted = True       # Telegram says "not found"
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await cb.on_callback(update, FakeContext(ctx, bot))
    assert bot.sent, "the hub must still arrive as a fresh message"
    assert storage.get_user(UID)["ui"]["active_menu_message_id"] == bot.sent[-1][0]
    stale_errors = [r for r in caplog.records if "failure path=callback" in r.getMessage()]
    assert not stale_errors, "a replaced window is not an incident"
    storage.close()


@pytest.mark.asyncio
async def test_message_is_not_modified_counts_as_success(tmp_path, caplog):
    """Re-rendering the exact screen the player already sees is a no-op, not a
    failure — it must not produce an incident line or a scary toast."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = None
    storage.save_user(doc)
    bot = FakeBot()
    await cb.on_callback(FakeUpdate(query_data="cultivate:view:hub", user_id=UID,
                                    message_id=500, bot=bot), FakeContext(ctx, bot))
    anchor = storage.get_user(UID)["ui"]["active_menu_message_id"]
    again = FakeUpdate(query_data="cultivate:view:hub", user_id=UID,
                       message_id=anchor, bot=bot)
    again.callback_query.message.text = bot.sent[-1][1]     # identical screen
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await cb.on_callback(again, FakeContext(ctx, bot))
    again_errors = [r for r in caplog.records if "failure path=callback" in r.getMessage()]
    assert not again_errors, "a re-render of the same screen is not a failure"
    assert again.callback_query.answered, "the tap is still acknowledged"
    storage.close()


@pytest.mark.asyncio
async def test_a_toast_that_arrived_too_late_does_not_become_an_error(tmp_path, caplog):
    """answerCallbackQuery has a 30 s window. Losing the toast must not replace a
    rendered screen with an error message."""
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = None
    storage.save_user(doc)
    bot = FakeBot()
    update = FakeUpdate(query_data="cultivate:view:hub", user_id=UID,
                        message_id=500, bot=bot)
    update.callback_query.fail_answer = True
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await cb.on_callback(update, FakeContext(ctx, bot))
    assert bot.sent, "the screen was delivered"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    storage.close()


# ── F. an action this build does not know ────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_callback_action_explains_and_changes_nothing(tmp_path, caplog):
    """A stale or forged payload must not silently teleport the player.

    It used to fall through to the profile HUD with ``root: True`` — which deleted
    the screen they were reading (a battle HUD, a chronicle) and looked exactly
    like the button being broken. And because the fallthrough *was* a successful
    render, nothing was logged: the only trace was a confused player.
    """
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = None
    storage.save_user(doc)
    first = FakeUpdate(query_data="cultivate:view:hub", user_id=UID, message_id=500,
                       bot=FakeBot())
    await cb.on_callback(first, FakeContext(ctx, first.bot))
    anchor = storage.get_user(UID)["ui"]["active_menu_message_id"]

    bot = FakeBot()
    update = FakeUpdate(query_data="gacha:pull:legendary", user_id=UID,
                        message_id=anchor, bot=bot)
    with caplog.at_level(logging.WARNING, logger="grimhaven.bot"):
        await guard_update(storage.get_user, cb.on_callback)(update, FakeContext(ctx, bot))
    q = update.callback_query
    assert q.answered[-1] == (t("fa", "ERR_INVALID_ACTION"), True), \
        "the player must be told the button is dead, in their language"
    assert not q.edits, "the screen they are reading must not be replaced"
    assert not bot.sent, "no new root window may be created"
    assert storage.get_user(UID)["ui"]["active_menu_message_id"] == anchor
    assert not [r for r in caplog.records if "failure path=" in r.getMessage()], \
        "a dead button is an input outcome, not a defect with an incident id"
    storage.close()


#: stale / forged / hand-typed payloads of every family
UNKNOWN_PAYLOADS = [
    "cultivate:action:not_a_real_action",
    "bag:use:item_that_does_not_exist",
    "bag:action:sell:gear_broken_sword",
    "martial:equip:art_nope",
    "method:activate:method_nope",
    "breakthrough:action:nope",
    "sect:donate:999999",
    "shop:buy:pill_nope",
    "map:action:conquer:zone_nowhere",
    "gacha:pull:legendary",
]


@pytest.mark.parametrize("data", UNKNOWN_PAYLOADS)
@pytest.mark.asyncio
async def test_unknown_ids_never_mutate_the_document(tmp_path, caplog, data):
    """A bad id is refused, half-applied is not an option.

    Contract for every family: the tap is always answered (an unanswered callback
    leaves the client spinning forever), nothing the player did not ask for is
    written, and no raw locale key or "unknown error" text ever reaches the
    screen. Some families explain with a toast, others re-render the current
    screen — both are acceptable; a silent half-action is not.
    """
    storage = _storage(tmp_path)
    ctx = Ctx(storage, admin_ids=set())
    doc = new_user_doc(UID, "soul", "fa")
    doc["ui"]["active_menu_message_id"] = None
    storage.save_user(doc)
    await cb.on_callback(FakeUpdate(query_data="profile:view:main", user_id=UID,
                                   message_id=500, bot=FakeBot()),
                         FakeContext(ctx, FakeBot()))
    baseline = json.dumps(storage.get_user(UID), sort_keys=True)

    bot = FakeBot()
    update = FakeUpdate(query_data=data, user_id=UID,
                        message_id=storage.get_user(UID)["ui"]["active_menu_message_id"],
                        bot=bot)
    with caplog.at_level(logging.ERROR, logger="grimhaven.bot"):
        await guard_update(storage.get_user, cb.on_callback)(update, FakeContext(ctx, bot))
    after = json.loads(json.dumps(storage.get_user(UID)))
    after["cultivation"].pop("last_afk_timestamp", None)
    base = json.loads(baseline)
    base["cultivation"].pop("last_afk_timestamp", None)
    assert json.dumps(after, sort_keys=True) == json.dumps(base, sort_keys=True), f"{data} changed the player's state"
    q = update.callback_query
    assert q.answered, f"{data} left the tap unanswered — the client spins forever"
    shown = " ".join([*(q.edits or []), *(txt for txt, _ in q.answered if txt),
                      *(txt for _mid, txt, _kb in bot.sent if txt)])
    assert "ERR_" not in shown and "_UNKNOWN" not in shown, f"{data} leaked a locale key: {shown[:160]!r}"
    assert t("fa", "ERR_UNKNOWN") not in shown, f"{data} was reported as an unknown error"
    incidents = [r for r in caplog.records if "failure path=" in r.getMessage()]
    assert not incidents, f"{data} raised inside the handler: {incidents}"
    storage.close()


# ── G. the log vocabulary stays greppable ────────────────────────────────────

#: every stage a failure line may name; adding a synonym here (or a new label in
#: a handler) must be a deliberate act, because "grep stage=telegram-render" is
#: how an incident in a player's screenshot is located in the Actions log
KNOWN_STAGES = {
    "load-user", "canonicalize", "state-guard", "settle", "dispatch", "combat",
    "persist", "telegram-render", "persist-render-state",
}


@pytest.mark.parametrize("module", ["commands.py", "callbacks.py"])
def test_handlers_only_use_documented_stage_labels(module):
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "grimhaven" / "bot" / "handlers"
            / module).read_text(encoding="utf-8")
    labels = set(re.findall(r'stage = "([a-z-]+)"', src))
    assert labels, f"{module} lost its stage tracking entirely"
    assert labels <= KNOWN_STAGES, f"{module} invented stage labels: {labels - KNOWN_STAGES}"
    # every safety net must report the stage it reached, not a fixed one
    assert "stage=stage," not in src, \
        f"{module} reports a raw stage without normalising a render failure"


# ── H. the API surface the whole outage came from ────────────────────────────

def test_no_handler_reaches_telegram_through_a_telegram_object():
    """``message.bot`` / ``query.bot`` / ``update.bot`` do not exist any more.

    python-telegram-bot v20 removed ``TelegramObject.bot``; the four call sites
    that still used it raised ``AttributeError`` on every root render, which the
    blanket handler excepts turned into "unknown error". A syntax-tree guard is
    the cheapest way to keep a future round from re-introducing the shortcut —
    the hand-written unit fakes hid it for a whole release cycle.
    """
    import ast
    from pathlib import Path
    handlers = Path(__file__).resolve().parent.parent / "grimhaven" / "bot"
    offenders: list[str] = []
    for path in sorted(handlers.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "bot":
                continue
            base = node.value
            name = getattr(base, "id", None) or getattr(base, "attr", None)
            if name in {"update", "query", "message", "sent", "self"}:
                offenders.append(f"{path.name}:{node.lineno}: ….bot  (on {name!r})")
    assert not offenders, ("reach Telegram through ContextTypes.bot only:\n"
                           + "\n".join(offenders))


def test_every_api_call_site_in_the_handlers_passes_the_bot_explicitly():
    """The lifecycle helpers take ``bot`` as an argument instead of digging it out
    of an object, so there is exactly one way to get a Bot and it is visible in
    the signature."""
    import inspect
    from pathlib import Path
    import grimhaven.bot.handlers.callbacks as callbacks_mod
    import grimhaven.bot.handlers.commands as commands_mod
    for module, names in ((commands_mod, ("_present", "_send")),
                          (callbacks_mod, ("_apply_render", "_send_fresh",
                                           "_combat_turn", "_run_tribulation"))):
        for name in names:
            fn = getattr(module, name, None)
            assert fn is not None, f"{module.__name__} lost {name}"
            params = list(inspect.signature(fn).parameters)
            assert params[0] == "bot", f"{module.__name__}.{name} must take bot first: {params}"
            src = inspect.getsource(fn)
            assert "context.bot" not in src, f"{name} should not reach for the context"
    # …and both entry points hand it over from the context
    for module, fn in ((commands_mod, commands_mod._run), (callbacks_mod, cb.on_callback)):
        body = inspect.getsource(fn)
        assert "context.bot" in body, f"{module.__name__} must thread context.bot"

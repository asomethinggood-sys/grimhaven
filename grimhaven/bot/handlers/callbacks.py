"""Inline-button dispatcher — every game action, including live combat.

Contract (bot & demo both call `_dispatch`): pure function
    (ctx, user, action, arg, settle_res) -> (text, InlineKeyboardMarkup | None)
It mutates the user document; the caller persists it and renders.
"""
from __future__ import annotations

import datetime as dt
import random

from telegram import Update
from telegram.ext import ContextTypes

from .. import keyboards as kbs
from ...core import combat_engine as ce
from ...core.data_loader import data_registry
from ...core.offline_engine import OfflineAdventureEngine
from ...core.state_machine import UserStatus, get_status, active_debuff, paralysis_active
from ...engine import items as items_mod
from ...engine import world as world_mod
from ...engine.constants import (
    ZONES,
    DAO_PATHS,
    METHODS,
    REALM_NAMES,
    REALM_STAGES,
)
from ...engine.cultivation import CultivationEngine
from ...engine.models import afk_hourly_rate, in_seclusion, parse_iso, utcnow
from ...localization import t
from ...render import (
    bag_item_text,
    bag_text,
    combat_view_text,
    dao_text,
    item_name,
    map_text,
    martial_text,
    deck_picker_text,
    outcome_text,
    sect_text,
    shop_text,
    target_label,
    zone_text,
)
from .common import Ctx


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx: Ctx = context.bot_data["ctx"]
    data = query.data or "menu"
    tg_user = update.effective_user
    user, _ = ctx.get_or_create_user(tg_user)
    lang = user["account"]["language"]
    if user["account"].get("is_banned") and not data.startswith("setlang"):
        await query.answer(t(lang, "ERR_BANNED"), show_alert=True)
        return

    action, _, arg = data.partition(":")

    # light-weight feedback keys — no state change
    if data == "combat:noop":
        await query.answer(t(lang, "COMBAT_ON_CD"))
        return
    if data == "combat:no_qi":
        await query.answer(t(lang, "COMBAT_NO_QI"))
        return
    await query.answer()

    settle_res = ctx.settle(user)
    try:
        text, keyboard = _dispatch_full(ctx, user, action, arg, settle_res, raw=data)
    except Exception:  # pragma: no cover — safety net, never crash the bot
        import traceback
        traceback.print_exc()
        text, keyboard = ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
    ctx.save(user)
    if keyboard is None:
        await query.edit_message_text(text)
    else:
        await query.edit_message_text(text, reply_markup=keyboard)


def _dispatch_full(ctx: Ctx, user: dict, action: str, arg: str, settle_res: dict,
                   raw: str = "", now: dt.datetime | None = None):
    now = now or utcnow()
    lang = user["account"]["language"]

    # ── live combat intercept ──
    if action == "combat" or user["combat"].get("session"):
        if action == "combat" or raw.startswith("combat:"):
            return _combat_dispatch(ctx, user, raw, now)

    return _dispatch(ctx, user, action, arg, settle_res, now=now)


def _dispatch(ctx: Ctx, user: dict, action: str, arg: str, settle_res: dict,
              now: dt.datetime | None = None):
    """Legacy-compatible entry used by the web demo too."""
    now = now or utcnow()
    lang = user["account"]["language"]
    raw = f"{action}:{arg}" if arg else action
    action, _, arg = raw.partition(":")

    if action in ("cancel", ""):
        return ctx.back_reply(user, t(lang, "CANCELLED"))
    if action == "back":
        if arg.startswith("bag"):
            return _bag(ctx, user, "tab:gear")
        if arg.startswith("combat"):
            return _combat_dispatch(ctx, user, "combat:view", now)
        if arg.startswith("martial"):
            return _dispatch(ctx, user, "martial", "", settle_res, now)
        if arg.startswith("shop"):
            return _dispatch(ctx, user, "shop", "", settle_res, now)
        return ctx.profile_reply(user, now)

    if action == "start":
        if user.get("onboarded"):
            return ctx.profile_reply(user, now)
        return t(lang, "WELCOME_NEW"), kbs.language_kb()

    # ── live combat intercept (also used by the web demo) ──
    if action == "combat":
        return _combat_dispatch(ctx, user, raw, now)

    # ── navigation ───────────────────────────────────────────────────────────
    if action == "menu":
        return ctx.profile_reply(user, now)
    if action == "deep":
        return ctx.deep_reply(user)
    if action == "language":
        return "🌐 Language / زبان", kbs.language_kb()
    if action == "setlang":
        user["account"]["language"] = arg if arg in ("fa", "en") else "fa"
        lang = user["account"]["language"]
        return ctx.profile_reply(user, now)

    # ── cultivation & meditation claim (Part 3 §1) ───────────────────────────
    if action == "meditate":
        if user["combat"].get("session"):
            return ctx.back_reply(user, t(lang, "GUARD_COMBAT"))
        res = CultivationEngine.start_meditation(user, now)
        if res["status"] == "ALREADY_MEDITATING":
            return ctx.back_reply(user, t(lang, "ALREADY_MEDITATING"))
        if res["status"] == "IN_SECLUSION":
            return ctx.back_reply(user, t(lang, "SECLUSION_ACTIVE", minutes=_seclusion_left(user, now)))
        if res["status"] == "BLOCKED":
            return ctx.back_reply(user, t(lang, "GUARD_PARALYSIS"))
        rate = round(afk_hourly_rate(user, world_boost=ctx.world_boost(now), now=now))
        return (t(lang, "MEDITATION_STARTED", rate=rate), kbs.main_menu(lang, user))

    if action == "stop_meditate":
        if not user["cultivation"].get("meditating"):
            return ctx.back_reply(user, t(lang, "NOT_MEDITATING"))
        report, detail = OfflineAdventureEngine.process_meditation_claim(
            user, now, settle_res, world_boost=ctx.world_boost(now))
        CultivationEngine.stop_meditation(user, now)
        return report, kbs.main_menu(lang, user)

    if action == "breakthrough":
        if in_seclusion(user, now):
            return ctx.back_reply(user, t(lang, "SECLUSION_ACTIVE", minutes=_seclusion_left(user, now)))
        realm, stage = user["cultivation"]["current_realm_index"], user["cultivation"]["current_stage"]
        stages = REALM_STAGES[realm]
        cost = stages[min(stage, len(stages) - 1)]
        target_realm, target_stage = realm, stage + 1
        if target_stage >= len(stages):
            target_realm, target_stage = realm + 1, 0
        if realm >= 9 and stage >= len(stages) - 1:
            return ctx.back_reply(user, t(lang, "AT_DAO_SOVEREIGN"))
        if user["cultivation"]["qi_current"] < cost:
            return ctx.back_reply(user, t(lang, "ERR_QI_NOT_ENOUGH", need=cost,
                                          have=user["cultivation"]["qi_current"]))
        if realm == 1 and stage == len(stages) - 1 and not user["cultivation"].get("dao_path"):
            return dao_text(lang, user), kbs.dao_kb(lang, False)
        rate = CultivationEngine.success_rate(user)
        minutes = CultivationEngine.seclusion_minutes(user)
        text = t(lang, "CONFIRM_BREAKTHROUGH",
                 target=target_label(lang, target_realm, target_stage),
                 cost=cost, rate=round(rate, 1), minutes=minutes)
        return text, kbs.breakthrough_confirm_kb(lang)

    if action == "breakthrough_do":
        res = CultivationEngine.begin_breakthrough(user, now)
        return _breakthrough_started(ctx, user, res)

    if action == "check_tribulation":
        res = CultivationEngine.resolve_breakthrough(user, now)
        return _tribulation_result(ctx, user, res)

    # ── bag / spatial ring (Part 3 §5) ────────────────────────────────────────
    if action == "bag":
        return _bag(ctx, user, arg)
    if action == "backpack":  # legacy alias
        return bag_text(lang, user, "gear"), kbs.bag_kb(lang, user, "gear")

    # ── shop ─────────────────────────────────────────────────────────────────
    if action == "shop":
        if items_mod.shop_locked(user):
            return ctx.back_reply(user, t(lang, "SHOP_LOCKED"))
        page = int(arg) if arg.isdigit() else 0
        return shop_text(lang, user), kbs.shop_kb(lang, user, page)
    if action == "buy":
        res = items_mod.buy(user, arg)
        status = res.get("status")
        if status == "NOT_ENOUGH_STONES":
            return ctx.back_reply(user, t(lang, "NOT_ENOUGH_STONES", price=res["price"]))
        if status == "REALM_LOCKED":
            return ctx.back_reply(user, t(lang, "SHOP_REALM_LOCKED",
                                          realm=t(lang, REALM_NAMES[res["realm"]])))
        if status == "ALREADY_OWNED":
            return ctx.back_reply(user, t(lang, "SHOP_ALREADY_OWNED"))
        if status != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        note = t(lang, "BOUGHT", item=res["label"], price=res["price"])
        if arg.startswith("art:"):
            note += "\n" + t(lang, "ART_LEARNED")
        return note, kbs.back_to_menu(lang)

    # ── martial hall & deck (Part 3 §4) ──────────────────────────────────────
    if action == "martial":
        if arg.startswith("slot:"):
            slot = int(arg.split(":", 1)[1] or 0)
            return deck_picker_text(lang, user, slot), kbs.deck_picker_kb(lang, user, slot)
        if arg.startswith("equip:"):
            slot_s, _, tech_id = arg[6:].partition(":")
            res = items_mod.set_loadout(user, tech_id or None,
                                        int(slot_s) if slot_s.isdigit() else None)
            return _deck_result(ctx, user, res)
        if arg.startswith("unequip:"):
            res = items_mod.set_loadout(user, None, int(arg[8:]) if arg[8:].isdigit() else None)
            return _deck_result(ctx, user, res)
        return martial_text(lang, user), kbs.martial_kb(lang, user)
    if action == "method":
        res = items_mod.activate_method(user, arg)
        if res["status"] != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return ctx.back_reply(user, t(lang, "METHOD_ACTIVATED",
                                      method=t(lang, METHODS[arg]["key"]), mult=res["tech_mult"]))

    # ── map / zones / travel / combat entry ──────────────────────────────────
    if action == "map":
        return map_text(lang, user, ctx.storage), kbs.map_kb(lang, ctx.storage,
                                                              user["location"]["current_zone_id"])
    if action == "zone":
        if arg not in ZONES:
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        zone = world_mod.zone_doc(ctx.storage, arg)
        return zone_text(lang, zone, ctx.storage, user["user_id"]), kbs.zone_kb(lang, zone, user)
    if action == "travel":
        res = world_mod.travel(ctx.storage, user, arg, now)
        if res["status"] == "SECLUSION_BLOCKED":
            return ctx.back_reply(user, t(lang, "SECLUSION_BLOCKED"))
        if res["status"] == "ZONE_LOCKED_REALM":
            return ctx.back_reply(user, t(lang, "ZONE_LOCKED_REALM",
                                          realm=t(lang, REALM_NAMES[res["min_realm"]])))
        return ctx.back_reply(user, t(lang, "TRAVELED",
                                      zone=t(lang, res["zone"]["key"]), vein=res["zone"]["vein_density"]))
    if action == "hunt":
        if arg not in ZONES:
            return ctx.back_reply(user, t(lang, "MAP_EMPTY_ZONE"))
        return _start_hunt(ctx, user, arg, now)
    if action == "conquer":
        if arg not in ZONES:
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return _start_conquest(ctx, user, arg, now)
    if action == "rival":
        return _rival_choice(ctx, user, arg, now)
    if action in ("mercy", "plunder"):
        from ...engine import combat as combat_mod
        user.pop("pending_rival", None)
        res = combat_mod.mercy_or_plunder(user, action)
        if action == "mercy":
            return ctx.back_reply(user, t(lang, "MERCY_DONE", n=1))
        return ctx.back_reply(user, t(lang, "PLUNDER_DONE", qi=res["qi"], stones=res["stones"], n=5))

    # ── sects ────────────────────────────────────────────────────────────────
    if action == "sect":
        return sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user, ctx.storage)
    if action == "join":
        res = world_mod.join_sect(ctx.storage, user, arg)
        if res["status"] == "SECT_LOCKED":
            return ctx.back_reply(user, t(lang, "SECT_LOCKED"))
        if res["status"] == "ALIGNMENT_MISMATCH":
            return ctx.back_reply(user, t(lang, "SECT_ALIGNMENT_MISMATCH"))
        sect = res["sect"]
        return ctx.back_reply(user, t(lang, "SECT_JOINED",
                                      sect=t(lang, sect["key"]) if sect.get("key") else sect.get("name", "")))
    if action == "leave_sect":
        world_mod.leave_sect(ctx.storage, user)
        return ctx.back_reply(user, t(lang, "SECT_LEFT"))
    if action == "found_sect":
        res = world_mod.found_sect(ctx.storage, user)
        if res["status"] == "SECT_FOUND_LOCKED":
            return ctx.back_reply(user, t(lang, "SECT_FOUND_LOCKED"))
        return ctx.back_reply(user, t(lang, "SECT_FOUNDED", sect=res["sect"]["name"]))

    # ── Dao, alignment & sacrifices ──────────────────────────────────────────
    if action == "dao":
        if not arg:
            return dao_text(lang, user), kbs.dao_kb(lang, bool(user["cultivation"].get("dao_path")))
        dao, align = arg.split(":", 1) if ":" in arg else (arg, None)
        res = items_mod.choose_dao(user, dao, align)
        if res["status"] == "TOO_EARLY":
            return ctx.back_reply(user, dao_text(lang, user) + "\n\n" + t(lang, "CHOOSE_DAO_FIRST"))
        if res["status"] == "ALREADY_CHOSEN":
            return ctx.back_reply(user, t(lang, "DAO_ALREADY",
                                          dao=t(lang, DAO_PATHS[res["dao"]]["key"])))
        return ctx.back_reply(user, t(lang, "DAO_CHOSEN",
                                      dao=t(lang, DAO_PATHS[dao]["key"])) + "\n" +
                              t(lang, "ALIGN_CHOSEN",
                                alignment=t(lang, "ALIGN_ORTHODOX_NAME" if res["alignment"] == "orthodox"
                                            else "ALIGN_DEMONIC_NAME")))
    if action == "sacrifice_menu":
        return t(lang, "SACRIFICE_TITLE"), kbs.sacrifice_kb(lang)
    if action == "sacrifice":
        res = world_mod.perform_sacrifice(user, arg, now)
        if res["status"] == "DANTIAN_EXPLOSION":
            return ctx.back_reply(user, t(lang, "DANTIAN_EXPLOSION", qi=res["qi_lost"]))
        text = t(lang, "SACRIFICE_DONE", sacrifice=t(lang, f"SACRIFICE_{arg.upper()}"))
        if res.get("enforcer_damage"):
            text += "\n" + t(lang, "ENFORCER_ATTACK", damage=res["enforcer_damage"])
        if res.get("aura_revealed"):
            text += "\n" + t(lang, "CORRUPTION_AURA", value=res["corruption"])
        return ctx.back_reply(user, text)

    # ── stone activation (quick catalyst switch from menu) ───────────────────
    if action == "stone":
        res = items_mod.activate_stone(user, arg)
        if res["status"] == "NO_STONES":
            return ctx.back_reply(user, t(lang, "NO_STONES"))
        return ctx.back_reply(user, t(lang, "STONE_ACTIVATED", stone=arg,
                                      boost=int(res["boost"] * 100)))

    # ── admin panel buttons ──────────────────────────────────────────────────
    if action == "admin":
        if not ctx.is_admin(user["user_id"]):
            return ctx.back_reply(user, t(lang, "ADMIN_ONLY"))
        if arg == "drops":
            return _admin_drops(ctx, user)
        sub = arg.split(":", 1)[1] if ":" in arg else arg
        usage_key = {
            "broadcast": "USAGE_BROADCAST", "inspect": "USAGE_INSPECT",
            "realm": "USAGE_SET_REALM", "judge": "USAGE_SEAL", "boost": "USAGE_BOOST",
        }.get(sub, "USAGE_BROADCAST")
        from ...render import admin_text
        return admin_text(lang, ctx.storage, ctx.boost_label()) + "\n\n" + t(lang, usage_key), \
            kbs.admin_kb(lang)

    return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))


# ── bag (tab / inspect / act) ────────────────────────────────────────────────

def _bag(ctx: Ctx, user: dict, arg: str):
    lang = user["account"]["language"]
    sub, _, rest = arg.partition(":")
    if sub in ("back", ""):
        sub = "tab"
        rest = "gear"
    if sub == "tab":
        tab = rest if rest in ("gear", "consumables", "materials") else "gear"
        return bag_text(lang, user, tab), kbs.bag_kb(lang, user, tab)
    if sub == "item":
        kind, _, item_id = rest.partition(":")
        kind = "gear" if kind == "gear" else "item"
        owned = item_id in user["inventory"].get("gear", {})
        equipped = any(g and g.get("id") == item_id for g in user["equipment"].values())
        if kind == "gear" and not owned and not equipped:
            return bag_text(lang, user, "gear"), kbs.bag_kb(lang, user, "gear")
        if kind == "item" and not items_mod.count_item(user, item_id):
            return bag_text(lang, user, "consumables"), kbs.bag_kb(lang, user, "consumables")
        return bag_item_text(lang, user, kind, item_id), kbs.item_kb(lang, user, kind, item_id,
                                                                      equipped=equipped and not owned)
    if sub == "use":
        res = items_mod.use_item(user, rest)
        return ctx.back_reply(user, _use_result(lang, rest, res))
    if sub == "equip":
        res = items_mod.equip(user, rest)
        if res.get("status") != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return ctx.back_reply(user, t(lang, "EQUIPPED", item=res["label"]))
    if sub == "unequip":
        res = items_mod.unequip(user, rest)
        if res.get("status") != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return bag_text(lang, user, "gear"), kbs.bag_kb(lang, user, "gear")
    if sub == "sell":
        kind, _, item_id = rest.partition(":")
        res = items_mod.sell(user, kind, item_id)
        if res.get("status") != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return ctx.back_reply(user, t(lang, "SOLD", item=res["label"], price=res["price"]))
    if sub == "repair":
        res = items_mod.repair_gear(user, rest)
        if res.get("status") == "NOT_ENOUGH_STONES":
            return ctx.back_reply(user, t(lang, "NOT_ENOUGH_STONES", price=res["price"]))
        if res.get("status") != "OK":
            key = {"NOT_OWNED": "REPAIR_NOT_OWNED", "ALREADY_PERFECT": "REPAIR_ALREADY_PERFECT",
                   "NOT_ENOUGH_STONES": "REPAIR_NOT_ENOUGH_STONES"}.get(res["status"], "ERR_UNKNOWN")
            return ctx.back_reply(user, t(lang, key))
        return ctx.back_reply(user, t(lang, "REPAIR_DONE", price=res["price"]))
    return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))


def _use_result(lang: str, item_id: str, res: dict) -> str:
    status = res.get("status")
    if status == "OK":
        return t(lang, res["key"], item=res.get("label", ""), value=res.get("value", 0))
    if status == "IN_COMBAT":
        return t(lang, "USE_IN_COMBAT")
    if status == "COMBAT_ONLY":
        return t(lang, "USE_COMBAT_ONLY", item=res.get("label", ""))
    if status == "NONE_LEFT":
        return t(lang, "ITEM_NONE_LEFT", item=item_name(item_id, lang))
    return t(lang, status if status != "UNKNOWN_ACTION" else "ERR_UNKNOWN")


def _deck_result(ctx: Ctx, user: dict, res: dict):
    lang = user["account"]["language"]
    if res.get("status") == "OK":
        return martial_text(lang, user), kbs.martial_kb(lang, user)
    if res.get("status") == "DECK_FULL":
        return ctx.back_reply(user, t(lang, "DECK_FULL", cap=res["cap"]))
    return ctx.back_reply(user, t(lang, "DECK_BAD_SLOT"))


# ── combat entry points ──────────────────────────────────────────────────────

def _start_hunt(ctx: Ctx, user: dict, zone_id: str, now):
    lang = user["account"]["language"]
    if in_seclusion(user, now) or get_status(user) is UserStatus.MEDITATING:
        return ctx.back_reply(user, t(lang, "FIGHT_BLOCKED_SECLUSION"))
    zone = world_mod.zone_doc(ctx.storage, zone_id)
    if paralysis_active(user, now):
        return ctx.back_reply(user, t(lang, "GUARD_PARALYSIS"))
    if active_debuff(user, now) and zone["guard_level"] >= 3:
        return ctx.back_reply(user, t(lang, "GUARD_INJURED_PERILOUS"))
    beasts = data_registry.beasts_for_guard(zone["guard_level"])
    if not beasts:
        return ctx.back_reply(user, t(lang, "MAP_EMPTY_ZONE"))
    rng = random.Random()
    enemy = ce.make_beast(rng.choice(beasts).enemy_id, rng)
    ce.start_session(user, enemy, "hunt", zone_id)
    return _combat_render(user, user["combat"]["session"],
                          [t(lang, "COMBAT_BEGUN", enemy=ce.enemy_name(enemy, lang))])


def _start_conquest(ctx: Ctx, user: dict, zone_id: str, now):
    lang = user["account"]["language"]
    if in_seclusion(user, now) or get_status(user) is UserStatus.MEDITATING:
        return ctx.back_reply(user, t(lang, "FIGHT_BLOCKED_SECLUSION"))
    zone = world_mod.zone_doc(ctx.storage, zone_id)
    if zone.get("owner_kind") == "user" and zone.get("owner") == user["user_id"]:
        return ctx.back_reply(user, t(lang, "ZONE_ALREADY_YOURS"))
    if paralysis_active(user, now) or active_debuff(user, now):
        return ctx.back_reply(user, t(lang, "GUARD_INJURED"))
    rng = random.Random()
    enemy = ce.make_guardian(zone["guard_level"], rng)
    ce.start_session(user, enemy, "conquest", zone_id)
    return _combat_render(user, user["combat"]["session"],
                          [t(lang, "COMBAT_CONQUEST_BEGUN", zone=t(lang, zone["key"]))])


def _combat_dispatch(ctx: Ctx, user: dict, raw: str, now):
    lang = user["account"]["language"]
    session = user["combat"].get("session")
    if not session:
        # late click after the battle ended — refresh the scroll
        return ctx.profile_reply(user, now)
    if session.get("finished"):
        outcome = session.get("outcome", "FLED")
        text = outcome_text(lang, user, outcome, session)
        kb = kbs.victory_kb(lang, user, session)
        ce.close_session(user)
        return text, kb or kbs.back_to_menu(lang)
    if ce.session_expired(session, now):
        ce.close_session(user)
        return ctx.back_reply(user, t(lang, "COMBAT_EXPIRED")), None

    action, _, arg = raw.partition(":")
    sub = arg

    if sub == "view":
        return _combat_render(user, session, [t(lang, "COMBAT_RESUMED",
                                                enemy=ce.enemy_name(session["enemy"], lang),
                                                rnd=session["round"])])
    if sub == "simulate":
        outcome, summary, snapshot = ce.simulate(user, rng=random.Random())
        session = user["combat"].get("session") or snapshot
        text = combat_view_text(lang, user, session, summary)
        if user["combat"].get("session") is None:
            return outcome_text(lang, user, outcome, session), _post_combat_kb(ctx, user, session, now)
        return text, kbs.combat_kb(lang, user, session)
    if sub.startswith("tech:"):
        tech_id = sub.split(":", 1)[1]
        outcome, lines = ce.resolve_round(user, "combat:tech", tech_id, rng=random.Random())
    elif sub.startswith("item:"):
        item_id = sub.split(":", 1)[1]
        outcome, lines = ce.resolve_round(user, f"combat:item:{item_id}", None, rng=random.Random())
    elif sub in ("basic", "flee"):
        outcome, lines = ce.resolve_round(user, f"combat:{sub}", None, rng=random.Random())
    else:
        return _combat_render(user, session, [t(lang, "ERR_UNKNOWN")])

    if outcome == ce.OUT_CONTINUE and user["combat"].get("session"):
        return _combat_render(user, user["combat"]["session"], lines)
    # finished: merge the last round beats into the outcome screen
    tail = "\n".join(lines[-3:])
    text = outcome_text(lang, user, outcome, session)
    text += "\n\n" + tail if tail else ""
    return text, _post_combat_kb(ctx, user, session, now)


def _post_combat_kb(ctx: Ctx, user: dict, session: dict, now):
    lang = user["account"]["language"]
    won = session.get("outcome") == ce.OUT_VICTORY
    if session.get("kind") == "rival":
        if won:
            user["pending_rival"] = True
            return kbs.mercy_plunder_kb(lang)
        return kbs.main_menu(lang, user)
    if won and session.get("kind") == "conquest" and session.get("zone_id"):
        world_mod.apply_conquest_result(ctx.storage, user, session["zone_id"], won=True, now=now)
        zone = world_mod.zone_doc(ctx.storage, session["zone_id"])
        user["location"]["vein_density"] = max(user["location"].get("vein_density", 1.0),
                                                zone["vein_density"])
    if user.get("pending_rival"):
        return kb_rival(lang)
    return kbs.main_menu(lang, user)


def kb_rival(lang: str):
    return kbs.kb([
        [kbs.InlineKeyboardButton(t(lang, "RIVAL_FIGHT"), callback_data="rival:accept")],
        [kbs.InlineKeyboardButton(t(lang, "RIVAL_IGNORE"), callback_data="rival:ignore")],
    ])


def _rival_choice(ctx: Ctx, user: dict, arg: str, now):
    lang = user["account"]["language"]
    if arg == "ignore":
        user.pop("pending_rival", None)
        return ctx.back_reply(user, t(lang, "RIVAL_IGNORED"))
    user.pop("pending_rival", None)
    rng = random.Random()
    enemy = ce.make_rival(user["cultivation"]["current_realm_index"], rng)
    ce.start_session(user, enemy, "rival")
    return _combat_render(user, user["combat"]["session"],
                          [t(lang, "COMBAT_BEGUN", enemy=ce.enemy_name(enemy, lang))])


def _combat_render(user: dict, session: dict, lines: list[str]):
    lang = user["account"]["language"]
    text = combat_view_text(lang, user, session, lines)
    return text, kbs.combat_kb(lang, user, session)


# ── legacy helpers kept for commands & admin ─────────────────────────────────

def _seclusion_left(user: dict, now) -> int:
    finish = parse_iso(user["cultivation"].get("seclusion_finish_time"))
    if not finish:
        return 0
    return max(1, int((finish - now).total_seconds() // 60) + 1)


def _breakthrough_started(ctx: Ctx, user: dict, res: dict):
    lang = user["account"]["language"]
    if res["status"] == "QI_NOT_ENOUGH":
        return ctx.back_reply(user, t(lang, "ERR_QI_NOT_ENOUGH",
                                      need=res["need"], have=res["have"]))
    if res["status"] == "CHOOSE_DAO_FIRST":
        return dao_text(lang, user), kbs.dao_kb(lang, False)
    if res["status"] == "MERIDIANS_SEALED":
        return ctx.back_reply(user, t(lang, "STATUS_MERIDIAN_SEALED", hours=6))
    if res["status"] == "IN_COMBAT":
        return ctx.back_reply(user, t(lang, "GUARD_COMBAT"))
    if res["status"] == "INJURED":
        return ctx.back_reply(user, t(lang, "GUARD_INJURED"))
    if res["status"] == "ALREADY_IN_SECLUSION":
        return ctx.back_reply(user, t(lang, "SECLUSION_ACTIVE", minutes=_seclusion_left(user, utcnow())))
    if res["status"] == "AT_DAO_SOVEREIGN":
        return ctx.back_reply(user, t(lang, "AT_DAO_SOVEREIGN"))
    target = target_label(lang, res["target_realm"], res["target_stage"])
    return ctx.back_reply(user, t(lang, "SECLUSION_STARTED", minutes=res["minutes"], target=target))


def _tribulation_result(ctx: Ctx, user: dict, res: dict):
    lang = user["account"]["language"]
    if res["status"] == "NO_SECLUSION":
        return ctx.profile_reply(user)
    if res["status"] == "STILL_IN_SECLUSION":
        return ctx.back_reply(user, t(lang, "STILL_IN_SECLUSION", minutes=res["remaining_minutes"]))
    target = target_label(lang, user["cultivation"]["current_realm_index"],
                          user["cultivation"]["current_stage"])
    lightning = res.get("lightning")
    bolt = t(lang, "TRIB_BOLT", lightning=lightning) if lightning else ""
    if res["status"] == "SUCCESS":
        return ctx.back_reply(user, t(lang, "BREAKTHROUGH_SUCCESS", target=target) + "\n" + bolt)
    if res["status"] == "MIRACLE_SAVED":
        return ctx.back_reply(user, t(lang, "MIRACLE_SAVED", target=target) + "\n" + bolt)
    ftype = res.get("type")
    if ftype == "MINOR":
        return ctx.back_reply(user, t(lang, "FAILED_MINOR",
                                      qi_burned=res.get("qi_burned", 0),
                                      sealed_hours=res.get("sealed_hours", 6)) + "\n" + bolt)
    if ftype == "DEVIATION":
        lost = sum(res.get("stones_lost", {}).values())
        return ctx.back_reply(user, t(lang, "FAILED_DEVIATION", stones_lost=lost) + "\n" + bolt)
    if res.get("saved_by_doll"):
        return ctx.back_reply(user, t(lang, "SAVED_BY_DOLL") + "\n" + bolt)
    shattered = res.get("shattered")
    shatter = ""
    if shattered:
        from ...render import gear_label
        shatter = t(lang, "SHATTER_NOTE", item=gear_label(lang, {"id": shattered.get("id") if isinstance(shattered, dict) else shattered.get("gear_id", shattered)}))
    return ctx.back_reply(user, t(lang, "FAILED_ANNIHILATION", shatter=shatter) + "\n" + bolt)


def _admin_drops(ctx: Ctx, user: dict):
    """🎁 Rain of Blessings: every registered user gets stones + Qi."""
    lang = user["account"]["language"]
    stones, qi_gift = 10, 1_000
    count = 0
    for doc in ctx.storage.all_users():
        doc["inventory"]["spirit_stones"]["low"] += stones
        cul = doc["cultivation"]
        cul["qi_current"] = min(cul["qi_capacity"], cul["qi_current"] + qi_gift)
        ctx.storage.save_user(doc)
        count += 1
    from ...render import admin_text
    return admin_text(lang, ctx.storage, ctx.boost_label()) + "\n\n" + \
        t(lang, "DONE_DROPS", stones=stones, qi=qi_gift), kbs.admin_kb(lang)


def _shop_item_name(lang: str, shop_id: str) -> str:
    kind, _, ref = shop_id.partition(":")
    if kind == "art":
        art = data_registry.get_martial_art(ref)
        return art.name_for(lang) if art else ref
    obj = data_registry.get_consumable(ref) or data_registry.get_equipment(ref)
    if obj:
        return obj.name_for(lang)
    from ...render import shop_row_label
    return ref

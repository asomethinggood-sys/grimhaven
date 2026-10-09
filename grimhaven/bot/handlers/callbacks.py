"""Callback dispatcher v2 — every game action, spec namespaces (mainPrompt2).

Contract (bot & demo both call `_dispatch`): pure function
    (ctx, user, action, arg, settle_res) -> (text, keyboard, opts)
where ``opts`` tells the async layer what to *do* with the render:

    alert    → answer the callback with show_alert (popup) and change nothing
    answer   → answer softly (toast) alongside a render
    root     → single-window lifecycle: delete the tracked active-menu message,
               send this render fresh and store the new message id
    send     → dispatch a NEW message (battle rounds), keep the old one
    strip    → remove the clicked message's inline keyboard before resolving
    tribulation → staged 3-phase dramatic sequence (async layer sleeps 2s)

All state mutation happens here (the engines own the math); the caller
persists the user document afterwards.
"""
from __future__ import annotations

import datetime as dt
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

MessageNotModified = BadRequest  # PTB v21 surfaces "not modified" as BadRequest
from telegram.ext import ContextTypes

from .. import keyboards as kbs
from ...core import combat_engine as ce
from ...core.data_loader import data_registry
from ...core.middleware import callback_blocked
from ...core.state_machine import get_status, UserStatus
from ...engine import items as items_mod
from ...engine import world as world_mod
from ...engine.cultivation import CultivationEngine
from ...engine.models import iso, parse_iso, utcnow
from ...localization import Locale, t
from ... import render as R

# legacy callback shim → spec namespace (keeps stale messages clickable)
_ALIAS_EXACT = {
    "menu": "profile:view:main",
    "meditate": "cultivate:view:hub",
    "stop_meditate": "cultivate:action:claim",
    "breakthrough": "breakthrough:view:prep",
    "breakthrough_do": "breakthrough:action:confirm",
    "check_tribulation": "breakthrough:view:prep",
    "map": "map:view:world",
    "martial": "martial:view:main",
    "shop": "shop:view:hub",
    "sect": "sect:view:main",
    "settings": "settings:view:main",
    "language": "settings:view:main",
    "backpack": "bag:tab:gear:1",
    "deep": "profile:view:meridians",
    "leave_sect": "sect:action:leave",
    "found_sect": "sect:action:found",
    "mercy": "combat:rival:mercy",
    "plunder": "combat:rival:plunder",
    "combat:basic": "combat:act:basic:none",
    "combat:flee": "combat:act:flee:none",
    "combat:simulate": "combat:act:sim:none",
    "combat:view": "combat:refresh",
    "combat:noop": "noop",
    "combat:no_qi": "noop",
    "cancel": "noop",
    "noop": "noop",
}


def _canon(raw: str, user: dict) -> str:
    """Normalize any legacy/short payload to the v2 namespace."""
    raw = (raw or "profile:view:main").strip()
    if raw in _ALIAS_EXACT:
        raw = _ALIAS_EXACT[raw]
    if raw.startswith("noop"):
        return "noop"
    parts = raw.split(":")
    root = parts[0]
    if root == "combat" and len(parts) >= 2 and parts[1] != "act":
        # legacy combat:tech:<id> / combat:item:<id> — inject the live round
        rnd = (user.get("combat", {}).get("session") or {}).get("round", 1)
        if parts[1] == "rival":
            return raw
        if parts[1] == "tech":
            return f"combat:act:tech:{parts[2] if len(parts) > 2 else 'none'}:{rnd}"
        if parts[1] == "item":
            return f"combat:act:item:{':'.join(parts[2:]) or 'none'}:{rnd}"
        if parts[1] == "view":
            return "combat:refresh"
        return raw
    if root == "zone" and len(parts) == 2:
        return f"map:zone:inspect:{parts[1]}"
    if root == "travel" and len(parts) == 2:
        return f"map:action:settle:{parts[1]}"
    if root == "hunt":
        return f"map:action:hunt:{parts[1]}" if len(parts) == 2 else "map:view:world"
    if root == "conquer":
        return f"map:action:conquer:{parts[1]}" if len(parts) == 2 else "map:view:world"
    if root == "bag":
        rest = parts[1:]
        if not rest or rest[0] == "back":
            ui = user.get("ui", {})
            return f"bag:tab:{ui.get('bag_tab', 'gear')}:{ui.get('bag_page', 1)}"
        if rest[0] == "tab":
            tab = rest[1] if len(rest) > 1 else "gear"
            if tab not in ("gear", "consumables", "materials"):
                tab = "gear"
            try:
                page = int(rest[2]) if len(rest) > 2 else 1
            except ValueError:
                page = 1
            return f"bag:tab:{tab}:{page}"
        if rest[0] == "item":  # legacy bag:item:<kind>:<id>
            kind = rest[1] if len(rest) > 1 else "item"
            iid = rest[2] if len(rest) > 2 else ""
            tab = "gear" if kind == "gear" else "consumables"
            return f"bag:inspect:{tab}:1:{iid}"
        if rest[0] == "inspect":
            if len(rest) == 4:
                return raw          # canonical bag:inspect:<tab>:<page>:<id>
            if len(rest) == 2:
                return f"bag:inspect:gear:1:{rest[1]}"
        if rest[0] in ("use", "sell", "equip", "unequip", "repair", "assign"):
            act = {"use": "consume", "sell": "sell", "equip": "equip", "unequip": "unequip",
                   "repair": "consume", "assign": "assign_battle"}[rest[0]]
            return f"bag:action:{act}:{rest[1] if len(rest) > 1 else ''}"
        return "bag:tab:gear:1"
    return raw


def _split(raw: str) -> tuple[str, list[str]]:
    parts = raw.split(":")
    return parts[0], parts[1:]


# ── Telegram entry point ─────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx = context.bot_data["ctx"]
    data = query.data or "profile:view:main"
    tg_user = update.effective_user
    user, _ = ctx.get_or_create_user(tg_user)
    lang = user["account"]["language"]
    if user["account"].get("is_banned") and not data.startswith(("setlang", "noop")):
        await query.answer(t(lang, "ERR_BANNED"), show_alert=True)
        return

    data = _canon(data, user)
    if data == "noop":
        await query.answer()
        return

    blocked = callback_blocked(user, data)
    if blocked:
        await query.answer(t(lang, blocked), show_alert=True)
        return

    settle_res = ctx.settle(user)

    # ── atomic combat pipeline (spec P4 §1) ──
    if data.startswith("combat:act:"):
        await _combat_turn(query, ctx, user, data, now := utcnow())
        return

    try:
        text, keyboard, opts = _route(ctx, user, data, settle_res)
    except Exception:  # pragma: no cover — safety net, never crash the bot
        import traceback
        traceback.print_exc()
        text, keyboard, opts = t(lang, "ERR_UNKNOWN"), None, {}
    ctx.save(user)
    if opts.get("tribulation"):
        await _run_tribulation(query, user, text, keyboard, opts)
    else:
        await _apply_render(query, user, text, keyboard, opts)
    # persist the (possibly new) root anchor set during the render step
    ctx.save(user)


async def _run_tribulation(query, user: dict, text: str, keyboard, opts: dict) -> None:
    """Spec P6 §3.3 — strip buttons, two 2-second atmospheric edits, then the
    resolution card as the new root window."""
    import asyncio
    trib = opts["tribulation"]
    try:
        await query.edit_message_text(trib["phases"][0])
        await asyncio.sleep(2.0)
        await query.edit_message_text(trib["phases"][1])
        await asyncio.sleep(2.0)
    except (MessageNotModified, BadRequest):
        pass
    await _apply_render(query, user, text, keyboard, opts)


async def _apply_render(query, user: dict, text: str, keyboard, opts: dict) -> None:
    """Execute the render contract (edit / fresh-send / root lifecycle / alert)."""
    alert = opts.get("alert")
    answer = opts.get("answer")
    lang = user["account"]["language"]
    if opts.get("root"):
        # single-window lifecycle: delete the previous root, send a new one
        menu_id = (user.get("ui") or {}).get("active_menu_message_id")
        if menu_id:
            try:
                await query.bot.delete_message(query.message.chat_id, menu_id)
            except BadRequest:
                try:
                    await query.bot.edit_message_reply_markup(
                        chat_id=query.message.chat_id, message_id=menu_id, reply_markup=None)
                except (BadRequest, MessageNotModified):
                    pass
        try:
            sent = await query.message.reply_text(text, reply_markup=keyboard)
            user.setdefault("ui", {})["active_menu_message_id"] = sent.message_id
        except BadRequest:
            pass
        if alert:
            await query.answer(alert, show_alert=True)
        elif answer:
            await query.answer(answer)
        else:
            await query.answer()
        return
    if opts.get("send"):
        try:
            await query.bot.send_message(query.message.chat_id, text, reply_markup=keyboard)
        except BadRequest:
            pass
    else:
        try:
            if keyboard is not None:
                await query.edit_message_text(text, reply_markup=keyboard)
            else:
                await query.edit_message_text(text)
        except MessageNotModified:
            pass
        except BadRequest:
            pass
    if alert:
        await query.answer(alert, show_alert=True)
    elif answer:
        await query.answer(answer)
    else:
        await query.answer()


# ── combat: the 5-step atomic turn ───────────────────────────────────────────

def _combat_step(ctx, user: dict, data: str, now):
    """Round-token-validated battle turn — shared by the PTB handler and the
    demo/test dispatch path. Returns (text, keyboard, opts)."""
    lang = user["account"]["language"]
    parts = data.split(":")           # combat:act:<verb>:<arg…>:<round>
    try:
        rnd = int(parts[-1])
    except ValueError:
        rnd = -1
    verb = parts[2] if len(parts) > 2 else ""
    arg = ":".join(parts[3:-1]) or "none"
    session = user["combat"].get("session")

    if not session or session.get("finished"):
        return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now), kbs.profile_kb(lang),
                {"answer": t(lang, "ALERT_NO_COMBAT"), "no_render": True})
    if rnd != session["round"]:
        return (R.combat_hud_text(lang, user, session), kbs.battle_kb(lang, user, session),
                {"answer": t(lang, "ALERT_STALE_TURN"), "no_render": True})
    session["turn_lock"] = rnd

    engine_action = {"tech": "combat:tech", "basic": "combat:basic",
                     "flee": "combat:flee"}.get(verb, "combat:basic")
    action_id = None
    if verb == "tech":
        action_id = arg
    elif verb == "item":
        engine_action, action_id = f"combat:item:{arg}", None

    opts: dict = {"send": True}
    if verb == "sim":
        outcome, summary, snapshot = ce.simulate(user, rng=random.Random(), max_rounds=40)
        snapshot = snapshot or session
        text, keyboard, card_opts = _combat_terminal(user, session, snapshot, summary)
        opts.update(card_opts)
        _terminal_effects(ctx, user, snapshot, now)
        return text, keyboard, opts

    outcome, lines = ce.resolve_round(user, engine_action, action_id, rng=random.Random())
    session = user["combat"].get("session")
    if outcome == ce.OUT_CONTINUE and session:
        return (R.combat_hud_text(lang, user, session),
                kbs.battle_kb(lang, user, session), opts)
    snapshot = session or {}
    text, keyboard, card_opts = _combat_terminal(user, snapshot, snapshot, lines)
    opts.update(card_opts)
    _terminal_effects(ctx, user, snapshot, now)
    return text, keyboard, opts


def _terminal_effects(ctx, user: dict, session: dict, now) -> None:
    """Post-battle bookkeeping: conquest flag, then dissolve the session."""
    outcome = session.get("outcome")
    if outcome == ce.OUT_VICTORY and session.get("kind") == "conquest" and session.get("zone_id"):
        world_mod.apply_conquest_result(ctx.storage, user, session["zone_id"], won=True, now=now)
        zone = world_mod.zone_doc(ctx.storage, session["zone_id"])
        if zone:
            vd = max(user["location"].get("vein_density", 1.0), zone.get("vein_density", 1.0))
            user["location"]["vein_density"] = vd
            user["location"]["density"] = vd
    ce.close_session(user)


async def _combat_turn(query, ctx, user: dict, data: str, now) -> None:
    """PTB wrapper: toast the guard alerts, de-weaponize the pressed message,
    then run the shared turn."""
    lang = user["account"]["language"]
    session = user["combat"].get("session")
    if not session or session.get("finished"):
        await query.answer(t(lang, "ALERT_NO_COMBAT"), show_alert=False)
        return
    parts = data.split(":")
    try:
        rnd = int(parts[-1])
    except ValueError:
        rnd = -1
    if rnd != session["round"]:
        await query.answer(t(lang, "ALERT_STALE_TURN"), show_alert=False)
        return
    # instant de-weaponization of the trigger message (spec P4 §4.1 step 2)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except (MessageNotModified, BadRequest):
        pass
    text, keyboard, opts = _combat_step(ctx, user, data, now)
    if opts.get("no_render"):
        return
    await _apply_render(query, user, text, keyboard, opts)
    ctx.save(user)


def _combat_terminal(user: dict, session: dict, snapshot: dict, lines: list[str]):
    """Victory loot scroll / flee / miracle / death card (may chain to rival)."""
    lang = user["account"]["language"]
    outcome = snapshot.get("outcome") or session.get("outcome") or ce.OUT_CONTINUE
    zone_id = (snapshot.get("zone_id") or session.get("zone_id")
               or user["location"].get("current_zone_id"))
    kind = snapshot.get("kind") or session.get("kind")
    if outcome == ce.OUT_VICTORY:
        text = R.loot_scroll_text(lang, user, snapshot)
        if kind == "rival":
            return (text + "\n" + t(lang, "RIVAL_CHOICE"),
                    kbs.mercy_plunder_kb(lang), {})
        return text, kbs.loot_scroll_kb(lang, zone_id), {}
    if outcome == ce.OUT_FLED:
        return R.flee_card_text(lang, user, snapshot), kbs.flee_kb(lang, zone_id), {}
    if outcome == ce.OUT_MIRACLE:
        return t(lang, "MIRACLE_CARD"), kbs.recovery_kb(lang), {}
    if outcome == ce.OUT_DEATH:
        rep = R._death_summary(user)
        realm_lbl = R.realm_stage_label(lang, user["cultivation"]["current_realm_index"],
                                        user["cultivation"]["current_stage"])
        return (t(lang, "DEATH_CARD") + "\n" + t(lang, "DEATH_REPORT", minutes=rep["minutes"],
                realm=realm_lbl), kbs.recovery_kb(lang), {})
    # timeout / stalemate after simulate
    if not user["combat"].get("session"):
        return t(lang, "COMBAT_STALEMATE"), kbs.flee_kb(lang, zone_id), {}
    return R.combat_hud_text(lang, user, user["combat"]["session"]), \
        kbs.battle_kb(lang, user, user["combat"]["session"]), {}


# ── the router (pure) ────────────────────────────────────────────────────────

def _dispatch(ctx, user: dict, action: str, arg: str, settle_res: dict,
              now: dt.datetime | None = None):
    """Legacy-friendly 4-arg form kept for the demo/tests: returns (text, kb, opts)."""
    raw = f"{action}:{arg}" if arg else action
    raw = _canon(raw, user)
    return _route(ctx, user, raw, settle_res, now=now)


def _dispatch_pair(ctx, user, action, arg, settle_res, now=None):
    text, kb, _opts = _dispatch(ctx, user, action, arg, settle_res, now=now)
    return text, kb


def _route(ctx, user: dict, data: str, settle_res: dict,
           now: dt.datetime | None = None):
    now = now or utcnow()
    lang = user["account"]["language"]
    root, args = _split(data)

    if root == "noop" or data == "noop":
        return t(lang, "NOOP"), None, {}
    if root == "combat" and data.startswith("combat:act:"):
        return _combat_step(ctx, user, data, now)
    if data == "combat:refresh":
        session = user["combat"].get("session")
        if not session:
            return R.hud_text(ctx.storage, user, ctx.world_boost(now), now), \
                kbs.profile_kb(lang), {}
        return (R.combat_hud_text(lang, user, session),
                kbs.battle_kb(lang, user, session), {})
    if data.startswith("combat:rival:"):
        verdict = data.split(":")[-1]
        res = world_mod.rival_verdict(user, verdict) if hasattr(world_mod, "rival_verdict") \
            else _rival_verdict(user, verdict)
        return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now) + "\n" + res,
                kbs.profile_kb(lang), {"root": True})

    if root == "setlang":
        return _setlang(ctx, user, args, now)
    if root == "profile":
        return _profile(ctx, user, args, settle_res, now)
    if root == "settings":
        return _settings(ctx, user, args, now)
    if root == "cultivate":
        return _cultivate(ctx, user, args, settle_res, now)
    if root == "map":
        return _map(ctx, user, args, settle_res, now)
    if root == "bag":
        return _bag(ctx, user, args, now)
    if root == "martial":
        return _martial(ctx, user, args, now)
    if root == "shop":
        return _shop(ctx, user, args, now)
    if root == "sect":
        return _sect(ctx, user, args, now)
    if root == "breakthrough":
        return _breakthrough(ctx, user, args, now)
    if root == "dao":
        return _dao(ctx, user, args, now)
    if root == "admin":
        if not ctx.is_admin(user["user_id"]):
            return t(lang, "ADMIN_ONLY"), None, {"alert": t(lang, "ADMIN_ONLY")}
        sub = data.split(":", 1)[1] if ":" in data else ""
        if sub == "drops":
            return _admin_drops(ctx, user)
        usage_key = {
            "usage:broadcast": "USAGE_BROADCAST", "usage:inspect": "USAGE_INSPECT",
            "usage:realm": "USAGE_SET_REALM", "usage:judge": "USAGE_SEAL",
            "usage:boost": "USAGE_BOOST",
        }.get(sub, "USAGE_BROADCAST")
        return (R.admin_text(lang, ctx.storage, ctx.boost_label()) + "\n\n" + t(lang, usage_key),
                kbs.admin_kb(lang), {})
    # unknown → gently back to the scroll
    return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
            kbs.profile_kb(lang), {"root": True})


def _rival_verdict(user: dict, verdict: str) -> str:
    from ...engine.combat import mercy_or_plunder
    lang = user["account"]["language"]
    mercy_or_plunder(user, verdict)
    return t(lang, "RIVAL_JUDGED", verdict=t(lang, "BTN_MERCY" if verdict == "mercy" else "BTN_PLUNDER"))


# ── screens ───────────────────────────────────────────────────────────────────

def _setlang(ctx, user, args, now):
    new_lang = args[0] if args and args[0] in ("fa", "en") else "fa"
    user["account"]["language"] = new_lang
    return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
            kbs.profile_kb(new_lang), {"root": True})


def _profile(ctx, user, args, settle_res, now):
    lang = user["account"]["language"]
    view = args[0] if args else "main"
    if view == "meridians":
        return R.meridians_text(lang, user), kbs.back_profile_kb(lang), {}
    if view == "karma":
        return R.karma_text(lang, user), kbs.back_profile_kb(lang), {}
    return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
            kbs.profile_kb(lang), {"root": True})


def _settings(ctx, user, args, now):
    lang = user["account"]["language"]
    if args and args[0] == "panel_tip":
        return (t(lang, "SETTINGS_TITLE"), kbs.settings_kb(lang),
                {"alert": t(lang, "PANEL_RESTORED")})
    if args and args[0] == "help":
        return t(lang, "HELP_TEXT"), kbs.back_profile_kb(lang), {}
    return t(lang, "SETTINGS_TITLE"), kbs.settings_kb(lang), {"root": True}


def _cultivate(ctx, user, args, settle_res, now):
    lang = user["account"]["language"]
    verb = args[0] if args else "view"
    if verb == "view":
        return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
                kbs.meditate_hub_kb(lang), {"root": True})
    if verb == "action" and (len(args) < 2 or args[1] == "claim"):
        res = CultivationEngine.claim(user, now=now, world_boost=ctx.world_boost(now))
        report = res.get("chronicle") or t(lang, "CHRONICLE_EMPTY")
        return report, kbs.chronicle_kb(lang), {}
    if verb == "catalyst":
        sub = args[1] if len(args) > 1 else "menu"
        if sub == "menu":
            return R.catalyst_menu_text(lang, user), kbs.catalyst_kb(lang, user), {}
        if sub == "stone" and len(args) > 2:
            grade = args[2]
            cul = user["cultivation"]
            if cul.get("active_stone") == grade:
                cul["active_stone"] = None
                return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
                        kbs.meditate_hub_kb(lang), {"answer": t(lang, "CAT_OFF")})
            if grade in ("low", "mid", "high", "heavenly") and grade in SPIRIT_KEYS:
                cul["active_stone"] = grade
                return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
                        kbs.meditate_hub_kb(lang),
                        {"alert": t(lang, "CAT_ON", stone=t(lang, f"STONE_{grade.upper()}"))})
            return t(lang, "ERR_UNKNOWN"), kbs.catalyst_kb(lang, user), {}
    if verb == "mantra":
        user.setdefault("ui", {})["mantra_back"] = "cultivate:view:hub"
        return R.mantra_menu_text(lang, user), kbs.mantra_kb(lang, user), {}
    if verb == "stop":      # legacy: stop == claim
        res = CultivationEngine.claim(user, now=now, world_boost=ctx.world_boost(now))
        report = res.get("chronicle") or t(lang, "CHRONICLE_EMPTY")
        return report, kbs.chronicle_kb(lang), {}
    if verb == "start":     # legacy: no-op trance → hub
        CultivationEngine.start_meditation(user, now=now)
        return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
                kbs.meditate_hub_kb(lang), {})
    return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
            kbs.meditate_hub_kb(lang), {"root": True})


SPIRIT_KEYS = ("low", "mid", "high", "heavenly")


def _map(ctx, user, args, settle_res, now):
    lang = user["account"]["language"]
    if not args or args[0] == "view":
        return R.map_text(lang, user, ctx.storage), kbs.map_kb(lang, user), {"root": True}
    if args[0] == "zone" and len(args) >= 3 and args[1] == "inspect":
        zone_id = args[2]
        zdef = data_registry.get_zone(zone_id)
        if not zdef:
            return t(lang, "ALERT_ZONE_NA"), None, {"alert": t(lang, "ALERT_ZONE_NA")}
        realm = user["cultivation"]["current_realm_index"]
        if realm < zdef.min_realm:
            # realm gate — alert only, NO screen change (spec P3 §1)
            return (R.map_text(lang, user, ctx.storage), kbs.map_kb(lang, user),
                    {"alert": t(lang, "ALERT_REALM_GATE", rec=zdef.rec_for(lang))})
        return (R.zone_hub_text(lang, user, zone_id),
                kbs.zone_hub_kb(lang, user, zone_id), {})
    if args[0] == "action" and len(args) >= 3:
        verb, zone_id = args[1], args[2]
        zdef = data_registry.get_zone(zone_id) or data_registry.get_zone(
            user["location"].get("current_zone_id", ""))
        zone_id = zdef.zone_id if zdef else zone_id
        if verb == "settle":
            res = world_mod.travel(ctx.storage, user, zone_id, now=now)
            if res["status"] != "OK":
                return (R.map_text(lang, user, ctx.storage), kbs.map_kb(lang, user),
                        {"alert": t(lang, "ALERT_TRAVEL_BLOCKED")})
            return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                    {"alert": t(lang, "ALERT_SETTLED", zone=zdef.name_for(lang),
                                density=zdef.density)})
        if verb == "hunt":
            return _start_hunt(ctx, user, zone_id, now, ambush=False)
        if verb == "gather":
            return _gather(ctx, user, zone_id, now)
        if verb == "conquer":
            # kept for legacy / admin use — no UI entry any more
            enemy = ce.make_guardian(zdef.guard if zdef else 1, random.Random())
            if not enemy:
                return t(lang, "ERR_UNKNOWN"), None, {}
            ce.start_session(user, enemy, "conquest", zone_id)
            session = user["combat"]["session"]
            return (R.combat_hud_text(lang, user, session),
                    kbs.battle_kb(lang, user, session), {"send": True})
    return R.map_text(lang, user, ctx.storage), kbs.map_kb(lang, user), {"root": True}


def _start_hunt(ctx, user, zone_id: str, now, ambush: bool):
    lang = user["account"]["language"]
    proto = data_registry.get_random_enemy_by_zone(zone_id)
    if not proto:
        return (t(lang, "ALERT_NO_BEASTS"), None, {"alert": t(lang, "ALERT_NO_BEASTS")})
    enemy = ce.make_beast(proto.enemy_id, random.Random())
    ce.start_session(user, enemy, "hunt", zone_id)
    session = user["combat"]["session"]
    header = t(lang, "AMBUSH_HEADER") if ambush else ""
    return (header + R.combat_hud_text(lang, user, session),
            kbs.battle_kb(lang, user, session), {"send": True})


def _gather(ctx, user, zone_id: str, now):
    """70 harvest / 20 ambush / 10 hazard, 5 Qi per probe (spec P3 §4)."""
    lang = user["account"]["language"]
    zdef = data_registry.get_zone(zone_id)
    if not zdef:
        return t(lang, "ERR_UNKNOWN"), None, {}
    cul = user["cultivation"]
    cost = zdef.gather.qi_cost
    if cul["qi_current"] < cost:
        return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                {"alert": t(lang, "ALERT_GATHER_NO_QI", cost=cost)})
    last = parse_iso(user["progress"].get("last_gather_at"))
    if last and (now - last).total_seconds() < 60:
        wait = int(61 - (now - last).total_seconds())
        return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                {"alert": t(lang, "ALERT_GATHER_CD", wait=wait)})
    cul["qi_current"] -= cost
    user["progress"]["last_gather_at"] = iso(now)
    roll = random.uniform(0, 100)
    g = zdef.gather
    if roll <= 70:
        rare = random.uniform(0, 100) <= 20
        iid = g.uncommon_item_id if (rare and g.uncommon_item_id) else g.common_item_id
        name = (g.uncommon_name_en if (rare and lang == "en") else g.uncommon_name) if rare \
            else (g.common_name_en if lang == "en" else g.common_name)
        from ...engine.models import add_item, ring_has_room
        if not iid:
            return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                    {"alert": t(lang, "ALERT_NOTHING")})
        if not ring_has_room(user, iid) and iid not in user["inventory"].get("items", {}):
            return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                    {"alert": t(lang, "ALERT_RING_FULL")})
        add_item(user, iid, 1)
        return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
                {"alert": t(lang, "ALERT_GATHER_OK", item=name or iid,
                            rare=t(lang, "GATHER_RARE") if rare else t(lang, "GATHER_COMMON"))})
    if roll <= 90:
        text, kb, opts = _start_hunt(ctx, user, zone_id, now, ambush=True)
        return text, kb, opts
    vis = user["stats"]["visible"]
    vis["physique_hp"] = max(1, vis["physique_hp"] - 10)
    return (R.zone_hub_text(lang, user, zone_id), kbs.zone_hub_kb(lang, user, zone_id),
            {"alert": t(lang, "ALERT_GATHER_HAZARD")})


def _bag(ctx, user, args, now):
    lang = user["account"]["language"]
    ui = user.setdefault("ui", {})
    verb = args[0] if args else "tab"
    if verb == "tab":
        tab = args[1] if len(args) > 1 and args[1] in R.BAG_TABS else "gear"
        try:
            page = int(args[2]) if len(args) > 2 else 1
        except ValueError:
            page = 1
        ui["bag_tab"], ui["bag_page"] = tab, page
        return R.bag_text(lang, user, tab, page), kbs.bag_kb(lang, user, tab, page), {}
    if verb == "inspect" and len(args) >= 4:
        tab, page, item_id = args[1], args[2], ":".join(args[3:])
        ui["bag_tab"], ui["bag_page"] = tab, int(page) if page.isdigit() else 1
        return (R.bag_inspect_text(lang, user, item_id),
                kbs.item_kb(lang, user, tab, int(page) if page.isdigit() else 1, item_id), {})
    if verb == "action" and len(args) >= 3:
        act, item_id = args[1], ":".join(args[2:])
        tab = ui.get("bag_tab", "gear")
        page = int(ui.get("bag_page", 1) or 1)

        def _back(extra=None):
            opts = {}
            if extra:
                opts["alert"] = extra
            return R.bag_text(lang, user, tab, page), kbs.bag_kb(lang, user, tab, page), opts

        if act == "equip":
            res = items_mod.equip(user, item_id)
            msg = t(lang, "EQUIP_OK", item=res.get("label", "")) if res.get("status") == "OK" \
                else t(lang, f"ERR_{res.get('status', 'UNKNOWN')}")
            return _back(msg)
        if act == "unequip":
            res = items_mod.unequip(user, item_id)
            return _back(t(lang, "UNEQUIP_OK") if res.get("status") == "OK"
                         else t(lang, f"ERR_{res.get('status', 'UNKNOWN')}"))
        if act == "sell":
            kind = "gear" if item_id in (user["inventory"].get("gear") or {}) else "item"
            res = items_mod.sell(user, kind, item_id)
            return _back(t(lang, "SELL_OK", item=res.get("label", ""), stones=res.get("price", 0))
                         if res.get("status") == "OK" else t(lang, "SELL_FAIL"))
        if act == "consume":
            res = items_mod.use_item(user, item_id)
            if res.get("status") == "OK":
                msg = t(lang, res.get("key", "CONSUME_OK"), item=res.get("label", ""),
                        value=res.get("value", ""))
            else:
                msg = t(lang, f"USE_{res.get('status')}", item=res.get("label", ""))
            return _back(msg)
        if act == "assign_battle":
            items_mod.set_battle_item(user, item_id)
            nm = R.item_name(item_id, lang)
            return _back(t(lang, "ASSIGN_BATTLE_OK", item=nm))
    return R.bag_text(lang, user, "gear", 1), kbs.bag_kb(lang, user, "gear", 1), {}


def _martial(ctx, user, args, now):
    lang = user["account"]["language"]
    verb = args[0] if args else "view"
    if verb == "view":
        return R.martial_text(lang, user), kbs.martial_kb(lang, user), {"root": True}
    if verb == "mantra":
        sub = args[1] if len(args) > 1 else "menu"
        back_to = user.get("ui", {}).get("mantra_back", "martial:view:main")
        if sub == "menu":
            user.setdefault("ui", {})["mantra_back"] = "martial:view:main"
            return R.mantra_menu_text(lang, user), kbs.mantra_kb(lang, user), {}
        if sub == "back":
            return _route(ctx, user, back_to, {})
        if sub == "set" and len(args) > 2:
            mid = args[2]
            m = data_registry.get_method(mid)
            if not m:
                return t(lang, "ERR_UNKNOWN"), kbs.martial_kb(lang, user), {}
            owned = user["inventory"].setdefault("methods", [])
            if mid not in owned:
                if m.tier != "mortal":
                    return (R.mantra_menu_text(lang, user), kbs.mantra_kb(lang, user),
                            {"alert": t(lang, "MANTRA_LOCKED", tier=m.tier)})
                owned.append(mid)
            user["cultivation"]["active_method_id"] = mid
            if back_to.startswith("cultivate"):
                return (R.meditate_hub_text(lang, user, ctx.world_boost(now), now),
                        kbs.meditate_hub_kb(lang),
                        {"alert": t(lang, "MANTRA_SET", name=m.name_for(lang))})
            return (R.martial_text(lang, user), kbs.martial_kb(lang, user),
                    {"alert": t(lang, "MANTRA_SET", name=m.name_for(lang))})
    if verb == "slot":
        from ...engine.models import max_slots as _ms
        cap = _ms(user)
        sub = args[1] if len(args) > 1 else ""
        try:
            idx = int(args[2]) if len(args) > 2 else 0
        except ValueError:
            idx = 0
        if sub == "select":
            if idx >= cap:
                return (R.martial_text(lang, user), kbs.martial_kb(lang, user),
                        {"alert": t(lang, "SLOT_LOCKED_ALERT")})
            return (R.deck_picker_text(lang, user, idx), kbs.slot_picker_kb(lang, user, idx), {})
        loadout = user["combat"]["loadout"]
        if sub == "equip" and len(args) > 3:
            tid = args[3]
            tech = data_registry.get_technique(tid)
            if not tech:
                return t(lang, "ERR_UNKNOWN"), kbs.martial_kb(lang, user), {}
            res = items_mod.set_loadout(user, tid, idx)
            if res.get("status") != "OK":
                return (R.martial_text(lang, user), kbs.martial_kb(lang, user),
                        {"alert": t(lang, f"ERR_{res.get('status', 'UNKNOWN')}")})
            return (R.martial_text(lang, user), kbs.martial_kb(lang, user),
                    {"answer": t(lang, "SLOT_SET", tech=(tech.name_en if lang == "en" and tech.name_en else tech.name), n=idx + 1)})
        if sub == "clear":
            res = items_mod.set_loadout(user, None, idx)
            return (R.martial_text(lang, user), kbs.martial_kb(lang, user),
                    {"answer": t(lang, "SLOT_CLEARED")})
    return R.martial_text(lang, user), kbs.martial_kb(lang, user), {"root": True}


def _shop(ctx, user, args, now):
    lang = user["account"]["language"]
    verb = args[0] if args else "view"
    if verb == "view":
        return R.shop_text(lang, user), kbs.shop_kb(lang), {"root": True}
    if verb == "tab" and args[1:]:
        cat = args[1]
        try:
            page = int(args[2]) if len(args) > 2 else 1
        except ValueError:
            page = 1
        if cat == "arts":
            return (R.arts_catalog_text(lang, user, page), kbs.arts_kb(lang, user, page), {})
        if cat in ("pills", "gear", "talismans"):
            return (R.shop_booth_text(lang, user, cat), kbs.booth_kb(lang, user, cat, page), {})
        return R.shop_text(lang, user), kbs.shop_kb(lang), {}
    if verb == "art" and args[1:]:
        sub = args[1]
        if sub == "inspect" and len(args) >= 4:
            art_id, page = args[2], _as_page(args[3])
            art = data_registry.get_martial_art(art_id)
            if not art:
                return t(lang, "ERR_UNKNOWN"), None, {"alert": t(lang, "ERR_UNKNOWN")}
            return (R.art_inspect_text(lang, user, art_id),
                    kbs.art_card_kb(lang, user, art_id, page), {})
        if sub == "claim_free" and len(args) >= 4:
            art_id, page = args[2], _as_page(args[3])
            art = data_registry.get_martial_art(art_id)
            if not art:
                return t(lang, "ERR_UNKNOWN"), None, {"alert": t(lang, "ERR_UNKNOWN")}
            items_mod.learn_art(user, art_id)
            return (R.art_inspect_text(lang, user, art_id),
                    kbs.art_card_kb(lang, user, art_id, page),
                    {"alert": t(lang, "ART_CLAIMED", name=art.name_for(lang))})
    if verb == "booth" and len(args) >= 4 and args[1] == "claim":
        booth, item_id = args[2], ":".join(args[3:])
        if booth not in items_mod.BOOTH_CATALOGS:
            return R.shop_text(lang, user), kbs.shop_kb(lang), {}
        granted = items_mod.booth_claim(user, item_id, booth=booth)
        nm = R.item_name(item_id, lang)
        if granted:
            return (R.shop_booth_text(lang, user, booth), kbs.booth_kb(lang, user, booth),
                    {"alert": t(lang, "BOOTH_CLAIMED", name=nm, qty=granted)})
        return (R.shop_booth_text(lang, user, booth), kbs.booth_kb(lang, user, booth),
                {"alert": t(lang, "BOOTH_HAVE")})
    return R.shop_text(lang, user), kbs.shop_kb(lang), {"root": True}


def _as_page(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 1


def _sect(ctx, user, args, now):
    lang = user["account"]["language"]
    realm = user["cultivation"]["current_realm_index"]
    verb = args[0] if args else "view"
    if realm < 2:
        return R.sect_gate_text(lang), kbs.sect_gate_kb(lang), {"root": True}
    if verb == "view":
        return R.sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user), {"root": True}
    if verb == "action" and args[1:]:
        act = args[1]
        if act == "join" and len(args) > 2:
            res = world_mod.join_sect(ctx.storage, user, args[2])
        elif act == "leave":
            res = world_mod.leave_sect(ctx.storage, user)
        elif act == "found":
            res = world_mod.found_sect(ctx.storage, user)
        else:
            res = {"status": "ERR"}
        if res.get("status") in ("OK", None):
            note = (t(lang, "SECT_JOINED") if act == "join" else
                    t(lang, "SECT_LEFT") if act == "leave" else t(lang, "SECT_FOUNDED"))
        else:
            # engine statuses are SECT_LOCKED / UNKNOWN_SECT / ALIGNMENT_MISMATCH /
            # SECT_FOUND_LOCKED / ERR — map to SECT_<STATUS> without doubling the prefix
            suffix = str(res.get("status", "ERR")).removeprefix("SECT_")
            note = t(lang, f"SECT_{suffix}")
        return (R.sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user), {"alert": note})
    if verb == "sacrifice" and args[1:]:
        kind = args[1]
        res = world_mod.perform_sacrifice(user, kind, now=now)
        if res.get("status") == "OK":
            return (R.sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user),
                    {"alert": t(lang, "SACRIFICE_OK", boost=res.get("boost", 0),
                                hours=res.get("hours", 0))})
        return (R.sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user),
                {"alert": t(lang, "SACRIFICE_NO")})
    return R.sect_text(lang, user, ctx.storage), kbs.sect_kb(lang, user), {"root": True}


def _breakthrough(ctx, user, args, now):
    lang = user["account"]["language"]
    verb = args[0] if args else "view"
    if verb == "view":
        prep = CultivationEngine.tribulation_prep(user, now=now)
        status = prep["status"]
        if status == "QI_NOT_FULL":
            return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
                    kbs.profile_kb(lang),
                    {"alert": t(lang, "BT_NOT_FULL", have=prep["have"], max=prep["max"])})
        if status != "READY":
            note_key = {"MERIDIANS_SEALED": "STATUS_MERIDIAN_SEALED_T", "IN_COMBAT": "GUARD_COMBAT",
                        "INJURED": "GUARD_INJURED", "AT_DAO_SOVEREIGN": "BT_SUPREME",
                        "CHOOSE_DAO_FIRST": "BT_CHOOSE_DAO"}.get(status, "ERR_UNKNOWN")
            if status == "CHOOSE_DAO_FIRST":
                return R.dao_prompt_text(lang, user), kbs.dao_kb(lang, False), {"root": True}
            return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now), kbs.profile_kb(lang),
                    {"alert": t(lang, note_key)})
        return (R.breakthrough_prep_text(lang, user, prep), kbs.bt_prep_kb(lang), {"root": True})
    if verb == "pill":
        sub = args[1] if len(args) > 1 else "select"
        if sub == "select":
            return t(lang, "BT_PILL_PROMPT"), kbs.bt_pill_kb(lang, user), {}
        if sub == "use" and len(args) > 2:
            iid = args[2]
            item = data_registry.get_consumable(iid)
            if not item or item.action != "breakthrough_bonus":
                return t(lang, "ERR_UNKNOWN"), kbs.bt_prep_kb(lang), {}
            if not items_mod.consume_item(user, iid):
                return t(lang, "ITEM_NONE_LEFT", item=item.name_for(lang)), kbs.bt_prep_kb(lang), {}
            user["cultivation"]["active_pill"] = iid
            prep = CultivationEngine.tribulation_prep(user, now=now)
            if prep["status"] != "READY":
                prep = {"status": "READY", "rate": CultivationEngine.spec_success_rate(user),
                        "qi_deviation_risk": 0.0, "dao_heart": user["stats"]["hidden"]["dao_heart_stability"],
                        "target_realm": user["cultivation"]["current_realm_index"],
                        "target_stage": user["cultivation"]["current_stage"]}
            return (R.breakthrough_prep_text(lang, user, prep), kbs.bt_prep_kb(lang),
                    {"alert": t(lang, "BT_PILL_TAKEN", name=item.name_for(lang))})
        if sub == "drop":
            user["cultivation"]["active_pill"] = None
            prep = CultivationEngine.tribulation_prep(user, now=now)
            if prep["status"] == "READY":
                return (R.breakthrough_prep_text(lang, user, prep), kbs.bt_prep_kb(lang), {})
            return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
                    kbs.profile_kb(lang), {})
    if verb == "action" and (len(args) < 2 or args[1] == "confirm"):
        prep = CultivationEngine.tribulation_prep(user, now=now)
        if prep["status"] != "READY":
            if prep["status"] == "CHOOSE_DAO_FIRST":
                return R.dao_prompt_text(lang, user), kbs.dao_kb(lang, False), {"root": True}
            alert = (t(lang, "BT_NOT_FULL", have=prep.get("have", user["cultivation"]["qi_current"]),
                       max=prep.get("max", user["cultivation"]["qi_capacity"]))
                     if prep["status"] == "QI_NOT_FULL" else
                     t(lang, {"MERIDIANS_SEALED": "STATUS_MERIDIAN_SEALED_T",
                              "IN_COMBAT": "GUARD_COMBAT", "INJURED": "GUARD_INJURED",
                              "AT_DAO_SOVEREIGN": "BT_SUPREME"}.get(prep["status"], "ERR_UNKNOWN")))
            return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
                    kbs.profile_kb(lang), {"alert": alert})
        res = CultivationEngine.confirm_tribulation(user, now=now, rng=random.Random())
        if res["status"] == "SUCCESS" or res["status"] == "MIRACLE_SAVED":
            text = R.breakthrough_win_text(lang, user, res)
            if res["status"] == "MIRACLE_SAVED":
                text = t(lang, "BT_MIRACLE_LINE") + "\n\n" + text
            kb = kbs.bt_win_kb(lang)
        else:
            text = R.breakthrough_fail_text(lang, res)
            kb = kbs.bt_fail_kb(lang)
        # the async layer performs the 2s×2 dramatic edits before this final
        trib = {"phases": [t(lang, "BT_PHASE_1"), t(lang, "BT_PHASE_2")],
                "success": res["status"] in ("SUCCESS", "MIRACLE_SAVED")}
        return text, kb, {"tribulation": trib, "root": True}
    return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
            kbs.profile_kb(lang), {"root": True})


def _dao(ctx, user, args, now):
    lang = user["account"]["language"]
    if len(args) >= 3:
        dao, align = args[1], args[2]
        res = items_mod.choose_dao(user, dao, align) if hasattr(items_mod, "choose_dao") \
            else _choose_dao(user, dao, align)
        if res.get("status") == "OK":
            return (R.hud_text(ctx.storage, user, ctx.world_boost(now), now),
                    kbs.profile_kb(lang), {"alert": t(lang, "DAO_CHOSEN", dao=R.DAO_PATHS[dao]["key"])})
        return R.dao_prompt_text(lang, user), kbs.dao_kb(lang, False), {"alert": t(lang, "ERR_UNKNOWN")}
    return R.dao_prompt_text(lang, user), kbs.dao_kb(lang, False), {}


def _choose_dao(user, dao, align) -> dict:
    from ...engine.constants import DAO_PATHS
    if dao not in DAO_PATHS:
        return {"status": "ERR"}
    spec = DAO_PATHS[dao]
    allowed = ("demonic",) if dao == "blood" else ("orthodox", "demonic")
    if align not in allowed:
        return {"status": "ERR"}
    user["cultivation"]["dao_path"] = dao
    user["cultivation"]["alignment"] = align
    user["cultivation"]["alignment_locked"] = True
    return {"status": "OK"}


def _admin_drops(ctx, user: dict):
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
    text = R.admin_text(lang, ctx.storage, ctx.boost_label()) + "\n\n" + \
        t(lang, "DONE_DROPS", stones=stones, qi=qi_gift) + f" ({count} users)"
    return text, kbs.admin_kb(lang), {}

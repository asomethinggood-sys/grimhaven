"""God-Mode admin terminal commands (doc §9.1).

/broadcast <lang: fa|en|all> <text>   — Heavenly Revelation to all users
/inspect <user_id>                     — full profile incl. hidden stats
/modify_qi <user_id> <amount>          — instant Dantian adjustment
/set_realm <user_id> <realm 1-9> <stage>
/grant_item <user_id> <item_id> <tier>
/seal_meridians <user_id> <hours>
/purge_demon <user_id>
/world_boost <rate> <duration_hours>
"""
from __future__ import annotations

import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from ...engine import items as items_mod
from ...engine.constants import MAX_REALM, METHODS, REALM_NAMES, REALM_STAGES
from ...engine.models import iso, recompute_visible_stats, utcnow
from ...localization import t, t_map
from .common import Ctx

USAGE = {
    "broadcast": "USAGE_BROADCAST",
    "inspect": "USAGE_INSPECT",
    "modify_qi": "USAGE_MODIFY_QI",
    "set_realm": "USAGE_SET_REALM",
    "grant_item": "USAGE_GRANT_ITEM",
    "seal_meridians": "USAGE_SEAL",
    "purge_demon": "USAGE_PURGE",
    "world_boost": "USAGE_BOOST",
}


def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Ctx | None:
    ctx: Ctx = context.bot_data["ctx"]
    if not ctx.is_admin(update.effective_user.id):
        return None
    return ctx


def _lang_of(context, update) -> str:
    ctx: Ctx = context.bot_data["ctx"]
    user = ctx.storage.get_user(update.effective_user.id)
    return user["account"]["language"] if user else "fa"


async def _usage(update: Update, context: ContextTypes.DEFAULT_TYPE, cmd: str) -> None:
    lang = _lang_of(context, update)
    await update.message.reply_text(t(lang, USAGE[cmd]))


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    args = context.args or []
    if len(args) < 2 or args[0] not in ("fa", "en", "all"):
        await _usage(update, context, "broadcast")
        return
    target_lang, text = args[0], " ".join(args[1:])
    sent = 0
    for doc in ctx.storage.all_users():
        lang = doc["account"]["language"]
        if target_lang != "all" and lang != target_lang:
            continue
        try:
            await context.bot.send_message(
                doc["user_id"], t(lang, "GLOBAL_BROADCAST", text=text))
            sent += 1
        except Exception:  # blocked/deleted users
            continue
    await update.message.reply_text(t(_lang_of(context, update), "DONE_BROADCAST", n=sent))


async def cmd_inspect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    if not context.args or not context.args[0].isdigit():
        await _usage(update, context, "inspect")
        return
    target = ctx.storage.get_user(int(context.args[0]))
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    cul, vis, hid = target["cultivation"], target["stats"]["visible"], target["stats"]["hidden"]
    realm = cul["current_realm_index"]
    from ...render import realm_stage_label
    from ...core.data_loader import data_registry
    zone = ctx.storage.get_zone(target["location"]["current_zone_id"])
    sect = ctx.storage.get_sect(target["location"].get("sect_id") or "") if target["location"].get("sect_id") else None
    # resolve the active method to its display name — never leak the raw id
    _m = data_registry.get_method(cul.get("active_method_id") or "")
    if _m is None and cul.get("active_method_id") in METHODS:
        method_name = t(lang, METHODS[cul["active_method_id"]]["key"])
    else:
        method_name = _m.name_for(lang) if _m else (cul.get("active_method_id") or "—")
    text = t_map(lang, "INSPECT_REPORT", {
        "name": target["account"]["username"], "id": target["user_id"],
        "lang": target["account"]["language"],
        "banned": "⛔" if target["account"].get("is_banned") else "—",
        "realm": t(lang, REALM_NAMES[realm]),
        "stage": realm_stage_label(lang, realm, cul["current_stage"]),
        "qi": cul["qi_current"], "cap": cul["qi_capacity"],
        "path": cul.get("dao_path") or "—",
        "alignment": cul["alignment"],
        "method": method_name,
        "hp": f"{vis['physique_hp']}/{vis['max_hp']}",
        "sense": vis["spiritual_sense"], "circ": vis["circulation_velocity"],
        "luck": hid["karmic_luck"], "charisma": hid["dao_affinity_charisma"],
        "daoheart": hid["dao_heart_stability"], "corruption": hid["demonic_corruption"],
        "zone": t(lang, zone["key"]) if zone else "—",
        "sect": (t(lang, sect["key"]) if sect and sect.get("key") else (sect or {}).get("name", "—")),
    })
    await update.message.reply_text(text)


async def cmd_modify_qi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    if len(context.args or []) != 2 or not context.args[1].lstrip("-").isdigit():
        await _usage(update, context, "modify_qi")
        return
    target = ctx.storage.get_user(int(context.args[0]))
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    amount = int(context.args[1])
    cul = target["cultivation"]
    cul["qi_current"] = max(0, min(cul["qi_capacity"], cul["qi_current"] + amount))
    ctx.storage.save_user(target)
    await update.message.reply_text(t(lang, "DONE_MODIFY_QI", id=target["user_id"], amount=amount))


async def cmd_set_realm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    args = context.args or []
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        await _usage(update, context, "set_realm")
        return
    target = ctx.storage.get_user(int(args[0]))
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    realm = max(1, min(MAX_REALM, int(args[1])))
    stage = max(0, min(int(args[2]), len(REALM_STAGES[realm]) - 1))
    cul = target["cultivation"]
    cul["current_realm_index"] = realm
    cul["current_stage"] = stage
    cul["qi_capacity"] = REALM_STAGES[realm][stage]
    cul["qi_current"] = min(cul["qi_current"], cul["qi_capacity"])
    recompute_visible_stats(target)
    ctx.storage.save_user(target)
    from ...render import realm_stage_label
    await update.message.reply_text(
        t(lang, "DONE_SET_REALM", id=target["user_id"], realm=t(lang, REALM_NAMES[realm]),
          stage=realm_stage_label(lang, realm, stage)))


async def cmd_grant_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    args = context.args or []
    if len(args) < 2:
        await _usage(update, context, "grant_item")
        return
    target = ctx.storage.get_user(int(args[0]) if args[0].isdigit() else -1)
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    tier = args[2] if len(args) > 2 else "mortal"
    res = items_mod.grant_item(target, args[1], tier)
    ctx.storage.save_user(target)
    # grant_item returns a ready display name in "label" — never show the raw id
    await update.message.reply_text(
        t(lang, "DONE_GRANT_ITEM", item=res.get("label") or args[1], id=target["user_id"]))


async def cmd_seal_meridians(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    args = context.args or []
    if len(args) != 2 or not args[1].isdigit():
        await _usage(update, context, "seal_meridians")
        return
    target = ctx.storage.get_user(int(args[0]))
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    hours = int(args[1])
    target["cultivation"]["meridian_sealed_until"] = iso(utcnow() + dt.timedelta(hours=hours))
    ctx.storage.save_user(target)
    await update.message.reply_text(t(lang, "DONE_SEAL", id=target["user_id"], hours=hours))


async def cmd_purge_demon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    if not context.args or not context.args[0].isdigit():
        await _usage(update, context, "purge_demon")
        return
    target = ctx.storage.get_user(int(context.args[0]))
    if not target:
        await update.message.reply_text(t(lang, "USER_NOT_FOUND"))
        return
    target["stats"]["hidden"]["demonic_corruption"] = 0
    ctx.storage.save_user(target)
    await update.message.reply_text(t(lang, "DONE_PURGE", id=target["user_id"]))


async def cmd_world_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _guard(update, context)
    if not ctx:
        return
    lang = _lang_of(context, update)
    args = context.args or []
    if len(args) != 2:
        await _usage(update, context, "world_boost")
        return
    try:
        rate, hours = float(args[0]), float(args[1])
    except ValueError:
        await _usage(update, context, "world_boost")
        return
    until = utcnow() + dt.timedelta(hours=hours)
    ctx.storage.set_meta("world_boost", {"rate": rate, "until": iso(until)})
    await update.message.reply_text(t(lang, "DONE_BOOST", rate=rate, hours=hours))
    # announce the event to every user
    for doc in ctx.storage.all_users():
        try:
            await context.bot.send_message(
                doc["user_id"],
                t(doc["account"]["language"], "WORLD_BOOST_NOTICE", rate=rate, hours=hours))
        except Exception:
            continue

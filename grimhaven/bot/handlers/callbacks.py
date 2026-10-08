"""Inline-button dispatcher — every game action of the design doc."""
from __future__ import annotations

import random

from telegram import Update
from telegram.ext import ContextTypes

from ...engine import combat as combat_mod
from ...engine import items as items_mod
from ...engine import world as world_mod
from ...engine.constants import (
    DAO_PATHS,
    HERBS,
    METHODS,
    NPC_SECTS,
    PILLS,
    REALM_NAMES,
    REALM_STAGES,
    SPIRIT_STONES,
    TECHNIQUES,
    ZONES,
)
from ...engine.cultivation import CultivationEngine
from ...engine.models import afk_hourly_rate, in_seclusion, parse_iso, utcnow
from ...localization import t
from ...render import (
    backpack_text,
    battle_log_text,
    dao_text,
    map_text,
    martial_text,
    realm_stage_label,
    sect_text,
    shop_text,
    target_label,
    zone_text,
)
from .. import keyboards as kbs
from .common import Ctx


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx: Ctx = context.bot_data["ctx"]
    data = query.data or ""
    tg_user = update.effective_user
    user, _ = ctx.get_or_create_user(tg_user)
    if user["account"].get("is_banned") and not data.startswith("setlang"):
        await query.answer(t(user["account"]["language"], "ERR_BANNED"))
        return
    lang = user["account"]["language"]
    await query.answer()

    # always accrue offline Qi before acting
    settle_res = ctx.settle(user)

    action, *rest = data.split(":", 1)
    arg = rest[0] if rest else ""

    try:
        text, keyboard = _dispatch(ctx, user, action, arg, settle_res)
    except Exception:  # pragma: no cover — safety net, never crash the bot
        import traceback
        traceback.print_exc()
        text, keyboard = ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
    ctx.save(user)
    if keyboard is None:
        await query.edit_message_text(text)
    else:
        await query.edit_message_text(text, reply_markup=keyboard)


def _dispatch(ctx: Ctx, user: dict, action: str, arg: str, settle_res: dict):
    lang = user["account"]["language"]
    now = utcnow()

    # ── navigation ───────────────────────────────────────────────────────────
    if action == "menu":
        return ctx.profile_reply(user, now)
    if action == "language":
        return "🌐 Language / زبان", kbs.language_kb()
    if action == "setlang":
        user["account"]["language"] = arg if arg in ("fa", "en") else "fa"
        lang = user["account"]["language"]
        return ctx.profile_reply(user, now)

    # ── cultivation ──────────────────────────────────────────────────────────
    if action == "meditate":
        res = CultivationEngine.start_meditation(user, now)
        rate = round(afk_hourly_rate(user, world_boost=ctx.world_boost(now), now=now))
        if res["status"] == "ALREADY_MEDITATING":
            return ctx.back_reply(user, t(lang, "ALREADY_MEDITATING"))
        if res["status"] == "IN_SECLUSION":
            return ctx.back_reply(user, t(lang, "SECLUSION_ACTIVE",
                                          minutes=_seclusion_left(user, now)))
        return (t(lang, "MEDITATION_STARTED", rate=rate), kbs.main_menu(lang, True, False))
    if action == "stop_meditate":
        res = CultivationEngine.stop_meditation(user, now)
        if res["status"] == "NOT_MEDITATING":
            return ctx.back_reply(user, t(lang, "NOT_MEDITATING"))
        return ctx.back_reply(user, t(lang, "MEDITATION_STOPPED", gained=res["gained"]))

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

    # ── backpack / items ─────────────────────────────────────────────────────
    if action == "backpack":
        return backpack_text(lang, user), kbs.backpack_kb(lang, user)
    if action == "stone":
        res = items_mod.activate_stone(user, arg)
        if res["status"] == "NO_STONES":
            return ctx.back_reply(user, t(lang, "NO_STONES"))
        return ctx.back_reply(user, t(lang, "STONE_ACTIVATED", stone=arg,
                                      boost=int(res["boost"] * 100)))
    if action == "pill":
        res = items_mod.use_pill(user, arg)
        if res["status"] == "NO_PILL":
            return ctx.back_reply(user, t(lang, "NO_PILL"))
        return ctx.back_reply(user, t(lang, "PILL_USED",
                                      pill=t(lang, PILLS[arg]["key"]), bonus=res["bonus"]))
    if action == "herb":
        res = items_mod.use_herb(user, arg)
        if res["status"] == "NO_HERB":
            return ctx.back_reply(user, t(lang, "NO_HERB"))
        key = "HERB_USED_ROOT" if res["status"] == "ROOT" else "HERB_USED_LOTUS"
        return ctx.back_reply(user, t(lang, key))
    if action == "equip":
        res = items_mod.equip(user, arg)
        if res["status"] != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return backpack_text(lang, user), kbs.backpack_kb(lang, user)
    if action == "unequip":
        items_mod.unequip(user, arg)
        return backpack_text(lang, user), kbs.backpack_kb(lang, user)

    # ── shop ─────────────────────────────────────────────────────────────────
    if action == "shop":
        if items_mod.shop_locked(user):
            return ctx.back_reply(user, t(lang, "SHOP_LOCKED"))
        return shop_text(lang, user), kbs.shop_kb(lang)
    if action == "buy":
        res = items_mod.buy(user, arg)
        if res["status"] == "NOT_ENOUGH_STONES":
            return ctx.back_reply(user, t(lang, "NOT_ENOUGH_STONES", price=res["price"]))
        return ctx.back_reply(user, t(lang, "BOUGHT",
                                      item=_shop_item_name(lang, arg), price=res["price"]))

    # ── martial arts ─────────────────────────────────────────────────────────
    if action == "martial":
        return martial_text(lang, user), kbs.martial_kb(lang, user)
    if action == "method":
        res = items_mod.activate_method(user, arg)
        if res["status"] != "OK":
            return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))
        return ctx.back_reply(user, t(lang, "METHOD_ACTIVATED",
                                      method=t(lang, METHODS[arg]["key"]), mult=res["tech_mult"]))
    if action == "loadout":
        slot_s, tech = arg.split(":", 1)
        tech_id = None if tech in ("clear", "none") else tech
        items_mod.set_loadout(user, int(slot_s), tech_id)
        return martial_text(lang, user), kbs.martial_kb(lang, user)

    # ── map / zones / conquest ───────────────────────────────────────────────
    if action == "map":
        return map_text(lang, user, ctx.storage), \
            kbs.map_kb(lang, ctx.storage, user["location"]["current_zone_id"])
    if action == "zone":
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
    if action == "conquer":
        if in_seclusion(user, now):
            return ctx.back_reply(user, t(lang, "FIGHT_BLOCKED_SECLUSION"))
        zone = world_mod.zone_doc(ctx.storage, arg)
        if zone.get("owner_kind") == "user" and zone.get("owner") == user["user_id"]:
            return ctx.back_reply(user, t(lang, "ZONE_ALREADY_YOURS"))
        rng = random.Random()
        guardian = combat_mod.make_guardian(zone["guard_level"], rng)
        result = combat_mod.duel(user, guardian, rng=rng)
        report = battle_log_text(lang, result)
        if result["won"]:
            world_mod.apply_conquest_result(ctx.storage, user, arg, won=True, now=now)
            return ctx.back_reply(user, report + "\n\n" + t(lang, "CONQUEST_VICTORY", zone=t(lang, zone["key"])))
        return ctx.back_reply(user, report + "\n\n" + t(lang, "CONQUEST_DEFEAT"))
    if action == "hunt":
        if in_seclusion(user, now):
            return ctx.back_reply(user, t(lang, "FIGHT_BLOCKED_SECLUSION"))
        zone = world_mod.zone_doc(ctx.storage, arg)
        monsters = world_mod.MONSTERS_PER_GUARD.get(zone["guard_level"], ["hungry_wolf"])
        monster_key = random.choice(monsters)
        res = combat_mod.hunt_monster(user, zone["guard_level"], monster_key)
        if res.get("status") == "BLOCKED":
            return ctx.back_reply(user, t(lang, "FIGHT_BLOCKED_SECLUSION"))
        report = battle_log_text(lang, res)
        if res["won"]:
            extra = t(lang, "FIGHT_MONSTER_VICTORY", monster=t(lang, res["monster"]),
                      stones=res["stones"], qi=res["qi"])
            # rival encounter: mercy or plunder choice after every third win
            if user["combat"]["wins"] % 3 == 0:
                user.setdefault("pending_rival", True)
                return report + "\n\n" + extra, kbs.mercy_plunder_kb(lang)
            return ctx.back_reply(user, report + "\n\n" + extra)
        return ctx.back_reply(user, report + "\n\n" + t(lang, "FIGHT_MONSTER_DEFEAT",
                                                        monster=t(lang, res["monster"])))
    if action in ("mercy", "plunder"):
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

    # ── Dao & alignment ──────────────────────────────────────────────────────
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

    # ── admin panel buttons ──────────────────────────────────────────────────
    if action == "admin":
        if not ctx.is_admin(user["user_id"]):
            return ctx.back_reply(user, t(lang, "ADMIN_ONLY"))
        if arg == "drops":
            return _admin_drops(ctx, user)
        sub = arg.split(":", 1)[1] if ":" in arg else arg
        usage_key = {
            "usage": {
                "broadcast": "USAGE_BROADCAST", "inspect": "USAGE_INSPECT",
                "realm": "USAGE_SET_REALM", "judge": "USAGE_SEAL", "boost": "USAGE_BOOST",
            },
        }.get("usage", {}).get(sub, "USAGE_BROADCAST") if arg.startswith("usage") else "USAGE_BROADCAST"
        from ...render import admin_text
        return admin_text(lang, ctx.storage, ctx.boost_label()) + "\n\n" + t(lang, usage_key), \
            kbs.admin_kb(lang)

    return ctx.back_reply(user, t(lang, "ERR_UNKNOWN"))


def _admin_drops(ctx: Ctx, user: dict):
    """🎁 Rain of Blessings: every registered user gets stones + Qi."""
    from ...engine.cultivation import CultivationEngine as _CE  # noqa: F401
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


# ── helpers ──────────────────────────────────────────────────────────────────

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
    if res["status"] == "SUCCESS":
        return ctx.back_reply(user, t(lang, "BREAKTHROUGH_SUCCESS", target=target))
    if res["status"] == "MIRACLE_SAVED":
        return ctx.back_reply(user, t(lang, "MIRACLE_SAVED", target=target))
    ftype = res.get("type")
    if ftype == "MINOR":
        return ctx.back_reply(user, t(lang, "FAILED_MINOR",
                                      qi_burned=res.get("qi_burned", 0),
                                      sealed_hours=res.get("sealed_hours", 6)))
    if ftype == "DEVIATION":
        lost = sum(res.get("stones_lost", {}).values())
        return ctx.back_reply(user, t(lang, "FAILED_DEVIATION", stones_lost=lost))
    if res.get("saved_by_doll"):
        return ctx.back_reply(user, t(lang, "SAVED_BY_DOLL"))
    shattered = res.get("shattered")
    shatter = ""
    if shattered:
        from ...render import gear_label
        shatter = t(lang, "SHATTER_NOTE", item=gear_label(lang, shattered))
    return ctx.back_reply(user, t(lang, "FAILED_ANNIHILATION", shatter=shatter))


def _shop_item_name(lang: str, shop_id: str) -> str:
    from ...render import shop_button_label
    label = shop_button_label(lang, shop_id)
    return label.split("—")[0].strip()

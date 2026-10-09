# Upgrade v2 — implementation map (from docs: google_drive/mainPrompt2.txt + dataenemies.json)

Six spec parts → python-telegram-bot implementation (spec written for aiogram; map concepts).

## P0 Data layer
- `data/enemies.json` = NEW schema: {beasts:[10 spec enemies (new schema: enemy_id, zone_id, name, tier_title, icon, lore, stats{max_hp,spiritual_atk,defense,speed}, rewards{exp_qi,spirit_stones,loot_drops[{item_id,name,chance_percent}]}, actions[{action_id,name,damage_multiplier,descriptions{hit,crit,miss}}]) + converted legacy beasts for zones 3-7], guardians:[...keep], rival:{...keep}}.
- Zone rename → spec ids: zone_valley_mortals, zone_ordinary_cave, zone_bamboo_forest, zone_mist_peak, zone_blood_marsh, zone_thunder_plateau, zone_heavenly_spring. Alias map in ensure_v2 from old ids (mortal_valley/common_cave/misty_peak/heaven_spring).
- ZONES get: key(label), density, min_realm, danger_tier(1🟢 1peak🟡 2🟠 3🔴 4🟣), lore, gather (list of item ids/herbs).
- New materials in consumables.json: wolf_pelt, wolf_fang + per-enemy loot ids + zone gather herbs/ores (category material, sell price).
- `data/cultivation_methods.json`: METHODS moved from constants → data; data_loader adds: Enemy models, enemies_by_zone, get_random_enemy_by_zone, methods index.

## P1 UI lifecycle (bot layer)
- reply_kb: is_persistent=True, resize_keyboard=True, one_time_keyboard=False. 8 buttons EXACT labels (fa): 🧘 مدیتیشن و تهذیب|⚡ اقدام به شکست سد / 🗺 نقشه و شکار|🎒 کوله‌پشتی و گنجینه / 📜 لوح سرنوشت (پروفایل)|🏛 پاویون تجارت / ⛩ فرقه|⚙️ تنظیمات. NO inline duplication of these 8 anywhere.
- user["ui"]["active_menu_message_id"]: dock presses → delete old menu msg (fallback strip markup, catch MessageNotModified/BadRequest), send new root, store id. In-tab nav = edit in place.
- /panel: delete user's cmd msg → dock+«⚡ جریان چی در دستانت جاری شد؛ پنل سرنوشت پایدار گردید.» → track msg id. Register in BotFather commands.
- /start: ≤2-line narrative + ONE inline [📜 گشودن لوح سرنوشت]=profile:view:main + separate dock msg («لوح ابزارهای جهان تهذیب بر صفحه نقش بست.»). No language wall; lang toggle lives in تنظیمات (settings screen: lang, martial hall, help, panel hint).

## P1 Profile HUD (render+callbacks)
- callbacks: profile:view:main | profile:view:meridians | profile:view:karma.
- main template (spec §4.1): realm+layer, qi bar ▰▱, rate+location, hp bar, atk, def phys/spirit, speed, equipped block (skip empty slots ENTIRELY — never «— هیچ —»), sect line or «آواره و بدون وابستگی». 2 inline buttons.
- meridians modal: divine sense(+crit%,comprehension), dao heart %, deviation risk %, fortune, qi purity %, meridians X/8, lifespan years, alignment|karma. Single 🔙 back (profile:view:main) — edit in place.
- karma modal: alignment themed (نور/تاریکی/بی‌طرف), karma pts, corruption idx, wins/losses/deaths, dao path milestones. back.
- Derived helpers in models: crit_rate (sense), comprehension, qi_purity, meridians_opened (realm-based /8), lifespan (realm table), karma = luck? NO: karma = karmic_luck. alignment label themed.

## P2 Bag (bag:tab:<t>:<page>, bag:inspect, bag:action:*)
- header: 🎒 ─── [ کوله‌پشتی و حلقه فضایی ] ─── 🎒 / ظرفیت انبار used/max قلم / سنگ‌های روح low🟢 mid🔵 high🟣.
- tab row always: 🔸 badge on active (🔸 ⚔️ تجهیزات etc).
- gear tab: equipped-on-body section (active slots only) + spare gear buttons; empty notice «هیچ جنگ‌افزار اضافه‌ای در حلقه فضایی یافت نمی‌شود.»
- 5 items/page, nav row [◀️ قبلی]|[صفحه X از Y noop]|[بعدی ▶️].
- inspect card 🔍 شناسنامه آیتم: icon name [tier], qty, lore «», effects, sell value. Actions by category: consumable [👄 بلعیدن آنی (خارج از نبرد)]|[⚔️ تخصیص به اسلات نبرد] / [💰 فروش (n)] / [🔙 بازگشت به لیست → origin tab:page]; gear [⚔️ تجهیز]|[🔓 خارج کردن] / sell / back; material sell / back.
- ⚔️ تخصیص → user["combat"]["item_slots"][:2] = battle keyboard row 4 sources (fallback: first 2 usable owned).
- NO raw slugs visible anywhere (test!).

## P3 Map & gathering (map:view:world, map:zone:inspect:<z>, map:action:settle|hunt|gather:<z>)
- 2-col grid w/ 🟢🟡🟠🔴🟣 + density (۱.۰×…), back inline row.
- gated zone click → alert only (show_alert) «⚠️ هاله این منطقه کالبد ضعیف تو را درهم خواهد شکست! … حداقل … قلمرو {required}» — NO screen change.
- zone card: danger badge, min realm, lore «», density, native beasts summary (from enemies_by_zone), resources list. Buttons: [🧘 استقرار و برپایی خیمه مراقبه]|✅ already settled(noop) / [⚔️ گشت‌زنی و شکار بیست بومی]|[🌿 کاوش گیاهان و رگه‌های مخفی] / [🗺️ بازگشت به نقشه جهان]. Conquest removed from UI (engine keeps for admin/legacy).
- settle = travel: update zone+density, show_alert «🧘 در {zone} مستقر شدی… نرخ {density}×…», refresh kb.
- gather: cost 5 Qi (+60s cooldown user["progress"]["last_gather_at"]); roll 70 harvest (1-3 items from zone gather list; ring full → alert), 20 → AMBUSH straight into live combat, 10 → hazard −10 HP «⚠️ پای بر گازی سمی نهادی؛ ۱۰ واحد…».

## P4 Combat (the centerpiece)
- Rewrite core/combat_engine.py round model: session in user["combat"]["session"] (keep fields; enemy now spec-shape w/ actions pool).
- Flow per action: validate (round token in cb data `combat:act:<verb>:<id>:<round>`) → strip clicked msg markup → resolve → persist → **send NEW message** for next round (not edit).
- Ghost click / stale round → `answer("⚠️ این فرصت از دست رفت! لطفا از آخرین پیام نبرد اقدام کنید.")`.
- HUD template exactly per spec §2 (▰▱ 10-seg bars, ⚡VS⚡ section, 📜 وقایع راند پیشین: 🔹 player narrative / 🔸 enemy counter).
- 3-tier narratives: player: martial_arts.json descriptions normal/crit/grazed (crit via sense check; grazed when def absorbs >50%); enemy: random action from enemy.actions, roll miss(evade vs speed)/crit(10-15%)/hit; .format(damage=…, target=…) + fa digits.
- keyboard matrix: 2-col techniques [⚡ name (۱۵ چی)] / cd → [⏳ name (۲ دور)] noop / low qi → [🚫 name (کمبود چی)] noop; row [👊 ضربه مریدین (+۱۰ چی)]|[💨 تکنیک فرار]; row ≤2 combat items [📜/💊 name (×n)]; row [⚡ شبیه‌سازی آنی نبرد].
- victory → loot scroll card per spec (+exp_qi, +stones, loot_drops roll 1..100 ≤ chance), single btn [🎒 جمع‌آوری و استقرار در منطقه]→map:zone:inspect:<zone>. flee card «💨 با گام‌های بادپا…» + [🗺️ بازگشت به اقلیم منطقه]. death: miracle(3h injury) / true death(25% qi,15% stones,45min paralysis) themed cards.
- statuses: keep (tech status_inflict) minimal text line «🌀 وضعیت: پایدار» or effects list.
- simulate: loop w/o messages → end card directly.

## P5 Martial pavilion / deck / sect gate / free shop
- martial:view:main: active mantra + capacity n/max, slots [۱..۶] (🔒 beyond capacity), learned-but-unassigned list; kb: [📜 تعویض روش درونی (مانترا)] / [🔄 اسلات i]s / [🔙 بازگشت به لوح سرنوشت].
- capacity: base 3; realm≥2 +1; realm≥3 +1; Mind Expansion artifact equipped +1; cap 6. Locked slots reject.
- slot select flow (edit in place, list unassigned techs `[⚡ name (qi)]`, clear, cancel). equip dedupes (swap). Persist.
- martial:mantra:menu → list owned/available methods (from methods data) → set active → back to hub. (Same as cultivate:mantra:select — share screen.)
- sect: realm<2 → gatekeeper card exactly (⛩️ دروازه کوهستان فرقه… «فانی نوپا!…» + status line) + single 🔙 btn; ≥2 existing dashboard restyled.
- shop: NO realm gate (L1 open). shop:view:hub (balances + free-era banner) rows: [📜 طومارهای هنرهای رزمی (۳۰ مکتب)] / [💊 اکسیرها و قرص‌ها]|[⚔️ سلاح و ردا] / [📜 طلسم‌های نبرد]|[🔙 لوح].
- arts catalog: 6/page × 5 pages, owned ✅; shop:art:inspect:<id>:<page> card (element, id shown?? NO raw slug — spec card shows شناسه مکتب: {art_id} → spec EXPLICITLY wants it there: keep, it's a card not button… follow spec: show art_id line), 3 sub-techniques lines, price 0; [📥 دریافت و آموختن طومار (رایگان)] → alert «✨ طومار … حک شد!…» + button morph to [🥋 انتقال به تالار جهت تجهیز اسلات] + [🔙 بازگشت به لیست طومارها (page!)].
- pills/gear/talismans booths: free claim, cap 5 per claim; buttons shop:tab:<x> lists.

## P6 Meditation hub / chronicle / tribulation
- Cultivation is CONTINUOUS idle accrual; no start/stop trance anymore; meditating locks REMOVED (GUARD_MEDITATING retired; combat/seclusion-tribulation/injury guards kept; seclusion replaced by instant dramatic trial).
- cultivate:view:hub: template (location+mantra, qi bar % + rate + elapsed since last claim + unclaimed) rows: [📥 استخراج چی و مرور سیر آفاق و انفس] / [📜 تعویض طومار تنفس و مانترا]|[🔮 استفاده از کاتالیزور] / [🔙 بازگشت به لوح سرنوشت].
- claim → OfflineAdventureEngine rewrite: event counts 1/2/3/4 (<1h, 1-4h, 4-8h, ≥8h) sampled w/o replacement from offline_events.json (50), timestamps HH:MM interpolated chronologically; apply effects (bonus_qi, spirit_stones, dao_heart clamp 0-100, hp, item if slots); report template exact; single btn [🧘 بازگشت به مراقبه].
- catalyst menu: stones grades (+owned count) & pills → use → alert → back hub.
- breakthrough: dock ⚡ or /breakthrough → pre-flight qi full else alert «⚠️ دان‌تیان تو هنوز لبریز نشده است (x/y)! شکست زودهنگام سد، شریان‌هایت را پاره خواهد کرد.»
- prep screen breakthrough:view:prep: target realm/layer, success% [formula: clamp(10,90, base + daoheart*.25 + pill + fortune*.2 - deviation*.4 - realm_pen)], warning block; rows: [⚡ فرود آوردن خشم آسمان (اقدام نهایی)] / [💊 مصرف قرص تثبیت‌کننده] / [🔙 انصراف و تحکیم تمرکز].
- confirm: strip kb; edit1 «☁️ ابرهای تیره…»; sleep 2; edit2 «⚡ صاعقه طلایی…»; sleep 2; roll →
  success card 🎉 (new realm/layer, +max_qi, +hp, +atk, +lifespan, slot-unlock note if major realm) btn [📜 ثبت منزلت در لوح سرنوشت].
  fail card 💥 (qi half, HP→20%, 120-min inner_demon_deviation debuff rate×.5, dao heart −3) btns [💊 ترمیم جراحات در کوله‌پشتی→bag:tab:consumables:1]|[🧘 استقرار مجدد جهت بهبودی→cultivate:view:hub].
- Tribulation at major realm crossings: keep bolt damage flavor line inside success card (TRIB_BOLT) — existing tribulation rolls merge into outcome resolution (miracle saved etc.).

## Cross-cutting
- All fa templates from spec verbatim; en mirrors. Persian digits via num().
- Legacy callback aliases (menu→profile:view:main, bag:tab:x→:1, martial→martial:view:main, meditate→cultivate:view:hub, stop_meditate→cultivate:action:claim, shop→shop:view:hub, map→map:view:world, zone:x→map:zone:inspect:x, travel→map:action:settle:x, hunt:x→map:action:hunt:x, breakthrough→breakthrough confirm path, setlang, combat:* → new) kept in one shim so GHA artifact users' stale buttons don't crash.
- Middleware: GUARD_MEDITATING gone; IN_COMBAT guard: allow any combat:act:* (round validated by handler); SECLUSION obsolete (tribulation now instant; status only exists legacy → treat as idle).
- Demo: same dispatcher; add top dock row mirror; alerts as toast (already).
- Admin flow unchanged (drops etc) but restyle minimal.
- Tests: rewrite journey tests to new namespace; add spec acceptance tests: persistence kwargs, panel, orphan cleanup (mocked bot), no-slugs regex, no «— هیچ —» regex, pagination math + return-to-page, round-lock ghost reject, narrative placeholders, 3-tier loot drop seeded, slot capacity matrix, free claim, gather tiers seeded, chronicle event counts/timestamps, breakthrough preflight + fail debuff numbers, locale symmetry. Target: everything green.

## Order of attack
1. data (enemies, methods, materials, zones constants) 2. data_loader 3. models (aliases, derived, ui state, item_slots, debuff) 4. engine items (battle slots, free claim) 5. combat_engine rewrite 6. offline engine rewrite 7. cultivation (continuous rate, prep math, phases API for handlers) 8. render.py full re-templating 9. keyboards.py (dock, tab bars, pagination, battle kb) 10. callbacks+commands (lifecycle, new namespace, aliases) 11. middleware 12. locales 13. tests 14. demo client 15. live verify + balance sims + commit/push.

## STATUS (round 2) — DONE
- ①–⑥ data→cultivation: complete (earlier turns).
- ⑦ render re-templating: complete — spec templates verbatim, ▰▱ bars, per-screen builders.
- ⑧ keyboards: complete — persistent DOCK_ROWS (is_persistent, resize, not one-time), tab bars, battle kb w/ round-token payloads, booth/art/sect/bt boards.
- ⑨ callbacks+commands: complete — `_canon` legacy shim, `_combat_step` (shared by PTB + demo), round-token stale guard (soft toast + weaponize-strip), root single-window lifecycle (delete-old → send-new → anchor persisted AFTER render), tribulation 3-phase edits, reply-dock router in commands.py.
- ⑩ locales: complete — ~600 keys added (fa verbatim, en mirror); `Locale.num` accepts numeric strings; symmetry test green.
- ⑪ acceptance: complete — tests/test_v2_ui_sweep.py (every screen × every button × fa/en: no raw keys, no unfilled placeholders, tap-safe), tests/test_v2_async_flow.py (lifecycle, strip, stale-token, tribulation staging), tests/test_v2_acceptance.py (dock persistence payload, no inline dock dup, pagination 5/page + return-to-origin page, slot matrix, free claim ×1, battle item row, fail-card numbers). 61/61 green.
- ⑫ demo: same dispatcher, dock bar mirror, alert toasts; live-verified incl. hunt→round2→stale-toast→sim-terminal.
- Middleware v2: meditating treated as IDLE; combat allows combat:* + bag nav/consume/assign; paralysis/injury roots re-keyed to v2 namespaces; 15-min stale sessions auto-close in Ctx.settle.

# ⚔️ Grimhaven — «نبرد جاودانگان و تسخیر آسمان»

**War of the Immortals: Conquest of Heaven** — a bilingual (فارسی / English)
Xianxia **strategy & idle** game for Telegram, built exactly to the Master Game
Design Document ([docs/master_prompt_fa.txt](docs/master_prompt_fa.txt)).

Bot: [@Grimheaven_bot](https://t.me/Grimheaven_bot)

---

## 🎮 The game

You are a mortal cultivator. Meditate (AFK) to gather **Qi**, fill your
Dantian, survive **closed-door seclusion**, and face the **heavenly
tribulation lightning** to break through — from *Qi Condensation* all the way
to *Dao Sovereign*:

| # | Realm | Stages | Qi needed |
|---|---|---|---|
| 1 | تصفیه چی — Qi Condensation | 9 layers | 66,500 |
| 2 | پی‌ریزی بنیاد — Foundation Establishment | Early→Peak | 240,000 |
| 3 | هسته طلایی — Golden Core | Early→Peak | 1,200,000 |
| 4 | روح نوزاد — Nascent Soul | Early→Peak | 6,000,000 |
| 5 | انشعاب روح — Spirit Severing | 3 severings + Peak | 24,000,000 |
| 6 | پژواک خلاء — Void Refinement | Early→Peak | 100,000,000 |
| 7 | صعود به مصیبت — Tribulation Ascension | 9 lightning trials | 540,000,000 |
| 8 | نامیرای حقیقی — True Immortal | Early→Peak | 1,000,000,000 |
| 9 | حاکم دائو — Dao Sovereign | Cosmic fusion | ∞ |

### Systems implemented (all from the design doc)

- **AFK cultivation engine** — `Qi/h = Base × Tech × (1+Catalyst) × Vein × (1+Luck×0.002)` (ch. 5)
- **Breakthrough & tribulation** — success formula, clamped 5–95%, seclusion
  timers (15 min → 72 h), failure states *Minor / Qi Deviation / Annihilation*
  (60/30/10), karmic **miracle roll** salvation (ch. 2–3)
- **Karmic Luck** — treasure encounters (>70), bandit ambushes (<20),
  lightning-damage variance, karma economy (+2 help, +5 save mortals, −5
  plunder, −10 blood rites…) (ch. 3)
- **Hidden stats matrix** — luck / charisma / Dao-heart / corruption with the
  doc's reveal conditions (purple aura at corruption 30…) (ch. 4)
- **Catalysts** — spirit stones (low +15% … heavenly +300%), ancient herbs,
  territory veins ×1.0–5.0, demonic sacrifices (+100%/+500% with corruption)
  (ch. 5)
- **Orthodox vs Demonic** alignments + **five Dao paths**: Sword, Alchemy,
  Body Tempering, Five Elements, Blood & Soul-Devouring (ch. 6)
- **Equipment** — 7 tiers (Mortal→Divine) across 6 slots (weapon, robe,
  spatial ring, talisman, companion, soul-bound natal artifact) (ch. 7)
- **Martial arts** — 7 internal methods (tech ×1.0–10.0) + 4-slot combat
  loadout with ultimates costing 50% of the Dantian (ch. 7)
- **World map** — 7 zones with spirit-vein densities, travel realm gates,
  guardian **conquest battles**, monster hunts with mercy/plunder choices (ch. 1–2)
- **Sects** — join 3 NPC sects at Foundation; found your own at Void
  Refinement (ch. 2)
- **Trade Pavilion** shop priced in spirit stones (ch. 2)
- **God-Mode admin panel** `/admin` — broadcast, global drops, deep inspect,
  `/modify_qi`, `/set_realm`, `/grant_item`, `/seal_meridians`,
  `/purge_demon`, `/world_boost` (ch. 9)
- **Full bilingual UI** — every string keyed in `locales/locale_fa.json` +
  `locale_en.json`; per-user language flag; Persian digits in FA (ch. 1, 8)
- **MongoDB-style JSON documents** stored in SQLite (`users`, `zones`,
  `sects`, `meta`) — matches the doc §10.1 schema 1:1 (ch. 10)

## 🧱 Repository layout

```
grimhaven/
├── run_bot.py               # Telegram bot entry (long-polling or webhook)
├── run_demo.py              # browser-playable demo console
├── requirements.txt
├── .env.example             # copy to .env and fill TELEGRAM_BOT_TOKEN
├── locales/                 # locale_fa.json / locale_en.json (doc §1.2)
├── grimhaven/
│   ├── config.py            # env/.env settings
│   ├── localization.py      # key-based bilingual text + Persian digits
│   ├── render.py            # every screen renderer (doc ch. 8 mockups)
│   ├── engine/              # pure game logic, fully unit-tested
│   │   ├── constants.py     # all numbers straight from the design doc
│   │   ├── models.py        # user JSON document + derived stats
│   │   ├── cultivation.py   # AFK settle, seclusion, tribulation rolls
│   │   ├── combat.py        # turn-based duels, hunts, mercy/plunder
│   │   ├── items.py         # shop, equipment, pills/herbs, Dao choice
│   │   └── world.py         # zones, conquest, sects, sacrifices
│   ├── db/storage.py        # SQLite document store (Mongo-style)
│   ├── bot/                 # python-telegram-bot layer (handlers, keyboards)
│   └── demo/                # FastAPI chat-console sharing the same engine
├── tests/                   # 31 tests anchored to the design doc
├── deploy/                  # Dockerfile, docker-compose, render.yaml, systemd
└── docs/                    # master prompt + hosting guide
```

## ▶️ Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # put the @BotFather token + your admin id in
python run_bot.py               # the bot goes live on @Grimheaven_bot
python run_demo.py              # or open the browser demo → http://localhost:8000
pytest                          # 31 passing tests
```

> The sandbox this repo was built in has **no route to api.telegram.org**, so
> final deployment is one step away — see [docs/DEPLOY.md](docs/DEPLOY.md)
> (Render one-click, Docker, VPS/systemd, PythonAnywhere).

## 🕹 First session

`/start` → pick زبان → 🧘 *Begin AFK Meditation* → watch Qi accrue → at a full
Dantian press ⚡️ *Attempt Breakthrough*, survive seclusion, and roll against
the tribulation. At layer 9 choose one of the **five Dao paths** and your
alignment — then conquer veins on the 🗺 map, trade at the 🏮 pavilion, and
climb to Dao Sovereign.

## 👑 Admin (Hall of Heavenly Will)

Put your Telegram id in `ADMIN_IDS`, then:

```
/admin                      → panel: server stats, rapid commands
/broadcast all متن وحی      → global revelation (fa|en|all)
/inspect 123456789          → full profile incl. hidden stats
/modify_qi 123456789 50000  → instant Dantian adjustment
/set_realm 123456789 3 2    → force realm/stage
/grant_item 123456789 weapon heaven
/seal_meridians 123456789 6 → punish: 6h halved AFK yield
/purge_demon 123456789      → zero demonic corruption
/world_boost 2 24           → ×2 cultivation for everyone, 24 h
```

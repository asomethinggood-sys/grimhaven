# Sticker Reactions

The game sends a **sticker** at the moments that matter: a victory, a
breakthrough, a tribulation survived or failed. It is the emoji-grade layer
underneath the illustrations in [`docs/ARTWORK.md`](ARTWORK.md) — the picture is
the vista, the sticker is the face the cultivator makes.

Everything here is optional and off the critical path. With the whole block
removed from `.env`, or `data/sticker_map.json` deleted, the bot plays exactly
as it did before: **no reaction ever costs a turn, a stone, or a message.**

---

## 1. The one rule that matters

> A sticker is decoration. If anything about it fails — the pack was renamed,
> the `file_id` went stale, Telegram refuses, the map is malformed — the layer
> goes quiet and the game continues.

This is enforced in four places, and each has a test:

| Failure | What happens | Where |
| --- | --- | --- |
| `data/sticker_map.json` missing or malformed | `Ctx.stickers` is `None`; no moment ever gets a spec | `handlers/common.py` |
| `getStickerSet` refuses (pack deleted/renamed) | `pick()` returns `None`; the moment is skipped | `stickers/service.py` |
| `sendSticker` rejects a cached id | the set is re-resolved **once**, then one retry | `stickers/delivery.py` |
| Anything at all raises | logged, swallowed, screen still sent | `_apply_sticker` |

There is no fallback from sticker to artwork to text, because the sticker *is*
already the fallback — the artwork hook fires independently on the same screen,
so a player who gets no sticker still gets the illustration and the text.

---

## 2. What triggers a reaction

Moments are defined in `data/sticker_map.json`; handlers only ask for one by
name. Nothing is hard-coded to a specific pack or file.

| Moment | Fires when | Throttle |
| --- | --- | --- |
| `victory` | a combat encounter ends in victory (per zone) | once per 45 min per zone |
| `breakthrough` | a tribulation succeeds | once per attempt number |
| `breakthrough_fail` | a tribulation fails | once per attempt number |
| `milestone_realm` | a breakthrough crosses a whole realm | once per attempt number |

Routine taps never react. A normal battle round, a menu, a hunt roll — silent on
purpose, which is also the contract most likely to rot, so
`tests/test_sticker_flow_wiring.py` asserts both the fire **and** the silence.

### Deduplication

Reactions reuse the artwork layer's store (`user["ui"]["artwork_sent"]`) under a
`sticker:` prefix, so there is one dedup mechanism in the codebase, not two:

* `once_key` — one reaction per key ever within the window (`sticker:victory:z3`)
* `throttle_minutes` — the window, per moment, from the map
* a per-chat cooldown (`STICKER_COOLDOWN_SECONDS`) — a floor between any two
  reactions, so a burst of rapid taps cannot produce a burst of stickers

A retried callback therefore cannot double-post: the key is claimed *before* the
network call, exactly as artwork does.

---

## 3. Choosing what is sent

For a given moment the service resolves its pack once (`getStickerSet`), caches
the sticker list **in memory only** (ids are never written to the DB), and picks
a face:

1. the category's ordered emoji list decides *preference* — the first sticker
   in the pack whose `emoji` matches wins;
2. otherwise a deterministic rotation over the pack, never repeating the
   previously used face.

So a pack of generic reaction faces behaves sensibly without per-sticker
configuration, and adding a better-matching face to the pack improves the
choice with no code change.

---

## 4. Packs

`data/sticker_map.json` ships with two entries:

| Map id | Telegram short name | Contents | Default |
| --- | --- | --- | --- |
| `s_anime_v4_1` | `anime_v4_1_by_shadowthiefo` | 52 static reaction faces | **enabled** |
| `anime_animated` | `anianimated` | 40 animated stickers | disabled |

Set `STICKER_PACKS=` to restrict to specific packs (by map id or by Telegram
short name) without editing the map.

### Adding your own pack

```jsonc
{
  "id": "my_pack",
  "short_name": "my_pack_by_someone",   // the t.me/addstickers/<short_name> slug
  "format": "static",                    // or "animated" / "video"
  "enabled": true
}
```

Then `getStickerSet` is called for that short name on the first reaction.

### A limitation worth stating plainly

**Premium stickers cannot be sent by a bot.** Telegram's Premium animated
stickers are an effect a *Premium user* attaches; the Bot API exposes them
read-only as `Sticker.premium_animation` and there is no `sendSticker` parameter
that attaches one. Likewise **custom emoji** requires the *bot owner* to hold
Telegram Premium (Bot API 9.4) before a bot may send any — so this layer ships
regular stickers only. Should the owner ever hold Premium, custom emoji is a
separate feature, not a flag on this one.

Also note: a `file_id` is bot-specific and Telegram may invalidate it at any
time. That is exactly why the ids are resolved at runtime, cached only in
memory, and refreshed once on rejection instead of being committed anywhere.

---

## 5. Configuration

All optional — see the sticker block in `.env.example`. The defaults are the
recommended settings.

| Variable | Default | Meaning |
| --- | --- | --- |
| `STICKER_ENABLED` | `1` | master switch; `0` = hooks stay, nothing is ever sent |
| `STICKER_MAP_PATH` | `data/sticker_map.json` | the catalog |
| `STICKER_PACKS` | *(empty)* | restrict to these packs; empty = every enabled pack |
| `STICKER_TIMEOUT` | `8` | per API call, seconds |
| `STICKER_TASK_TIMEOUT` | `20` | whole reaction, seconds |
| `STICKER_CACHE_TTL_MINUTES` | `720` | how long resolved ids are trusted |
| `STICKER_COOLDOWN_SECONDS` | `30` | floor between two reactions in one chat |

---

## 6. Files

| Path | Responsibility |
| --- | --- |
| `data/sticker_map.json` | the catalog — packs, categories, moments, throttles |
| `grimhaven/stickers/catalog.py` | parse + **validate** the map; `None` on any problem |
| `grimhaven/stickers/service.py` | resolve packs, cache in memory, pick a face |
| `grimhaven/stickers/delivery.py` | dedup, cooldown, send, refresh-once retry |
| `grimhaven/bot/handlers/common.py` | `Ctx.stickers` — lazy, failure-tolerant |
| `grimhaven/bot/handlers/callbacks.py` | `_sticker_spec` / `_apply_sticker` + the hooks |

Tests: `tests/test_sticker_reactions.py` (layer behaviour) and
`tests/test_sticker_flow_wiring.py` (real game flows actually fire it).

# 🖼️ Artwork System — image discovery, caching & Telegram delivery

Grimhaven decorates key game events (breakthrough tribulations, realm
milestones, zone views, hunts, victories) with a themed **photo message**
sent through the same bot that plays the game. The picture is a bonus: the
game is fully playable with `ARTWORK_ENABLED=0`, and every artwork failure
is swallowed so a gameplay action never rolls back or errors because an
image could not be fetched.

```
 game event (callback handler)
   │  opts["artwork"] = {asset_key, queries?, caption, once_key, throttle_minutes}
   ▼
 delivery.begin_artwork ──► synchronously claims once_key in user["ui"]   (dedupe)
   │ create_task (bounded, tracked, cancelled on shutdown)
   ▼
 ArtworkService.get_or_fetch_artwork(asset_key)
   │ cache hit in SQLite `artwork` table? ──yes──► row
   │ no: provider search → candidates (best first)
   ▼
 Fetcher (rate-limited per host, retries w/ backoff, size caps)
   ▼
 validate_image_bytes (magic bytes, dims, aspect, truncation, HTML traps)
   ▼
 atomic write  assets/artwork/files/<sha256>.<ext>   +  index row
 ▼
 send_photo(file_id … fallback InputFile … >5 MB → send_document)
```

## Where each piece lives

| Path | Role |
|---|---|
| `grimhaven/images/validate.py` | strict bytes gate: PNG/JPEG/GIF/WebP only, min width, max side/bytes, truncation & HTML-error-page detection, sha256 |
| `grimhaven/images/fetch.py` | `Fetcher`: httpx async client, per-host rate limiting, bounded concurrency semaphore, retries with backoff on 429/5xx, streaming size caps, descriptive User-Agent |
| `grimhaven/images/providers.py` | search providers: Wikimedia Commons (generator=search + extmetadata), Openverse (license-filtered), Pixabay (optional, only with key). `PROVIDER_REGISTRY` is the extension point |
| `grimhaven/images/service.py` | `ArtworkService`: event map, queries, cache, two-phase fetch+store with content-hash dedupe, failure cooldown, LRU eviction, prefetch |
| `grimhaven/images/delivery.py` | the Telegram side: once-key claim, background task, file_id→InputFile→document fallback ladder, caption clamp, never-raise policy |
| `grimhaven/db/storage.py` | the `artwork` table lives in the **game database** (SCHEMA_VERSION 4) — no second database |
| `data/artwork_map.json` | event routing table: per event → search queries (English, descriptive), caption locale key, min width, throttle window |
| `locales/locale_{fa,en}.json` | `ART_CAP_*` caption keys + `{zone}` placeholders |

## Which events get art, and when

The staged flow of the original game is preserved exactly. Images arrive
**beside** the text flow, never replacing a message:

* **Breakthrough / heavenly tribulation** — priority path. The fetch starts
  the moment the tribulation begins; after phase 1 the handler joins the
  background task with a small budget (2.5 s) so the picture is sent between
  the drama edits and the result card when it is ready. If it is not ready,
  the game continues and the photo never arrives late into the next screen.
  Failure rolls reuse the *same* cached picture with the fail caption
  (`ART_CAP_BREAKTHROUGH_FAIL`) — no second fetch. A realm-jump win switches
  to the `milestone_realm` asset. After resolution, the other three assets
  are warm-prefetched for the next attempt.
* **Zone inspect / settle** (`zone:<id>`), **hunt start** (`hunt:<id>`),
  **victory** (`victory`) — the picture is applied when the card is sent and
  throttled (12 h / 60 min / 30 min by default, see `artwork_map.json`) so
  spamming buttons cannot flood the chat.
* Everything else stays text-only. If `ctx.settings` is absent (demo mode,
  tests) no artwork layer is built at all.

### No duplicate sends on retry

`begin_artwork` claims `once_key` (e.g. `breakthrough:12`) **synchronously**
in the user document *before* spawning the task; the claim is persisted with
the same `ctx.save` that stores the game state. A redelivered callback with
the same once-key finds the claim and skips. A new attempt has a new key, so
each real event gets exactly one picture. Keys are pruned past 48 h to keep
the user doc small.

## Sources & licensing

**Pinterest — why it is not wired.** Pinterest's official API (v5) exposes
pin search only to *approved partner accounts* (beta, OAuth, application
required); there is no public search endpoint, and scraping pins would
violate their ToS. A "Pinterest integration" that cannot actually retrieve
images was rejected in favour of openly-licensed sources that verifiably
work. Thumbnails on pinimg.com are preview cache, not redistribution rights.

**What is used instead**

1. **Wikimedia Commons** (default, keyless) — free-content repository. We
   request `extmetadata` and store `LicenseShortName`/`Artist`/`Credit`
   beside every file; downloads go through thumbnail URLs so we fetch the
   size we display. Polite rate limiting (≥1.1 s/req) + `maxlag=5` respect
   the Wikimedia API etiquette policy, and the descriptive `User-Agent`
   follows their bot policy.
2. **Openverse** (WordPress.org, keyless) — indexes CC-licensed images from
   Flickr etc. We filter `licenses=cc0,pdm,by,by-sa` (no NC/ND) and store
   the API's prebuilt attribution sentence. Anonymous tier ≈100 req/day —
   well beyond our volume; a client-id can be added via OAuth2 if ever
   needed (not required).
3. **Pixabay** (optional) — enabled only when `PIXABAY_API_KEY` is set;
   its Content License permits commercial use without attribution.

Unsplash/Pexels were evaluated and dropped: both require API keys +
approval and their ToS restrict server-side caching/downloading in ways an
attribution pipeline must respect; Commons + Openverse cover the same need
with cleaner guarantees. **No paid service is a mandatory dependency.**

**Attribution policy.** every cache row keeps `provider, query, source_url,
page_url, license, author, attribution`. Files are stored next to that index
in the same SQLite database, so an operator can always answer "where did
this picture come from and under what license" (`grimhaven.storage
.artwork_get(key)`). The bot never shows the raw URL or paths to players —
captions are gameplay text only.

## Configuration (all optional — `.env`, see `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `ARTWORK_ENABLED` | `1` | master switch; `0` = pure text game |
| `ARTWORK_DIR` | `assets/artwork` | cache root (`files/` lives inside; git-ignored) |
| `ARTWORK_PROVIDERS` | `commons,openverse` | comma list, order = priority; unknown names are logged + skipped |
| `ARTWORK_TIMEOUT` | `12` | per-request timeout (seconds) |
| `ARTWORK_MAX_RETRIES` | `2` | retries on 429/5xx/transport errors, exponential backoff + `Retry-After` respected |
| `ARTWORK_CONCURRENCY` | `3` | simultaneous provider requests (semaphore) |
| `ARTWORK_TASK_TIMEOUT` | `45` | hard cap of one whole background artwork task |
| `ARTWORK_MIN_WIDTH` | `480` | smaller images are rejected |
| `ARTWORK_MAX_SIDE` | `10000` | pixel-dimension sanity cap |
| `ARTWORK_MAX_BYTES` | `5000000` | download size cap (Telegram photo limit is 10 MB; we stay lean) |
| `ARTWORK_MAX_ASSETS` | `400` | LRU eviction beyond this many cached assets (files + rows) |
| `ARTWORK_FAIL_COOLDOWN_MINUTES` | `30` | retry window after all candidates failed for a key |
| `ARTWORK_USER_AGENT` | repo-tagged | be polite; set a contact address for production |
| `OPENVERSE_LICENSES` | `cc0,pdm,by,by-sa` | license filter |
| `PIXABAY_API_KEY` | *(empty)* | empty ⇒ Pixabay disabled |

## Reliability contract

* **Never blocks the event loop** — all provider IO is async httpx; the
  handler only awaits the tiny join budget during tribulation.
* **Bounded background work** — one task per user event, tracked in
  `service._tasks`, capped by `ARTWORK_TASK_TIMEOUT`, hard-cancelled in
  `post_shutdown`. Prefetch respects `ARTWORK_CONCURRENCY` and dedupes
  against in-flight keys.
* **Graceful degradation** — network down, all providers down, every
  candidate rejected, Telegram upload failure, stale `file_id`: in every
  case the player gets exactly the text-only experience and the game state
  is already saved. Failures log at warning/error; a `failed` row + cooldown
  prevents hammering a dead source.
* **Dedupe on content** — two event keys that resolve to the same picture
  share one file on disk (one index row each); eviction checks the shared
  hash before deleting.
* **`file_id` reuse is strictly process-local** — each successful upload
  registers its Telegram `file_id` in an in-memory registry on the artwork
  service (content hash → id, bounded LRU), so identical bytes are uploaded
  at most once per bot run, and every quoted id is one this process itself
  just received from Telegram. Ids are **never** read back from the
  `telegram_file_id` column on the send path: Telegram reclaims bot-uploaded
  files after roughly a day, the game database outlives every hosting cycle,
  and quoting a reclaimed id fails with 400 `can't find file for file_id of
  type 'PhotoSize'` on the first send after a restart (a real production
  incident). The column is still refreshed on every fresh upload — purely
  as ops metadata. Files >5 MB (near the photo limit) go out as documents
  instead.
* **Atomic cache writes** — `*.part` + `os.replace`; a crash cannot leave a
  half file being served.
* **Schema is additive** — old (v3) databases gain the `artwork` table on
  open; nothing existing is rewritten. `integrity_report()`/`counts()`
  include it.

## Operating

```bash
python scripts/artwork_probe.py     # live end-to-end: search→download→validate→cache
                                    # (runs in CI after the unit suite; exit 0 = works)
```

CI runs `artwork-probe` on every push/PR to main (`continue-on-error`, so a
third-party outage never reddens the game, but the log tells you within
minutes when retrieval stops working).

**Troubleshooting**

* *No pictures at all* — check `ARTWORK_ENABLED`, then the startup warning
  `artwork: no providers configured`. A per-key failure cooldown (30 min)
  explains one-off silence after a source hiccup.
* *Everything rejected* — raise `ARTWORK_MAX_BYTES` only alongside the
  provider's own sizes; more often `ARTWORK_MIN_WIDTH` is above what the
  source offers for niche queries. The rejected reason is logged
  (`not-image`, `truncated`, `html-error-page`, `low-resolution`, …).
* *Cache housekeeping* — `service.clear_cache()` (admin code path) removes
  rows + files; `assets/artwork/` is disposable by design.
* *Search results off-theme* — tune `data/artwork_map.json` queries; it is
  data, not code. Keep queries English and descriptive (5+ words).

## Testing map

`tests/test_artwork_system.py` (25 tests) pins every contract with fakes
only — no network, no bot token: schema-accurate provider payloads through
`httpx.MockTransport`, synthesized valid PNG/JPEG/WebP and adversarial
bodies (HTML disguised as image, truncated JPEG, oversize streams), real
`FakeBot` photo recording, retry/cooldown/dedupe/eviction behaviour, and the
handler-flow tests that prove: the photo is sent mid-tribulation, a failed
upload never disturbs the saved game, a replayed callback does not double
send, and file_id reuse falls back to a fresh upload. `scripts/artwork_probe.py`
is the live counterpart (CI).

### Honest limitation

The sandbox used during development has no outbound access beyond package
registries, so live provider downloads could not be executed there. The
request/response schemas of both APIs were verified against the live
endpoints at implementation time, parsing is covered by fixture tests, and
the CI probe job provides continuous real-network verification from GitHub's
runners.

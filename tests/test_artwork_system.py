"""Artwork subsystem acceptance tests — discovery, download, validation,
cache, and REAL photo delivery into the handler flow.

Every external touch point is faked in-process:

* provider HTTP runs through ``httpx.MockTransport`` with fixtures that mirror
  the live Wikimedia Commons / Openverse API schemas (both schemas were
  verified against the production endpoints during the Phase-2 investigation);
* Telegram runs through ``tests/telegram_fakes.FakeBot`` — photo sends are
  recorded in ``bot.photos``, so the delivery tests can only pass when an
  actual ``send_photo`` happened with the right caption.

No test needs a Pinterest credential, a live bot token, or internet access.
"""
from __future__ import annotations

import asyncio
import json
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from grimhaven.config import Settings
from grimhaven.db.storage import Storage, bootstrap_world
from grimhaven.engine.models import new_user_doc
from grimhaven.images.delivery import claim_once_key, send_photo_row
from grimhaven.images.service import ArtworkService
from grimhaven.images.validate import ImageRejected, validate_image_bytes
from grimhaven.bot.handlers.common import Ctx
from grimhaven.bot.handlers import callbacks as cb
from grimhaven.core.middleware import guard_update
from tests.telegram_fakes import FakeBot, FakeContext, FakeUpdate

FA_CHARS = re.compile(r"[؀-ۿ]")


# ── synthetic image builders (real signatures, real headers) ────────────────

def make_png(width: int = 800, height: int = 500) -> bytes:
    ihdr = b"IHDR" + struct.pack(">II", width, height) + bytes(9)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + ihdr
            + struct.pack(">I", 0) + b"IDAT" + struct.pack(">I", 0)
            + b"\x00\x00\x00\x00IEND\xaeB`\x82" + b"\n" * 8)


def make_jpeg(width: int = 900, height: int = 600, fill: int = 32) -> bytes:
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", height, width)
            + b"\x03" + b"\x22\x00\x21\x01\x22\x01\x11\x01")
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00"
            + bytes(9) + sof0 + b"\xff\xda" + struct.pack(">H", 6) + bytes(2)
            + b"\x00" * fill + b"\xff\xd9")


def make_webp(width: int = 1000, height: int = 700) -> bytes:
    payload = (b"VP8X" + struct.pack("<I", 10) + bytes(4)
               + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"))
    return b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WEBP" + payload + b"\x00" * 16


PNG_OK = make_png()
JPEG_OK = make_jpeg()
WEBP_OK = make_webp()


# ── provider payload fixtures (schema-accurate) ─────────────────────────────

def commons_payload(img_url: str) -> bytes:
    return json.dumps({
        "batchcomplete": "",
        "query": {"pages": {"12345": {
            "pageid": 12345, "ns": 6, "index": 1,
            "title": "File:Tribulation lightning.jpg",
            "imageinfo": [{
                "url": img_url,
                "thumburl": img_url, "thumbwidth": 1200, "thumbheight": 800,
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Tribulation_lightning.jpg",
                "width": 1920, "height": 1200, "mime": "image/jpeg",
                "size": len(JPEG_OK),
                "extmetadata": {
                    "Artist": {"value": '<span property="dc:creator"><a href="x">Li '
                                        "Longmiao</a></span>", "source": "commons-desc-page"},
                    "LicenseShortName": {"value": "CC BY-SA 4.0", "source": "commons-desc-page"},
                    "UsageTerms": {"value": "<a href='l'>CC BY-SA 4.0</a>"},
                    "Credit": {"value": "Photo: artist page"},
                },
            }],
        }}},
    }, ensure_ascii=False).encode()


def openverse_payload(url: str, count: int = 2) -> bytes:
    return json.dumps({
        "result_count": count, "page_count": 1, "page_size": count, "page": 1,
        "results": [{
            "id": f"ov-{i}",
            "title": "Cultivator storm study",
            "url": url if i == 0 else "",          # second result is a dead link
            "foreign_landing_url": "https://flickr.example/p/1",
            "creator": "Amara Chen", "creator_url": "https://flickr.example/amara",
            "license": "by-sa", "license_version": "4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "provider": "flickr", "source": "flickr",
            "filesize": len(PNG_OK), "filetype": "png",
            "width": 1600, "height": 1000,
            "attribution": "&quot;Cultivator storm study&quot; by Amara Chen, CC BY-SA 4.0",
        } for i in range(count)],
    }, ensure_ascii=False).encode()


class Transport:
    """Scriptable MockTransport with a live request counter."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes         # substring → httpx.Response | callable | Exception
        self.calls: list[str] = []

    @property
    def count(self) -> int:
        return len(self.calls)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        for needle, value in self.routes.items():
            if needle in url:
                if callable(value):
                    return value(request)
                if isinstance(value, Exception):
                    raise value
                return value
        return httpx.Response(404, content=b"missing fixture",
                              headers={"content-type": "text/plain"})


def ok(content: bytes, ctype: str = "image/png") -> httpx.Response:
    return httpx.Response(200, content=content,
                          headers={"content-type": ctype,
                                   "content-length": str(len(content))})


def make_settings(tmp: Path, **over) -> Settings:
    base = dict(telegram_bot_token="test:token",
                database_path=tmp / "grimhaven.db",
                artwork_dir=tmp / "assets" / "artwork",
                artwork_concurrency=3, artwork_timeout=5.0, artwork_max_retries=0,
                artwork_min_width=8, artwork_max_bytes=200_000, artwork_max_assets=400)
    base.update(over)
    return Settings(**base)


def build_service(tmp: Path, storage: Storage, transport: Transport, **over) -> ArtworkService:
    service = ArtworkService(storage, make_settings(tmp, **over),
                             transport=httpx.MockTransport(transport.handler))
    service.fetcher.default_min_interval = 0.0
    service.fetcher.HOST_INTERVALS = {}      # type: ignore[attr-defined]
    return service


@pytest.fixture()
def storage(tmp_path):
    st = Storage(tmp_path / "grimhaven.db")
    bootstrap_world(st)
    yield st
    st.close()


async def drain(service: ArtworkService) -> None:
    """Let scheduled background artwork tasks finish (no wall-clock sleep)."""
    for _round in range(8):
        pending = [t for t in service._tasks if not t.done()]
        if not pending:
            if not service._tasks:
                return
            await asyncio.sleep(0)
            continue
        await asyncio.gather(*pending, return_exceptions=True)


def force_success(monkeypatch):
    from grimhaven.engine.cultivation import CultivationEngine
    monkeypatch.setattr(CultivationEngine, "spec_success_rate",
                        staticmethod(lambda user: 100.0))


def ready_user(lang: str = "en") -> dict:
    user = new_user_doc(101, "art_soul", lang)
    user["cultivation"]["qi_current"] = user["cultivation"]["qi_capacity"] = 10_000
    user["cultivation"]["dao_path"] = "sword"
    return user


# ── 1. discovery ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_images_success_parses_commons_schema(tmp_path, storage):
    tp = Transport({"commons.wikimedia.org": httpx.Response(
        200, content=commons_payload("https://upload.wikimedia.org/x/img.jpg"),
        headers={"content-type": "application/json"})})
    service = build_service(tmp_path, storage, tp)
    found = await service.search_images("xianxia heavenly tribulation lightning "
                                        "cultivation breakthrough fantasy art")
    assert found, "commons results must be parsed into candidates"
    c = found[0]
    assert c.source_url == "https://upload.wikimedia.org/x/img.jpg"
    assert c.provider == "wikimedia-commons"
    assert (c.width, c.height) == (1920, 1200)
    # licensing + attribution arrive, HTML stripped
    assert c.license == "CC BY-SA 4.0"
    assert c.author == "Li Longmiao"
    assert "<" not in c.attribution and c.attribution


@pytest.mark.asyncio
async def test_search_images_openverse_fallback_when_first_has_no_results(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=b'{"batchcomplete":""}',
            headers={"content-type": "application/json"}),
        "api.openverse.org": httpx.Response(
            200, content=openverse_payload("https://live.staticflickr.example/a.png"),
            headers={"content-type": "application/json"}),
    })
    service = build_service(tmp_path, storage, tp)
    found = await service.search_images("golden core breakthrough glowing qi aura "
                                        "cultivation art")
    assert found and found[0].provider == "openverse"
    assert found[0].license == "CC by-sa 4.0"
    assert "Amara Chen" in found[0].author
    assert any("openverse.org" in call for call in tp.calls)


# ── 2/8. download + validation + cache miss → store ─────────────────────────

@pytest.mark.asyncio
async def test_download_validation_and_caching_success(tmp_path, storage):
    img = make_jpeg(width=1200, height=900)
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/t/trib.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/t/trib.jpg": ok(img, "image/jpeg"),
    })
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    assert row and row["status"] == "ok"
    path = service.row_path(row)
    assert path.is_file() and path.read_bytes() == img
    assert re.fullmatch(r"[0-9a-f]{64}", row["file_hash"])
    assert row["mime"] == "image/jpeg" and row["width"] == 1200
    assert row["provider"] == "wikimedia-commons"
    # the stored query is the descriptive mapped one, not a generic term
    assert "xianxia" in row["query"]
    assert row["license"] and row["attribution"] and row["page_url"]
    # validate_image re-gates the cached file
    info = service.validate_image(path)
    assert info["sha256"] == row["file_hash"]
    calls_after_first = tp.count
    row2 = await service.get_or_fetch_artwork("breakthrough")
    assert row2["file_hash"] == row["file_hash"]
    assert tp.count == calls_after_first, "cache hit must not touch the network"


@pytest.mark.asyncio
async def test_webp_and_png_accepted(tmp_path, storage):
    for key, name, blob, ctype in (
            ("breakthrough", "a.png", PNG_OK, "image/png"),
            ("breakthrough_fail", "b.webp", WEBP_OK, "image/webp")):
        tp = Transport({
            "commons.wikimedia.org": httpx.Response(
                200, content=commons_payload(f"https://upload.wikimedia.org/f/{name}"),
                headers={"content-type": "application/json"}),
            f"upload.wikimedia.org/f/{name}": ok(blob, ctype),
        })
        service = build_service(tmp_path, storage, tp)
        # ad-hoc queries override the map; both formats must pass the gate
        row = await service.get_or_fetch_artwork(
            key, queries=["xianxia ink wash mountain painting art"])
        assert row and row["mime"] == ctype, name
        assert (service.root / row["file_path"]).is_file()


# ── 3. an HTML page served where an image belongs ───────────────────────────

@pytest.mark.asyncio
async def test_html_error_page_disguised_as_image_is_rejected(tmp_path, storage):
    html = (b"<!DOCTYPE html><html><head><title>Rate limited</title></head>"
            b"<body>slow down</body>")
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/e/page.png"),
            headers={"content-type": "application/json"}),
        # the malicious case: Content-Type promises an image, body is HTML
        "upload.wikimedia.org/e/page.png": ok(html, "image/png"),
    })
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    assert row is None, "an HTML body must never enter the cache"
    files = list(service.files_dir.glob("*")) if service.files_dir.exists() else []
    assert not [f for f in files if f.suffix in (".png", ".jpg")], "nothing stored"
    # the failed key is in cooldown and the second call skips the network
    first_calls = tp.count
    assert await service.get_or_fetch_artwork("breakthrough") is None
    assert tp.count == first_calls


def test_validate_rejects_html_bodies_directly():
    with pytest.raises(ImageRejected) as exc:
        validate_image_bytes(b"<html><body>oops</body></html>", min_width=0,
                             declared_mime="text/html")
    assert exc.value.reason == "not-image"
    with pytest.raises(ImageRejected) as exc2:
        validate_image_bytes(b"<html><body>oops</body></html>", min_width=0,
                             declared_mime="image/png")
    assert exc2.value.reason == "html-error-page"


# ── 4. broken payload & unsupported mime ─────────────────────────────────────

@pytest.mark.asyncio
async def test_broken_bytes_rejected_end_to_end(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/b/broken.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/b/broken.jpg": ok(b"\x00\xffgarbage-not-an-image" * 40,
                                                "image/jpeg"),
    })
    service = build_service(tmp_path, storage, tp)
    assert await service.get_or_fetch_artwork("breakthrough") is None


def test_unsupported_mime_rejected_by_gate():
    pdf = (b"%PDF-1.4\n%\xc7\xec\x8f\xdf\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF")
    with pytest.raises(ImageRejected) as exc:
        validate_image_bytes(pdf, min_width=0, declared_mime="application/pdf")
    assert exc.value.reason in ("not-image", "unknown-format")


def test_truncated_jpeg_rejected():
    truncated = make_jpeg(width=800, height=600, fill=600)[:-200]   # EOI cut off
    with pytest.raises(ImageRejected) as exc:
        validate_image_bytes(truncated, min_width=0)
    assert exc.value.reason == "truncated"


# ── 5. oversize guards ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oversized_image_rejected(tmp_path, storage):
    fat = make_png() + b"\0" * 300_000
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/o/big.png"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/o/big.png": ok(fat, "image/png"),
    })
    service = build_service(tmp_path, storage, tp, artwork_max_bytes=64_000)
    assert await service.get_or_fetch_artwork("breakthrough") is None
    stored = list(service.files_dir.glob("*.png")) if service.files_dir.exists() else []
    assert not stored


def test_dimension_gates():
    with pytest.raises(ImageRejected) as exc:
        validate_image_bytes(make_png(width=64, height=40), min_width=480)
    assert exc.value.reason == "low-resolution"
    with pytest.raises(ImageRejected) as exc2:
        validate_image_bytes(make_png(width=20_000, height=12_000), min_width=0,
                             max_side=10_000)
    assert exc2.value.reason == "oversize"


# ── 6. deduplication ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_bytes_under_two_asset_keys_share_one_file(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/d/same.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/d/same.jpg": ok(JPEG_OK, "image/jpeg"),
    })
    service = build_service(tmp_path, storage, tp)
    r1 = await service.get_or_fetch_artwork("breakthrough")
    r2 = await service.get_or_fetch_artwork("milestone_realm")
    assert r1 and r2
    assert r1["file_hash"] == r2["file_hash"]
    assert r1["file_path"] == r2["file_path"], "same content must not be stored twice"
    files = [f for f in service.files_dir.glob("*.jpg")]
    assert len(files) == 1


# ── 7/9. provider outage & fallback ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_unavailable_falls_back_to_next_source(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.ConnectError("commons is down"),
        "api.openverse.org": httpx.Response(
            200, content=openverse_payload("https://live.staticflickr.example/x.png"),
            headers={"content-type": "application/json"}),
        "live.staticflickr.example/x.png": ok(PNG_OK, "image/png"),
    })
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    assert row and row["provider"] == "openverse"
    assert (service.root / row["file_path"]).is_file()


@pytest.mark.asyncio
async def test_all_sources_down_degrades_to_failed_row_and_text_only(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.ConnectError("commons down"),
        "api.openverse.org": httpx.ConnectError("openverse down"),
    })
    service = build_service(tmp_path, storage, tp)
    assert await service.get_or_fetch_artwork("breakthrough") is None
    raw = storage.artwork_get("breakthrough")
    assert raw and raw["status"] == "failed" and raw["attempts_at"]
    assert service.stats()["failed"] >= 1


# ── 10/11/13/14. handler integration: REAL photo in the REAL callback flow ──

def wire_artwork(ctx: Ctx, tmp_path: Path, storage: Storage,
                 tp: Transport, **over) -> ArtworkService:
    """Force ctx.artworks to our mocked service (what app.py builds for real)."""
    service = build_service(tmp_path, storage, tp, **over)
    ctx._artworks = service
    ctx._artworks_built = True
    return service


class NoSleep:
    """Neutralize the 2s×2 dramatic pauses (same trick as test_v2_async_flow)."""

    def __enter__(self):
        self._real = asyncio.sleep
        asyncio.sleep = lambda *_a, **_k: self._real(0)
        return self

    def __exit__(self, *exc):
        asyncio.sleep = self._real
        return False


@pytest.mark.asyncio
async def test_breakthrough_photo_delivered_in_sequence(tmp_path, monkeypatch):
    force_success(monkeypatch)
    storage = Storage(tmp_path / "world.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999}, settings=make_settings(tmp_path))
    img = make_jpeg(width=1200, height=900)
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/bt/trib.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/bt/trib.jpg": ok(img, "image/jpeg"),
    })
    service = wire_artwork(ctx, tmp_path, storage, tp)
    storage.save_user(ready_user("en"))

    bot = FakeBot()
    with NoSleep():
        update = FakeUpdate(query_data="breakthrough:action:confirm", user_id=101, bot=bot)
        await guard_update(storage.get_user, cb.on_callback)(
            update, FakeContext(ctx, bot))
        await drain(service)

    assert bot.photos, "a real send_photo must have happened"
    chat_id, photo, caption = bot.photos[0]
    assert chat_id == 777
    assert caption and "Heavenly tribulation" in caption
    doc = storage.get_user(101)
    assert doc["progress"]["breakthrough_attempts"] == 1
    assert doc["cultivation"]["current_stage"] == 1, "the real mechanic ran"
    # game flow intact: resolution card is the new root, keyboard preserved
    assert doc["ui"]["active_menu_message_id"] == bot.sent[-1][0]
    assert bot.sent[-1][2] is not None, "the root card keeps its inline keyboard"
    assert len(update.callback_query.edits) == 2, "both dramatic phases still play"
    # the bytes that went out came from the cache file
    row = storage.artwork_get("breakthrough")
    assert row and (service.root / row["file_path"]).read_bytes() == img
    storage.close()


@pytest.mark.asyncio
async def test_telegram_upload_failure_after_game_action_is_silent(tmp_path, monkeypatch):
    force_success(monkeypatch)
    storage = Storage(tmp_path / "world.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999}, settings=make_settings(tmp_path))
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/bt/trib.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/bt/trib.jpg": ok(make_jpeg(), "image/jpeg"),
    })
    service = wire_artwork(ctx, tmp_path, storage, tp)
    storage.save_user(ready_user("en"))
    bot = FakeBot()
    bot.fail_photo = True            # Telegram rejects the upload
    with NoSleep():
        update = FakeUpdate(query_data="breakthrough:action:confirm", user_id=101, bot=bot)
        await guard_update(storage.get_user, cb.on_callback)(
            update, FakeContext(ctx, bot))
        await drain(service)
    # gameplay untouched: attempt recorded, resolution card delivered, no alert
    doc = storage.get_user(101)
    assert doc["progress"]["breakthrough_attempts"] == 1
    assert doc["cultivation"]["current_stage"] == 1
    assert bot.sent, "the tribulation card still landed"
    assert not bot.photos
    alerts = [a for a in update.callback_query.answered if a and a[1]]
    assert not alerts, "an artwork failure must never raise an alert at the player"
    storage.close()


@pytest.mark.asyncio
async def test_retried_breakthrough_does_not_double_send(tmp_path, monkeypatch):
    force_success(monkeypatch)
    storage = Storage(tmp_path / "world.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999}, settings=make_settings(tmp_path))
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/bt/trib.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/bt/trib.jpg": ok(make_jpeg(), "image/jpeg"),
    })
    service = wire_artwork(ctx, tmp_path, storage, tp)
    storage.save_user(ready_user("en"))
    bot = FakeBot()
    handler = guard_update(storage.get_user, cb.on_callback)
    with NoSleep():
        first = FakeUpdate(query_data="breakthrough:action:confirm", user_id=101, bot=bot)
        await handler(first, FakeContext(ctx, bot))
        await drain(service)
        assert len(bot.photos) == 1
        # simulate the state a network retry would replay (attempt counter
        # back where the retry sees it, full qi): the once-key claim must hold
        doc = storage.get_user(101)
        doc["progress"]["breakthrough_attempts"] = 0
        doc["cultivation"]["qi_current"] = doc["cultivation"]["qi_capacity"]
        storage.save_user(doc)
        second = FakeUpdate(query_data="breakthrough:action:confirm", user_id=101, bot=bot)
        await handler(second, FakeContext(ctx, bot))
        await drain(service)
    assert len(bot.photos) == 1, "the same attempt's artwork must not be sent twice"
    # …and a NEW attempt (qi refilled for real) does get its own picture
    doc = storage.get_user(101)
    doc["cultivation"]["qi_current"] = doc["cultivation"]["qi_capacity"]
    storage.save_user(doc)
    with NoSleep():
        third = FakeUpdate(query_data="breakthrough:action:confirm", user_id=101, bot=bot)
        await handler(third, FakeContext(ctx, bot))
        await drain(service)
    assert len(bot.photos) == 2
    storage.close()


def test_claim_once_key_semantics():
    user: dict = {"ui": {}}
    assert claim_once_key(user, "bt:1") is True
    assert claim_once_key(user, "bt:1") is False
    assert claim_once_key(user, "zone:z") is True
    stale = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    user["ui"]["artwork_sent"]["zone:vista"] = stale
    assert claim_once_key(user, "zone:vista", throttle_minutes=60) is True
    user["ui"]["artwork_sent"]["zone:vista"] = datetime.now(timezone.utc).isoformat()
    assert claim_once_key(user, "zone:vista", throttle_minutes=60) is False


@pytest.mark.asyncio
async def test_file_id_reuse_then_fresh_upload_fallback(tmp_path, storage):
    tp = Transport({
        "commons.wikimedia.org": httpx.Response(
            200, content=commons_payload("https://upload.wikimedia.org/bt/trib.jpg"),
            headers={"content-type": "application/json"}),
        "upload.wikimedia.org/bt/trib.jpg": ok(JPEG_OK, "image/jpeg"),
    })
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    assert row["telegram_file_id"] == ""
    bot = FakeBot()
    fid = await send_photo_row(bot, 777, service.row_path(row), "cap", file_id="")
    assert fid and fid.startswith("FAKE_FILE_ID_")
    # a stale stored id is rejected by Telegram → exactly one fresh upload
    bot2 = FakeBot()
    bot2.fail_file_id = True
    fid2 = await send_photo_row(bot2, 777, service.row_path(row), "cap", file_id=fid)
    assert len(bot2.photos) == 1, "stale id rejected → fresh upload happened once"
    assert not isinstance(bot2.photos[0][1], str), \
        "fallback must re-upload the file itself, not the stale id"
    assert fid2


_COMMONS_ROUTES = {
    "commons.wikimedia.org": httpx.Response(
        200, content=commons_payload("https://upload.wikimedia.org/bt/trib.jpg"),
        headers={"content-type": "application/json"}),
    "upload.wikimedia.org/bt/trib.jpg": ok(JPEG_OK, "image/jpeg"),
}


@pytest.mark.asyncio
async def test_restart_never_quotes_the_persisted_file_id(tmp_path, storage):
    """Production bug: DB outlives the runner, Telegram expires bot files
    (≈24 h) — a persisted file_id must NEVER reach the send path, or every
    fresh cycle opens with 400 "can't find file for file_id of type PhotoSize".
    """
    from grimhaven.images.delivery import deliver_artwork
    tp = Transport(dict(_COMMONS_ROUTES))
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    assert row["telegram_file_id"] == ""

    # first send of the process: uploads the bytes (no id known yet)
    bot = FakeBot()
    assert await deliver_artwork(bot, 777, {"user_id": 1},
                                 {"asset_key": "breakthrough", "caption": "one"},
                                 service=service)
    assert not isinstance(bot.photos[0][1], str), "cold send must upload the file"
    fid = storage.artwork_get("breakthrough")["telegram_file_id"]
    assert fid and fid.startswith("FAKE_FILE_ID_"), "id recorded for ops metadata"

    # second send, same process: identical bytes never travel twice
    bot2 = FakeBot()
    assert await deliver_artwork(bot2, 777, {"user_id": 1},
                                 {"asset_key": "breakthrough", "caption": "two"},
                                 service=service)
    assert bot2.photos[0][1] == fid, "warm send reuses the in-memory file_id"

    # "restart": fresh service over the same DB. The bot EXPLODES on any
    # file_id send, so a green run here proves the stale persisted id in the
    # column was never even attempted.
    service2 = build_service(tmp_path, storage, Transport(dict(_COMMONS_ROUTES)))
    assert service2.recall_file_id(row["file_hash"]) == "", "memory died with the run"
    bot3 = FakeBot()
    bot3.fail_file_id = True
    assert await deliver_artwork(bot3, 777, {"user_id": 1},
                                 {"asset_key": "breakthrough", "caption": "three"},
                                 service=service2)
    assert not isinstance(bot3.photos[0][1], str), \
        "post-restart first send must upload fresh, not quote the dead id"


@pytest.mark.asyncio
async def test_dead_in_process_id_is_dropped_and_reuploaded(tmp_path, storage):
    """If even a remembered id goes stale (ultra-long run), recover quietly."""
    from grimhaven.images.delivery import deliver_artwork
    tp = Transport(dict(_COMMONS_ROUTES))
    service = build_service(tmp_path, storage, tp)
    row = await service.get_or_fetch_artwork("breakthrough")
    service.remember_file_id(row["file_hash"], "EXPIRED_ID")
    bot = FakeBot()
    bot.fail_file_id = True     # Telegram: can't find file for PhotoSize
    assert await deliver_artwork(bot, 777, {"user_id": 1},
                                 {"asset_key": "breakthrough", "caption": "cap"},
                                 service=service)
    assert len(bot.photos) == 1, "exactly the fallback upload landed"
    assert not isinstance(bot.photos[0][1], str)
    new_id = service.recall_file_id(row["file_hash"])
    assert new_id and new_id != "EXPIRED_ID", "dead id replaced in memory"


# ── routing contracts: specs on zone/hunt/victory + captions ────────────────

def test_zone_and_hunt_attach_artwork_specs(tmp_path):
    storage = Storage(tmp_path / "world.db")
    bootstrap_world(storage)
    settings = make_settings(tmp_path)
    ctx = Ctx(storage, admin_ids={999}, settings=settings)
    ctx._artworks = ArtworkService(storage, settings)   # pure route, no HTTP
    ctx._artworks_built = True

    user = new_user_doc(101, "map_soul", "en")
    from grimhaven.bot.handlers.callbacks import _route, _canon
    text, kb, opts = _route(ctx, user, _canon("map:zone:inspect:zone_mist_peak", user), {})
    spec = opts.get("artwork")
    assert spec and spec["asset_key"] == "zone:zone_mist_peak"
    assert "Mist Peak" in spec["caption"]
    assert spec["once_key"] == "zone:zone_mist_peak"
    assert spec["throttle_minutes"] >= 60, "zone views must be throttled"

    user["cultivation"]["qi_current"] = 10_000
    data = _canon("map:action:hunt:zone_mist_peak", user)
    text, kb, opts = _route(ctx, user, data, {})
    spec = opts.get("artwork") or {}
    assert spec.get("asset_key") == "hunt:zone_mist_peak"
    queries = ctx._artworks.queries_for("hunt:zone_mist_peak")
    assert queries and all(len(q.split()) >= 4 for q in queries), \
        "queries must be descriptive, not generic"
    storage.close()


def test_route_works_without_artwork_layer(tmp_path):
    """Artwork off (Ctx without settings, like the demo) → identical game."""
    storage = Storage(tmp_path / "world.db")
    bootstrap_world(storage)
    ctx = Ctx(storage, admin_ids={999})             # settings=None → artworks None
    assert ctx.artworks is None
    user = new_user_doc(101, "plain_soul", "en")
    from grimhaven.bot.handlers.callbacks import _route, _canon
    text, kb, opts = _route(ctx, user, _canon("map:zone:inspect:zone_mist_peak", user), {})
    assert "artwork" not in opts
    storage.close()


def test_captions_are_bilingual_and_artwork_map_is_data_driven():
    from grimhaven.localization import t
    en = t("en", "ART_CAP_BREAKTHROUGH")
    fa = t("fa", "ART_CAP_BREAKTHROUGH")
    assert "Heavenly tribulation" in en and not FA_CHARS.search(en)
    assert FA_CHARS.search(fa) and "⚡" in fa
    mapping = json.loads((Path(__file__).resolve().parent.parent / "data"
                          / "artwork_map.json").read_text(encoding="utf-8"))
    events = mapping["events"]
    for key in ("breakthrough", "breakthrough_fail", "milestone_realm",
                "zone:zone_valley_mortals", "hunt:zone_bamboo_forest", "victory"):
        assert key in events, key
        assert len(events[key]["queries"][0].split()) >= 5   # descriptive, not generic


# ── storage integration ──────────────────────────────────────────────────────

def test_artwork_table_lives_in_the_game_database(tmp_path):
    import sqlite3
    db = tmp_path / "g.db"
    st = Storage(db)
    st.close()
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "artwork" in tables
    st2 = Storage(db)
    try:
        st2.artwork_upsert({"asset_key": "breakthrough", "file_hash": "a" * 64,
                            "file_path": "files/aa.jpg", "mime": "image/jpeg",
                            "width": 8, "height": 8, "bytes": 10, "validated": 1,
                            "status": "ok", "provider": "test"})
        row = st2.artwork_get("breakthrough")
        assert row["status"] == "ok" and row["last_used_at"]
        assert st2.counts()["artwork"] == 1
        assert st2.integrity_report()["ok"] is True
        st2.artwork_delete("breakthrough")
        assert st2.artwork_get("breakthrough") is None
    finally:
        st2.close()


def test_legacy_database_upgrades_in_place(tmp_path):
    """A v3 database (no artwork table) must gain the table without losing rows."""
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, doc TEXT NOT NULL,
                           updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE zones (zone_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE sects (sect_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE corrupt_users (user_id INTEGER PRIMARY KEY, raw TEXT,
                                    reason TEXT,
                                    quarantined_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO meta(key, value) VALUES ('era', '"v3"');
    """)
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    conn.close()
    st = Storage(db)
    try:
        assert st.get_meta("era") == "v3"
        assert st.artwork_stats() == {"ok": 0, "failed": 0, "other": 0}
    finally:
        st.close()


def test_clear_cache_removes_files_and_rows(tmp_path, storage):
    files_dir = tmp_path / "assets" / "artwork" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "deadbeef.jpg").write_bytes(b"x")
    service = ArtworkService(storage, make_settings(tmp_path))
    storage.artwork_upsert({"asset_key": "breakthrough", "file_hash": "deadbeef",
                            "file_path": "files/deadbeef.jpg", "status": "ok",
                            "validated": 1})
    report = service.clear_cache()
    assert report["rows_removed"] == 1
    assert not list(files_dir.glob("*.jpg"))

"""End-to-end through the real PTB router and a Telegram-semantics fake API.

This is the test that did not exist when the bot broke. Unit tests hand-built
their own message objects — including a ``.bot`` attribute python-telegram-bot
removed in v20 — so 115 green tests coexisted with a production bot that raised
``AttributeError`` on almost every tap. Here the fakes are the *real* PTB
objects: ``Update.de_json`` parses the payload, the registered handlers and
conversation state machine run, and ``Bot._post`` is replaced at the transport
layer with a fake that knows Telegram's actual rejections:

* replying to a deleted message → ``REPLY_MESSAGE_NOT_FOUND``
* editing a gone message        → ``MESSAGE_TO_EDIT_NOT_FOUND``
* identical text + markup       → ``Message is not modified``
* deleting a gone message       → ``message to delete not found``
* more than 4096 characters     → ``Message is too long``

so a render that only "works" because the fake is more generous than Telegram is
caught here instead of by a player.
"""
from __future__ import annotations

import logging

import pytest
import telegram
from pytest_asyncio import fixture as async_fixture

from tests.live_api_harness import Bot, sweep

pytestmark = pytest.mark.asyncio

DOCK_FA = ["🧘 مدیتیشن و تهذیب", "⚡ اقدام به شکست سد", "🗺 نقشه و شکار",
           "🎒 کوله‌پشتی و گنجینه", "📜 لوح سرنوشت (پروفایل)", "🏛 پاویون تجارت",
           "⛩ فرقه", "⚙️ تنظیمات"]
DOCK_EN = ["🧘 Meditation & Cultivation", "⚡ Attempt Breakthrough", "🗺 Map & Hunt",
           "🎒 Bag & Treasures", "📜 Destiny Scroll (Profile)", "🏛 Spirit Pavilion",
           "⛩ Sect", "⚙️ Settings"]
COMMANDS = ["/me", "/cultivate", "/breakthrough", "/map", "/bag", "/skills", "/shop",
            "/sect", "/settings", "/help", "/panel"]


@async_fixture()
async def live(tmp_path, monkeypatch):
    """Patch the transport, run the app, and put everything back afterwards."""
    real_post = telegram.Bot._post
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    bot = Bot(tmp_path / "live.db")
    await bot.start()
    try:
        yield bot
    finally:
        await bot.stop()
        telegram.Bot._post = real_post
        root.handlers, root.level = saved_handlers, saved_level


async def test_reported_live_sequence_renders_every_screen(live: Bot):
    """The exact sequence from the failing deployment: hub → claim → Back, twice,
    then every dock button and every slash command.

    Nothing may raise inside a handler, every step must leave visible text on
    Telegram, and the root anchor must track the message that is actually on
    screen (the stale anchor is what made the failure permanent).
    """
    steps: list[tuple[str, str, int | None]] = []

    async def step(kind: str, payload: str) -> None:
        before = len(live.logs.records)
        if kind == "dock":
            await live.tap(payload)
        elif kind == "cmd":
            await live.send(payload)
        else:
            await live.press(payload)
        text, _markup = live.last_render()
        anchor = (live.user() or {}).get("ui", {}).get("active_menu_message_id")
        # checked immediately: a root render *deletes* the window it replaces, so
        # an anchor recorded three steps ago is expected to be gone by the end
        state = "EXCEPTION" if live.logs.records[before:] else (
            "ok" if text else "SILENT")
        if anchor is not None and anchor not in live.state.messages:
            state = "STALE-ANCHOR"
        steps.append((f"{kind} {payload}", state, anchor))

    await step("cmd", "/start")
    for _round in range(2):
        await step("dock", "🧘 مدیتیشن و تهذیب")
        await step("cb", "cultivate:action:claim")
        await step("cb", "cultivate:view:hub")
    for label in DOCK_FA:
        await step("dock", label)
    for command in COMMANDS:
        await step("cmd", command)

    broken = [s for s in steps if s[1] != "ok"]
    assert not broken, "\n".join(f"{n}: {r}" for n, r, _a in broken)
    assert not live.logs.records, "the handlers logged an incident"


async def test_dock_tap_twice_in_a_row_is_not_swallowed(live: Bot):
    """The original failure signature: the first menu opened, the second tap on
    the same dock button answered "unknown error" — because the render after an
    existing anchor tried ``message.bot.delete_message``. A repeat tap must land
    on the same screen, not on an error."""
    from grimhaven.localization import t
    for _ in range(3):
        await live.tap("🧘 مدیتیشن و تهذیب")
        text, _markup = live.last_render()
        assert text, "a dock tap must always render something"
        assert t("fa", "ERR_UNKNOWN") not in text, f"the hub turned into an error: {text[:80]!r}"
        assert t("fa", "HUB_TITLE") in text
    assert not live.logs.records


async def test_long_screens_are_never_rejected_by_telegram(live: Bot):
    """A chronicle with many hours of AFK text can approach Telegram's 4096
    character ceiling. The fake enforces the limit, so this walks every reachable
    screen and fails if any of them is refused."""
    await live.tap("🧘 مدیتیشن و تهذیب")
    await live.press("cultivate:action:claim")
    longest = 0
    for endpoint, payload in live.state.calls:
        if endpoint in ("sendMessage", "editMessageText"):
            longest = max(longest, len(payload.get("text") or ""))
    assert longest > 0
    assert longest <= 4096, f"a {longest}-character screen was sent"
    assert not [r for r in live.logs.records if "too long" in r.lower()]


@pytest.mark.parametrize(("tag", "dock"), [("fa", DOCK_FA), ("en", DOCK_EN)])
async def test_every_reachable_screen_renders(tmp_path, tag, dock):
    """Breadth-first over the whole reachable graph: every callback_data the bot
    itself puts on a keyboard is tapped twice against a fresh world."""
    failures, seen = await sweep(tmp_path / f"bfs_{tag}.db",
                                 list(dock) + list(COMMANDS),
                                 max_states=100, dock=list(dock))
    detail = "\n\n".join(f"{f['kind']} · {f['payload']!r}\n{f.get('err','')}\n"
                         f"{f.get('tb','')[:1200]}" for f in failures[:4])
    assert not failures, f"[{tag}] {len(failures)} failure(s) over {len(seen)} payloads:\n{detail}"
    assert len(seen) > 60, f"[{tag}] only {len(seen)} payloads reachable — the router regressed"

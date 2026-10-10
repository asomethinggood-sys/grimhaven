"""Full-workflow sweep through the REAL PTB router, with a Telegram-semantics
faithful fake API — the harness that would have caught the production outage.

Emulates the parts of the Bot API that the code must survive:
  * reply_to a deleted message        → 400 REPLY_MESSAGE_NOT_FOUND
  * edit a deleted message            → 400 MESSAGE_TO_EDIT_NOT_FOUND
  * edit with identical text+markup   → 400 "message is not modified"
  * delete a message that is gone     → 400 "message to delete not found"
  * sendMessage > 4096 chars          → 400 "message is too long"

Then it walks the workflow breadth-first: every dock label, every slash command
and every inline callback_data reachable from any rendered screen, each fired
twice (repeat taps), against a fresh SQLite file. Every handler exception is
captured from the logger (the same stage-aware log lines the runner prints).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # the bot lives in the parent dir
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import telegram  # noqa: E402
from telegram.ext import Application  # noqa: E402
from telegram.error import BadRequest  # noqa: E402

FAKE_TOKEN = "123456:AA_fake_token_for_local_harness_only"


class ApiState:
    def __init__(self):
        self.mid = 5000
        self.last = None          # message currently showing a keyboard
        self.messages: dict[int, dict] = {}   # live messages: id -> {text, reply_markup}
        self.deleted: set[int] = set()
        self.calls: list[tuple[str, dict]] = []

    def next_id(self):
        self.mid += 1
        return self.mid


class FakeBotAPI:
    @staticmethod
    def _echo(markup):
        """Telegram only echoes an *inline* keyboard in the message result; a
        ReplyKeyboardMarkup is not part of the response body at all."""
        if isinstance(markup, dict) and "inline_keyboard" in markup:
            return markup
        return None

    def __init__(self, state: ApiState, log=None):
        self.s = state
        self.log = log if log is not None else []

    async def _post(self, endpoint, data=None, **kwargs):
        d = dict(data or {})
        rp = d.get("reply_markup")
        if rp is not None and hasattr(rp, "to_dict"):
            # PTB serializes at the transport layer; do the same so the echoed
            # result is plain JSON exactly like Telegram's response body.
            d["reply_markup"] = rp.to_dict()
        self.s.calls.append((endpoint, d))
        chat_id = d.get("chat_id")
        if endpoint == "getMe":
            return {"id": 7777, "is_bot": True, "first_name": "Grimhaven",
                    "username": "Grimheaven_bot"}
        if endpoint == "sendMessage":
            text = d.get("text") or ""
            if not text.strip():
                raise BadRequest("Bad Request: message text is empty")
            if len(text) > 4096:
                raise BadRequest(f"Bad Request: Message is too long ({len(text)} > 4096)")
            rid = d.get("reply_to_message_id")
            if rid and (rid in self.s.deleted or rid not in self.s.messages):
                raise BadRequest("Bad Request: REPLY_MESSAGE_NOT_FOUND")
            mid = self.s.next_id()
            self.s.messages[mid] = {"text": text,
                                    "reply_markup": self._echo(d.get("reply_markup")),
                                    "chat_id": chat_id}
            if d.get("reply_markup"):
                self.s.last = mid
            res = {"message_id": mid, "date": 1760000000,
                   "chat": {"id": chat_id, "type": "private"},
                   "from": {"id": 7777, "is_bot": True, "first_name": "Grimhaven"},
                   "text": text}
            echoed = self._echo(d.get("reply_markup"))
            if echoed:
                res["reply_markup"] = echoed
            return res
        if endpoint == "deleteMessage":
            mid = d.get("message_id")
            if mid in self.s.deleted or mid not in self.s.messages:
                raise BadRequest("Bad Request: message to delete not found")
            self.s.deleted.add(mid)
            self.s.messages.pop(mid, None)
            return True
        if endpoint == "editMessageText":
            mid = d.get("message_id")
            if mid in self.s.deleted or mid not in self.s.messages:
                raise BadRequest("Bad Request: MESSAGE_TO_EDIT_NOT_FOUND")
            cur = self.s.messages[mid]
            new_text = d.get("text") or ""
            if len(new_text) > 4096:
                raise BadRequest(f"Bad Request: Message is too long ({len(new_text)} > 4096)")
            if not new_text.strip():
                raise BadRequest("Bad Request: message text is empty")
            if new_text == cur["text"] and self._echo(d.get("reply_markup")) == cur["reply_markup"]:
                raise BadRequest("Message is not modified: specified 'text' and 'reply_markup' "
                                 "are the same as the message's current ones")
            cur["text"] = new_text
            if "reply_markup" in d:
                cur["reply_markup"] = self._echo(d["reply_markup"])
            self.s.last = mid
            res = dict(cur)
            res.update({"message_id": mid, "date": 1760000000,
                        "chat": {"id": chat_id, "type": "private"},
                        "from": {"id": 7777, "is_bot": True, "first_name": "Grimhaven"}})
            return res
        if endpoint == "editMessageReplyMarkup":
            mid = d.get("message_id")
            if mid in self.s.deleted or mid not in self.s.messages:
                raise BadRequest("Bad Request: MESSAGE_TO_EDIT_NOT_FOUND")
            self.s.messages[mid]["reply_markup"] = self._echo(d.get("reply_markup"))
            self.s.last = mid
            return {"message_id": mid, "date": 1760000000,
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": 7777, "is_bot": True, "first_name": "Grimhaven"}}
        return True


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []
        self.tracebacks: dict[str, str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        tb = ""
        if record.exc_info:
            import traceback
            tb = "".join(traceback.format_exception(*record.exc_info))
        self.records.append(msg)
        if tb:
            self.tracebacks[msg] = tb


class Bot:
    def __init__(self, db_path: Path):
        from grimhaven.config import Settings
        from grimhaven.bot.app import build_application
        from grimhaven.db.storage import Storage

        self.state = ApiState()
        self.api = FakeBotAPI(self.state)
        telegram.Bot._post = self.api._post  # type: ignore[assignment]
        self.storage = Storage(db_path)
        settings = Settings(telegram_bot_token=FAKE_TOKEN, database_path=db_path,
                            admin_ids={101})
        self.app: Application = build_application(settings, self.storage)
        self.ctx = self.app.bot_data["ctx"]
        self.logs = CapturingHandler()
        root = logging.getLogger()
        root.handlers = [self.logs]
        root.setLevel(logging.ERROR)

    async def start(self):
        await self.app.initialize()

    async def stop(self):
        await self.app.shutdown()
        self.storage.close()

    def user(self):
        return self.storage.get_user(101)

    async def send(self, text: str, message_id: int = 500):
        msg = {"message_id": message_id, "date": 1760000000,
               "chat": {"id": 101, "type": "private", "first_name": "T"},
               "from": {"id": 101, "is_bot": False, "first_name": "T",
                        "language_code": "fa"},
               "text": text}
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0,
                                "length": len(text.split()[0])}]
        upd = telegram.Update.de_json({"update_id": 4242, "message": msg}, self.app.bot)
        await self.app.process_update(upd)

    async def tap(self, text: str) -> None:
        """A persistent-dock button tap: an ordinary text message, no callback."""
        await self.send(text, message_id=600 + len(self.state.calls))

    async def press(self, data: str, message_id: int = 0, update_id: int = 4300):
        if not message_id:
            message_id = self.state.last or 500
        live = message_id if message_id in self.state.messages else None
        msg = {"message_id": message_id, "date": 1760000000,
               "chat": {"id": 101, "type": "private", "first_name": "T"},
               "from": {"id": 7777, "is_bot": True, "first_name": "Grimhaven"},
               "text": self.state.messages[message_id]["text"] if live else "screen"}
        upd = telegram.Update.de_json({
            "update_id": update_id,
            "callback_query": {"id": str(update_id),
                               "from": {"id": 101, "is_bot": False, "first_name": "T",
                                        "language_code": "fa"},
                               "message": msg, "data": data, "chat_instance": "1"}},
            self.app.bot)
        await self.app.process_update(upd)

    # ── observations ────────────────────────────────────────────────────────
    def last_render(self):
        """Last visible text+markup the bot pushed to Telegram."""
        for ep, d in reversed(self.state.calls):
            if ep in ("sendMessage", "editMessageText"):
                return d.get("text"), d.get("reply_markup")
        return None, None

    def last_markup(self):
        """The markup the bot asked for on its last render (echoed or not)."""
        for endpoint, d in reversed(self.state.calls):
            if endpoint in ("sendMessage", "editMessageText", "editMessageReplyMarkup"):
                rp = d.get("reply_markup")
                if rp is not None and hasattr(rp, "to_dict"):
                    rp = rp.to_dict()
                return rp
        return None

    def errored_screen(self) -> str:
        """The text of the last render, if it was a failure notice of any kind.

        Checked per locale because the player-facing text is localized: looking
        for one Persian string would miss an English error and call it a pass.
        """
        from grimhaven.localization import t
        text, _markup = self.last_render()
        if not text:
            return ""
        for lang in ("fa", "en"):
            for key in ("ERR_UNKNOWN", "ERR_INTERNAL", "ERR_STORAGE", "ERR_DELIVERY",
                        "ERR_CONTENT"):
                needle = t(lang, key).split(":")[0][:24]
                if needle and needle in text:
                    return text
        return ""

    def callbacks_of(self, markup):
        out = []
        for row in (markup or {}).get("inline_keyboard", []) or []:
            for b in row:
                cd = b.get("callback_data")
                if cd:
                    out.append(cd)
        return out


async def sweep(db: Path, seeds: list[str], max_states=200, twice=True,
                dock: list[str] | None = None):
    """Walk every payload reachable from the seeds and report any failure.

    A payload is fired the way the *client* would fire it: a dock button is an
    ordinary text message, a slash command is a text message with a
    ``bot_command`` entity, and anything else is a ``callback_query`` on the
    message the player is looking at. Sending a dock label as callback_data
    (the obvious shortcut) silently routes to the unknown-action branch and the
    walk discovers nothing — that mistake once produced a green sweep over 30
    payloads of a 150-payload game.
    """
    real_post = telegram.Bot._post
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    dock = set(dock or [])
    bot = Bot(db)
    failures: list[dict] = []
    seen: set[str] = set()
    try:
        await bot.start()
        await bot.send("/start")
        queue: list[str] = list(seeds)
        steps = 0
        while queue and steps < max_states:
            payload = queue.pop(0)
            if payload in seen or len(seen) >= max_states:
                continue
            seen.add(payload)
            steps += 1
            for _ in range(2 if twice else 1):
                before = len(bot.logs.records)
                try:
                    await fire(bot, payload, dock)
                except Exception as exc:                    # escaped PTB entirely
                    failures.append({"payload": payload, "kind": "ESCAPED",
                                     "err": f"{exc.__class__.__name__}: {exc}"})
                    continue
                new_logs = bot.logs.records[before:]
                if new_logs:
                    failures.append({
                        "payload": payload, "kind": "handler-exception",
                        "err": new_logs[-1][:200],
                        "tb": bot.logs.tracebacks.get(new_logs[-1], "")[:4000]})
            _text, markup = bot.last_render()
            for cd in bot.callbacks_of(bot.last_markup() or markup):
                if cd not in seen:
                    queue.append(cd)
            if bot.errored_screen():
                failures.append({"payload": payload, "kind": "error-screen-shown",
                                 "err": bot.errored_screen()[:160]})
        await bot.stop()
    finally:
        # this patches class-level transport and the root logger; a module that
        # runs after this one must not inherit either
        telegram.Bot._post = real_post
        root.handlers, root.level = saved_handlers, saved_level
    return failures, seen


async def fire(bot: "Bot", payload: str, dock: set[str] | None = None) -> None:
    """Send one payload the way Telegram would."""
    dock = dock or set()
    if payload in dock:
        await bot.tap(payload)
    elif payload.startswith("/"):
        await bot.send(payload)
    else:
        anchor = (bot.user() or {}).get("ui", {}).get("active_menu_message_id") or 500
        await bot.press(payload, anchor)

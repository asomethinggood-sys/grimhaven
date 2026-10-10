"""Faithful PTB fakes for the handler-level tests.

Why this module exists: every previous round faked the Telegram objects with
hand-written classes that carried a ``.bot`` attribute — and python-telegram-bot
v20 **removed** ``TelegramObject.bot``. The production code called
``message.bot.delete_message(...)`` / ``query.bot.delete_message(...)``, so the
fakes passed while the deployed bot raised ``AttributeError`` on every root
screen. These fakes therefore

* expose **no** ``.bot`` on messages or callback queries (like real PTB ≥ 20),
* give the callback the *same* message id the document's root anchor holds
  (the real chat: the button you tap lives on the message you are looking at),
* model Telegram's rejections (deleted message, "message is not modified",
  expired callback query) so a swallowed render failure is visible in a test.
"""
from __future__ import annotations

from types import SimpleNamespace

from telegram.error import BadRequest


class FakeBot:
    """Only the methods the handlers actually call."""

    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []
        self.deleted: list[int] = []
        self.stripped: list[int] = []
        self.edited_markup: list[tuple[int, object]] = []
        #: every photo send: (chat_id, photo, caption) — the artwork layer
        #: asserts on this to prove a REAL upload happened
        self.photos: list[tuple[int, object, object]] = []
        self.documents: list[tuple[int, object, object]] = []
        #: True → Telegram refuses anything carrying a keyboard (a screen), while
        #: plain text (the failure notice) still lands. That is the realistic shape
        #: of a render rejection and it is what the reporter must cope with.
        self.fail_send = False
        self.fail_delete = False
        #: True → send_photo raises: the artwork layer must swallow it in silence
        #: (the game action already landed; a picture must never become an error)
        self.fail_photo = False
        #: when set, send_photo raises only for string photo ids (a stale
        #: Telegram file_id) so the InputFile re-upload path is exercised
        self.fail_file_id = False
        self._mid = 9000

    def next_id(self) -> int:
        self._mid += 1
        return self._mid

    async def delete_message(self, chat_id, message_id):
        if self.fail_delete:
            raise BadRequest("Bad Request: message to delete not found")
        self.deleted.append(message_id)
        return True

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if self.fail_send:
            # what Telegram actually rejects is the keyboard on the screen, so a
            # plain-text notice (the failure report) still gets through
            raise BadRequest("Bad Request: BUTTON_TYPE_INVALID")
        mid = self.next_id()
        self.sent.append((mid, text, reply_markup))
        return SimpleNamespace(message_id=mid, chat_id=chat_id)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None,
                         **kwargs):
        if self.fail_photo or (self.fail_file_id and isinstance(photo, str)):
            raise BadRequest("Bad Request: PHOTO_FILE_INVALID")
        mid = self.next_id()
        self.photos.append((chat_id, photo, caption))
        return SimpleNamespace(message_id=mid, chat_id=chat_id,
                               photo=[SimpleNamespace(file_id=f"FAKE_FILE_ID_{mid}")])

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        if self.fail_photo:
            raise BadRequest("Bad Request: DOCUMENT_INVALID")
        mid = self.next_id()
        self.documents.append((chat_id, document, caption))
        return SimpleNamespace(message_id=mid, chat_id=chat_id)

    async def edit_message_reply_markup(self, chat_id=None, message_id=None,
                                        reply_markup=None, **kwargs):
        self.stripped.append(message_id)
        return True


class FakeMessage:
    """A message the player can tap. Note: no ``bot`` attribute — like PTB v21."""

    def __init__(self, bot: FakeBot, text: str | None = None, message_id: int = 500,
                 chat_id: int = 777):
        self.bot_ref = bot
        self.text = text
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat = SimpleNamespace(id=chat_id,
                                    send_message=self._chat_send)
        self.replies: list[str] = []
        self.reply_markups: list[object] = []
        self.edits: list[str] = []
        self.deleted = False

    @property
    def bot(self):  # pragma: no cover - guards against the removed attribute
        raise AssertionError("PTB v21 removed Message.bot; use context.bot")

    async def reply_text(self, text, reply_markup=None, **kwargs):
        if self.deleted:
            raise BadRequest("Bad Request: REPLY_MESSAGE_NOT_FOUND")
        if reply_markup is not None and self.bot_ref.fail_send:
            raise BadRequest("Bad Request: BUTTON_TYPE_INVALID")
        self.replies.append(text)
        self.reply_markups.append(reply_markup)
        mid = self.bot_ref.next_id()
        return SimpleNamespace(message_id=mid, chat_id=self.chat_id)

    async def _chat_send(self, text, reply_markup=None, **kwargs):
        return await self.bot_ref.send_message(self.chat_id, text,
                                               reply_markup=reply_markup)

    async def delete(self):
        self.deleted = True
        return True


class FakeQuery:
    """A callback query on ``message_id`` (the message that holds the keyboard)."""

    def __init__(self, bot: FakeBot, data: str, message_id: int = 500,
                 chat_id: int = 777):
        self.bot_ref = bot
        self.data = data
        self.answered: list[tuple[str | None, bool]] = []
        self.edits: list[str] = []
        self.stripped = 0
        self.fail_answer = False
        self.message = FakeMessage(bot, "previous screen", message_id, chat_id)

    @property
    def bot(self):  # pragma: no cover - guards against the removed attribute
        raise AssertionError("PTB v21 removed CallbackQuery.bot; use context.bot")

    async def answer(self, text=None, show_alert=False, **kwargs):
        if self.fail_answer:
            raise BadRequest("Bad Request: query is too old and response "
                             "timeout expired")
        self.answered.append((text, show_alert))
        return True

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        # the instant de-weaponization of the pressed keyboard
        self.stripped += 1
        return True

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        if self.message.deleted:
            raise BadRequest("Bad Request: MESSAGE_TO_EDIT_NOT_FOUND")
        if text == self.message.text and reply_markup is None:
            raise BadRequest("Message is not modified: specified text is the same")
        self.edits.append(text)
        self.message.text = text
        return self.message


class FakeUpdate:
    def __init__(self, *, text=None, query_data=None, bot: FakeBot | None = None,
                 user_id: int = 70, message_id: int = 500, update_id: int = 4242):
        bot = bot or FakeBot()
        self.bot = bot
        self.update_id = update_id
        self.effective_user = SimpleNamespace(id=user_id, username=f"soul{user_id}",
                                              first_name="Tester")
        self.callback_query: FakeQuery | None = None
        self.message: FakeMessage | None = None
        if query_data is not None:
            self.callback_query = FakeQuery(bot, query_data, message_id)
        else:
            self.message = FakeMessage(bot, text, message_id)

    # convenience accessors used by the assertions
    @property
    def replies(self):
        return self.message.replies if self.message else []

    @property
    def edits(self):
        return self.callback_query.edits if self.callback_query else []

    @property
    def answered(self):
        return self.callback_query.answered if self.callback_query else []

    @property
    def stripped(self):
        return self.callback_query.stripped if self.callback_query else 0


class FakeContext:
    """What PTB hands a handler: ``bot_data`` **and** ``bot``."""

    def __init__(self, ctx, bot: FakeBot | None = None, args=None):
        self.bot_data = {"ctx": ctx}
        self.bot = bot or FakeBot()
        self.args = args or []

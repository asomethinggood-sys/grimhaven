"""Shared handler context: user loading/migration, AFK settlement, world boost."""
from __future__ import annotations

import datetime as dt

from ...db.storage import Storage
from ...engine.cultivation import CultivationEngine
from ...engine.models import ensure_v2, new_user_doc, parse_iso, utcnow
from ...render import deep_profile_text, profile_text
from ..keyboards import back_to_menu, deep_kb, main_menu


class Ctx:
    """Everything a handler needs, pulled from the shared application bot_data."""

    def __init__(self, storage: Storage, admin_ids: set[int]):
        self.storage = storage
        self.admin_ids = admin_ids

    # world boost (admin /world_boost) ─ {rate, until}
    def world_boost(self, now: dt.datetime | None = None) -> float:
        now = now or utcnow()
        boost = self.storage.get_meta("world_boost")
        if not boost:
            return 1.0
        until = parse_iso(boost.get("until"))
        if until and until > now:
            return float(boost.get("rate", 1.0))
        return 1.0

    def boost_label(self) -> str:
        rate = self.world_boost()
        return f"×{rate:g}" if rate != 1.0 else "—"

    def get_or_create_user(self, tg_user) -> tuple[dict, bool]:
        doc = self.storage.get_user(tg_user.id)
        if doc:
            ensure_v2(doc)
            return doc, False
        username = tg_user.username or tg_user.first_name or f"cultivator_{tg_user.id}"
        doc = ensure_v2(new_user_doc(tg_user.id, username))
        self.storage.save_user(doc)
        return doc, True

    def settle(self, user: dict, now: dt.datetime | None = None) -> dict:
        return CultivationEngine.settle_afk(user, now=now, world_boost=self.world_boost(now))

    def save(self, user: dict) -> None:
        self.storage.save_user(user)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def profile_reply(self, user: dict, now: dt.datetime | None = None):
        """(text, keyboard) for the streamlined destiny-scroll panel."""
        now = now or utcnow()
        text = profile_text(self.storage, user, world_boost=self.world_boost(now), now=now)
        keyboard = main_menu(user["account"]["language"], user)
        return text, keyboard

    def deep_reply(self, user: dict):
        lang = user["account"]["language"]
        return deep_profile_text(lang, user), deep_kb(lang)

    def back_reply(self, user: dict, text: str):
        return text, back_to_menu(user["account"]["language"])

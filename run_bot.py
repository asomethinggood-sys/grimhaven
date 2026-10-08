#!/usr/bin/env python3
"""Entry point — «نبرد جاودانگان و تسخیر آسمان» Telegram bot (@Grimheaven_bot).

Usage:
    python run_bot.py            # long-polling (default)
    WEBHOOK_URL=... python run_bot.py   # webhook mode behind HTTPS
"""
from __future__ import annotations

import sys

from grimhaven.bot.app import build_application
from grimhaven.config import settings
from grimhaven.db.storage import Storage

BANNER = """
⚔️  Grimhaven — War of the Immortals: Conquest of Heaven
    نبرد جاودانگان و تسخیر آسمان
"""


def main() -> int:
    print(BANNER)
    if not settings.telegram_bot_token or "replace-me" in settings.telegram_bot_token:
        print("❌ TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add the "
              "token from @BotFather, then run again.")
        return 1
    storage = Storage(settings.database_path)
    app = build_application(settings, storage)

    if settings.webhook_url:
        print(f"🛰  webhook mode → {settings.webhook_url}")
        app.run_webhook(
            listen=settings.listen_host,
            port=settings.listen_port,
            url_path="webhook",
            webhook_url=settings.webhook_url.rstrip("/") + "/webhook",
        )
    else:
        print("📡 long-polling mode — the Heavenly Will is listening…")
        app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

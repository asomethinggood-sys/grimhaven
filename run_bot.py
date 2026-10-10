#!/usr/bin/env python3
"""Entry point — «نبرد جاودانگان و تسخیر آسمان» Telegram bot (@Grimheaven_bot).

Usage:
    python run_bot.py                        # long-polling (default)
    WEBHOOK_URL=... python run_bot.py        # webhook mode behind HTTPS

Operator switches (all environment, no code push needed):

    GRIMHAVEN_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR   default INFO
    GRIMHAVEN_RESET_WORLD=1     wipe the mutable SQLite state and rebuild the
                                 world from the checked-in data/*.json before
                                 booting (static content is never touched)
    GRIMHAVEN_ALLOW_CORRUPT_DB=1  boot anyway after a failed integrity check

Why the boot sequence is this careful: the hosted instance restores its database
from a workflow artifact, and the failure that took the bot down was a *storage*
problem that only became visible as "unknown error" on every button. So the
process now (1) configures logging before importing anything that logs, (2)
reports the database's integrity, row counts and journal mode as its first act,
and (3) refuses to serve players from a database it already knows is broken.
"""
from __future__ import annotations

import logging
import sys


def _configure_logging(level: str) -> None:
    """Send the incident lines somewhere a human (and the Actions log) can read.

    python-telegram-bot v20 stopped configuring logging for you, and
    ``logging.lastResort`` only prints WARNING+ with no timestamp or level — so
    an ``incident=`` line was effectively unfindable in the Actions log. stdout,
    because that is what the workflow collects.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    # the long-poller is chatty at INFO (one line per getUpdates); httpx per request
    for chatty in ("httpx", "httpcore", "telegram.ext._application",
                   "telegram.ext._updater", "apscheduler"):
        logging.getLogger(chatty).setLevel(
            logging.DEBUG if level == "DEBUG" else logging.WARNING)


def main() -> int:
    from grimhaven.config import settings        # after logging, so .env errors log

    _configure_logging(settings.log_level)
    log = logging.getLogger("grimhaven.boot")
    print(__import__("grimhaven").__doc__ or "")
    log.info("boot: db=%s log_level=%s webhook=%s admins=%d",
             settings.database_path, settings.log_level,
             settings.webhook_url or "off", len(settings.admin_ids))

    if not settings.telegram_bot_token or "replace-me" in settings.telegram_bot_token:
        print("❌ TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add the "
              "token from @BotFather, then run again.")
        return 1

    from grimhaven.db.storage import Storage, reset
    from grimhaven.errors import StorageError
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    if settings.reset_world:
        report = reset(settings.database_path)
        log.warning("GRIMHAVEN_RESET_WORLD=1 → mutable world state wiped: %s", report)
        print("🧹 world reset — rebuilt from data/*.json "
              f"(removed: {', '.join(report['removed']) or 'nothing'})")

    try:
        storage = Storage(settings.database_path)
    except StorageError as exc:
        # The file is not a usable database at all — a truncated artifact from the
        # restore step looks exactly like this. There is nothing to serve, so the
        # only useful output is the repair command.
        log.error("the world database could not be opened: %s", exc)
        print(f"❌ no usable database at {settings.database_path}: {exc}\n"
              "   repair:  GRIMHAVEN_RESET_WORLD=1 python run_bot.py\n"
              "            (or: python scripts/reset_world.py --yes --db "
              "<path> --keep-audit /tmp/audit.json)")
        return 2
    try:
        integrity = storage.integrity_report()
    except Exception as exc:  # pragma: no cover — the report must never be the crash
        log.exception("the integrity check itself failed")
        integrity = {"ok": False, "path": str(settings.database_path),
                     "integrity_check": [f"report failed: {exc}"],
                     "journal_mode": "?", "schema_version": 0, "size_bytes": 0,
                     "counts": {}}
    counts = integrity["counts"]
    log.info("database: %s (%s bytes, journal=%s, schema v%s) counts=%s",
             integrity["path"], integrity["size_bytes"], integrity["journal_mode"],
             integrity["schema_version"], counts)
    if not integrity["ok"]:
        log.error("DATABASE CHECK FAILED: %s", "; ".join(integrity["integrity_check"]))
        if not settings.allow_corrupt_db:
            print("❌ the world database failed its integrity check; refusing to boot "
                  "so no player is served from a broken vault.\n"
                  f"   path: {integrity['path']}\n"
                  "   repair:  GRIMHAVEN_RESET_WORLD=1 python run_bot.py\n"
                  "            (or: python scripts/reset_world.py --yes)\n"
                  "   or set GRIMHAVEN_ALLOW_CORRUPT_DB=1 to boot anyway.")
            storage.close()
            return 2
        log.warning("GRIMHAVEN_ALLOW_CORRUPT_DB=1 → booting despite the failed check")
    quarantined = counts.get("corrupt_users") or 0
    if quarantined:
        log.warning("%d unreadable player document(s) are quarantined in corrupt_users "
                    "(the row keeps its bytes so the evidence survives); inspect with: "
                    "python scripts/reset_world.py --inspect", quarantined)
    def n(key: str) -> str:
        value = counts.get(key)
        return "?" if value is None else str(value)

    print(f"🗄  world ready — {n('users')} cultivator(s), {n('zones')} zone(s), "
          f"{n('sects')} sect(s), journal={integrity['journal_mode']}, "
          f"schema v{integrity['schema_version']}")

    from grimhaven.bot.app import build_application
    app = build_application(settings, storage)

    try:
        if settings.webhook_url:
            log.info("webhook mode → %s", settings.webhook_url)
            app.run_webhook(
                listen=settings.listen_host,
                port=settings.listen_port,
                url_path="webhook",
                webhook_url=settings.webhook_url.rstrip("/") + "/webhook",
            )
        else:
            log.info("long-polling mode — the Heavenly Will is listening…")
            app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:                     # Ctrl-C in a terminal
        log.info("interrupted by operator")
        return 130
    except Exception:                             # never die without a traceback
        log.exception("the bot stopped unexpectedly")
        return 1
    finally:
        try:
            storage.close()                        # checkpoint the WAL into the file
        except Exception:                          # …that we save as an artifact
            log.exception("could not close the database cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

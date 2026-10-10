"""Runtime configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path | None = None) -> None:
    """Tiny dependency-free .env loader (KEY=VALUE lines)."""
    env_file = path or ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


@dataclass
class Settings:
    telegram_bot_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    admin_ids: set[int] = field(default_factory=set)
    database_path: Path = ROOT_DIR / "data" / "grimhaven.db"
    locales_dir: Path = ROOT_DIR / "locales"
    webhook_url: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    demo_host: str = "0.0.0.0"
    demo_port: int = 8000
    #: everything the operator needs to flip from the Actions UI — no code push
    log_level: str = "INFO"
    reset_world: bool = False
    #: refuse to boot against a database that failed PRAGMA integrity_check
    allow_corrupt_db: bool = False

    @classmethod
    def load(cls) -> "Settings":
        admin_raw = os.environ.get("ADMIN_IDS", "")
        admins = set()
        for chunk in admin_raw.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.lstrip("-").isdigit():
                admins.add(int(chunk))
        db_path = Path(
            os.environ.get("DATABASE_PATH", str(ROOT_DIR / "data" / "grimhaven.db"))
        )
        if not db_path.is_absolute():
            db_path = ROOT_DIR / db_path
        port = os.environ.get("LISTEN_PORT", "8080")
        demo_port = os.environ.get("DEMO_PORT", "8000")
        truthy = ("1", "true", "yes", "on")
        level = os.environ.get("GRIMHAVEN_LOG_LEVEL", "INFO").strip().upper()
        return cls(
            log_level=level if level in {"DEBUG", "INFO", "WARNING", "ERROR"} else "INFO",
            reset_world=os.environ.get("GRIMHAVEN_RESET_WORLD", "").strip().lower() in truthy,
            allow_corrupt_db=os.environ.get("GRIMHAVEN_ALLOW_CORRUPT_DB", "")
            .strip().lower() in truthy,
            admin_ids=admins,
            database_path=db_path,
            webhook_url=os.environ.get("WEBHOOK_URL", ""),
            listen_host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
            listen_port=int(port) if port.isdigit() else 8080,
            demo_host=os.environ.get("DEMO_HOST", "0.0.0.0"),
            demo_port=int(demo_port) if demo_port.isdigit() else 8000,
        )


settings = Settings.load()

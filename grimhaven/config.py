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
    # ── artwork subsystem (grimhaven/images) ──────────────────────────────
    #: master switch; off → every artwork hook becomes a no-op
    artwork_enabled: bool = True
    #: cache root; files land under <dir>/files/<sha256>.<ext>
    artwork_dir: Path = ROOT_DIR / "assets" / "artwork"
    #: ordered, comma-separated; unknown names are logged and skipped
    artwork_providers: str = "commons,openverse"
    #: per-request network timeout (seconds)
    artwork_timeout: float = 12.0
    #: bounded retries with exponential backoff on transient failures
    artwork_max_retries: int = 2
    #: simultaneous downloads/searches across all providers
    artwork_concurrency: int = 3
    #: reject candidates below this native width
    artwork_min_width: int = 480
    #: Telegram photo hard limit on each side
    artwork_max_side: int = 10_000
    #: download byte cap (also the cached-file cap for validation)
    artwork_max_bytes: int = 5_000_000
    #: LRU cap on index rows/files before eviction kicks in
    artwork_max_assets: int = 400
    #: after a total fetch failure, retry this asset only after N minutes
    artwork_fail_cooldown_minutes: int = 30
    #: hard ceiling for one background artwork task
    artwork_task_timeout: float = 45.0
    #: polite identity for provider APIs (required by Wikimedia etiquette)
    artwork_user_agent: str = ("GrimhavenBot/1.0 (Telegram cultivation game; "
                               "https://github.com/asomethinggood-sys/grimhaven)")
    #: Openverse licence filter (modification-friendly, non-commercial-safe)
    openverse_licenses: str = "cc0,pdm,by,by-sa"
    #: optional — Pixabay provider stays disabled while empty
    pixabay_api_key: str = ""

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

        def _num(key: str, default, cast):
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return cast(raw)
            except ValueError:
                return default

        artwork_dir_raw = os.environ.get("ARTWORK_DIR", "").strip()
        artwork_dir = Path(artwork_dir_raw) if artwork_dir_raw else (ROOT_DIR / "assets" / "artwork")
        if not artwork_dir.is_absolute():
            artwork_dir = ROOT_DIR / artwork_dir
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
            # artwork subsystem — see docs/ARTWORK.md and .env.example
            artwork_enabled=os.environ.get("ARTWORK_ENABLED", "1").strip().lower() in truthy,
            artwork_dir=artwork_dir,
            artwork_providers=os.environ.get("ARTWORK_PROVIDERS",
                                             "commons,openverse").strip(),
            artwork_timeout=_num("ARTWORK_TIMEOUT", 12.0, float),
            artwork_max_retries=_num("ARTWORK_MAX_RETRIES", 2, int),
            artwork_concurrency=_num("ARTWORK_CONCURRENCY", 3, int),
            artwork_min_width=_num("ARTWORK_MIN_WIDTH", 480, int),
            artwork_max_side=_num("ARTWORK_MAX_SIDE", 10_000, int),
            artwork_max_bytes=_num("ARTWORK_MAX_BYTES", 5_000_000, int),
            artwork_max_assets=_num("ARTWORK_MAX_ASSETS", 400, int),
            artwork_fail_cooldown_minutes=_num("ARTWORK_FAIL_COOLDOWN_MINUTES", 30, int),
            artwork_task_timeout=_num("ARTWORK_TASK_TIMEOUT", 45.0, float),
            artwork_user_agent=os.environ.get(
                "ARTWORK_USER_AGENT",
                "GrimhavenBot/1.0 (Telegram cultivation game; "
                "https://github.com/asomethinggood-sys/grimhaven)").strip(),
            openverse_licenses=os.environ.get("OPENVERSE_LICENSES",
                                              "cc0,pdm,by,by-sa").strip(),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", "").strip(),
        )


settings = Settings.load()

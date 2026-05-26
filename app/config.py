from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    uploads_dir: Path
    screenshots_dir: Path
    profile_dir: Path
    logs_dir: Path
    homelessbot_media_root: Path | None
    admin_username: str
    admin_password: str
    api_key_pepper: str
    callback_shared_secret: str
    public_base_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    timezone: str
    root_path: str
    scheduler_enabled: bool
    worker_enabled: bool
    instagram_dry_run: bool
    playwright_headless: bool
    instagram_username: str
    max_media_bytes: int
    upload_timeout_seconds: int
    stuck_processing_minutes: int


def _path(root: Path, name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else root / path


def get_settings() -> Settings:
    root = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()
    media_root_raw = os.getenv("HOMELESSBOT_MEDIA_ROOT", "").strip()
    media_root = Path(media_root_raw).expanduser().resolve() if media_root_raw else None
    return Settings(
        project_root=root,
        database_path=_path(root, "DATABASE_PATH", "db/scheduler.sqlite3"),
        uploads_dir=_path(root, "UPLOADS_DIR", "uploads"),
        screenshots_dir=_path(root, "SCREENSHOTS_DIR", "screenshots"),
        profile_dir=_path(root, "PROFILE_DIR", "profile"),
        logs_dir=_path(root, "LOGS_DIR", "logs"),
        homelessbot_media_root=media_root,
        admin_username=os.getenv("ADMIN_USERNAME", ""),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        api_key_pepper=os.getenv("API_KEY_PEPPER", ""),
        callback_shared_secret=os.getenv("CALLBACK_SHARED_SECRET", ""),
        public_base_url=os.getenv("PUBLIC_BASE_URL", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        timezone=os.getenv("APP_TIMEZONE", "Asia/Jakarta"),
        root_path=os.getenv("ROOT_PATH", ""),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
        worker_enabled=_bool("WORKER_ENABLED", True),
        instagram_dry_run=_bool("IG_DRY_RUN", False),
        playwright_headless=_bool("PLAYWRIGHT_HEADLESS", True),
        instagram_username=os.getenv("IG_USERNAME", "menitambahan").strip() or "menitambahan",
        max_media_bytes=_int("MAX_MEDIA_BYTES", 15 * 1024 * 1024),
        upload_timeout_seconds=_int("UPLOAD_TIMEOUT_SECONDS", 120),
        stuck_processing_minutes=_int("STUCK_PROCESSING_MINUTES", 30),
    )


settings = get_settings()


def ensure_runtime_dirs() -> None:
    for path in [
        settings.database_path.parent,
        settings.uploads_dir,
        settings.screenshots_dir,
        settings.profile_dir,
        settings.logs_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)

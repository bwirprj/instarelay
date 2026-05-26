from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import settings


APP_TZ = ZoneInfo(settings.timezone)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_utc_iso() -> str:
    return to_utc_iso(now_utc())


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_wib_schedule(post_date: str, post_time: str) -> tuple[datetime, str, str]:
    local = datetime.strptime(f"{post_date} {post_time}", "%Y-%m-%d %H:%M").replace(tzinfo=APP_TZ)
    return local.astimezone(UTC), post_date, post_time


def utc_iso_to_wib(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M")

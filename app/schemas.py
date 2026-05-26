from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SchedulePostRequest(BaseModel):
    media_paths: list[str] = Field(min_length=1, max_length=10)
    caption: str = ""
    hashtags: list[str] | str = Field(default_factory=list)
    headline: str = ""
    post_date: str
    post_time: str
    source: str = "homelessbot"
    external_id: str | None = None
    idempotency_key: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    callback_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelPostRequest(BaseModel):
    source: str = "homelessbot"
    external_id: str
    reason: str = "Canceled by upstream"


class RetryPostRequest(BaseModel):
    reason: str = "Manual retry"

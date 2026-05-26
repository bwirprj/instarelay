from __future__ import annotations

from datetime import timedelta

import httpx

from app.config import settings
from app.db import add_event, set_callback_state
from app.timezone import now_utc, to_utc_iso, utc_iso_to_wib


CALLBACK_BACKOFF_MINUTES = [1, 5, 15, 60, 360]


def event_for_status(status: str) -> str:
    return {
        "published": "post.published",
        "failed": "post.failed",
        "retry_scheduled": "post.retry_scheduled",
        "canceled": "post.canceled",
    }.get(status, f"post.{status}")


def callback_payload(post: dict) -> dict:
    return {
        "event": event_for_status(post["status"]),
        "status": post["status"],
        "post_id": post["id"],
        "source": post.get("source"),
        "external_id": post.get("external_id"),
        "headline": post.get("headline"),
        "scheduled_at_utc": post.get("scheduled_at_utc"),
        "scheduled_at_wib": f"{post.get('scheduled_date_wib')} {post.get('scheduled_time_wib')}",
        "published_at_utc": post.get("published_at_utc"),
        "published_at_wib": utc_iso_to_wib(post.get("published_at_utc")),
        "next_retry_at_utc": post.get("next_retry_at_utc"),
        "next_retry_at_wib": utc_iso_to_wib(post.get("next_retry_at_utc")),
        "retry_count": post.get("retry_count", 0),
        "error_message": post.get("error_message"),
        "screenshot_path": post.get("screenshot_path"),
        "media_deleted": bool(post.get("media_deleted_at_utc")),
        "instagram_result": post.get("instagram_result") or {},
    }


def send_callback(post: dict) -> None:
    callback_url = post.get("callback_url")
    if not callback_url:
        return

    attempts = int(post.get("callback_attempts") or 0) + 1
    headers = {"content-type": "application/json"}
    if settings.callback_shared_secret:
        headers["X-Instagram-Scheduler-Secret"] = settings.callback_shared_secret
    payload = callback_payload(post)

    try:
        response = httpx.post(callback_url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as error:
        if attempts >= len(CALLBACK_BACKOFF_MINUTES):
            set_callback_state(post["id"], "failed", attempts, str(error), None)
        else:
            next_retry = to_utc_iso(now_utc() + timedelta(minutes=CALLBACK_BACKOFF_MINUTES[attempts - 1]))
            set_callback_state(post["id"], "retry_scheduled", attempts, str(error), next_retry)
        add_event(post["id"], "warning", "callback_failed", "Callback delivery failed", {"error": str(error)})
        return

    set_callback_state(post["id"], "sent", attempts, None, None)
    add_event(post["id"], "info", "callback_sent", "Callback delivered", {"url": callback_url})

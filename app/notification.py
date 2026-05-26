from __future__ import annotations

import httpx

from app.config import settings
from app.timezone import utc_iso_to_wib


def send_telegram(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        return


def notify_status(post: dict, status: str, error: str | None = None) -> None:
    headline = post.get("headline") or post.get("external_id") or post["id"]
    if status == "published":
        text = "\n".join(
            [
                "Instagram post published",
                f"Headline: {headline}",
                f"Scheduled: {post.get('scheduled_date_wib')} {post.get('scheduled_time_wib')} WIB",
                f"Published: {utc_iso_to_wib(post.get('published_at_utc'))} WIB",
                f"Source: {post.get('source')}",
            ]
        )
    elif status == "retry_scheduled":
        text = "\n".join(
            [
                "Instagram post retry scheduled",
                f"Headline: {headline}",
                f"Retry: {post.get('retry_count')}/{post.get('max_retries')}",
                f"Reason: {error or post.get('error_message')}",
            ]
        )
    else:
        text = "\n".join(
            [
                f"Instagram post {status}",
                f"Headline: {headline}",
                f"Reason: {error or post.get('error_message') or '-'}",
                f"Source: {post.get('source')}",
            ]
        )
    send_telegram(text)

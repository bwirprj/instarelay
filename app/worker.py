from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from app.automation import RecoverableAutomationError, TerminalAutomationError, publish_to_instagram
from app.callbacks import send_callback
from app.config import settings
from app.db import add_event, delete_media_folder, get_post, update_post_status
from app.notification import notify_status
from app.timezone import now_utc, now_utc_iso, to_utc_iso


RETRY_DELAYS = [5, 15, 30]


def _screenshot_path(post_id: str) -> str:
    folder = settings.screenshots_dir / post_id
    folder.mkdir(parents=True, exist_ok=True)
    return str((folder / f"error-{now_utc_iso().replace(':', '-')}.png").resolve())


def _capture_static_error(post: dict, path: str) -> None:
    Path(path).write_text(
        f"Screenshot unavailable. Post {post['id']} failed before/after browser screenshot capture.\n",
        encoding="utf-8",
    )


def process_post(post: dict) -> dict:
    post_id = post["id"]
    try:
        result = publish_to_instagram(post)
        published = update_post_status(
            post_id,
            "published",
            published_at_utc=now_utc_iso(),
            error_message=None,
            next_retry_at_utc=None,
            instagram_result_json=result.result,
        )
        add_event(post_id, "success", "publish_succeeded", "Instagram publish completed", result.result)
        if published:
            delete_media_folder(published)
            published = get_post(post_id) or published
            notify_status(published, "published")
            send_callback(published)
        return published or post
    except TerminalAutomationError as error:
        screenshot = _screenshot_path(post_id)
        _capture_static_error(post, screenshot)
        failed = update_post_status(
            post_id,
            "failed",
            error_message=str(error),
            screenshot_path=screenshot,
            next_retry_at_utc=None,
        )
        add_event(post_id, "error", "publish_failed", "Terminal publish failure", {"error": str(error)})
        if failed:
            notify_status(failed, "failed", str(error))
            send_callback(failed)
        return failed or post
    except RecoverableAutomationError as error:
        retry_count = int(post.get("retry_count") or 0) + 1
        screenshot = _screenshot_path(post_id)
        _capture_static_error(post, screenshot)
        if retry_count <= int(post.get("max_retries") or 3):
            delay = RETRY_DELAYS[min(retry_count - 1, len(RETRY_DELAYS) - 1)]
            next_retry = to_utc_iso(now_utc() + timedelta(minutes=delay))
            retrying = update_post_status(
                post_id,
                "retry_scheduled",
                retry_count=retry_count,
                next_retry_at_utc=next_retry,
                error_message=str(error),
                screenshot_path=screenshot,
            )
            add_event(
                post_id,
                "warning",
                "retry_scheduled",
                "Recoverable publish failure; retry scheduled",
                {"error": str(error), "retry_count": retry_count, "next_retry_at_utc": next_retry},
            )
            if retrying:
                notify_status(retrying, "retry_scheduled", str(error))
                send_callback(retrying)
            return retrying or post

        failed = update_post_status(
            post_id,
            "failed",
            retry_count=retry_count,
            error_message=str(error),
            screenshot_path=screenshot,
            next_retry_at_utc=None,
        )
        add_event(post_id, "error", "publish_failed", "Publish failed after max retries", {"error": str(error)})
        if failed:
            notify_status(failed, "failed", str(error))
            send_callback(failed)
        return failed or post

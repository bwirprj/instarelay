from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.callbacks import send_callback
from app.config import ensure_runtime_dirs, settings
from app.db import (
    add_event,
    delete_media_folder,
    find_post,
    find_post_by_idempotency,
    get_post,
    insert_post,
    list_events,
    list_posts,
    list_posts_by_statuses,
    list_recent_events,
    migrate,
    new_id,
    set_publish_now,
    stats,
    update_post_status,
)
from app.media import ingest_media
from app.scheduler import scheduler_tick, start_scheduler, stop_scheduler
from app.schemas import CancelPostRequest, RetryPostRequest, SchedulePostRequest
from app.security import create_api_key, list_api_keys, require_admin, require_api_key, revoke_api_key
from app.timezone import now_utc_iso, parse_wib_schedule


templates = Jinja2Templates(directory=str(settings.project_root / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_runtime_dirs()
    migrate()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="InstaRelay", root_path=settings.root_path, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(settings.project_root / "static")), name="static")


def normalize_hashtags(value: list[str] | str) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def post_response(post: dict, duplicate: bool = False) -> dict:
    return {
        "post_id": post["id"],
        "external_id": post.get("external_id"),
        "status": post["status"],
        "duplicate": duplicate,
        "scheduled_at_wib": f"{post.get('scheduled_date_wib')} {post.get('scheduled_time_wib')}",
        "scheduled_at_utc": post.get("scheduled_at_utc"),
        "media_count": len(post.get("media_files") or []),
    }


def cancel_post(post: dict, reason: str) -> dict:
    if post["status"] == "processing":
        raise HTTPException(status_code=409, detail="Post is already processing")
    if post["status"] == "published":
        raise HTTPException(status_code=409, detail="Published posts cannot be canceled by scheduler")
    if post["status"] == "canceled":
        return post
    canceled = update_post_status(
        post["id"],
        "canceled",
        canceled_at_utc=now_utc_iso(),
        cancel_reason=reason,
        next_retry_at_utc=None,
    )
    if not canceled:
        raise HTTPException(status_code=404, detail="Post not found")
    media_deleted = delete_media_folder(canceled)
    canceled = get_post(post["id"]) or canceled
    add_event(
        post["id"],
        "warning",
        "cancel_requested",
        "Post canceled",
        {"reason": reason, "media_deleted": media_deleted},
    )
    send_callback(canceled)
    return canceled


@app.get("/health")
def health() -> dict:
    return {"ok": True, "scheduler_enabled": settings.scheduler_enabled, "worker_enabled": settings.worker_enabled}


@app.post("/api/v1/posts/schedule")
def schedule_post(
    payload: SchedulePostRequest,
    api_key: Annotated[dict, Depends(require_api_key)],
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    idempotency_key = payload.idempotency_key or idempotency_header
    if idempotency_key:
        existing = find_post_by_idempotency(api_key["id"], idempotency_key)
        if existing:
            return post_response(existing, duplicate=True)
    if payload.external_id:
        existing = find_post(payload.source, payload.external_id)
        if existing:
            return post_response(existing, duplicate=True)

    try:
        scheduled_utc, date_wib, time_wib = parse_wib_schedule(payload.post_date, payload.post_time)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="post_date/post_time must use YYYY-MM-DD and HH:mm") from error

    post_id = new_id("post")
    media_files: list[str] = []
    try:
        media_files = ingest_media(post_id, payload.media_paths)
        post = insert_post(
            {
                "id": post_id,
                "api_key_id": api_key["id"],
                "source": payload.source,
                "external_id": payload.external_id,
                "idempotency_key": idempotency_key,
                "headline": payload.headline,
                "media_source_type": "shared_path",
                "media_refs": payload.media_paths,
                "media_urls": payload.image_urls,
                "media_files": media_files,
                "caption": payload.caption,
                "hashtags": normalize_hashtags(payload.hashtags),
                "metadata": payload.metadata,
                "scheduled_date_wib": date_wib,
                "scheduled_time_wib": time_wib,
                "scheduled_at_utc": scheduled_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "callback_url": payload.callback_url,
            }
        )
        add_event(post_id, "info", "media_ingested", "Media ingested into scheduler uploads", {"count": len(media_files)})
        return post_response(post, duplicate=bool(post.get("duplicate")))
    except Exception:
        if media_files:
            shutil.rmtree(Path(media_files[0]).parent, ignore_errors=True)
        raise


@app.get("/api/v1/posts")
def api_list_posts(
    _: Annotated[dict, Depends(require_api_key)],
    status: str | None = None,
    source: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    return list_posts(status=status, source=source, page=page, page_size=page_size)


@app.get("/api/v1/posts/{post_id}")
def api_get_post(post_id: str, _: Annotated[dict, Depends(require_api_key)]) -> dict:
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    post["events"] = list_events(post_id)
    return post


@app.post("/api/v1/posts/{post_id}/retry")
def api_retry_post(post_id: str, _: Annotated[dict, Depends(require_api_key)], payload: RetryPostRequest | None = None) -> dict:
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post["status"] not in {"failed", "retry_scheduled"}:
        raise HTTPException(status_code=409, detail="Only failed/retry_scheduled posts can be retried")
    updated = update_post_status(post_id, "pending", next_retry_at_utc=None, error_message=None)
    add_event(post_id, "warning", "manual_retry", "Manual retry requested", {"reason": (payload.reason if payload else "")})
    return post_response(updated or post)


@app.post("/api/v1/posts/{post_id}/publish-now")
def api_publish_now(post_id: str, _: Annotated[dict, Depends(require_api_key)]) -> dict:
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post["status"] not in {"pending", "retry_scheduled"}:
        raise HTTPException(status_code=409, detail="Only pending/retry_scheduled posts can publish now")
    updated = set_publish_now(post_id)
    add_event(post_id, "warning", "publish_now", "Publish-now requested")
    return post_response(updated or post)


@app.delete("/api/v1/posts/{post_id}")
def api_cancel_post_by_id(post_id: str, _: Annotated[dict, Depends(require_api_key)]) -> dict:
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    canceled = cancel_post(post, "Canceled by API")
    return {"post_id": canceled["id"], "status": canceled["status"], "media_deleted": bool(canceled.get("media_deleted_at_utc"))}


@app.post("/api/v1/posts/cancel")
def api_cancel_post_by_external(payload: CancelPostRequest, _: Annotated[dict, Depends(require_api_key)]) -> dict:
    post = find_post(payload.source, payload.external_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    canceled = cancel_post(post, payload.reason)
    return {"post_id": canceled["id"], "status": canceled["status"], "media_deleted": bool(canceled.get("media_deleted_at_utc"))}


@app.post("/api/v1/admin/tick")
def api_run_tick(_: Annotated[dict, Depends(require_api_key)]) -> dict:
    scheduler_tick()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _: Annotated[str, Depends(require_admin)]):
    scheduled_posts = list_posts_by_statuses(["pending"], limit=20, order="scheduled_asc")
    sent_posts = list_posts_by_statuses(["published", "sent"], limit=20, order="published_desc")
    review_posts = list_posts_by_statuses(["failed", "canceled", "retry_scheduled"], limit=20, order="updated_desc")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats(),
            "scheduled_posts": scheduled_posts,
            "sent_posts": sent_posts,
            "review_posts": review_posts,
            "settings": settings,
        },
    )


@app.get("/posts", response_class=HTMLResponse)
def dashboard_posts(
    request: Request,
    _: Annotated[str, Depends(require_admin)],
    status: str | None = None,
    page: int = 1,
):
    data = list_posts(status=status, page=page, page_size=25)
    return templates.TemplateResponse("posts.html", {"request": request, "data": data, "status": status})


@app.get("/logs", response_class=HTMLResponse)
def dashboard_logs(request: Request, _: Annotated[str, Depends(require_admin)]):
    return templates.TemplateResponse("logs.html", {"request": request})


@app.get("/api/logs")
def dashboard_logs_data(_: Annotated[str, Depends(require_admin)], limit: int = 100) -> dict:
    return {"events": list_recent_events(limit=limit)}


@app.get("/posts/{post_id}", response_class=HTMLResponse)
def dashboard_post_detail(request: Request, post_id: str, _: Annotated[str, Depends(require_admin)]):
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return templates.TemplateResponse(
        "post_detail.html",
        {"request": request, "post": post, "events": list_events(post_id)},
    )


@app.post("/posts/{post_id}/cancel")
def dashboard_cancel(post_id: str, _: Annotated[str, Depends(require_admin)]):
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    cancel_post(post, "Canceled from scheduler dashboard")
    return RedirectResponse(url=f"../{post_id}", status_code=303)


@app.post("/posts/{post_id}/retry")
def dashboard_retry(post_id: str, _: Annotated[str, Depends(require_admin)]):
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    update_post_status(post_id, "pending", next_retry_at_utc=None, error_message=None)
    add_event(post_id, "warning", "manual_retry", "Manual retry requested from dashboard")
    return RedirectResponse(url=f"../{post_id}", status_code=303)


@app.post("/posts/{post_id}/publish-now")
def dashboard_publish_now(post_id: str, _: Annotated[str, Depends(require_admin)]):
    if not get_post(post_id):
        raise HTTPException(status_code=404, detail="Post not found")
    set_publish_now(post_id)
    add_event(post_id, "warning", "publish_now", "Publish-now requested from dashboard")
    return RedirectResponse(url=f"../{post_id}", status_code=303)


@app.post("/posts/{post_id}/resend-callback")
def dashboard_resend_callback(post_id: str, _: Annotated[str, Depends(require_admin)]):
    post = get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    send_callback(post)
    return RedirectResponse(url=f"../{post_id}", status_code=303)


@app.get("/api-keys", response_class=HTMLResponse)
def dashboard_api_keys(request: Request, _: Annotated[str, Depends(require_admin)], token: str | None = None):
    return templates.TemplateResponse("api_keys.html", {"request": request, "keys": list_api_keys(), "token": token})


@app.post("/api-keys", response_class=HTMLResponse)
def dashboard_create_key(
    request: Request,
    _: Annotated[str, Depends(require_admin)],
    name: Annotated[str, Form()] = "homelessbot",
):
    _, token = create_api_key(name)
    return templates.TemplateResponse("api_keys.html", {"request": request, "keys": list_api_keys(), "token": token})


@app.post("/api-keys/{key_id}/revoke")
def dashboard_revoke_key(key_id: str, _: Annotated[str, Depends(require_admin)]):
    revoke_api_key(key_id)
    return RedirectResponse(url="../../api-keys", status_code=303)


@app.get("/media/{post_id}/{filename}")
def dashboard_media(post_id: str, filename: str, _: Annotated[str, Depends(require_admin)]):
    path = (settings.uploads_dir / post_id / filename).resolve()
    root = settings.uploads_dir.resolve()
    if root != path and root not in path.parents:
        raise HTTPException(status_code=404, detail="Media not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path)

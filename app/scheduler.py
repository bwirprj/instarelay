from __future__ import annotations

import threading

from apscheduler.schedulers.background import BackgroundScheduler

from app.callbacks import send_callback
from app.config import settings
from app.db import add_event, claim_due_post, list_due_callbacks
from app.worker import process_post


worker_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None


def scheduler_tick() -> None:
    for post in list_due_callbacks(limit=5):
        send_callback(post)

    if not settings.worker_enabled:
        return
    if not worker_lock.acquire(blocking=False):
        return
    try:
        post = claim_due_post()
        if not post:
            return
        add_event(post["id"], "info", "worker_started", "Worker started processing post")
        process_post(post)
    finally:
        worker_lock.release()


def start_scheduler() -> BackgroundScheduler | None:
    global scheduler
    if not settings.scheduler_enabled:
        return None
    if scheduler and scheduler.running:
        return scheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        scheduler_tick,
        "interval",
        minutes=1,
        id="scheduler_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


def stop_scheduler() -> None:
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)

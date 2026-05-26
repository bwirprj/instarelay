from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from app.config import ensure_runtime_dirs, settings
from app.timezone import now_utc_iso


def json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def json_load(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def get_conn() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(settings.database_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def conn_ctx() -> Iterable[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def migrate() -> None:
    with conn_ctx() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_used_at TEXT
            );

            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                api_key_id TEXT,
                source TEXT NOT NULL DEFAULT 'external',
                external_id TEXT,
                idempotency_key TEXT,
                headline TEXT,
                media_source_type TEXT NOT NULL DEFAULT 'shared_path',
                media_refs_json TEXT NOT NULL,
                media_urls_json TEXT NOT NULL DEFAULT '[]',
                media_files_json TEXT NOT NULL,
                caption TEXT NOT NULL DEFAULT '',
                hashtags_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                scheduled_date_wib TEXT NOT NULL,
                scheduled_time_wib TEXT NOT NULL,
                scheduled_at_utc TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                next_retry_at_utc TEXT,
                locked_at TEXT,
                processing_started_at TEXT,
                last_attempt_at TEXT,
                callback_url TEXT,
                callback_status TEXT NOT NULL DEFAULT 'not_sent',
                callback_attempts INTEGER NOT NULL DEFAULT 0,
                callback_last_error TEXT,
                callback_next_retry_at_utc TEXT,
                canceled_at_utc TEXT,
                cancel_reason TEXT,
                media_deleted_at_utc TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at_utc TEXT,
                error_message TEXT,
                screenshot_path TEXT,
                instagram_result_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(api_key_id) REFERENCES api_keys(id)
            );

            CREATE TABLE IF NOT EXISTS post_events (
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL,
                level TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(post_id) REFERENCES posts(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_source_external
            ON posts(source, external_id)
            WHERE external_id IS NOT NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_idempotency
            ON posts(api_key_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_posts_due
            ON posts(status, scheduled_at_utc, next_retry_at_utc);

            CREATE INDEX IF NOT EXISTS idx_posts_callback_due
            ON posts(callback_status, callback_next_retry_at_utc);
            """
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key, fallback in {
        "media_refs_json": [],
        "media_urls_json": [],
        "media_files_json": [],
        "hashtags_json": [],
        "metadata_json": {},
        "instagram_result_json": {},
    }.items():
        if key in data:
            data[key.removesuffix("_json")] = json_load(data[key], fallback)
    return data


def add_event(post_id: str, level: str, event_type: str, message: str, meta: Any | None = None) -> None:
    with conn_ctx() as conn:
        conn.execute(
            """
            INSERT INTO post_events (id, post_id, level, event_type, message, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("evt"), post_id, level, event_type, message, json_dump(meta or {}), now_utc_iso()),
        )


def list_events(post_id: str) -> list[dict[str, Any]]:
    with conn_ctx() as conn:
        rows = conn.execute(
            "SELECT * FROM post_events WHERE post_id = ? ORDER BY created_at ASC",
            (post_id,),
        ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        item["meta"] = json_load(item.pop("meta_json"), {})
        events.append(item)
    return events


def list_recent_events(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = min(max(1, limit), 300)
    with conn_ctx() as conn:
        rows = conn.execute(
            """
            SELECT
                post_events.id,
                post_events.post_id,
                post_events.level,
                post_events.event_type,
                post_events.message,
                post_events.meta_json,
                post_events.created_at,
                posts.headline,
                posts.status,
                posts.source,
                posts.external_id
            FROM post_events
            LEFT JOIN posts ON posts.id = post_events.post_id
            ORDER BY post_events.created_at DESC, post_events.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    events = []
    for row in rows:
        item = dict(row)
        item["meta"] = json_load(item.pop("meta_json"), {})
        events.append(item)
    return events


def insert_post(data: dict[str, Any]) -> dict[str, Any]:
    with conn_ctx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = None
            if data.get("idempotency_key") and data.get("api_key_id"):
                existing = conn.execute(
                    "SELECT * FROM posts WHERE api_key_id = ? AND idempotency_key = ?",
                    (data["api_key_id"], data["idempotency_key"]),
                ).fetchone()
            if not existing and data.get("external_id"):
                existing = conn.execute(
                    "SELECT * FROM posts WHERE source = ? AND external_id = ?",
                    (data["source"], data["external_id"]),
                ).fetchone()
            if existing:
                conn.execute("COMMIT")
                item = row_to_dict(existing)
                item["duplicate"] = True
                return item

            now = now_utc_iso()
            post_id = data["id"]
            conn.execute(
                """
                INSERT INTO posts (
                    id, api_key_id, source, external_id, idempotency_key, headline,
                    media_source_type, media_refs_json, media_urls_json, media_files_json,
                    caption, hashtags_json, metadata_json, scheduled_date_wib,
                    scheduled_time_wib, scheduled_at_utc, callback_url, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id,
                    data.get("api_key_id"),
                    data.get("source", "external"),
                    data.get("external_id"),
                    data.get("idempotency_key"),
                    data.get("headline", ""),
                    data.get("media_source_type", "shared_path"),
                    json_dump(data.get("media_refs", [])),
                    json_dump(data.get("media_urls", [])),
                    json_dump(data.get("media_files", [])),
                    data.get("caption", ""),
                    json_dump(data.get("hashtags", [])),
                    json_dump(data.get("metadata", {})),
                    data["scheduled_date_wib"],
                    data["scheduled_time_wib"],
                    data["scheduled_at_utc"],
                    data.get("callback_url"),
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    add_event(post_id, "info", "schedule_created", "Scheduled post created", {"source": data.get("source")})
    return get_post(post_id) or {}


def get_post(post_id: str) -> dict[str, Any] | None:
    with conn_ctx() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row_to_dict(row)


def find_post(source: str, external_id: str) -> dict[str, Any] | None:
    with conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM posts WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
    return row_to_dict(row)


def find_post_by_idempotency(api_key_id: str, idempotency_key: str) -> dict[str, Any] | None:
    with conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM posts WHERE api_key_id = ? AND idempotency_key = ?",
            (api_key_id, idempotency_key),
        ).fetchone()
    return row_to_dict(row)


def list_posts(status: str | None = None, source: str | None = None, page: int = 1, page_size: int = 25) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn_ctx() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS count FROM posts {where}", params).fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT * FROM posts
            {where}
            ORDER BY scheduled_at_utc DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {"items": [row_to_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


def list_posts_by_statuses(statuses: list[str], limit: int = 12, order: str = "updated_desc") -> list[dict[str, Any]]:
    if not statuses:
        return []
    safe_limit = min(max(1, limit), 50)
    placeholders = ", ".join("?" for _ in statuses)
    order_sql = {
        "scheduled_asc": "scheduled_at_utc ASC, created_at ASC",
        "published_desc": "COALESCE(published_at_utc, updated_at) DESC, scheduled_at_utc DESC",
        "updated_desc": "updated_at DESC, scheduled_at_utc DESC",
    }.get(order, "updated_at DESC, scheduled_at_utc DESC")
    with conn_ctx() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM posts
            WHERE status IN ({placeholders})
            ORDER BY {order_sql}
            LIMIT ?
            """,
            [*statuses, safe_limit],
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def update_post_status(post_id: str, status: str, **patch: Any) -> dict[str, Any] | None:
    allowed = {
        "retry_count",
        "next_retry_at_utc",
        "locked_at",
        "processing_started_at",
        "last_attempt_at",
        "published_at_utc",
        "error_message",
        "screenshot_path",
        "instagram_result_json",
        "canceled_at_utc",
        "cancel_reason",
        "media_deleted_at_utc",
    }
    fields = ["status = ?", "updated_at = ?"]
    values: list[Any] = [status, now_utc_iso()]
    for key, value in patch.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        values.append(json_dump(value) if key.endswith("_json") else value)
    values.append(post_id)
    with conn_ctx() as conn:
        conn.execute(f"UPDATE posts SET {', '.join(fields)} WHERE id = ?", values)
    return get_post(post_id)


def set_publish_now(post_id: str) -> dict[str, Any] | None:
    now = now_utc_iso()
    with conn_ctx() as conn:
        conn.execute(
            """
            UPDATE posts
            SET status = 'pending', scheduled_at_utc = ?, next_retry_at_utc = NULL,
                error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, post_id),
        )
    return get_post(post_id)


def set_callback_state(post_id: str, status: str, attempts: int, error: str | None, next_retry: str | None) -> None:
    with conn_ctx() as conn:
        conn.execute(
            """
            UPDATE posts
            SET callback_status = ?, callback_attempts = ?, callback_last_error = ?,
                callback_next_retry_at_utc = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, attempts, error, next_retry, now_utc_iso(), post_id),
        )


def claim_due_post() -> dict[str, Any] | None:
    now = now_utc_iso()
    with conn_ctx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT * FROM posts
                WHERE (
                    status = 'pending' AND scheduled_at_utc <= ?
                ) OR (
                    status = 'retry_scheduled' AND next_retry_at_utc IS NOT NULL AND next_retry_at_utc <= ?
                )
                ORDER BY scheduled_at_utc ASC, created_at ASC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE posts
                SET status = 'processing', locked_at = ?, processing_started_at = ?,
                    last_attempt_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, now, now, row["id"]),
            )
            conn.execute("COMMIT")
            post_id = row["id"]
        except Exception:
            conn.execute("ROLLBACK")
            raise
    add_event(post_id, "info", "job_claimed", "Post claimed by worker")
    return get_post(post_id)


def list_due_callbacks(limit: int = 10) -> list[dict[str, Any]]:
    now = now_utc_iso()
    with conn_ctx() as conn:
        rows = conn.execute(
            """
            SELECT * FROM posts
            WHERE callback_url IS NOT NULL
              AND callback_status = 'retry_scheduled'
              AND callback_next_retry_at_utc <= ?
            ORDER BY callback_next_retry_at_utc ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def stats() -> dict[str, int]:
    with conn_ctx() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM posts GROUP BY status").fetchall()
    result = {row["status"]: row["count"] for row in rows}
    for status in ["pending", "processing", "published", "retry_scheduled", "failed", "canceled"]:
        result.setdefault(status, 0)
    return result


def delete_media_folder(post: dict[str, Any]) -> bool:
    media_files = post.get("media_files") or []
    folders = {Path(path).parent for path in media_files if path}
    deleted = False
    for folder in folders:
        try:
            if folder.exists() and folder.is_dir() and settings.uploads_dir.resolve() in folder.resolve().parents:
                for child in folder.iterdir():
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                folder.rmdir()
                deleted = True
        except OSError:
            pass
    if deleted:
        update_post_status(post["id"], post["status"], media_deleted_at_utc=now_utc_iso())
        add_event(post["id"], "info", "media_deleted", "Scheduler-owned media deleted")
    return deleted

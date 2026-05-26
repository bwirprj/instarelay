from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from app.config import settings
from app.db import conn_ctx, new_id
from app.timezone import now_utc_iso


basic_scheme = HTTPBasic(auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def _hash_api_key(token: str) -> str:
    material = f"{settings.api_key_pepper}:{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def create_api_key(name: str) -> tuple[dict[str, str], str]:
    token = f"igs_live_{secrets.token_urlsafe(32)}"
    key_id = new_id("key")
    now = now_utc_iso()
    record = {
        "id": key_id,
        "name": name.strip() or "API Key",
        "key_prefix": token[:18],
        "key_hash": _hash_api_key(token),
        "status": "active",
        "created_at": now,
    }
    with conn_ctx() as conn:
        conn.execute(
            """
            INSERT INTO api_keys (id, name, key_prefix, key_hash, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (record["id"], record["name"], record["key_prefix"], record["key_hash"], now),
        )
    return record, token


def list_api_keys() -> list[dict[str, str]]:
    with conn_ctx() as conn:
        rows = conn.execute(
            """
            SELECT id, name, key_prefix, status, created_at, revoked_at, last_used_at
            FROM api_keys
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_api_key(key_id: str) -> None:
    with conn_ctx() as conn:
        conn.execute(
            "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE id = ?",
            (now_utc_iso(), key_id),
        )


def require_admin(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_scheme)]) -> str:
    if not settings.admin_username or not settings.admin_password:
        raise HTTPException(status_code=503, detail="Dashboard auth is not configured")
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Instagram Scheduler"'},
        )
    username_ok = hmac.compare_digest(credentials.username, settings.admin_username)
    password_ok = hmac.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Instagram Scheduler"'},
        )
    return credentials.username


def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, str]:
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing bearer API key")
    digest = _hash_api_key(credentials.credentials)
    with conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND status = 'active'",
            (digest,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now_utc_iso(), row["id"]))
    request.state.api_key = dict(row)
    return dict(row)

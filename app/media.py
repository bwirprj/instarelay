from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from app.config import settings

ALLOWED_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/webp": (b"RIFF", ".webp"),
}


def _safe_relative_path(raw: str) -> Path:
    if not raw or raw.startswith("/"):
        raise HTTPException(status_code=400, detail=f"Invalid media path: {raw}")
    pure = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise HTTPException(status_code=400, detail=f"Unsafe media path: {raw}")
    return Path(*pure.parts)


def _detect_image(path: Path) -> tuple[str, str]:
    with path.open("rb") as handle:
        header = handle.read(16)
    if header.startswith(ALLOWED_SIGNATURES["image/jpeg"][0]):
        return ALLOWED_SIGNATURES["image/jpeg"]
    if header.startswith(ALLOWED_SIGNATURES["image/png"][0]):
        return ALLOWED_SIGNATURES["image/png"]
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ALLOWED_SIGNATURES["image/webp"]
    raise HTTPException(status_code=400, detail=f"Unsupported image file: {path.name}")


def resolve_media_paths(media_paths: list[str]) -> list[Path]:
    if not settings.homelessbot_media_root:
        raise HTTPException(status_code=500, detail="HOMELESSBOT_MEDIA_ROOT is not configured")
    root = settings.homelessbot_media_root.resolve()
    if not root.exists():
        raise HTTPException(status_code=500, detail=f"HOMELESSBOT_MEDIA_ROOT does not exist: {root}")

    resolved: list[Path] = []
    for raw in media_paths:
        rel = _safe_relative_path(raw)
        candidate = (root / rel).resolve()
        if root != candidate and root not in candidate.parents:
            raise HTTPException(status_code=400, detail=f"Media path escapes root: {raw}")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=400, detail=f"Media file not found: {raw}")
        if candidate.stat().st_size > settings.max_media_bytes:
            raise HTTPException(status_code=400, detail=f"Media file too large: {raw}")
        _detect_image(candidate)
        resolved.append(candidate)
    return resolved


def ingest_media(post_id: str, media_paths: list[str]) -> list[str]:
    source_paths = resolve_media_paths(media_paths)
    target_dir = settings.uploads_dir / post_id
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    try:
        for index, source in enumerate(source_paths, start=1):
            _, ext = _detect_image(source)
            target = target_dir / f"slide-{index}{ext}"
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            copied.append(str(target.resolve()))
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return copied

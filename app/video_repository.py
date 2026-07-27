"""Video repository: CRUD operations for the videos table."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def insert_video(
    conn: sqlite3.Connection,
    *,
    filename: str,
    original_name: str,
    file_path: str,
    file_size_bytes: int = 0,
    resolution: str = "1920x1080",
    batch_id: str | None = None,
    source_audio: str | None = None,
    background_path: str | None = None,
    title: str = "",
    description: str = "",
    tags: str = "",
    privacy: str = "private",
) -> dict[str, Any]:
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO videos
           (filename, original_name, title, description, tags, privacy,
            file_path, file_size_bytes, resolution, batch_id, source_audio,
            background_path, upload_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local_only', ?, ?)""",
        (filename, original_name, title, description, tags, privacy,
         file_path, file_size_bytes, resolution, batch_id, source_audio,
         background_path, now, now),
    )
    conn.commit()
    return get_video(conn, cur.lastrowid)


def upsert_patch_video(
    conn: sqlite3.Connection, *, book_id: int, patch_id: int,
    file_path: str, resolution: str, file_size_bytes: int | None = None,
) -> dict[str, Any]:
    path = Path(file_path)
    size = path.stat().st_size if file_size_bytes is None else file_size_bytes
    now = _now_iso()
    row = conn.execute(
        """INSERT INTO videos
           (filename,original_name,file_path,file_size_bytes,resolution,source_audio,
            background_path,upload_status,book_id,patch_id,created_at,updated_at)
           SELECT ?,?,?,?, ?,p.audio_path,NULL,'local_only',?,?,?,?
           FROM patch p WHERE p.id=?
           ON CONFLICT(patch_id) WHERE patch_id IS NOT NULL DO UPDATE SET
             filename=excluded.filename,original_name=excluded.original_name,
             file_path=excluded.file_path,file_size_bytes=excluded.file_size_bytes,
             resolution=excluded.resolution,book_id=excluded.book_id,
             source_audio=excluded.source_audio,updated_at=excluded.updated_at
           RETURNING *""",
        (path.name, path.name, str(path), size, resolution,
         book_id, patch_id, now, now, patch_id),
    ).fetchone()
    conn.commit()
    if row is None:
        raise ValueError(f"patch {patch_id} not found")
    return dict(row)


def get_video(conn: sqlite3.Connection, video_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def list_videos(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    upload_status: str = "",
    batch_id: str = "",
    sort: str = "created_at",
    order: str = "desc",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    where_clauses = []
    params: list[Any] = []

    if search:
        where_clauses.append(
            "(filename LIKE ? OR title LIKE ? OR description LIKE ? OR tags LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])

    if upload_status:
        where_clauses.append("upload_status = ?")
        params.append(upload_status)

    if batch_id:
        where_clauses.append("batch_id = ?")
        params.append(batch_id)

    if date_from:
        where_clauses.append("created_at >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("created_at <= ?")
        params.append(date_to + "T23:59:59")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    valid_sorts = {"created_at", "filename", "file_size_bytes", "upload_status"}
    sort_col = sort if sort in valid_sorts else "created_at"
    order_dir = "ASC" if order.lower() == "asc" else "DESC"

    count = conn.execute(f"SELECT COUNT(*) FROM videos {where}", params).fetchone()[0]
    per_page = max(1, min(100, per_page))
    total_pages = max(1, (count + per_page - 1) // per_page)
    page = max(1, min(total_pages, page))
    offset = (page - 1) * per_page

    rows = conn.execute(
        f"SELECT * FROM videos {where} ORDER BY {sort_col} {order_dir} LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    return {
        "videos": [dict(r) for r in rows],
        "total": count,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def update_video(
    conn: sqlite3.Connection,
    video_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"title", "description", "tags", "privacy", "upload_status",
               "youtube_video_id", "youtube_upload_id", "error_message", "duration_sec"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_video(conn, video_id)
    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [video_id]
    conn.execute(f"UPDATE videos SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return get_video(conn, video_id)


def delete_video(conn: sqlite3.Connection, video_id: int) -> bool:
    row = conn.execute("SELECT file_path FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not row:
        return False
    file_path = Path(row["file_path"])
    if file_path.exists():
        file_path.unlink()
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    return True


def bulk_delete_videos(conn: sqlite3.Connection, video_ids: list[int]) -> int:
    if not video_ids:
        return 0
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(
        f"SELECT file_path FROM videos WHERE id IN ({placeholders})", video_ids
    ).fetchall()
    for row in rows:
        p = Path(row["file_path"])
        if p.exists():
            p.unlink()
    conn.execute(f"DELETE FROM videos WHERE id IN ({placeholders})", video_ids)
    conn.commit()
    return len(rows)


def bulk_update_upload_status(
    conn: sqlite3.Connection,
    video_ids: list[int],
    upload_status: str,
) -> int:
    if not video_ids:
        return 0
    placeholders = ",".join("?" * len(video_ids))
    now = _now_iso()
    conn.execute(
        f"UPDATE videos SET upload_status = ?, updated_at = ? WHERE id IN ({placeholders})",
        [upload_status, now] + video_ids,
    )
    conn.commit()
    return len(video_ids)

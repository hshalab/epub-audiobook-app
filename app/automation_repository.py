from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.automation_config import AutomationConfig, merge_automation_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_system_config(conn: sqlite3.Connection) -> AutomationConfig:
    row = conn.execute("SELECT config_json FROM automation_settings WHERE id = 1").fetchone()
    return merge_automation_config(json.loads(row["config_json"]) if row else {})


def save_system_config(conn: sqlite3.Connection, config: dict) -> AutomationConfig:
    resolved = merge_automation_config(config)
    now = _now()
    conn.execute(
        "INSERT INTO automation_settings (id,schema_version,config_json,created_at,updated_at) VALUES (1,1,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json, schema_version=excluded.schema_version, updated_at=excluded.updated_at",
        (_json(config), now, now),
    )
    conn.commit()
    return resolved


def get_effective_config(conn: sqlite3.Connection, book_id: int) -> AutomationConfig:
    system = conn.execute("SELECT config_json FROM automation_settings WHERE id = 1").fetchone()
    book = conn.execute("SELECT automation_config FROM book WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise ValueError(f"book {book_id} not found")
    return merge_automation_config(
        json.loads(system["config_json"]) if system else {},
        json.loads(book["automation_config"]) if book["automation_config"] else None,
    )


def save_book_override(conn: sqlite3.Connection, book_id: int, override: dict | None) -> AutomationConfig:
    cursor = conn.execute("UPDATE book SET automation_config = ? WHERE id = ?", (_json(override) if override is not None else None, book_id))
    if cursor.rowcount != 1:
        raise ValueError(f"book {book_id} not found")
    conn.commit()
    return get_effective_config(conn, book_id)


def upsert_media_asset(conn: sqlite3.Connection, file_path: str, filename: str, media_type: str) -> dict:
    now = _now()
    conn.execute(
        "INSERT INTO media_assets (file_path,filename,media_type,created_at,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(file_path) DO UPDATE SET filename=excluded.filename, media_type=excluded.media_type, updated_at=excluded.updated_at",
        (file_path, filename, media_type, now, now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM media_assets WHERE file_path = ?", (file_path,)).fetchone())


def set_book_media(conn: sqlite3.Connection, book_id: int, role: str, asset_ids: list[int]) -> None:
    with conn:
        conn.execute("DELETE FROM book_media_selection WHERE book_id = ? AND role = ?", (book_id, role))
        conn.executemany(
            "INSERT INTO book_media_selection (book_id,role,media_asset_id,position) VALUES (?,?,?,?)",
            [(book_id, role, asset_id, position) for position, asset_id in enumerate(asset_ids)],
        )


def list_book_media(conn: sqlite3.Connection, book_id: int, role: str) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT a.* FROM book_media_selection s JOIN media_assets a ON a.id=s.media_asset_id "
        "WHERE s.book_id=? AND s.role=? ORDER BY s.position",
        (book_id, role),
    )]


def enqueue_patch_pipeline(conn: sqlite3.Connection, patch_id: int) -> dict:
    patch = conn.execute(
        """SELECT p.book_id,b.overlay_config,b.background_image_path
           FROM patch p JOIN book b ON b.id=p.book_id WHERE p.id=?""",
        (patch_id,),
    ).fetchone()
    if patch is None:
        raise ValueError(f"patch {patch_id} not found")
    config = get_effective_config(conn, patch["book_id"])
    descriptor = lambda row: {
        "id": row["id"], "file_path": row["file_path"],
        "media_type": row["media_type"],
    }
    media = {
        role: [descriptor(row) for row in list_book_media(conn, patch["book_id"], role)]
        for role in ("background", "webcam")
    }
    config_snapshot = {
        "automation": config.model_dump(),
        "overlay_config": patch["overlay_config"],
        "background_fallback": patch["background_image_path"],
    }
    now = _now()
    conn.execute(
        "INSERT INTO patch_pipeline (patch_id,config_snapshot,media_snapshot,created_at,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(patch_id) DO NOTHING",
        (patch_id, _json(config_snapshot), _json(media), now, now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM patch_pipeline WHERE patch_id = ?", (patch_id,)).fetchone())


def claim_next_pipeline_stage(conn: sqlite3.Connection, stage: str) -> dict | None:
    column = _stage_column(stage)
    now = _now()
    row = conn.execute(
        f"""UPDATE patch_pipeline
            SET {column}='processing', attempt_count=attempt_count+1, updated_at=?
            WHERE id=(
                SELECT id FROM patch_pipeline
                WHERE stage=? AND {column}='pending'
                  AND (next_retry_at IS NULL OR next_retry_at<=?)
                ORDER BY id LIMIT 1
            )
              AND stage=? AND {column}='pending'
            RETURNING *""",
        (now, stage, now, stage),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def claim_pipeline_stage(
    conn: sqlite3.Connection, pipeline_id: int, stage: str,
) -> dict | None:
    column = _stage_column(stage)
    row = conn.execute(
        f"""UPDATE patch_pipeline
            SET {column}='processing', attempt_count=attempt_count+1, updated_at=?
            WHERE id=? AND stage=?
              AND {column} IN ('pending','waiting_for_audio','waiting_for_media')
              AND (next_retry_at IS NULL OR next_retry_at<=?)
            RETURNING *""",
        (_now(), pipeline_id, stage, _now()),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def advance_pipeline_stage(
    conn: sqlite3.Connection,
    pipeline_id: int,
    completed_stage: str,
    next_stage: str,
) -> dict:
    stages = ("thumbnail", "video", "upload", "playlist")
    _stage_column(completed_stage)
    _stage_column(next_stage)
    if stages.index(next_stage) != stages.index(completed_stage) + 1:
        raise ValueError(f"invalid pipeline transition: {completed_stage} -> {next_stage}")
    column = _stage_column(completed_stage)
    row = conn.execute(
        f"""UPDATE patch_pipeline
            SET {column}='done', stage=?, last_error=NULL, next_retry_at=NULL, updated_at=?
            WHERE id=? AND stage=?
            RETURNING *""",
        (next_stage, _now(), pipeline_id, completed_stage),
    ).fetchone()
    conn.commit()
    if row is None:
        raise ValueError(
            f"pipeline {pipeline_id} is not at stage {completed_stage}"
        )
    return dict(row)


def update_pipeline_stage(conn: sqlite3.Connection, pipeline_id: int, stage: str, status: str, *, error: str | None = None, next_retry_at: str | None = None, output_path: str | None = None) -> dict:
    column = _stage_column(stage)
    path_column = {"thumbnail": "thumbnail_path", "video": "video_path"}.get(stage)
    assignments = [f"{column}=?", "last_error=?", "next_retry_at=?", "updated_at=?"]
    values: list[object] = [status, error, _utc_iso(next_retry_at), _now()]
    if path_column:
        assignments.append(f"{path_column}=?")
        values.append(output_path)
    values.append(pipeline_id)
    conn.execute(f"UPDATE patch_pipeline SET {', '.join(assignments)} WHERE id=?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline_id,)).fetchone()
    if row is None:
        raise ValueError(f"pipeline {pipeline_id} not found")
    return dict(row)


def _utc_iso(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("next_retry_at must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _stage_column(stage: str) -> str:
    if stage not in {"thumbnail", "video", "upload", "playlist"}:
        raise ValueError(f"unknown pipeline stage: {stage}")
    return f"{stage}_status"


_STAGES = ("thumbnail", "video", "upload", "playlist")


def get_patch_pipeline(conn: sqlite3.Connection, patch_id: int) -> dict | None:
    return _dict(conn.execute("SELECT * FROM patch_pipeline WHERE patch_id = ?", (patch_id,)).fetchone())


def retry_pipeline_stage(conn: sqlite3.Connection, pipeline_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id = ?", (pipeline_id,)).fetchone()
    if row is None:
        return None
    stage = row["stage"]
    _stage_column(stage)
    status = row[f"{stage}_status"]
    if status != "failed":
        return None
    values = [stage, _now(), pipeline_id]
    conn.execute(
        f"UPDATE patch_pipeline SET {stage}_status='pending', last_error=NULL, next_retry_at=NULL, updated_at=? WHERE id=?",
        (_now(), pipeline_id),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM patch_pipeline WHERE id = ?", (pipeline_id,)).fetchone())


def get_or_create_playlist_map(conn: sqlite3.Connection, book_id: int, channel_id: str, playlist_id: str, mode: str) -> dict:
    now = _now()
    conn.execute(
        "INSERT INTO youtube_playlist_map (book_id,channel_id,playlist_id,mode,created_at,updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(book_id,channel_id) DO NOTHING",
        (book_id, channel_id, playlist_id, mode, now, now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM youtube_playlist_map WHERE book_id=? AND channel_id=?", (book_id, channel_id)).fetchone())

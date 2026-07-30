"""Persist and schedule bounded recovery for invalid rendered videos."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.jobqueue import store
from app.video_integrity import RECOVERABLE_OUTPUT_CODES, ValidationResult


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    retry_count: int
    job_id: int | None
    message: str


def infer_render_source(conn: sqlite3.Connection, upload: dict) -> tuple[str, int | None]:
    explicit_type = upload.get("render_source_type") or "external"
    explicit_id = upload.get("render_source_id")
    if explicit_type != "external" and explicit_id is not None:
        return explicit_type, int(explicit_id)
    pipeline = conn.execute(
        "SELECT patch_id FROM patch_pipeline WHERE youtube_upload_id=?", (upload["id"],)
    ).fetchone()
    if pipeline:
        return "patch", pipeline["patch_id"]
    video_id = upload.get("video_id")
    if video_id:
        video = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if video and video["patch_id"] is not None:
            return "patch", video["patch_id"]
        if (video and video["source_audio"] and video["background_path"]
                and video["render_config_json"]):
            return "standalone", video["id"]
    return "external", None


def _terminal(conn: sqlite3.Connection, upload_id: int, count: int,
              code: str, message: str) -> RecoveryDecision:
    conn.execute(
        """UPDATE youtube_uploads SET status='failed', validation_status='failed',
           validation_error_code=?, validation_error_message=?, error_message=? WHERE id=?""",
        (code, message[-2000:], message[-2000:], upload_id),
    )
    conn.commit()
    return RecoveryDecision("failed", count, None, message)


def schedule_rerender(conn: sqlite3.Connection, upload_id: int,
                      result: ValidationResult) -> RecoveryDecision:
    upload_row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    if upload_row is None:
        return RecoveryDecision("failed", 0, None, f"upload {upload_id} not found")
    upload = dict(upload_row)
    count = int(upload.get("integrity_retry_count") or 0)
    code = result.error_code or "validation_failed"
    if code not in RECOVERABLE_OUTPUT_CODES:
        conn.execute(
            """UPDATE youtube_uploads SET validation_status='failed',
               validation_error_code=?, validation_error_message=? WHERE id=?""",
            (code, result.message[-2000:], upload_id),
        )
        conn.commit()
        return RecoveryDecision("retry_validation", count, None, result.message)
    source_type, source_id = infer_render_source(conn, upload)
    if source_type == "external" or source_id is None:
        return _terminal(conn, upload_id, count, code,
                         f"{result.message}; automatic re-render unavailable for external file")
    if count >= 2:
        return _terminal(conn, upload_id, count, code,
                         f"{result.message}; re-render limit 2/2 exhausted")

    next_count = count + 1
    conn.execute(
        """UPDATE youtube_uploads SET status='waiting_for_rerender',
           validation_status='waiting_for_rerender', validation_error_code=?,
           validation_error_message=?, integrity_retry_count=?, render_source_type=?,
           render_source_id=? WHERE id=?""",
        (code, result.message[-2000:], next_count, source_type, source_id, upload_id),
    )
    if upload.get("video_id"):
        conn.execute("UPDATE videos SET upload_status='rerendering', error_message=? WHERE id=?",
                     (result.message[-2000:], upload["video_id"]))
    if source_type == "patch":
        conn.execute(
            """UPDATE patch_pipeline SET stage='video', video_status='rerendering',
               upload_status='waiting_for_rerender', last_error=? WHERE patch_id=?""",
            (result.message[-2000:], source_id),
        )
    conn.commit()

    if source_type == "book":
        job_type, payload = "video", {"book_job_id": source_id, "recovery_upload_id": upload_id}
    elif source_type == "patch":
        job_type, payload = "patch_video", {"patch_id": source_id, "recovery_upload_id": upload_id}
    else:
        job_type, payload = "standalone_video", {"video_id": source_id, "recovery_upload_id": upload_id}
    job_id = store.enqueue(
        conn, job_type, payload=payload,
        dedupe_key=f"{job_type}:source={source_id}:integrity_retry={next_count}",
    )
    return RecoveryDecision("rerender", next_count, job_id, result.message)


def resume_upload_after_render(conn: sqlite3.Connection, upload_id: int) -> int | None:
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    if row is None or row["status"] != "waiting_for_rerender":
        return None
    count = int(row["integrity_retry_count"] or 0)
    conn.execute(
        """UPDATE youtube_uploads SET status='pending', validation_status='pending',
           validation_error_code=NULL, validation_error_message=NULL, error_message=NULL
           WHERE id=?""", (upload_id,),
    )
    conn.commit()
    return store.enqueue(
        conn, "youtube_upload", payload={"upload_id": upload_id},
        dedupe_key=f"youtube_upload:upload={upload_id}:integrity_retry={count}",
    )

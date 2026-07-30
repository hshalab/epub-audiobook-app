"""Resumable, idempotent publishing stages for one patch."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import image_overlay, repository, video_gen, youtube
from app.config import settings
from app.image_overlay import ensure_patch_overlay
from app.repository import build_patch_metadata_context, get_book, get_patch
from app.video_repository import upsert_patch_video
from app.video_integrity import validate_video
from app.video_publish import publish_validated_video
from app.youtube_metadata import (get_book_youtube_config, get_patch_youtube_override,
                                  resolve_patch_chapter_range, resolve_patch_youtube_metadata)
from app.video_config import get_book_video_config

STAGES = ("thumbnail", "video", "upload", "thumbnail_setting", "playlist", "published")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(conn, patch_id):
    row = conn.execute("SELECT * FROM patch_pipeline WHERE patch_id=?", (patch_id,)).fetchone()
    return dict(row) if row else None


def fetch_thumbnail_inputs(conn: sqlite3.Connection, patch_id: int):
    """Read what warm_patch_thumbnail needs. Cheap: two row lookups and a config read."""
    patch = get_patch(conn, patch_id)
    book = get_book(conn, patch.book_id) if patch else None
    if not patch or not book:
        return None
    if not get_book_youtube_config(conn, patch.book_id).get("auto_upload"):
        return None
    return book, patch


def warm_patch_thumbnail(inputs) -> None:
    """Render the patch thumbnail ahead of time, outside the shared db_lock.

    enqueue_patch_publish renders it too, but ensure_patch_overlay is cached: it only
    redraws when the file is missing or the background is newer. Doing it here first turns
    the call under the lock into a stat check instead of a full PIL render, which would
    otherwise stall every request once per finished patch.
    """
    if inputs is None:
        return
    book, patch = inputs
    try:
        ensure_patch_overlay(book, patch, settings.default_font_path or None)
    except Exception:  # noqa: BLE001 - purely an optimization; the real render still runs
        logging.getLogger(__name__).warning(
            "thumbnail pre-render failed for patch %s; falling back to rendering under the lock",
            patch.id, exc_info=True,
        )


def on_patch_audio_ready(conn: sqlite3.Connection, patch_id: int) -> dict | None:
    patch = get_patch(conn, patch_id)
    if not patch:
        return None
    if get_book_youtube_config(conn, patch.book_id).get("auto_upload"):
        return enqueue_patch_publish(conn, patch_id)
    return None


def _build_metadata_snapshot(conn: sqlite3.Connection, book, patch) -> dict:
    metadata = resolve_patch_youtube_metadata(
        book, patch, get_patch_youtube_override(conn, patch.id), build_patch_metadata_context(conn, book, patch))
    metadata["automation"] = {"youtube": metadata.pop("youtube")}
    chapter_start, chapter_end, patch_name = resolve_patch_chapter_range(patch)
    metadata["playlist_template_values"] = {
        "book_title": book.title,
        "episode_number": patch.patch_index + 1,
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "patch_name": patch_name,
        "genre_tags": ",".join(metadata.get("tags", [])),
    }
    return metadata


def enqueue_patch_publish(conn: sqlite3.Connection, patch_id: int, *, force_new: bool = False) -> dict:
    patch = get_patch(conn, patch_id)
    book = get_book(conn, patch.book_id) if patch else None
    if not patch or not book:
        raise ValueError(f"patch {patch_id} not found")
    existing = _row(conn, patch_id)
    if existing and not force_new:
        return existing
    metadata = _build_metadata_snapshot(conn, book, patch)
    video_config = get_book_video_config(conn, book)
    voices_dir = Path(settings.data_root) / "voices"
    intro = voices_dir / video_config["intro_voice"] if video_config.get("intro_voice") else None
    outro = voices_dir / video_config["outro_voice"] if video_config.get("outro_voice") else None
    music_path = None
    if book.music_id is not None:
        music = repository.get_music(conn, book.music_id)
        if music and Path(music.file_path).is_file():
            music_path = music.file_path
    render_config = {
        "resolution": video_config["resolution"],
        "fps": video_config["fps"],
        "image_type": video_config["image_animation"],
        "codec": video_config["codec"],
        "crf": video_config["quality"],
        "audio_bitrate": video_config["audio_bitrate"],
        "music_path": music_path,
        "music_volume": book.music_volume,
        "intro_audio": str(intro) if intro and intro.is_file() else None,
        "outro_audio": str(outro) if outro and outro.is_file() else None,
    }
    media = {"patch_id": patch_id, "audio_path": patch.audio_path,
             "source_image": patch.image_path,
             "thumbnail_path": existing["thumbnail_path"] if existing else None,
             "render_config": render_config}
    thumbnail = ensure_patch_overlay(book, patch, settings.default_font_path or None) or media["thumbnail_path"]
    thumbnail_ready = bool(thumbnail and Path(thumbnail).is_file())
    media["thumbnail_path"] = thumbnail
    now = _now()
    if existing:
        conn.execute("""UPDATE patch_pipeline SET stage='upload', thumbnail_status=?,
            upload_status='pending', playlist_status='pending', youtube_upload_id=NULL, thumbnail_path=?,
            config_snapshot=?, media_snapshot=?, last_error=NULL, updated_at=? WHERE patch_id=?""",
            ("done" if thumbnail_ready else "pending", thumbnail, json.dumps(metadata), json.dumps(media), now, patch_id))
    else:
        conn.execute("""INSERT INTO patch_pipeline
            (patch_id, stage, thumbnail_status, thumbnail_path, config_snapshot, media_snapshot, created_at, updated_at)
            VALUES (?, 'thumbnail', ?, ?, ?, ?, ?, ?)""",
            (patch_id, "done" if thumbnail_ready else "pending", thumbnail, json.dumps(metadata), json.dumps(media), now, now))
    conn.commit()
    return _row(conn, patch_id)


def seed_patch_video(conn: sqlite3.Connection, patch_id: int, video_id: int, video_path: str) -> dict:
    row = _row(conn, patch_id) or enqueue_patch_publish(conn, patch_id)
    conn.execute("UPDATE patch_pipeline SET stage='upload', video_status='done', video_id=?, video_path=?, updated_at=? WHERE patch_id=?", (video_id, video_path, _now(), patch_id))
    conn.commit()
    return _row(conn, patch_id)


def _fail(conn, patch_id, stage, exc):
    message = str(exc)[:2000]
    conn.execute("UPDATE patch_pipeline SET stage=?, last_error=?, attempt_count=attempt_count+1, updated_at=? WHERE patch_id=?", (stage, message, _now(), patch_id))
    conn.commit()
    return _row(conn, patch_id)


def sync_pipeline_from_upload(conn: sqlite3.Connection, upload_id: int) -> dict | None:
    upload = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    if not upload:
        return None
    status = upload["status"]
    stage = "auth_required" if (upload["error_message"] or "").startswith("auth_required:") else "published" if status == "done" and upload["thumbnail_status"] == "done" and upload["playlist_status"] == "done" else "thumbnail_setting" if status == "done" else "upload"
    conn.execute("""UPDATE patch_pipeline SET stage=?, upload_status=?, thumbnail_status=?, playlist_status=?,
        last_error=?, updated_at=? WHERE youtube_upload_id=?""", (stage, status, upload["thumbnail_status"], upload["playlist_status"], upload["error_message"], _now(), upload_id))
    conn.commit()
    row = conn.execute("SELECT * FROM patch_pipeline WHERE youtube_upload_id=?", (upload_id,)).fetchone()
    return dict(row) if row else None


def _create_upload_atomically(conn: sqlite3.Connection, row: dict) -> int:
    # Re-resolve instead of trusting the enqueue-time snapshot: config/override edits
    # (and metadata fixes) made between enqueue and upload must reach YouTube. The
    # stored snapshot stays as the fallback when the current config no longer resolves.
    snapshot = row["config_snapshot"]
    try:
        patch = get_patch(conn, row["patch_id"])
        book = get_book(conn, patch.book_id) if patch else None
        if not patch or not book:
            raise ValueError(f"patch {row['patch_id']} not found")
        metadata = _build_metadata_snapshot(conn, book, patch)
        snapshot = json.dumps(metadata)
    except Exception:  # noqa: BLE001 - a stale snapshot still beats a stuck pipeline
        logging.getLogger(__name__).warning(
            "metadata re-resolve failed for patch %s; uploading with the enqueue-time snapshot",
            row["patch_id"], exc_info=True,
        )
        metadata = json.loads(row["config_snapshot"])
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute("SELECT youtube_upload_id FROM patch_pipeline WHERE patch_id=?", (row["patch_id"],)).fetchone()
        if current[0]:
            conn.commit()
            return current[0]
        conn.execute("UPDATE patch_pipeline SET stage='upload', upload_status='claiming', config_snapshot=?, updated_at=? WHERE patch_id=?", (snapshot, _now(), row["patch_id"]))
        cur = conn.execute("""INSERT INTO youtube_uploads
            (video_id, video_path, title, description, tags, privacy_status, status,
             metadata_snapshot, render_source_type, render_source_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 'patch', ?, ?)""", (row["video_id"], row["video_path"], metadata["title"], metadata["description"], json.dumps(metadata["tags"]), metadata["privacy_status"], snapshot, row["patch_id"], _now()))
        conn.execute("UPDATE patch_pipeline SET upload_status='queued', youtube_upload_id=?, updated_at=? WHERE patch_id=?", (cur.lastrowid, _now(), row["patch_id"]))
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        conn.execute("UPDATE patch_pipeline SET upload_status='failed', last_error=?, updated_at=? WHERE patch_id=?", ("upload claim failed", _now(), row["patch_id"]))
        conn.commit()
        raise


def run_patch_publish_stage(conn: sqlite3.Connection, patch_id: int) -> dict:
    row = _row(conn, patch_id) or enqueue_patch_publish(conn, patch_id)
    current_stage = row.get("stage", "thumbnail")
    try:
        if row["thumbnail_status"] == "done" and (not row["thumbnail_path"] or not Path(row["thumbnail_path"]).is_file()):
            conn.execute("UPDATE patch_pipeline SET thumbnail_status='pending', stage='thumbnail', updated_at=? WHERE patch_id=?", (_now(), patch_id)); conn.commit()
            row["thumbnail_status"] = "pending"
        if row["thumbnail_status"] != "done":
            current_stage = "thumbnail"
            patch = get_patch(conn, patch_id); book = get_book(conn, patch.book_id)
            path = ensure_patch_overlay(book, patch, settings.default_font_path or None)
            if not path or not Path(path).is_file(): raise ValueError("patch thumbnail could not be created")
            conn.execute("UPDATE patch_pipeline SET stage='video', thumbnail_status='done', thumbnail_path=? WHERE patch_id=?", (path, patch_id)); conn.commit()
        row = _row(conn, patch_id)
        if row["video_status"] == "done" and (not row["video_path"] or not Path(row["video_path"]).is_file()):
            conn.execute("UPDATE patch_pipeline SET video_status='pending', stage='video', updated_at=? WHERE patch_id=?", (_now(), patch_id)); conn.commit()
            row["video_status"] = "pending"
        if row["video_status"] != "done":
            current_stage = "video"
            patch = get_patch(conn, patch_id); book = get_book(conn, patch.book_id)
            output = row["video_path"] or str(Path(patch.audio_path or "").with_suffix(".mp4"))
            publish_validated_video(
                output,
                lambda temp: video_gen.generate_segment(
                    row["thumbnail_path"], patch.audio_path, temp,
                    resolution=tuple(map(int, book.video_resolution.split("x"))),
                    fps=book.video_fps or 30,
                ),
                validator=validate_video,
            )
            video = upsert_patch_video(conn, book_id=book.id, patch_id=patch_id, file_path=output, resolution=book.video_resolution)
            conn.execute("UPDATE patch_pipeline SET stage='upload', video_status='done', video_path=?, video_id=? WHERE patch_id=?", (output, video["id"], patch_id)); conn.commit()
        row = _row(conn, patch_id)
        if row["youtube_upload_id"]:
            upload = dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (row["youtube_upload_id"],)).fetchone() or {})
            if upload.get("status") in {"pending", "uploading"}:
                return row
            if upload.get("status") == "failed":
                conn.execute("UPDATE youtube_uploads SET status='pending', error_message=NULL WHERE id=?", (row["youtube_upload_id"],)); conn.execute("UPDATE patch_pipeline SET upload_status='pending', stage='upload', updated_at=? WHERE patch_id=?", (_now(), patch_id)); conn.commit()
                return _row(conn, patch_id)
            if upload.get("status") == "done":
                current_stage = "thumbnail_setting" if upload.get("thumbnail_status") != "done" else "playlist"
                result = youtube.publish_completed_upload(conn, row["youtube_upload_id"])
                refreshed = dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (row["youtube_upload_id"],)).fetchone())
                stage = "auth_required" if result.get("status") == "auth_required" else "published" if result.get("status") == "published" else current_stage
                conn.execute("""UPDATE patch_pipeline SET stage=?, upload_status='done', thumbnail_status=?,
                    playlist_status=?, last_error=? WHERE patch_id=?""", (stage, refreshed["thumbnail_status"], refreshed["playlist_status"], None if stage == "published" else result.get("error"), patch_id)); conn.commit()
                return _row(conn, patch_id)
        if not row["youtube_upload_id"]:
            _create_upload_atomically(conn, row)
        return _row(conn, patch_id)
    except Exception as exc:
        return _fail(conn, patch_id, current_stage, exc)


def retry_patch_publish(conn: sqlite3.Connection, patch_id: int) -> dict:
    return run_patch_publish_stage(conn, patch_id) if _row(conn, patch_id) else enqueue_patch_publish(conn, patch_id)

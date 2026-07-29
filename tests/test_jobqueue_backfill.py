"""Backfill pending legacy rows into the job queue idempotently."""
from __future__ import annotations

from datetime import datetime, timezone

from app import db, repository
from app.jobqueue import store
from app.jobqueue.backfill import backfill_pending_jobs, build_queue


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size,
                              status, created_at, updated_at)
           VALUES (1, 'Book', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?)""", (now, now))
    conn.commit()
    return conn


def _patch(conn, status="pending", index=0):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               attempt_count, created_at, updated_at)
           VALUES (1, ?, 0, 0, ?, 0, ?, ?)""", (index, status, now, now))
    conn.commit()
    return cur.lastrowid


def test_pending_patches_become_voxcpm_jobs(tmp_path):
    conn = _conn(tmp_path)
    patch_id = _patch(conn)
    _patch(conn, status="done", index=1)
    counts = backfill_pending_jobs(conn)
    assert counts["voxcpm_tts"] == 1
    job = store.list_jobs(conn, job_type="voxcpm_tts")[0]
    assert job.payload["patch_id"] == patch_id
    assert job.dedupe_key == f"voxcpm_tts:patch={patch_id}"
    assert job.book_id == 1


def test_pending_book_jobs_become_video_jobs(tmp_path):
    conn = _conn(tmp_path)
    book_job = repository.enqueue_book_job(conn, 1, "video")
    counts = backfill_pending_jobs(conn)
    assert counts["video"] == 1
    assert store.list_jobs(conn, job_type="video")[0].payload["book_job_id"] == book_job.id


def test_pending_uploads_become_youtube_jobs(tmp_path):
    conn = _conn(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO youtube_uploads (video_path, title, description, tags,
                                         privacy_status, status, created_at)
           VALUES ('/tmp/v.mp4', 'T', 'D', '', 'private', 'pending', ?)""", (now,))
    conn.commit()
    counts = backfill_pending_jobs(conn)
    assert counts["youtube_upload"] == 1
    assert store.list_jobs(conn, job_type="youtube_upload")[0].payload["upload_id"] == cur.lastrowid


def test_running_it_twice_creates_nothing_new(tmp_path):
    conn = _conn(tmp_path)
    _patch(conn)
    repository.enqueue_book_job(conn, 1, "video")
    first = backfill_pending_jobs(conn)
    second = backfill_pending_jobs(conn)
    assert second == {"voxcpm_tts": 0, "video": 0, "youtube_upload": 0}
    assert len(store.list_jobs(conn)) == sum(first.values())


def test_finished_job_does_not_block_new_backfill(tmp_path):
    conn = _conn(tmp_path)
    _patch(conn)
    backfill_pending_jobs(conn)
    job = store.list_jobs(conn, job_type="voxcpm_tts")[0]
    store.finish(conn, job.id, None)
    assert backfill_pending_jobs(conn)["voxcpm_tts"] == 1


def test_build_queue_registers_all_four_handlers(tmp_path):
    conn = _conn(tmp_path)
    queue = build_queue(lambda: db.connect(str(tmp_path / "a.db")))
    assert queue.capacity("voxcpm_tts") == 1
    assert queue.capacity("video") == 2
    assert queue.capacity("youtube_upload") == 1
    assert queue.capacity("light_tts") == 10
    assert {p["job_type"] for p in queue.pool_status()} == {
        "voxcpm_tts", "video", "youtube_upload", "light_tts"
    }

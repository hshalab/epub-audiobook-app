"""Build the job queue and import pending rows from legacy job tables."""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from app.config import settings
from app.jobqueue import store
from app.jobqueue.handlers import light_tts, patch_video, video, voxcpm_tts, youtube_upload
from app.jobqueue.runner import JobQueue, parse_concurrency

logger = logging.getLogger(__name__)


def build_queue(conn_factory: Callable[[], sqlite3.Connection]) -> JobQueue:
    from app import repository

    queue = JobQueue(
        conn_factory,
        concurrency=parse_concurrency(
            settings.queue_concurrency, default=settings.queue_default_concurrency
        ),
        default_concurrency=settings.queue_default_concurrency,
        poll_interval=settings.worker_poll_interval,
        reap_after_seconds=settings.queue_reap_after_seconds,
        is_paused=repository.is_queue_paused,
    )
    queue.register("voxcpm_tts", voxcpm_tts.handle)
    queue.register("video", video.handle)
    queue.register("patch_video", patch_video.handle)
    queue.register("youtube_upload", youtube_upload.handle, cancellable=False)
    queue.register("light_tts", light_tts.handle)
    return queue


def enqueue_pending_patch_jobs(conn: sqlite3.Connection, book_id: int | None = None) -> int:
    """Queue a voxcpm_tts job for every 'pending' patch, optionally of a single book.

    Deliberately NOT part of backfill_pending_jobs: synthesis is expensive and holds the
    GPU, so it only starts when an operator asks for it (Start queue / Retry failed /
    Requeue stuck / Reset all). Running it at startup would refill a queue that was just
    cleared from /queue."""
    if book_id is None:
        rows = conn.execute(
            "SELECT id, book_id FROM patch WHERE status='pending' ORDER BY book_id, patch_index"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, book_id FROM patch WHERE status='pending' AND book_id=? ORDER BY patch_index",
            (book_id,),
        ).fetchall()

    queued = 0
    for row in rows:
        if store.enqueue(
            conn,
            "voxcpm_tts",
            payload={"patch_id": row["id"]},
            book_id=row["book_id"],
            dedupe_key=f"voxcpm_tts:patch={row['id']}",
        ) is not None:
            queued += 1
    return queued


def backfill_pending_jobs(conn: sqlite3.Connection) -> dict[str, int]:
    """Re-attach queue rows to legacy tables that already hold pending work. Runs at
    startup, so it covers only the cheap resumable job types - see
    enqueue_pending_patch_jobs for why voxcpm_tts is excluded."""
    counts = {"video": 0, "youtube_upload": 0}

    for row in conn.execute(
        "SELECT id, book_id FROM book_job WHERE status='pending' AND job_type='video' ORDER BY id"
    ).fetchall():
        if store.enqueue(
            conn,
            "video",
            payload={"book_job_id": row["id"]},
            book_id=row["book_id"],
            dedupe_key=f"video:book_job={row['id']}",
        ) is not None:
            counts["video"] += 1

    for row in conn.execute(
        "SELECT id FROM youtube_uploads WHERE status='pending' ORDER BY id"
    ).fetchall():
        if store.enqueue(
            conn,
            "youtube_upload",
            payload={"upload_id": row["id"]},
            dedupe_key=f"youtube_upload:upload={row['id']}",
        ) is not None:
            counts["youtube_upload"] += 1

    return counts

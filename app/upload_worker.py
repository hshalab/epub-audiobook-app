"""Sequential YouTube upload queue worker."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading

from app import youtube
from app.video_repository import get_video, update_video

logger = logging.getLogger(__name__)

UPLOAD_DELAY = 2  # seconds between uploads


class UploadWorker:
    def __init__(self, conn: sqlite3.Connection, db_lock: threading.Lock | None = None):
        self.conn = conn
        self.db_lock = db_lock or threading.Lock()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Upload worker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Upload worker stopped")

    def enqueue(self, video_id: int | None, title: str, description: str, tags: str, privacy: str, video_path: str | None = None) -> int:
        """Enqueue a video for upload. If video_id is None or video not found, uses video_path directly."""
        with self.db_lock:
            file_path = video_path
            if video_id is not None:
                video = get_video(self.conn, video_id)
                if video:
                    file_path = video["file_path"]
                    tags_list = [t.strip() for t in tags.split(",") if t.strip()]
                    upload_id = youtube.enqueue_upload(
                        self.conn, file_path, title, description, tags_list, privacy, video_id=video_id
                    )
                    update_video(self.conn, video_id, upload_status="queued", youtube_upload_id=upload_id)
                    return upload_id
            # ponytail: direct enqueue without video record, for auto-upload before video table integration
            if not file_path:
                raise ValueError("video_path required when video_id is None or video not found")
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]
            return youtube.enqueue_upload(
                self.conn, file_path, title, description, tags_list, privacy, video_id=None
            )

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "processing": self._task is not None and not self._task.done(),
        }

    async def _run_loop(self):
        while self._running:
            try:
                with self.db_lock:
                    pending = youtube.get_pending_uploads(self.conn)

                for upload in pending:
                    if not self._running:
                        break
                    await self._process_upload(upload)
                    await asyncio.sleep(UPLOAD_DELAY)
            except Exception as e:
                logger.error("Upload worker error: %s", e)

            await asyncio.sleep(5)  # poll interval

    async def _process_upload(self, upload: dict):
        upload_id = upload["id"]
        video_id = upload.get("video_id")
        try:
            with self.db_lock:
                if video_id:
                    update_video(self.conn, video_id, upload_status="uploading")

            result = await asyncio.to_thread(
                self._do_upload, upload,
            )

            with self.db_lock:
                if video_id:
                    update_video(
                        self.conn, video_id,
                        upload_status="uploaded",
                        youtube_video_id=result.get("youtube_video_id", ""),
                    )
            logger.info("Upload %s done: %s", upload_id, result.get("youtube_video_id"))
        except Exception as e:
            logger.error("Upload %s failed: %s", upload_id, e)
            with self.db_lock:
                if video_id:
                    update_video(self.conn, video_id, upload_status="failed", error_message=str(e))
                youtube.mark_upload_failed(self.conn, upload_id, str(e))

    def _do_upload(self, upload: dict) -> dict:
        """Blocking upload - runs in thread via asyncio.to_thread."""
        with self.db_lock:
            return youtube.upload_video(
                self.conn,
                upload["file_path"],
                upload["title"],
                upload["description"],
                upload.get("tags", []),
                upload.get("privacy_status", "private"),
            )


# Singleton instance - set via init_worker()
upload_worker: UploadWorker | None = None


def init_worker(conn: sqlite3.Connection, db_lock: threading.Lock) -> UploadWorker:
    """Initialize and return the singleton upload worker."""
    global upload_worker
    upload_worker = UploadWorker(conn, db_lock)
    return upload_worker

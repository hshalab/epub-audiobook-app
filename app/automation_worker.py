from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app import automation_repository, image_overlay, video_compositor, video_repository, youtube
from app.automation_config import AutomationConfig, render_metadata_template
from app.config import settings

logger = logging.getLogger(__name__)
MAX_ERROR_LENGTH = 2000
KINDS = {"image": {".jpg", ".jpeg", ".png", ".webp"}, "video": {".mp4", ".webm", ".mov"}}


async def _temp_thread(temp: Path, function, /, *args, **kwargs):
    cancelled = threading.Event()

    def render():
        try:
            return function(*args, **kwargs)
        finally:
            if cancelled.is_set():
                _unlink_safe(temp)

    try:
        return await asyncio.to_thread(render)
    except asyncio.CancelledError:
        cancelled.set()
        _unlink_safe(temp)
        raise


def _unlink_safe(path: Path) -> None:
    """Same-process thread races on Windows can cause PermissionError when unlinking a
    temp file another thread just closed.  This is purely defensive."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


class AutomationWorker:
    def __init__(self, conn, db_lock: threading.Lock, data_root: str | Path, poll_interval: float = 2.0):
        self.conn = conn
        self.db_lock = db_lock
        self.data_root = Path(data_root)
        self.poll_interval = poll_interval
        self._stopping = False
        self._wake = asyncio.Event()
        with self.db_lock:
            self.conn.execute(
                """UPDATE patch_pipeline SET
                   thumbnail_status=CASE WHEN stage='thumbnail' AND thumbnail_status='processing' THEN 'pending' ELSE thumbnail_status END,
                   video_status=CASE WHEN stage='video' AND video_status='processing' THEN 'pending' ELSE video_status END
                   WHERE (stage='thumbnail' AND thumbnail_status='processing')
                      OR (stage='video' AND video_status='processing')"""
            )
            self.conn.commit()

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self.run_forever())

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()

    async def run_forever(self) -> None:
        while not self._stopping:
            if not await self.run_once():
                try:
                    await asyncio.wait_for(self._wake.wait(), self.poll_interval)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()

    def enqueue_book(self, book_id: int) -> list[dict]:
        with self.db_lock:
            ids = [row["id"] for row in self.conn.execute(
                "SELECT id FROM patch WHERE book_id=? ORDER BY patch_index", (book_id,)
            )]
            return [automation_repository.enqueue_patch_pipeline(self.conn, patch_id) for patch_id in ids]

    async def run_once(self) -> bool:
        cursor = 0
        while candidates := self._candidates(cursor):
            for candidate in candidates:
                cursor = candidate["id"]
                try:
                    completed = await self._process(candidate)
                except Exception as exc:
                    self._fail(candidate, candidate["stage"], exc)
                    continue
                if completed:
                    return True
        return False

    def _candidates(self, cursor: int) -> list[dict]:
        with self.db_lock:
            rows = self.conn.execute(
                """SELECT pp.*,p.book_id,p.audio_path,p.patch_index,p.name,
                           p.chapter_start,p.chapter_end,b.title
                    FROM patch_pipeline pp JOIN patch p ON p.id=pp.patch_id
                    JOIN book b ON b.id=p.book_id
                    WHERE pp.id>? AND pp.stage IN ('thumbnail','video')
                      AND CASE pp.stage WHEN 'thumbnail' THEN pp.thumbnail_status
                          ELSE pp.video_status END IN ('pending','waiting_for_audio','waiting_for_media')
                    ORDER BY pp.id LIMIT 100""",
                (cursor,),
            ).fetchall()
            return [dict(row) for row in rows]

    async def _process(self, row: dict) -> bool:
        config_data = json.loads(row["config_snapshot"])
        media = json.loads(row["media_snapshot"])
        config = AutomationConfig.model_validate(config_data["automation"])
        if not isinstance(media.get("background"), list) or not isinstance(media.get("webcam"), list):
            raise ValueError("media snapshot requires immutable asset descriptors")
        if row["stage"] == "thumbnail":
            return await self._thumbnail(row, config_data, media)
        return await self._video(row, config, media)

    async def _usable(self, assets: list[dict], role: str) -> list[dict]:
        usable = []
        for asset in assets:
            try:
                kind = asset["media_type"]
                path = Path(asset["file_path"]).resolve()
                if not self._trusted(path) or kind not in KINDS or path.suffix.lower() not in KINDS[kind] or not await asyncio.to_thread(path.is_file):
                    continue
                probe = await asyncio.to_thread(video_compositor.probe_media, str(path))
                if not any(stream.get("codec_type") == "video" for stream in probe["streams"]):
                    continue
                if role == "webcam" and kind != "video":
                    continue
                usable.append({"file_path": str(path), "kind": kind})
            except (KeyError, TypeError, ValueError, OSError):
                logger.warning("automation media asset unusable: %r", asset, exc_info=True)
        return usable

    def _trusted(self, path: Path) -> bool:
        default = Path(settings.default_background_image).resolve()
        roots = [(self.data_root / "backgrounds").resolve()]
        return path == default or any(path != root and path.is_relative_to(root) for root in roots)

    def _thumbnail_path(self, row: dict) -> Path:
        return (self.data_root / "books" / str(row["book_id"]) / "patch_overlays" / f"{row['patch_id']}.png").resolve()

    @staticmethod
    def _valid_image_sync(path: Path) -> bool:
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
            return image.width > 0 and image.height > 0
        except (OSError, ValueError):
            return False

    async def _thumbnail(self, row: dict, snapshot: dict, media: dict) -> bool:
        existing = row["thumbnail_path"]
        expected = self._thumbnail_path(row)
        if existing and Path(existing).resolve() == expected and await asyncio.to_thread(self._valid_image_sync, expected):
            if not self._claim(row, "thumbnail"):
                return False
            self._complete(row, "thumbnail", "video", existing)
            return True
        backgrounds = await self._usable(media["background"], "background")
        image = next((item for item in backgrounds if item["kind"] == "image"), None)
        fallback = snapshot.get("background_fallback")
        if not image and fallback and self._trusted(Path(fallback).resolve()) and Path(fallback).suffix.lower() in KINDS["image"] and await asyncio.to_thread(self._valid_image_sync, Path(fallback)):
            image = {"file_path": fallback, "kind": "image"}
        if not image:
            self._waiting(row, "thumbnail", "waiting_for_media")
            return False
        if not self._claim(row, "thumbnail"):
            return False
        output = expected
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        temp = output.with_name(f".{output.stem}.{uuid4().hex}.tmp.png")
        book = SimpleNamespace(
            id=row["book_id"], title=row["title"],
            overlay_config=snapshot.get("overlay_config"), background_image_path=None,
        )
        patch = SimpleNamespace(id=row["patch_id"], name=row["name"], patch_index=row["patch_index"])
        try:
            result = await _temp_thread(
                temp, image_overlay.ensure_patch_overlay, book, patch,
                background_path=image["file_path"], out_path=str(temp),
                include_marquee=False,
            )
            if not result or not await asyncio.to_thread(self._valid_image_sync, temp):
                raise RuntimeError("thumbnail renderer produced invalid output")
            await asyncio.to_thread(os.replace, temp, output)
            self._complete(row, "thumbnail", "video", str(output))
            return True
        finally:
            await asyncio.to_thread(temp.unlink, missing_ok=True)

    async def _valid_video(self, path: Path) -> bool:
        try:
            if not await asyncio.to_thread(path.is_file):
                return False
            probe = await asyncio.to_thread(video_compositor.probe_media, str(path))
            return probe.get("duration", 0) > 0 and any(
                stream.get("codec_type") == "video" for stream in probe.get("streams", [])
            )
        except (ValueError, OSError, TypeError):
            return False

    async def _video(self, row: dict, config: AutomationConfig, media: dict) -> bool:
        if not row["audio_path"] or not await asyncio.to_thread(Path(row["audio_path"]).is_file):
            self._waiting(row, "video", "waiting_for_audio")
            return False
        backgrounds = await self._usable(media["background"], "background")
        if not backgrounds and row["thumbnail_path"]:
            path = Path(row["thumbnail_path"]).resolve()
            if path == self._thumbnail_path(row) and await asyncio.to_thread(self._valid_image_sync, path):
                backgrounds = [{"file_path": str(path), "kind": "image"}]
        if not backgrounds:
            self._waiting(row, "video", "waiting_for_media")
            return False
        if not self._claim(row, "video"):
            return False
        output = self.data_root / "books" / str(row["book_id"]) / "patch_videos" / f"{row['patch_id']}.mp4"
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        temp = output.with_name(f".{output.stem}.{uuid4().hex}.tmp.mp4")
        detached = False
        try:
            if not await self._valid_video(output):
                webcam = await self._usable(media["webcam"], "webcam")
                try:
                    await _temp_thread(
                        temp, video_compositor.render_composite,
                        audio_path=row["audio_path"], backgrounds=backgrounds,
                        webcam=webcam, output_path=str(temp), config=config,
                    )
                except asyncio.CancelledError:
                    detached = True
                    raise
                if not await self._valid_video(temp):
                    raise RuntimeError("video renderer produced invalid output")
                await asyncio.to_thread(os.replace, temp, output)
            size = (await asyncio.to_thread(output.stat)).st_size
            with self.db_lock:
                video = video_repository.upsert_patch_video(
                    self.conn, book_id=row["book_id"], patch_id=row["patch_id"],
                    file_path=str(output), resolution=config.video.resolution,
                    file_size_bytes=size,
                )
                self.conn.execute("UPDATE patch_pipeline SET video_id=? WHERE id=?", (video["id"], row["id"]))
                self.conn.commit()
            upload_id = self._enqueue_upload(row, config, output)
            self._complete(row, "video", "upload", str(output))
            return True
        finally:
            if not detached:
                await asyncio.to_thread(temp.unlink, missing_ok=True)

    def _claim(self, row: dict, stage: str) -> bool:
        with self.db_lock:
            return automation_repository.claim_pipeline_stage(self.conn, row["id"], stage) is not None

    def _waiting(self, row: dict, stage: str, status: str) -> None:
        with self.db_lock:
            automation_repository.update_pipeline_stage(self.conn, row["id"], stage, status)

    def _complete(self, row: dict, stage: str, next_stage: str, path: str) -> None:
        with self.db_lock:
            automation_repository.update_pipeline_stage(self.conn, row["id"], stage, "processing", output_path=path)
            automation_repository.advance_pipeline_stage(self.conn, row["id"], stage, next_stage)

    def _enqueue_upload(self, row: dict, config: AutomationConfig, output: Path) -> int | None:
        if not config.youtube_auto_upload or not youtube.is_configured():
            return None
        template_ctx = {
            "book_title": row["title"],
            "patch_name": row["name"] or f"Patch {row['patch_index']}",
            "patch_index": row["patch_index"],
            "chapter_start": row["chapter_start"],
            "chapter_end": row["chapter_end"],
        }
        title = render_metadata_template(config.youtube.title_template, template_ctx)
        description = render_metadata_template(config.youtube.description_template, template_ctx) if config.youtube.description_template else ""
        tags = config.youtube.tags
        privacy = config.youtube.privacy
        with self.db_lock:
            upload_id = youtube.enqueue_upload(self.conn, str(output), title, description, tags, privacy_status=privacy)
            if upload_id:
                self.conn.execute("UPDATE patch_pipeline SET youtube_upload_id=? WHERE id=?", (upload_id, row["id"]))
                snapshot = json.dumps({"automation": config.youtube.model_dump(), "background_fallback": row.get("thumbnail_path")})
                self.conn.execute("UPDATE youtube_uploads SET metadata_snapshot=? WHERE id=?", (snapshot, upload_id))
                self.conn.commit()
        return upload_id

    def _fail(self, row: dict, stage: str, exc: Exception) -> None:
        error = str(exc)[-MAX_ERROR_LENGTH:]
        with self.db_lock:
            automation_repository.update_pipeline_stage(self.conn, row["id"], stage, "failed", error=error)
        logger.exception("automation pipeline %s stage %s failed: %s", row["id"], stage, error)

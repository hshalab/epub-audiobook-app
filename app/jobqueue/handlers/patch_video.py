"""Render or recover one patch video through the unified queue."""
from __future__ import annotations

from pathlib import Path

from app import repository, video_gen
from app.jobqueue.models import JobFatalError
from app.video_integrity import validate_video
from app.video_publish import publish_validated_video
from app.video_recovery import resume_upload_after_render
from app.video_repository import upsert_patch_video


def handle(ctx) -> dict:
    patch_id = ctx.job.payload.get("patch_id")
    if patch_id is None:
        raise JobFatalError("payload missing patch_id")
    patch = repository.get_patch(ctx.conn, patch_id)
    if patch is None:
        raise JobFatalError(f"patch {patch_id} not found")
    book = repository.get_book(ctx.conn, patch.book_id)
    pipeline = ctx.conn.execute("SELECT * FROM patch_pipeline WHERE patch_id=?", (patch_id,)).fetchone()
    if book is None or pipeline is None:
        raise JobFatalError("source_unavailable: book or pipeline missing")
    if not patch.audio_path or not Path(patch.audio_path).is_file():
        raise JobFatalError(f"source_unavailable: audio missing: {patch.audio_path}")
    image = pipeline["thumbnail_path"]
    if not image or not Path(image).is_file():
        raise JobFatalError(f"source_unavailable: thumbnail missing: {image}")
    output = pipeline["video_path"] or str(Path(patch.audio_path).with_suffix(".mp4"))
    resolution = tuple(map(int, (book.video_resolution or "1920x1080").split("x")))
    fps = book.video_fps or 30

    ctx.progress(0, 1, phase="encoding")
    publish_validated_video(
        output,
        lambda temp: video_gen.generate_segment(image, patch.audio_path, temp,
                                                  resolution=resolution, fps=fps),
        validator=validate_video,
    )
    video = upsert_patch_video(ctx.conn, book_id=book.id, patch_id=patch_id,
                               file_path=output, resolution=book.video_resolution)
    ctx.conn.execute(
        """UPDATE patch_pipeline SET stage='upload', video_status='done', video_path=?,
           video_id=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE patch_id=?""",
        (output, video["id"], patch_id),
    )
    ctx.conn.commit()
    recovery_upload_id = ctx.job.payload.get("recovery_upload_id")
    if recovery_upload_id is not None:
        resume_upload_after_render(ctx.conn, recovery_upload_id)
    ctx.progress(1, 1, phase="done")
    return {"output_path": output}

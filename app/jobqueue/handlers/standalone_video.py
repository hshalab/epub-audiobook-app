"""Re-render a standalone video from persisted application-owned inputs."""
from __future__ import annotations

import json
from pathlib import Path

from app import video_gen
from app.jobqueue.models import JobFatalError
from app.video_integrity import validate_video
from app.video_publish import publish_validated_video
from app.video_recovery import resume_upload_after_render
from app.video_repository import get_video, update_video


def handle(ctx) -> dict:
    video_id = ctx.job.payload.get("video_id")
    if video_id is None:
        raise JobFatalError("payload missing video_id")
    video = get_video(ctx.conn, video_id)
    if not video:
        raise JobFatalError(f"video {video_id} not found")
    audio = video.get("source_audio")
    background = video.get("background_path")
    try:
        config = json.loads(video.get("render_config_json") or "")
    except json.JSONDecodeError as exc:
        raise JobFatalError(f"source_unavailable: invalid render config: {exc}") from exc
    for label, path in (("audio", audio), ("background", background)):
        if not path or not Path(path).is_file():
            raise JobFatalError(f"source_unavailable: {label} missing: {path}")
    if not isinstance(config, dict) or not config:
        raise JobFatalError("source_unavailable: render config missing")
    output = video["file_path"]
    ctx.progress(0, 1, phase="encoding")
    publish_validated_video(
        output,
        lambda temp: video_gen.generate_standalone_video(audio, background, temp, **config),
        validator=validate_video,
    )
    update_video(ctx.conn, video_id, file_path=output, upload_status="queued",
                 error_message=None)
    recovery_upload_id = ctx.job.payload.get("recovery_upload_id")
    if recovery_upload_id is not None:
        resume_upload_after_render(ctx.conn, recovery_upload_id)
    ctx.progress(1, 1, phase="done")
    return {"output_path": output}

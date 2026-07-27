from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from app.automation_config import AutomationConfig
from app.ffmpeg import get_ffmpeg_path, get_ffprobe_path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg"}


def probe_media(path: str) -> dict:
    if not Path(path).exists():
        raise ValueError(f"media source does not exist: {path}")
    extension = Path(path).suffix.lower()
    if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
        raise ValueError(f"unsupported media extension {extension or '<none>'}: {path}")
    try:
        result = subprocess.run(
            [get_ffprobe_path(), "-v", "error", "-show_entries",
             "stream=codec_type:format=duration", "-of", "json", path],
            check=True, capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid probe output for {path}") from exc
    streams = data.get("streams", [])
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not has_video and not has_audio:
        raise ValueError(f"no video stream in {path}")
    image = extension in IMAGE_EXTENSIONS
    raw_duration = data.get("format", {}).get("duration")
    if not image and raw_duration is None:
        raise ValueError(f"missing duration for video source {path}")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid duration for {path}") from exc
    kind = "image" if image else ("video" if extension in VIDEO_EXTENSIONS else "audio")
    return {"duration": duration, "streams": streams, "kind": kind}


@lru_cache(maxsize=1)
def _ffmpeg_capabilities() -> tuple[str, str]:
    listings = []
    for option in ("-encoders", "-filters"):
        result = subprocess.run(
            [get_ffmpeg_path(), "-hide_banner", option],
            check=True, capture_output=True, text=True,
        )
        listings.append(result.stdout + result.stderr)
    return listings[0], listings[1]


def validate_ffmpeg_capabilities(config: AutomationConfig) -> None:
    encoders, filters = _ffmpeg_capabilities()
    if config.video.encoder == "h264_nvenc" and "h264_nvenc" not in encoders:
        raise RuntimeError("FFmpeg encoder h264_nvenc is unavailable")
    if config.webcam.enabled and config.webcam.corner_radius and " geq " not in filters:
        raise RuntimeError("FFmpeg rounded-corner filter support is unavailable")


def build_composite_command(
    audio_path: str,
    backgrounds: list[dict],
    webcam: list[dict],
    output_path: str,
    config: AutomationConfig,
    music_path: str | None = None,
) -> list[str]:
    try:
        narration = probe_media(audio_path)
    except ValueError as exc:
        raise ValueError(f"narration {audio_path}: {exc}") from exc
    if not any(stream.get("codec_type") == "audio" for stream in narration["streams"]):
        raise ValueError(f"narration {audio_path} has no audio stream")
    if narration["duration"] is None or narration["duration"] <= 0:
        raise ValueError(f"narration {audio_path} must have positive duration")
    if not backgrounds:
        raise ValueError("At least one background source is required")
    if any(source["kind"] != "video" for source in webcam):
        raise ValueError("Webcam sources must be video")

    width, height = map(int, config.video.resolution.split("x"))
    slot = config.video.background_duration_seconds
    duration = narration["duration"]
    inputs = ["-i", audio_path]
    chains = []

    for index, source in enumerate(backgrounds, 1):
        media = probe_media(source["file_path"])
        if not any(stream.get("codec_type") == "video" for stream in media["streams"]):
            raise ValueError(f"background {source['file_path']} has no video stream")
        if media["kind"] != source["kind"]:
            raise ValueError(
                f"background {source['file_path']} declared {source['kind']} but probes as {media['kind']}"
            )
        if source["kind"] == "image":
            inputs += ["-loop", "1", "-i", source["file_path"]]
        else:
            inputs += ["-stream_loop", "-1", "-i", source["file_path"]]
        chains.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={config.video.fps},"
            f"setsar=1,format=yuv420p,trim=duration={slot},setpts=PTS-STARTPTS[bg{index}]"
        )
    labels = "".join(f"[bg{i}]" for i in range(1, len(backgrounds) + 1))
    background_frames = slot * config.video.fps * len(backgrounds)
    chains.append(
        f"{labels}concat=n={len(backgrounds)}:v=1:a=0,"
        f"loop=loop=-1:size={background_frames}:start=0[background]"
    )
    video_label = "background"

    if config.webcam.enabled and webcam:
        cam_labels = []
        for source in webcam:
            media = probe_media(source["file_path"])
            if not any(stream.get("codec_type") == "video" for stream in media["streams"]):
                raise ValueError(f"webcam {source['file_path']} has no video stream")
            if media["kind"] != "video":
                raise ValueError(f"webcam {source['file_path']} must contain video")
            index = 1 + len(backgrounds) + len(cam_labels)
            inputs += ["-stream_loop", "-1", "-i", source["file_path"]]
            cam_width = width * config.webcam.width_percent // 100
            cam_height = cam_width * height // width
            chains.append(
                f"[{index}:v]scale={cam_width}:{cam_height}:force_original_aspect_ratio=increase,"
                f"crop={cam_width}:{cam_height},fps={config.video.fps},setsar=1,format=yuv420p,"
                f"trim=duration={slot},setpts=PTS-STARTPTS[cam{len(cam_labels)}]"
            )
            cam_labels.append(f"[cam{len(cam_labels)}]")
        webcam_filters = ""
        if config.webcam.corner_radius:
            radius = config.webcam.corner_radius
            webcam_filters += (
                ",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(gt(abs(W/2-X),W/2-%d)*gt(abs(H/2-Y),H/2-%d),"
                "if(lte(hypot(W/2-%d-abs(W/2-X),H/2-%d-abs(H/2-Y)),%d),255,0),255)'"
                % (radius, radius, radius, radius, radius)
            )
        if config.webcam.border_width:
            border = config.webcam.border_width
            webcam_filters += (
                f",pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:"
                f"color={config.webcam.border_color}"
            )
        webcam_frames = slot * config.video.fps * len(cam_labels)
        chains.append(
            f"{''.join(cam_labels)}concat=n={len(cam_labels)}:v=1:a=0,"
            f"loop=loop=-1:size={webcam_frames}:start=0{webcam_filters}[webcam]"
        )
        margin = config.webcam.margin
        positions = {
            "top-left": (str(margin), str(margin)),
            "top-right": (f"W-w-{margin}", str(margin)),
            "bottom-left": (str(margin), f"H-h-{margin}"),
            "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
        }
        x, y = positions[config.webcam.position]
        chains.append(f"[background][webcam]overlay={x}:{y}:shortest=1[video]")
        video_label = "video"
    else:
        chains.append("[background]null[video]")
        video_label = "video"

    audio_label = "0:a"
    if music_path:
        try:
            music = probe_media(music_path)
        except ValueError as exc:
            raise ValueError(f"music {music_path}: {exc}") from exc
        if not any(stream.get("codec_type") == "audio" for stream in music["streams"]):
            raise ValueError(f"music {music_path} has no audio stream")
        music_index = 1 + len(backgrounds) + (len(webcam) if config.webcam.enabled else 0)
        inputs += ["-stream_loop", "-1", "-i", music_path]
        chains += [f"[{music_index}:a]volume={config.video.music_volume}[music]",
                   "[0:a][music]amix=inputs=2:duration=first:normalize=0[audio]"]
        audio_label = "[audio]"

    quality = ["-crf", str(config.video.crf)] if config.video.encoder == "libx264" else ["-cq", str(config.video.cq)]
    return [
        get_ffmpeg_path(), "-y", *inputs, "-filter_complex", ";".join(chains),
        "-map", f"[{video_label}]", "-map", audio_label,
        "-c:v", config.video.encoder, "-preset", config.video.preset, *quality,
        "-c:a", "aac", "-b:a", config.video.audio_bitrate, "-pix_fmt", config.video.pixel_format,
        "-t", str(duration), "-shortest", output_path,
    ]


def render_composite(*args, **kwargs) -> None:
    config = kwargs.get("config") or args[4]
    validate_ffmpeg_capabilities(config)
    subprocess.run(build_composite_command(*args, **kwargs), check=True, capture_output=True, text=True)

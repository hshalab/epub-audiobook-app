"""Queue-independent validation for videos that may be uploaded to YouTube."""
from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from app.config import Settings

MAX_ERROR_CHARS = 2000
DRIFT_WARN_SECONDS = 1.0
DRIFT_FATAL_SECONDS = 5.0
RECOVERABLE_OUTPUT_CODES = frozenset({
    "probe_failed", "missing_video_stream", "missing_audio_stream",
    "invalid_duration", "unsupported_format", "av_drift", "decode_failed",
})
_VIDEO_CODECS = frozenset({"h264", "hevc"})
_AUDIO_CODECS = frozenset({"aac", "mp3"})
_CONTAINERS = frozenset({"mp4", "mov", "m4v"})


@dataclass(frozen=True)
class ValidationFacts:
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    video_duration: float = 0.0
    audio_duration: float = 0.0
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error_code: str | None
    message: str
    warnings: tuple[str, ...]
    facts: ValidationFacts
    elapsed_seconds: float


def decode_timeout(duration_seconds: float) -> int:
    return min(21600, max(300, ceil(max(0.0, duration_seconds) * 2 + 120)))


def _result(started: float, *, valid: bool = False, code: str | None = None,
            message: str = "", warnings: tuple[str, ...] = (),
            facts: ValidationFacts | None = None) -> ValidationResult:
    return ValidationResult(valid, code, message[-MAX_ERROR_CHARS:], warnings,
                            facts or ValidationFacts(), time.monotonic() - started)


def _duration(stream: dict, format_info: dict) -> float:
    raw = stream.get("duration")
    if raw in (None, ""):
        raw = format_info.get("duration")
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("duration must be finite and positive")
    return value


def validate_video(path: str | Path) -> ValidationResult:
    started = time.monotonic()
    media = Path(path)
    if not media.is_file():
        return _result(started, code="file_missing", message=f"file not found: {media}")
    if media.stat().st_size == 0:
        return _result(started, code="file_empty", message=f"file is empty: {media}")

    probe_cmd = [
        Settings.get_ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(media),
    ]
    try:
        probe = subprocess.run(probe_cmd, capture_output=True, text=True)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return _result(started, code="tool_unavailable", message=str(exc))
    if probe.returncode != 0:
        return _result(started, code="probe_failed", message=probe.stderr or "ffprobe failed")
    try:
        info = json.loads(probe.stdout)
        streams = info.get("streams") or []
        format_info = info.get("format") or {}
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        return _result(started, code="probe_failed", message=str(exc))

    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        return _result(started, code="missing_video_stream", message="no video stream")
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if audio is None:
        return _result(started, code="missing_audio_stream", message="no audio stream")
    try:
        video_duration = _duration(video, format_info)
        audio_duration = _duration(audio, format_info)
    except (TypeError, ValueError, OverflowError) as exc:
        return _result(started, code="invalid_duration", message=str(exc))

    container = str(format_info.get("format_name") or "")
    video_codec = str(video.get("codec_name") or "").lower()
    audio_codec = str(audio.get("codec_name") or "").lower()
    facts = ValidationFacts(
        container=container, video_codec=video_codec, audio_codec=audio_codec,
        video_duration=video_duration, audio_duration=audio_duration,
        width=int(video.get("width") or 0), height=int(video.get("height") or 0),
    )
    if not (_CONTAINERS.intersection(part.strip().lower() for part in container.split(","))
            and video_codec in _VIDEO_CODECS and audio_codec in _AUDIO_CODECS):
        return _result(started, code="unsupported_format",
                       message=f"unsupported output: {container}/{video_codec}/{audio_codec}",
                       facts=facts)

    drift = abs(video_duration - audio_duration)
    if drift >= DRIFT_FATAL_SECONDS:
        return _result(started, code="av_drift",
                       message=f"audio/video duration drift is {drift:.3f}s", facts=facts)
    warnings = ((f"audio/video duration drift is {drift:.3f}s",)
                if drift >= DRIFT_WARN_SECONDS else ())

    decode_cmd = [
        Settings.get_ffmpeg_path(), "-v", "error", "-xerror", "-i", str(media),
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
    ]
    try:
        decode = subprocess.run(
            decode_cmd, capture_output=True, text=True,
            timeout=decode_timeout(max(video_duration, audio_duration)),
        )
    except subprocess.TimeoutExpired as exc:
        return _result(started, code="validation_timeout", message=str(exc), facts=facts)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return _result(started, code="tool_unavailable", message=str(exc), facts=facts)
    if decode.returncode != 0:
        return _result(started, code="decode_failed", message=decode.stderr or "ffmpeg decode failed",
                       facts=facts)
    return _result(started, valid=True, warnings=warnings, facts=facts)

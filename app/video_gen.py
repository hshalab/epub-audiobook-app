"""Video generation: static image, Ken Burns animated, multi-segment concat, standalone."""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from app.ffmpeg import get_ffmpeg_path, get_ffprobe_path
from app.models import Book, Patch

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, dict], None]


def _emit(on_progress: ProgressCallback | None, event: str, **fields) -> None:
    if on_progress is not None:
        try:
            on_progress(event, fields)
        except Exception:
            pass


def _build_zoompan_filter(image_type: str, width: int, height: int, fps: int, duration: float) -> str:
    """Build ffmpeg zoompan filter string for Ken Burns effects."""
    total_frames = int(duration * fps)
    if total_frames < 1:
        total_frames = 1

    if image_type == "zoom-in":
        return (
            f"zoompan=z='min(zoom+0.0015,1.5)':d={total_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    elif image_type == "zoom-out":
        return (
            f"zoompan=z='if(eq(on,1),1.5,max(zoom-0.0015,1.0))':d={total_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    elif image_type == "pan-left":
        return (
            f"zoompan=z='1.2':d={total_frames}"
            f":x='iw*0.2*(1-on/{total_frames})':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    elif image_type == "pan-right":
        return (
            f"zoompan=z='1.2':d={total_frames}"
            f":x='iw*0.2*(on/{total_frames})':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    else:
        return (
            f"zoompan=z='1':d={total_frames}:s={width}x{height}:fps={fps}"
        )


def generate_segment(
    image_path: str,
    audio_path: str,
    out_path: str,
    *,
    image_type: str = "none",
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    audio_bitrate: str = "192k",
    crf: int = 23,
    use_nvenc: bool = False,
    music_path: str | None = None,
    music_volume: float = 0.15,
    marquee_path: str | None = None,
    marquee_meta: dict | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Generate a single video segment from image + audio.

    image_type: 'none' (static), 'zoom-in', 'zoom-out', 'pan-left', 'pan-right'
    music_path: optional background music file (looped, mixed at music_volume ratio)
    marquee_path: optional ticker strip PNG (3× wide of video, scrolls horizontally)
    marquee_meta: dict with marquee_height, speed_px_per_sec, scroll_unit_px

    on_progress: optional callback(event: str, fields: dict) for progress logging.
    Events: segment.start, segment.ffmpeg_start, segment.ffmpeg_done, segment.done,
            segment.failed.
    """
    if music_path is not None and not Path(music_path).exists():
        raise FileNotFoundError(f"music file not found: {music_path}")
    if marquee_path is not None and not Path(marquee_path).exists():
        raise FileNotFoundError(f"marquee band not found: {marquee_path}")

    video_codec = "h264_nvenc" if use_nvenc else "libx264"
    width, height = resolution

    _emit(on_progress, "segment.start", path=out_path, image_type=image_type,
          resolution=f"{width}x{height}", fps=fps, codec=video_codec,
          has_marquee=bool(marquee_path))

    if use_nvenc:
        quality_args = ["-cq", str(crf)]
        tune_args = []
    else:
        quality_args = ["-crf", str(crf)]
        tune_args = ["-tune", "stillimage"]

    inputs = [
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
    ]
    next_idx = 2
    music_idx: int | None = None
    marquee_idx: int | None = None
    if music_path:
        inputs.extend(["-stream_loop", "-1", "-i", music_path])
        music_idx = next_idx
        next_idx += 1
    has_marquee = bool(marquee_path and marquee_meta)
    if has_marquee:
        inputs.extend(["-loop", "1", "-i", marquee_path])
        marquee_idx = next_idx
        next_idx += 1

    if image_type == "none":
        base_vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
    else:
        if has_marquee:
            logger.warning(
                "video_gen: Ken Burns + marquee unsupported - disabling marquee for %s",
                out_path,
            )
            has_marquee = False
        _emit(on_progress, "segment.probe_duration", path=out_path, audio=audio_path)
        probe = subprocess.run(
            [get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 10.0
        zp_filter = _build_zoompan_filter(image_type, width, height, fps, duration)
        base_vf = f"{zp_filter},format=yuv420p"

    audio_chains: list[str] = []
    if music_idx is not None:
        audio_chains.append(f"[{music_idx}:a]volume={music_volume}[music]")
        audio_chains.append("[1:a][music]amix=inputs=2:duration=first:normalize=0[aout]")
        audio_map_label = "[aout]"
    else:
        audio_map_label = "1:a"

    if has_marquee and marquee_idx is not None:
        band_h = int(marquee_meta.get("marquee_height", 60))
        speed = int(marquee_meta.get("speed_px_per_sec", 80))
        scroll_unit = max(1, int(marquee_meta.get("scroll_unit_px", width)))
        band_vf = f"crop={width}:{band_h}:x='(t*{speed})%{scroll_unit}':y=0"
        bg_chain = f"[0:v]{base_vf}[bg]"
        band_chain = f"[{marquee_idx}:v]{band_vf}[band]"
        overlay_chain = "[bg][band]overlay=0:0[outv]"
        chains = audio_chains + [bg_chain, band_chain, overlay_chain]
        cmd = [
            get_ffmpeg_path(), "-y",
            *inputs,
            "-filter_complex", ";".join(chains),
            "-map", "[outv]",
            "-map", audio_map_label,
            "-c:v", video_codec,
            *tune_args,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-pix_fmt", "yuv420p",
            *quality_args,
            "-shortest",
            out_path,
        ]
    elif music_idx is not None:
        chains = audio_chains + [f"[0:v]{base_vf}"]
        cmd = [
            get_ffmpeg_path(), "-y",
            *inputs,
            "-filter_complex", ";".join(chains),
            "-map", "0:v",
            "-map", audio_map_label,
            "-c:v", video_codec,
            *tune_args,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-pix_fmt", "yuv420p",
            *quality_args,
            "-shortest",
            out_path,
        ]
    else:
        cmd = [
            get_ffmpeg_path(), "-y",
            *inputs,
            "-vf", base_vf,
            "-c:v", video_codec,
            *tune_args,
            "-c:a", "aac", "-b:a", audio_bitrate,
            "-pix_fmt", "yuv420p",
            *quality_args,
            "-shortest",
            out_path,
        ]

    _emit(on_progress, "segment.ffmpeg_start", path=out_path)
    t0 = time.monotonic()
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr_tail = (exc.stderr or "")[-500:]
        _emit(on_progress, "segment.failed", path=out_path,
              returncode=exc.returncode, stderr_tail=stderr_tail)
        raise

    elapsed = time.monotonic() - t0
    out_size = Path(out_path).stat().st_size if Path(out_path).exists() else 0
    _emit(on_progress, "segment.ffmpeg_done", path=out_path,
          elapsed_seconds=round(elapsed, 2), size_bytes=out_size)
    _emit(on_progress, "segment.done", path=out_path)


def concat_segments(
    segment_paths: list[str],
    out_path: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Concat multiple segment videos into one using ffmpeg concat demuxer."""
    if not segment_paths:
        raise ValueError("No segments to concat")

    _emit(on_progress, "concat.start", count=len(segment_paths), path=out_path)

    if len(segment_paths) == 1:
        Path(out_path).write_bytes(Path(segment_paths[0]).read_bytes())
        _emit(on_progress, "concat.done", count=1, path=out_path, mode="copy_single")
        return

    list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    try:
        for p in segment_paths:
            safe = p.replace("\\", "/").replace("'", "'\\''")
            list_file.write(f"file '{safe}'\n")
        list_file.close()

        cmd = [
            get_ffmpeg_path(), "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file.name,
            "-c", "copy",
            out_path,
        ]
        _emit(on_progress, "concat.ffmpeg_start", count=len(segment_paths))
        t0 = time.monotonic()
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            stderr_tail = (exc.stderr or "")[-500:]
            _emit(on_progress, "concat.failed", returncode=exc.returncode,
                  stderr_tail=stderr_tail)
            raise
        elapsed = time.monotonic() - t0
        out_size = Path(out_path).stat().st_size if Path(out_path).exists() else 0
        _emit(on_progress, "concat.ffmpeg_done", count=len(segment_paths),
              elapsed_seconds=round(elapsed, 2), size_bytes=out_size)
    finally:
        Path(list_file.name).unlink(missing_ok=True)

    _emit(on_progress, "concat.done", count=len(segment_paths), path=out_path)


def resolve_patch_image(patch: Patch, book: Book | None, default_image: str) -> str | None:
    """Resolve the image for a patch: patch.image_path -> book.background_image_path -> default."""
    if patch.image_path and Path(patch.image_path).exists():
        return patch.image_path
    if book and book.background_image_path and Path(book.background_image_path).exists():
        return book.background_image_path
    if Path(default_image).exists():
        return default_image
    return None


def generate_full_video(
    patches: list[Patch],
    book: Book,
    out_path: str,
    *,
    default_image: str,
    use_nvenc: bool = False,
    music_path: str | None = None,
    music_volume: float = 0.15,
    font_path: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Generate a full video by creating segments per patch and concatenating.

    music_path: optional background music file looped at music_volume ratio.
    font_path: passed to image_overlay.ensure_patch_overlay() for text rendering.

    on_progress: optional callback(event, fields) for progress logging.
    Events: video.start, video.segment_skipped, video.segments_done, video.done, video.failed.
    """
    from app import image_overlay

    w, h = (book.video_resolution or "1920x1080").split("x")
    resolution = (int(w), int(h))
    fps = book.video_fps or 30
    default_anim = book.default_image_animation or "none"

    eligible = [p for p in patches if p.audio_path]
    _emit(on_progress, "video.start", path=out_path, total_patches=len(patches),
          eligible_patches=len(eligible), resolution=f"{w}x{h}", fps=fps,
          codec="h264_nvenc" if use_nvenc else "libx264")

    segment_paths: list[str] = []
    tmp_dir = Path(out_path).parent / "_segments"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        for i, patch in enumerate(patches):
            if not patch.audio_path:
                _emit(on_progress, "video.segment_skipped",
                      patch_index=patch.patch_index, reason="no_audio")
                continue

            overlay = image_overlay.ensure_patch_overlay(book, patch, font_path)
            image = overlay or resolve_patch_image(patch, book, default_image)
            if not image:
                _emit(on_progress, "video.segment_skipped",
                      patch_index=patch.patch_index, reason="no_image")
                continue

            anim = patch.image_type if patch.image_type and patch.image_type != "static" else default_anim
            seg_path = str(tmp_dir / f"seg_{i:04d}.mp4")

            def _seg_progress(event: str, fields: dict, _p=patch) -> None:
                _emit(on_progress, event, patch_index=_p.patch_index,
                      patch_id=_p.id, **{k: v for k, v in fields.items() if k != "path"})

            # Resolve marquee band + meta if they exist for this patch.
            marquee_p = str(image_overlay.get_marquee_path(book.id, patch.id))
            marquee_m_p = image_overlay.get_marquee_meta_path(book.id, patch.id)
            seg_marquee_path: str | None = None
            seg_marquee_meta: dict | None = None
            if Path(marquee_p).exists() and marquee_m_p.exists():
                try:
                    seg_marquee_meta = json.loads(marquee_m_p.read_text(encoding="utf-8"))
                    seg_marquee_path = marquee_p
                except Exception as exc:
                    logger.warning("video_gen: invalid marquee meta for patch %s: %s", patch.id, exc)

            generate_segment(
                image, patch.audio_path, seg_path,
                image_type=anim,
                resolution=resolution,
                fps=fps,
                use_nvenc=use_nvenc,
                music_path=music_path,
                music_volume=music_volume,
                marquee_path=seg_marquee_path,
                marquee_meta=seg_marquee_meta,
                on_progress=_seg_progress,
            )
            segment_paths.append(seg_path)
            _emit(on_progress, "video.segment_done",
                  patch_index=patch.patch_index, patch_id=patch.id,
                  segment_index=len(segment_paths),
                  progress=f"{len(segment_paths)}/{len(eligible)}")

        if not segment_paths:
            _emit(on_progress, "video.failed", reason="no_segments")
            raise ValueError("No segments were generated")

        _emit(on_progress, "video.segments_done", count=len(segment_paths))
        concat_segments(segment_paths, out_path, on_progress=on_progress)

        out_size = Path(out_path).stat().st_size if Path(out_path).exists() else 0
        _emit(on_progress, "video.done", path=out_path, size_bytes=out_size)
    except Exception as exc:
        _emit(on_progress, "video.failed", error=str(exc))
        raise
    finally:
        for p in segment_paths:
            Path(p).unlink(missing_ok=True)
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


def generate_standalone_video(
    audio_path: str,
    image_path: str,
    out_path: str,
    *,
    resolution: str = "1920x1080",
    fps: int = 30,
    codec: str = "libx264",
    audio_bitrate: str = "192k",
    image_type: str = "none",
    crf: int = 23,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Generate a standalone video from a single audio + image (Video Creator page)."""
    w, h = resolution.split("x")
    res = (int(w), int(h))
    use_nvenc = codec == "h264_nvenc"
    generate_segment(
        image_path, audio_path, out_path,
        image_type=image_type,
        resolution=res,
        fps=fps,
        audio_bitrate=audio_bitrate,
        crf=crf,
        use_nvenc=use_nvenc,
        on_progress=on_progress,
    )

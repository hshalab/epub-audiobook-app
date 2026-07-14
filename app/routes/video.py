"""Standalone Video Creator page: upload audio + image + ffmpeg settings -> mp4."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import image_overlay, repository, video_gen
from app.config import settings
from app.deps import locked_conn

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

# In-memory step progress for debug UI, keyed by "{batch_id}:{idx}" or job_id.
# Best-effort: lost on restart, purged after 1h alongside tmp files.
_progress_store: dict[str, dict] = {}


def _record_step(job_key: str, event: str, fields: dict) -> None:
    entry = _progress_store.setdefault(
        job_key, {"status": "running", "steps": [], "updated_at": 0.0}
    )
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    entry["steps"].append({
        "t": datetime.now().strftime("%H:%M:%S"),
        "event": event,
        "detail": detail,
    })
    if event.endswith(".failed"):
        entry["status"] = "error"
    elif event == "job.done":
        entry["status"] = "done"
    entry["updated_at"] = time.time()


def _cleanup_progress_store(max_age_seconds: int = 3600) -> None:
    now = time.time()
    stale = [k for k, v in _progress_store.items()
             if (now - v.get("updated_at", 0)) > max_age_seconds]
    for k in stale:
        _progress_store.pop(k, None)


def _make_progress_logger(
    prefix: str, job_key: str | None = None, **base_fields
) -> video_gen.ProgressCallback:
    def _on_progress(event: str, fields: dict) -> None:
        parts = [f"event={prefix}.{event}"]
        merged = {**base_fields, **fields}
        for k, v in merged.items():
            parts.append(f"{k}={v}")
        logger.info(" ".join(parts))
        if job_key is not None:
            _record_step(job_key, event, fields)
    return _on_progress

_TMP_DIR = Path(settings.data_root) / "tmp" / "video_creator"
_VIDEOS_DIR = Path(settings.data_root) / "videos"
_BACKGROUNDS_DIR = Path(settings.data_root) / "backgrounds"

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

VALID_RESOLUTIONS = {"1920x1080", "1280x720", "854x480"}
VALID_FPS = {24, 30, 60}
VALID_CODECS = {"libx264", "h264_nvenc"}
VALID_AUDIO_BITRATES = {"128k", "192k", "256k", "320k"}
VALID_IMAGE_TYPES = {"none", "zoom-in", "zoom-out", "pan-left", "pan-right"}


def _cleanup_old_tmp_files(max_age_seconds: int = 3600) -> None:
    """Delete tmp files older than max_age_seconds. Best-effort."""
    _cleanup_progress_store(max_age_seconds)
    if not _TMP_DIR.exists():
        return
    now = time.time()
    for p in _TMP_DIR.iterdir():
        try:
            if p.is_dir():
                if (now - p.stat().st_mtime) > max_age_seconds:
                    shutil.rmtree(p, ignore_errors=True)
            elif p.is_file() and (now - p.stat().st_mtime) > max_age_seconds:
                p.unlink()
        except OSError:
            pass


def _validate_config(
    resolution: str, fps: int, codec: str,
    audio_bitrate: str, image_type: str, crf: int,
) -> dict:
    return {
        "resolution": resolution if resolution in VALID_RESOLUTIONS else "1920x1080",
        "fps": fps if fps in VALID_FPS else 30,
        "codec": codec if codec in VALID_CODECS else "libx264",
        "audio_bitrate": audio_bitrate if audio_bitrate in VALID_AUDIO_BITRATES else "192k",
        "image_type": image_type if image_type in VALID_IMAGE_TYPES else "none",
        "crf": crf if 18 <= crf <= 28 else 23,
    }


def _resolve_background_image(bg_path: str | None) -> Path | None:
    """Resolve background image from path, falling back to default."""
    if bg_path:
        p = Path(bg_path)
        if p.exists():
            return p
    default = settings.default_background_image
    if Path(default).exists():
        return Path(default)
    return None


def _render_overlay_for_batch(
    bg_path: Path, text: str, overlay_opts: dict, out_path: Path,
) -> Path | None:
    """Render user text onto a copy of the background. None on failure (caller
    falls back to the plain background rather than failing the video)."""
    try:
        from PIL import Image, ImageDraw
        cfg = image_overlay.get_default_overlay_config()
        cfg["position"] = overlay_opts.get("position", "top")
        cfg["font_size"] = int(overlay_opts.get("font_size", 52))
        cfg["text_color"] = overlay_opts.get("text_color", "#FFFFFF")
        img = Image.open(str(bg_path)).convert("RGB")
        draw = ImageDraw.Draw(img)
        font = image_overlay._load_font(None, cfg["font_size"])
        lines = image_overlay._wrap_lines(draw, text, font, img.size[0] - 80)
        image_overlay.render_overlay(img, lines, cfg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), "PNG")
        return out_path
    except Exception as exc:
        logger.error("video_creator: overlay render failed: %s", exc)
        return None


def _sanitize_basename(name: str) -> str:
    """Sanitize an audio filename into a safe video basename (no extension).

    Strips path components, replaces filesystem-unsafe characters with
    underscores, and falls back to 'video' if the result is empty.
    """
    base = Path(name or "").name
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', base)
    base = base.strip().strip('.')
    return base or "video"


def _unique_video_path(stem: str) -> Path:
    """Return a non-colliding path in _VIDEOS_DIR for the given stem."""
    candidate = _VIDEOS_DIR / f"{stem}.mp4"
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = _VIDEOS_DIR / f"{stem}_{i}.mp4"
        if not candidate.exists():
            return candidate
        i += 1


def _get_recent_videos(limit: int = 20) -> list[dict]:
    videos = []
    if _VIDEOS_DIR.exists():
        for f in sorted(_VIDEOS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix == ".mp4":
                videos.append({
                    "name": f.name,
                    "url": f"/video/videos/{f.name}",
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                })
            if len(videos) >= limit:
                break
    return videos


# ---------------------------------------------------------------------------
# Original single-file endpoints (kept for backward compatibility)
# ---------------------------------------------------------------------------

@router.get("/video", response_class=HTMLResponse)
def video_creator_page(request: Request):
    _cleanup_old_tmp_files()
    return templates.TemplateResponse(request, "video_creator.html", {
        "request": request,
        "video_url": None,
        "error": None,
        "recent_videos": _get_recent_videos(),
    })


@router.post("/video/generate", response_class=HTMLResponse)
async def generate_video(
    request: Request,
    audio: UploadFile = File(...),
    image: UploadFile | None = File(default=None),
    resolution: str = Form(default="1920x1080"),
    fps: int = Form(default=30),
    codec: str = Form(default="libx264"),
    audio_bitrate: str = Form(default="192k"),
    image_type: str = Form(default="none"),
    crf: int = Form(default=23),
):
    audio_ext = Path(audio.filename or "").suffix.lower()
    if audio_ext not in ALLOWED_AUDIO_EXTENSIONS:
        return templates.TemplateResponse(request, "video_creator.html", {
            "request": request,
            "video_url": None,
            "error": f"Unsupported audio format: {audio_ext}",
            "recent_videos": _get_recent_videos(),
        })

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]

    audio_path = _TMP_DIR / f"{job_id}_audio{audio_ext}"
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    image_path = None
    if image is not None and image.filename:
        img_ext = Path(image.filename).suffix.lower()
        if img_ext not in ALLOWED_IMAGE_EXTENSIONS:
            audio_path.unlink(missing_ok=True)
            return templates.TemplateResponse(request, "video_creator.html", {
                "request": request,
                "video_url": None,
                "error": f"Unsupported image format: {img_ext}",
                "recent_videos": _get_recent_videos(),
            })
        image_path = _TMP_DIR / f"{job_id}_image{img_ext}"
        with open(image_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
    else:
        default = settings.default_background_image
        if Path(default).exists():
            image_path = Path(default)
        else:
            audio_path.unlink(missing_ok=True)
            return templates.TemplateResponse(request, "video_creator.html", {
                "request": request,
                "video_url": None,
                "error": "Please upload an image or configure a default background image",
                "recent_videos": _get_recent_videos(),
            })

    cfg = _validate_config(resolution, fps, codec, audio_bitrate, image_type, crf)
    tmp_out = _TMP_DIR / f"{job_id}.mp4"

    progress_cb = _make_progress_logger(
        "video_creator.single", job_key=f"single:{job_id}",
        job_id=job_id, mode="single",
    )
    try:
        await asyncio.to_thread(
            video_gen.generate_standalone_video,
            str(audio_path), str(image_path), str(tmp_out),
            on_progress=progress_cb,
            **cfg,
        )
    except Exception as exc:
        tmp_out.unlink(missing_ok=True)
        return templates.TemplateResponse(request, "video_creator.html", {
            "request": request,
            "video_url": None,
            "error": f"Video generation failed: {exc}",
            "recent_videos": _get_recent_videos(),
        })

    _VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    final_name = f"{job_id}.mp4"
    final_path = _VIDEOS_DIR / final_name
    shutil.move(str(tmp_out), str(final_path))

    return templates.TemplateResponse(request, "video_creator.html", {
        "request": request,
        "video_url": f"/video/videos/{final_name}",
        "error": None,
        "recent_videos": _get_recent_videos(),
    })


# ---------------------------------------------------------------------------
# Batch upload endpoints
# ---------------------------------------------------------------------------

@router.post("/video/upload-batch")
async def upload_batch(
    files: list[UploadFile] = File(...),
):
    """Accept multiple audio files, save to a batch dir, return file metadata."""
    _cleanup_old_tmp_files()
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex[:12]
    batch_dir = _TMP_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict] = []
    errors: list[str] = []

    for idx, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            errors.append(f"{f.filename}: unsupported format ({ext})")
            continue
        safe_name = f"audio_{idx}{ext}"
        dest = batch_dir / safe_name
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append({
            "index": idx,
            "original_name": f.filename or safe_name,
            "saved_name": safe_name,
            "size_bytes": dest.stat().st_size,
            "path": str(dest),
        })

    meta = {"batch_id": batch_id, "files": saved, "created_at": time.time()}
    with open(batch_dir / "meta.json", "w") as mf:
        json.dump(meta, mf)

    return JSONResponse({
        "batch_id": batch_id,
        "files": [
            {"index": s["index"], "name": s["original_name"], "size_mb": round(s["size_bytes"] / (1024 * 1024), 2)}
            for s in saved
        ],
        "errors": errors,
    })


@router.post("/video/generate-batch")
async def generate_batch(request: Request):
    """Generate videos for selected items in a batch.

    JSON body:
    {
        "batch_id": "...",
        "selected": [0, 1, 2],          // indices to process
        "backgrounds": {                 // per-row background overrides
            "0": "/path/to/image.jpg",
            "1": null                     // use default
        },
        "config": {
            "resolution": "1920x1080",
            "fps": 30,
            "codec": "libx264",
            "audio_bitrate": "192k",
            "image_type": "none",
            "crf": 23
        }
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    batch_id = body.get("batch_id", "")
    selected = body.get("selected", [])
    backgrounds: dict = body.get("backgrounds", {})
    raw_cfg = body.get("config", {})

    batch_dir = _TMP_DIR / batch_id
    meta_path = batch_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Batch not found or expired")

    with open(meta_path) as mf:
        meta = json.load(mf)

    cfg = _validate_config(
        raw_cfg.get("resolution", "1920x1080"),
        raw_cfg.get("fps", 30),
        raw_cfg.get("codec", "libx264"),
        raw_cfg.get("audio_bitrate", "192k"),
        raw_cfg.get("image_type", "none"),
        raw_cfg.get("crf", 23),
    )

    # Music: resolve library id -> file path (batch-wide, optional)
    music_path: str | None = None
    music_volume = max(0, min(100, int(raw_cfg.get("music_volume", 15)))) / 100.0
    raw_music_id = raw_cfg.get("music_id")
    if raw_music_id is not None and str(raw_music_id).strip().isdigit():
        with locked_conn(request) as conn:
            music = repository.get_music(conn, int(raw_music_id))
        if music and Path(music.file_path).exists():
            music_path = music.file_path
        else:
            logger.warning("video_creator: music id %s not found/missing on disk", raw_music_id)

    # Overlay: batch-wide optional text
    overlay_opts = raw_cfg.get("overlay") or {}
    overlay_text = (overlay_opts.get("text") or "").strip()

    files_map = {f["index"]: f for f in meta["files"]}
    _VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    for idx in selected:
        idx = int(idx)
        job_key = f"{batch_id}:{idx}"
        _progress_store.pop(job_key, None)  # fresh log on regenerate
        finfo = files_map.get(idx)
        if not finfo:
            _record_step(job_key, "job.failed", {"reason": "file not found in batch"})
            results.append({"index": idx, "status": "error", "message": "File not found in batch"})
            continue

        audio_path = Path(finfo["path"])
        if not audio_path.exists():
            _record_step(job_key, "job.failed", {"reason": "audio file missing"})
            results.append({"index": idx, "status": "error", "message": "Audio file missing"})
            continue

        bg_path = backgrounds.get(str(idx))
        image_path = _resolve_background_image(bg_path)
        if image_path is None:
            _record_step(job_key, "job.failed", {"reason": "no background image"})
            results.append({"index": idx, "status": "error", "message": "No background image available"})
            continue

        _record_step(job_key, "job.start", {"file": finfo["original_name"]})
        if music_path:
            _record_step(job_key, "music.resolved", {"path": Path(music_path).name,
                                                     "volume": music_volume})

        if overlay_text:
            overlay_png = _TMP_DIR / f"{batch_id}_{idx}_overlay.png"
            rendered = _render_overlay_for_batch(image_path, overlay_text, overlay_opts, overlay_png)
            if rendered is not None:
                image_path = rendered
                _record_step(job_key, "overlay.rendered", {"path": overlay_png.name})
            else:
                _record_step(job_key, "overlay.failed_fallback", {"detail": "using plain background"})

        stem = _sanitize_basename(finfo["original_name"])
        final_path = _unique_video_path(stem)
        tmp_out = _TMP_DIR / f"{batch_id}_{idx}_{uuid.uuid4().hex[:6]}.mp4"
        progress_cb = _make_progress_logger(
            "video_creator.batch", job_key=job_key,
            batch_id=batch_id, index=idx, mode="batch",
        )
        started = time.time()
        try:
            await asyncio.to_thread(
                video_gen.generate_standalone_video,
                str(audio_path), str(image_path), str(tmp_out),
                on_progress=progress_cb,
                music_path=music_path,
                music_volume=music_volume,
                **cfg,
            )
            shutil.move(str(tmp_out), str(final_path))
            _record_step(job_key, "job.done", {"video": final_path.name})
            completed_at = datetime.now()
            results.append({
                "index": idx,
                "status": "done",
                "name": final_path.name,
                "video_url": f"/video/videos/{final_path.name}",
                "size_mb": round(final_path.stat().st_size / (1024 * 1024), 1),
                "elapsed_seconds": round(time.time() - started, 1),
                "completed_at": completed_at.isoformat(timespec="seconds"),
            })
        except Exception as exc:
            tmp_out.unlink(missing_ok=True)
            _record_step(job_key, "job.failed", {"error": str(exc)[:300]})
            results.append({
                "index": idx,
                "status": "error",
                "message": str(exc),
                "elapsed_seconds": round(time.time() - started, 1),
            })

    return JSONResponse({"results": results})


@router.get("/video/progress/{batch_id}")
def get_batch_progress(batch_id: str):
    """Per-file step progress for a running/finished batch (debug UI)."""
    prefix = f"{batch_id}:"
    jobs = {
        key[len(prefix):]: {"status": v["status"], "steps": v["steps"]}
        for key, v in _progress_store.items()
        if key.startswith(prefix)
    }
    return JSONResponse({"jobs": jobs})


# ---------------------------------------------------------------------------
# Background image management
# ---------------------------------------------------------------------------

@router.get("/video/backgrounds")
def list_backgrounds():
    """List available background images (user-uploaded + default)."""
    _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []

    default = settings.default_background_image
    if Path(default).exists():
        items.append({"name": "__default__", "path": default, "is_default": True})

    for f in sorted(_BACKGROUNDS_DIR.iterdir()):
        if f.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS:
            items.append({"name": f.name, "path": str(f), "is_default": False})

    return JSONResponse({"backgrounds": items})


@router.get("/video/backgrounds/preview")
def preview_background(path: str = ""):
    """Serve a background image file for the Configure Each File preview thumbnail.
    Only paths inside the backgrounds dir or matching the configured default are
    allowed; everything else returns 404 to avoid serving arbitrary disk files."""
    if not path:
        raise HTTPException(status_code=400, detail="path query parameter required")
    p = Path(path)
    if not p.is_absolute():
        p = (Path(settings.data_root) / path).resolve()

    default = Path(settings.default_background_image).resolve()
    allowed_roots = [default, _BACKGROUNDS_DIR.resolve()]
    try:
        p_resolved = p.resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="invalid path")
    if not any(p_resolved == root or root in p_resolved.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="path not allowed")

    if not p_resolved.exists() or not p_resolved.is_file():
        raise HTTPException(status_code=404, detail="background not found")

    media = "image/jpeg"
    ext = p_resolved.suffix.lower()
    if ext == ".png":
        media = "image/png"
    elif ext == ".webp":
        media = "image/webp"
    return FileResponse(str(p_resolved), media_type=media)


@router.post("/video/upload-background")
async def upload_background(file: UploadFile = File(...)):
    """Upload a new background image to the backgrounds directory."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {ext}")

    _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    dest = _BACKGROUNDS_DIR / safe_name
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    return JSONResponse({"name": safe_name, "path": str(dest)})


# ---------------------------------------------------------------------------
# Video serving
# ---------------------------------------------------------------------------

@router.get("/video/videos/{filename}")
def serve_video(filename: str):
    safe = Path(filename).name
    file_path = _VIDEOS_DIR / safe
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(file_path), media_type="video/mp4")


@router.get("/video/output/{filename}")
def serve_video_output(filename: str):
    safe = Path(filename).name
    file_path = _TMP_DIR / safe
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(file_path), media_type="video/mp4")

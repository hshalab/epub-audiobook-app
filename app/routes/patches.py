from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
import zipfile
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import audio_merge, drive_export, google_drive, image_overlay, repository, video_gen, video_repository, youtube
from app import db as app_db
from app.chunker import split_into_tts_chunks
from app.config import settings
from app.deps import locked_conn
from app.patch_publishing import enqueue_patch_publish, on_patch_audio_ready, retry_patch_publish
from app.youtube_metadata import get_patch_youtube_override, resolve_patch_youtube_metadata, save_patch_youtube_override
from app.video_config import get_book_video_config

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4"}
# A patch background may be a still image or a looping video clip.
ALLOWED_BACKGROUND_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | video_gen.VIDEO_BACKGROUND_EXTENSIONS


def _build_or_400(build, *args, **kwargs):
    """Run one of the drive_export.build_* functions, turning its ValueError
    (no text, missing voice reference clip, ...) into a 400 instead of a 500."""
    try:
        return build(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/books/{book_id}/patches/{patch_id}/delete")
def delete_patch(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.delete_patch(conn, patch_id)
    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/regenerate")
def regenerate_patch(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.reset_patch(conn, patch_id)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/image")
async def upload_patch_image(
    request: Request, book_id: int, patch_id: int,
    image: UploadFile = File(...),
):
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED_BACKGROUND_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported background format: {ext}")

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")

        img_dir = Path(settings.data_root) / "uploads" / str(book_id) / "patches" / str(patch_id)
        img_dir.mkdir(parents=True, exist_ok=True)

        if patch.image_path:
            Path(patch.image_path).unlink(missing_ok=True)

        filename = f"img_{uuid.uuid4().hex[:8]}{ext}"
        dest = img_dir / filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(image.file, f)

        repository.save_patch_image(conn, patch_id, str(dest))

    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/video")
async def upload_patch_video(
    request: Request, book_id: int, patch_id: int,
    video: UploadFile = File(...),
):
    ext = Path(video.filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video format: {ext}")

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")

    video_dir = Path(settings.data_root) / "books" / str(book_id) / "patch_videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / f"{patch_id}.mp4"
    with open(video_path, "wb") as dest:
        shutil.copyfileobj(video.file, dest)

    with locked_conn(request) as conn:
        existing = conn.execute("SELECT id FROM videos WHERE file_path = ?", (str(video_path),)).fetchone()
        if existing:
            conn.execute(
                "UPDATE videos SET file_size_bytes = ?, updated_at = datetime('now') WHERE id = ?",
                (video_path.stat().st_size, existing["id"]),
            )
            conn.commit()
        else:
            video_repository.insert_video(
                conn, filename=f"patch_{book_id}_{patch_id}.mp4",
                original_name=video.filename or f"patch_{patch_id}.mp4",
                file_path=str(video_path), file_size_bytes=video_path.stat().st_size,
                batch_id=f"patch:{book_id}", source_audio=patch.audio_path,
                background_path=patch.image_path, title=f"Patch {patch.patch_index + 1}",
            )

    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/image/delete")
def delete_patch_image(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.image_path:
            Path(patch.image_path).unlink(missing_ok=True)
        repository.clear_patch_image(conn, patch_id)
    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/image-type")
def update_image_type(
    request: Request, book_id: int, patch_id: int,
    image_type: str = Form(...),
):
    valid = {"static", "zoom-in", "zoom-out", "pan-left", "pan-right"}
    if image_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid image_type: {image_type}")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.update_patch_image_type(conn, patch_id, image_type)
    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.get("/books/{book_id}/patches/{patch_id}/image")
def get_patch_image(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        book = repository.get_book(conn, book_id)
        if patch.image_path and Path(patch.image_path).exists():
            return FileResponse(patch.image_path)
        if book and book.background_image_path and Path(book.background_image_path).exists():
            return FileResponse(book.background_image_path)
        default = settings.default_background_image
        if Path(default).exists():
            return FileResponse(default)
    raise HTTPException(status_code=404, detail="no image available")


def _patch_video_path(book_id: int, patch_id: int) -> Path:
    return Path(settings.data_root) / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"


def _patch_video_title(book, patch) -> str:
    label = patch.name or f"Patch {patch.patch_index + 1}"
    return f"{book.title} - {label}" if book and book.title else label


def _register_patch_video(conn, book, patch, video_path: Path) -> int:
    """Insert (or refresh) a `videos` row for a patch's MP4 so it shows in the
    Video Library and can be handed to the YouTube upload worker. Returns id."""
    existing = conn.execute(
        "SELECT id FROM videos WHERE file_path = ?", (str(video_path),)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE videos SET file_size_bytes = ?, updated_at = datetime('now') WHERE id = ?",
            (video_path.stat().st_size, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    rec = video_repository.insert_video(
        conn,
        filename=f"patch_{book.id}_{patch.id}.mp4",
        original_name=f"{_patch_video_title(book, patch)}.mp4",
        file_path=str(video_path),
        file_size_bytes=video_path.stat().st_size,
        resolution=book.video_resolution or "1920x1080",
        batch_id=f"patch:{book.id}",
        source_audio=patch.audio_path,
        background_path=patch.image_path,
        title=_patch_video_title(book, patch),
    )
    return rec["id"]


def _wants_json(request: Request, ajax: int) -> bool:
    return bool(ajax) or "application/json" in (request.headers.get("accept") or "")


@router.post("/books/{book_id}/patches/{patch_id}/generate-video")
async def generate_patch_video(
    request: Request, book_id: int, patch_id: int,
    upload_youtube: bool = Form(default=False),
    privacy: str = Form(default=""),
    ajax: int = Query(default=0),
):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status != "done" or not patch.audio_path:
            raise HTTPException(status_code=400, detail="Patch audio not ready")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        music_path = None
        if book.music_id is not None:
            music = repository.get_music(conn, book.music_id)
            if music and Path(music.file_path).exists():
                music_path = music.file_path
        video_config = get_book_video_config(conn, book)

    raw_bg = video_gen.resolve_patch_image(patch, book, settings.default_background_image)
    if not raw_bg:
        raise HTTPException(status_code=400, detail="No background image available")

    if video_gen.is_video_background(raw_bg):
        image = raw_bg
        image_type = "none"
    else:
        image = image_overlay.ensure_patch_overlay(book, patch, settings.default_font_path or None) or raw_bg
        image_type = patch.image_type if patch.image_type and patch.image_type != "static" else "none"

    w, h = (book.video_resolution or "1920x1080").split("x")
    resolution = (int(w), int(h))
    fps = book.video_fps or 30

    out_dir = Path(settings.data_root) / "books" / str(book_id) / "patch_videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"{patch_id}.mp4")

    try:
        await asyncio.to_thread(
            video_gen.generate_segment,
            image, patch.audio_path, out_path,
            image_type=image_type,
            resolution=resolution,
            fps=fps,
            use_nvenc=settings.use_nvenc,
            music_path=music_path,
            music_volume=book.music_volume,
            codec=video_config["codec"],
            quality=video_config["quality"],
            audio_bitrate=video_config["audio_bitrate"],
        )
    except Exception as exc:
        if _wants_json(request, ajax):
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)
        raise HTTPException(status_code=500, detail=str(exc))

    video_path = Path(out_path)
    with locked_conn(request) as conn:
        video_db_id = _register_patch_video(conn, book, patch, video_path)

    youtube_status: dict | None = None
    if upload_youtube:
        with locked_conn(request) as conn:
            from app.patch_publishing import seed_patch_video
            seed_patch_video(conn, patch_id, video_db_id, str(video_path))
            youtube_status = retry_patch_publish(conn, patch_id)

    if _wants_json(request, ajax):
        return JSONResponse({
            "status": "done",
            "video_url": f"/books/{book_id}/patches/{patch_id}/video?v={int(time.time())}",
            "youtube": youtube_status,
        })
    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/youtube-upload")
def upload_patch_video_to_youtube(
    request: Request, book_id: int, patch_id: int,
    privacy: str = Form(default=""),
):
    """Push a patch's already-generated MP4 (server-rendered or uploaded from
    Colab/Kaggle) to YouTube via the upload worker. Returns JSON."""
    video_path = _patch_video_path(book_id, patch_id)
    if not video_path.exists():
        raise HTTPException(status_code=400, detail="Chưa có video cho patch này")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        video_db_id = _register_patch_video(conn, book, patch, video_path)

    with locked_conn(request) as conn:
        from app.patch_publishing import seed_patch_video
        seed_patch_video(conn, patch_id, video_db_id, str(video_path))
        status = retry_patch_publish(conn, patch_id)
    return JSONResponse({"status": "queued", "pipeline": status})


@router.get("/books/{book_id}/patches/{patch_id}/overlay-image")
def get_patch_overlay_image(request: Request, book_id: int, patch_id: int):
    """Render (idempotent, cached) and serve the per-patch overlay PNG
    (background + "Book - Patch" text). Powers the row thumbnail, the lightbox
    preview, download, and the batch "generate images" action."""
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")

    overlay = image_overlay.ensure_patch_overlay(book, patch, settings.default_font_path or None)
    if overlay and Path(overlay).exists():
        return FileResponse(str(overlay), media_type="image/png")
    # Fall back to the raw patch/book background so the row still shows something.
    fallback = video_gen.resolve_patch_image(patch, book, settings.default_background_image)
    if fallback and Path(fallback).exists():
        return FileResponse(str(fallback))
    raise HTTPException(status_code=404, detail="Chưa có ảnh nền để tạo overlay")


@router.get("/books/{book_id}/patches/{patch_id}/video")
def get_patch_video(request: Request, book_id: int, patch_id: int):
    video_path = _patch_video_path(book_id, patch_id)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not generated yet")
    return FileResponse(str(video_path), media_type="video/mp4")


# ---------------------------------------------------------------------------
# Chunk manager: per-chunk view, max_chars override, resume-from-chunk,
# Google Drive export/import for Colab/Kaggle synthesis.
# ---------------------------------------------------------------------------


@router.get("/books/{book_id}/patches/{patch_id}/chunks", response_class=HTMLResponse)
def chunk_manager_page(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        book = repository.get_book(conn, book_id)
        worker = request.app.state.worker
        chunks = repository.get_patch_chunk_view(conn, patch, worker)
        exports = repository.list_patch_exports(conn, patch_id)
        sync_targets = repository.list_drive_sync_targets(conn)
    return templates.TemplateResponse(request, "chunk_manager.html", {
        "request": request,
        "book": book,
        "patch": patch,
        "chunks": chunks,
        "exports": exports,
        "sync_targets": sync_targets,
    })


@router.post("/books/{book_id}/patches/{patch_id}/max_chars")
def update_patch_max_chars(
    request: Request, book_id: int, patch_id: int,
    max_chars: str = Form(default=""),
):
    value: int | None = None
    if max_chars.strip():
        try:
            value = int(max_chars)
        except ValueError:
            raise HTTPException(status_code=400, detail="max_chars must be an integer")
        if value < 1:
            raise HTTPException(status_code=400, detail="max_chars must be >= 1")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        ok = repository.set_patch_max_chars(conn, patch_id, value)
    if not ok:
        raise HTTPException(status_code=400, detail="max_chars can only be changed while the patch is pending")
    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/resume_from_chunk")
def resume_patch_from_chunk(
    request: Request, book_id: int, patch_id: int,
    from_index: int = Form(...),
):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        ok = repository.resume_patch_from_chunk(conn, patch_id, from_index)
    if not ok:
        raise HTTPException(status_code=400, detail="patch must be 'failed' to resume from a chunk")
    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/export")
def export_patch_to_drive(request: Request, book_id: int, patch_id: int, sync_target_id: int = Form(...)):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=400, detail="cannot export a patch that is currently processing")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        target = repository.get_drive_sync_target(conn, sync_target_id)
        if target is None:
            raise HTTPException(status_code=400, detail="Sync target not found")

        # Compute the folder name up front so the same name is baked into the notebook and
        # used for the actual Drive folder (folder_name_for_patch has a timestamp, so it must
        # only be called once).
        folder_name = drive_export.folder_name_for_patch(book.title, patch)
        package_dir = _build_or_400(drive_export.build_export_package, conn, patch, drive_folder_name=folder_name, hf_token=settings.hf_token)
        try:
            folder = drive_export.publish_package(package_dir, target["folder_path"], folder_name)
            chunk_count = sum(1 for f in package_dir.iterdir() if f.name.startswith("chunk_") and f.suffix == ".txt")
            repository.create_patch_export(
                conn, patch_id, str(folder), str(folder), chunk_count,
                sync_target_id=target["id"], local_folder_path=str(folder),
            )
        except Exception as exc:
            logger.exception("export to Google Drive Desktop failed for patch %s", patch_id)
            raise HTTPException(status_code=500, detail=f"Drive Desktop export failed: {exc}")
        finally:
            shutil.rmtree(package_dir, ignore_errors=True)

    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)


@router.get("/books/{book_id}/patches/{patch_id}/export/download")
def download_patch_export(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        # Use the same naming convention as the Drive folder so the zip filename
        # matches the folder that would be created on Google Drive.
        folder_name = drive_export.folder_name_for_patch(book.title, patch)
        zip_path = _build_or_400(drive_export.build_export_zip, conn, patch, hf_token=settings.hf_token)
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"{folder_name}.zip",
    )


def _load_batch_patches(conn, book_id: int, patch_ids: list[int]):
    """Validate a multi-patch export selection and return (book, patches sorted by
    patch_index). Raises HTTPException on empty/unknown/processing selections."""
    if not patch_ids:
        raise HTTPException(status_code=400, detail="no patches selected")
    book = repository.get_book(conn, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    patches = []
    for patch_id in dict.fromkeys(patch_ids):  # dedupe, keep order
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail=f"patch {patch_id} not found")
        if patch.status == "processing":
            raise HTTPException(
                status_code=400,
                detail=f"cannot export patch {patch.name or patch.patch_index} while it is processing",
            )
        patches.append(patch)
    return book, sorted(patches, key=lambda p: p.patch_index)


@router.post("/books/{book_id}/patches/export-batch/download")
def download_batch_export(request: Request, book_id: int, patch_ids: list[int] = Form(...)):
    with locked_conn(request) as conn:
        book, patches = _load_batch_patches(conn, book_id, patch_ids)
        # Same convention as the single-patch download: compute the timestamped name
        # once and bake it into the notebook so its fallback matches the zip filename.
        folder_name = drive_export.folder_name_for_batch(book.title, patches)
        zip_path = _build_or_400(
            drive_export.build_batch_export_zip,
            conn, patches, drive_folder_name=folder_name, hf_token=settings.hf_token,
        )
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"{folder_name}.zip",
    )


@router.post("/books/{book_id}/patches/export-batch")
def export_batch_to_drive(request: Request, book_id: int, patch_ids: list[int] = Form(...), sync_target_id: int = Form(...)):
    with locked_conn(request) as conn:
        book, patches = _load_batch_patches(conn, book_id, patch_ids)
        target = repository.get_drive_sync_target(conn, sync_target_id)
        if target is None:
            raise HTTPException(status_code=400, detail="Sync target not found")

        folder_name = drive_export.folder_name_for_batch(book.title, patches)
        package_dir, batch_manifest = _build_or_400(
            drive_export.build_batch_export_package,
            conn, patches, drive_folder_name=folder_name, hf_token=settings.hf_token,
        )
        try:
            batch_folder = drive_export.publish_package(package_dir, target["folder_path"], folder_name)
            for entry in batch_manifest["patches"]:
                patch_folder = batch_folder / entry["folder"]
                repository.create_patch_export(
                    conn, entry["patch_id"], str(patch_folder), str(patch_folder), entry["chunk_count"],
                    sync_target_id=target["id"], local_folder_path=str(patch_folder), commit=False,
                )
            conn.commit()
        except Exception as exc:
            logger.exception("batch export to Google Drive failed for book %s", book_id)
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Drive Desktop export failed: {exc}")
        finally:
            shutil.rmtree(package_dir, ignore_errors=True)

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/patches/export-batch-api")
def export_batch_to_drive_api(request: Request, book_id: int, patch_ids: list[int] = Form(...), account_id: int = Form(...)):
    """Upload the batch package to Google Drive via the Drive API (drive.file scope) so
    the Kaggle notebook can use it. This is the API counterpart of export_batch_to_drive,
    which copies into a local Google Drive Desktop folder - files that arrive on Drive
    that way (or via rclone / manual upload) are invisible to the drive.file scope the
    Kaggle GDRIVE_CREDS secret uses, so Kaggle could never find them. Uploading through
    the app's own API makes the batch (and every result the notebook pushes back) visible
    to those same credentials.

    The account chosen here MUST be the one whose "Copy Kaggle credentials" JSON is stored
    in the Kaggle GDRIVE_CREDS secret: drive.file only reveals files created by that exact
    account."""
    with locked_conn(request) as conn:
        book, patches = _load_batch_patches(conn, book_id, patch_ids)
        if google_drive.get_account(conn, account_id) is None:
            raise HTTPException(status_code=400, detail="Google Drive account not found")

        folder_name = drive_export.folder_name_for_batch(book.title, patches)
        package_dir, batch_manifest = _build_or_400(
            drive_export.build_batch_export_package,
            conn, patches, drive_folder_name=folder_name, hf_token=settings.hf_token,
        )
        try:
            service = google_drive.get_drive_service(conn, account_id)
            root_id = google_drive.get_or_create_root_folder(service)
            batch_folder = google_drive.create_folder(service, folder_name, parent_id=root_id)
            folder_map = google_drive.upload_directory(service, batch_folder["id"], str(package_dir))
            for entry in batch_manifest["patches"]:
                sub = folder_map.get(entry["folder"], batch_folder)
                repository.create_patch_export(
                    conn, entry["patch_id"], sub["id"], sub["link"], entry["chunk_count"],
                    drive_account_id=account_id, commit=False,
                )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            logger.exception("batch export to Google Drive API failed for book %s", book_id)
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Drive API export failed: {exc}")
        finally:
            shutil.rmtree(package_dir, ignore_errors=True)

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/import")
def import_patch_from_drive(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=400, detail="cannot import while the patch is processing")
        export = repository.get_latest_patch_export(conn, patch_id)
        if export is None:
            raise HTTPException(status_code=400, detail="this patch has never been exported")
        if not export.local_folder_path:
            raise HTTPException(status_code=400, detail="Legacy Drive API export: export again through Google Drive Desktop or upload result files manually")
        package_folder = Path(export.local_folder_path)
        if not package_folder.is_dir():
            raise HTTPException(status_code=400, detail="Export folder is unavailable; check Google Drive Desktop or export again")

        text = repository.build_patch_text(conn, patch)
        max_chars = patch.max_chars or settings.tts_max_chars
        expected_chunk_count = len(split_into_tts_chunks(text, max_chars=max_chars))

        chunk_dir = Path(settings.data_root) / "books" / str(book_id) / "patches" / f"{patch_id}_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        # Imported chunks are not LightTTS output — invalidate its reuse marker.
        (chunk_dir / ".light_tts_meta").unlink(missing_ok=True)

        try:
            source_dir = package_folder / "output"
            if not source_dir.is_dir():
                source_dir = package_folder

            imported = 0
            for i in range(expected_chunk_count):
                name = f"chunk_{i:03d}.wav"
                local_path = chunk_dir / name
                if local_path.exists():
                    imported += 1
                    continue
                source_path = source_dir / name
                if not source_path.is_file():
                    break  # first missing chunk: stop here, contiguous prefix ends
                shutil.copy2(source_path, local_path)
                imported += 1

            if imported >= expected_chunk_count:
                book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
                audio_path = str(book_dir / f"{patch_id}.wav")
                chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(expected_chunk_count)]
                audio_merge.concat_wavs(chunk_paths, audio_path)
                # Chunk files (downloaded from Drive) are intentionally kept on disk, same as
                # the local synthesis path in worker.py - not auto-deleted after merge.
                repository.mark_patch_done(conn, patch_id, audio_path)
                on_patch_audio_ready(conn, patch_id)
                repository.update_patch_export(conn, export.id, status="imported", imported_chunk_count=imported)
            else:
                repository.update_patch_chunk_progress(conn, patch_id, imported)
                repository.update_patch_export(conn, export.id, status="partially_imported", imported_chunk_count=imported)
        except Exception as exc:
            logger.exception("import from Google Drive Desktop failed for patch %s", patch_id)
            repository.update_patch_export(conn, export.id, status="failed", error_message=str(exc))
            raise HTTPException(status_code=500, detail=f"Drive Desktop import failed: {exc}")

    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)


@router.post("/books/{book_id}/patches/{patch_id}/background")
async def set_patch_background(
    request: Request,
    book_id: int,
    patch_id: int,
    background_path: str = Form(default=""),
):
    """Set patch background to an existing library path (empty = clear to book default)."""
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        path = background_path.strip() or None
        if path:
            from app.routes.books import _list_backgrounds
            allowed = {item["path"] for item in _list_backgrounds()}
            if path not in allowed:
                raise HTTPException(status_code=400, detail="unknown background path")
        repository.save_patch_image(conn, patch_id, path)
    return JSONResponse({"ok": True, "patch_id": patch_id})


@router.post("/books/{book_id}/patches/{patch_id}/upload-audio")
async def upload_patch_audio(
    request: Request,
    book_id: int,
    patch_id: int,
    audio: UploadFile = File(...),
):
    """Upload a completed audio file for a patch and mark it as done."""
    ext = Path(audio.filename or "").suffix.lower()
    if ext not in {".wav", ".mp3", ".ogg", ".flac"}:
        raise HTTPException(status_code=400, detail=f"Unsupported audio format: {ext}")

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")

    audio_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{patch_id}.wav"
    with open(audio_path, "wb") as dest:
        shutil.copyfileobj(audio.file, dest)

    with locked_conn(request) as conn:
        repository.mark_patch_done(conn, patch_id, str(audio_path))
        on_patch_audio_ready(conn, patch_id)

    return JSONResponse({"ok": True, "patch_id": patch_id})


def _youtube_patch(conn, book_id, patch_id):
    book = repository.get_book(conn, book_id)
    patch = repository.get_patch(conn, patch_id)
    if not book or not patch or patch.book_id != book_id:
        raise HTTPException(404, "patch not found")
    return book, patch


@router.get("/books/{book_id}/youtube-metadata-preview")
def youtube_metadata_preview(request: Request, book_id: int, patch_id: int | None = None):
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        patch = repository.get_patch(conn, patch_id) if patch_id else next(iter(repository.list_patches(conn, book_id)), None)
        if not book or not patch or patch.book_id != book_id:
            raise HTTPException(404, "patch not found")
        return resolve_patch_youtube_metadata(book, patch, get_patch_youtube_override(conn, patch.id))


@router.get("/books/{book_id}/patches/{patch_id}/youtube-metadata")
def get_youtube_metadata(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        book, patch = _youtube_patch(conn, book_id, patch_id)
        override = get_patch_youtube_override(conn, patch_id)
        pipeline = conn.execute("SELECT stage,last_error,thumbnail_path,video_path,thumbnail_status,video_status,upload_status,playlist_status FROM patch_pipeline WHERE patch_id = ?", (patch_id,)).fetchone()
        return {"metadata": resolve_patch_youtube_metadata(book, patch, override), "override": override, "pipeline": dict(pipeline) if pipeline else {}}


@router.post("/books/{book_id}/patches/{patch_id}/youtube-metadata")
async def save_youtube_metadata(request: Request, book_id: int, patch_id: int):
    data = await request.json()
    with locked_conn(request) as conn:
        book, patch = _youtube_patch(conn, book_id, patch_id)
        try:
            save_patch_youtube_override(conn, patch_id, data)
            return resolve_patch_youtube_metadata(book, patch, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.post("/books/{book_id}/patches/{patch_id}/publish")
async def publish_patch(request: Request, book_id: int, patch_id: int):
    data = await request.json()
    with locked_conn(request) as conn:
        book, patch = _youtube_patch(conn, book_id, patch_id)
        if not youtube.is_configured() or not youtube.get_creds_from_db(conn):
            raise HTTPException(400, "YouTube connection is required")
        from app.upload_worker import upload_worker
        worker = upload_worker
        if worker is None or not worker.get_status().get("running"):
            raise HTTPException(503, "Upload worker is unavailable")
        override = {k: v for k, v in data.items() if k != "force_new"}
        if any(k in {"title", "description", "genre_tags", "tags", "privacy_status", "playlist"} for k in override):
            save_patch_youtube_override(conn, patch_id, override)
        effective_override = get_patch_youtube_override(conn, patch_id)
        metadata = resolve_patch_youtube_metadata(book, patch, effective_override)
        pipeline = enqueue_patch_publish(conn, patch_id, force_new=bool(data.get("force_new")))
    return {"metadata": metadata, "pipeline": pipeline}


@router.post("/books/{book_id}/patches/{patch_id}/publish/retry")
def retry_publish_patch(request: Request, book_id: int, patch_id: int, force_new: bool = False):
    conn = request.app.state.conn
    with request.app.state.db_lock:
        _youtube_patch(conn, book_id, patch_id)
        database = conn.execute("PRAGMA database_list").fetchone()[2]
    if not database or database == ":memory:":
        with request.app.state.db_lock:
            return enqueue_patch_publish(conn, patch_id, force_new=True) if force_new else retry_patch_publish(conn, patch_id)
    retry_conn = app_db.connect(database)
    try:
        return enqueue_patch_publish(retry_conn, patch_id, force_new=True) if force_new else retry_patch_publish(retry_conn, patch_id)
    finally:
        retry_conn.close()


@router.post("/books/{book_id}/patches/{patch_id}/import-local")
async def import_patch_from_upload(
    request: Request,
    book_id: int,
    patch_id: int,
    files: list[UploadFile] = File(...),
):
    """Import synthesized audio from uploaded files - no Google Drive connection needed.

    Accepts either the individual chunk_NNN.wav files, or a single .zip containing them
    (e.g. what you'd download after running the notebook on another Google account). Used
    for the fully-offline round trip: download package locally -> run on any Colab/Kaggle
    account -> upload the resulting .wav files back here.
    """
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=400, detail="cannot import while the patch is processing")

        text = repository.build_patch_text(conn, patch)
        max_chars = patch.max_chars or settings.tts_max_chars
        expected_chunk_count = len(split_into_tts_chunks(text, max_chars=max_chars))

        chunk_dir = Path(settings.data_root) / "books" / str(book_id) / "patches" / f"{patch_id}_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        # Uploaded chunks are not LightTTS output — invalidate its reuse marker.
        (chunk_dir / ".light_tts_meta").unlink(missing_ok=True)

        # Pull every chunk_NNN.wav out of the uploads (loose .wav files and/or .zip archives)
        # and drop them into the chunk dir, keeping only names we actually expect.
        wanted = {f"chunk_{i:03d}.wav" for i in range(expected_chunk_count)}
        saved = 0
        for upload in files:
            name = Path(upload.filename or "").name
            if name.lower().endswith(".zip"):
                tmp_zip = chunk_dir / f".upload_{uuid.uuid4().hex[:8]}.zip"
                try:
                    with open(tmp_zip, "wb") as out:
                        shutil.copyfileobj(upload.file, out)
                    with zipfile.ZipFile(tmp_zip) as zf:
                        for member in zf.namelist():
                            base = Path(member).name
                            if base in wanted:
                                with zf.open(member) as src, open(chunk_dir / base, "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                                saved += 1
                finally:
                    tmp_zip.unlink(missing_ok=True)
            elif name in wanted:
                with open(chunk_dir / name, "wb") as out:
                    shutil.copyfileobj(upload.file, out)
                saved += 1

        if saved == 0:
            raise HTTPException(
                status_code=400,
                detail="no matching chunk_NNN.wav files found in the upload",
            )

        # Same contiguous-prefix logic as the Drive import: count how many chunks we have
        # in order, merge into the patch WAV if the whole set is present, else just record
        # progress so the local worker (or another upload) can finish the rest.
        imported = 0
        for i in range(expected_chunk_count):
            if (chunk_dir / f"chunk_{i:03d}.wav").exists():
                imported += 1
            else:
                break

        if imported >= expected_chunk_count:
            book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
            audio_path = str(book_dir / f"{patch_id}.wav")
            chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(expected_chunk_count)]
            audio_merge.concat_wavs(chunk_paths, audio_path)
            repository.mark_patch_done(conn, patch_id, audio_path)
            on_patch_audio_ready(conn, patch_id)
        else:
            repository.update_patch_chunk_progress(conn, patch_id, imported)

    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)

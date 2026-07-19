from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import audio_merge, drive_export, google_drive, image_overlay, repository, video_gen
from app.chunker import split_into_tts_chunks
from app.config import settings
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


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
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {ext}")

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


@router.post("/books/{book_id}/patches/{patch_id}/generate-video")
async def generate_patch_video(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status != "done" or not patch.audio_path:
            raise HTTPException(status_code=400, detail="Patch audio not ready")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")

    image = image_overlay.ensure_patch_overlay(book, patch, settings.default_font_path or None)
    if not image:
        image = video_gen.resolve_patch_image(patch, book, settings.default_background_image)
    if not image:
        raise HTTPException(status_code=400, detail="No background image available")

    w, h = (book.video_resolution or "1920x1080").split("x")
    resolution = (int(w), int(h))
    fps = book.video_fps or 30
    image_type = patch.image_type if patch.image_type and patch.image_type != "static" else "none"

    out_dir = Path(settings.data_root) / "books" / str(book_id) / "patch_videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"{patch_id}.mp4")

    marquee_p = image_overlay.get_marquee_path(book_id, patch_id)
    marquee_m_p = image_overlay.get_marquee_meta_path(book_id, patch_id)
    seg_marquee_path: str | None = None
    seg_marquee_meta: dict | None = None
    if marquee_p.exists() and marquee_m_p.exists():
        try:
            import json
            seg_marquee_meta = json.loads(marquee_m_p.read_text(encoding="utf-8"))
            seg_marquee_path = str(marquee_p)
        except Exception:
            pass

    try:
        await asyncio.to_thread(
            video_gen.generate_segment,
            image, patch.audio_path, out_path,
            image_type=image_type,
            resolution=resolution,
            fps=fps,
            use_nvenc=settings.use_nvenc,
            marquee_path=seg_marquee_path,
            marquee_meta=seg_marquee_meta,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return RedirectResponse(url=f"/books/{book_id}/patches/build", status_code=303)


@router.get("/books/{book_id}/patches/{patch_id}/video")
def get_patch_video(request: Request, book_id: int, patch_id: int):
    video_path = Path(settings.data_root) / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"
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
        drive_connected = google_drive.any_account_connected(conn)
    return templates.TemplateResponse(request, "chunk_manager.html", {
        "request": request,
        "book": book,
        "patch": patch,
        "chunks": chunks,
        "exports": exports,
        "drive_connected": drive_connected,
        "drive_configured": google_drive.is_configured(conn),
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
def export_patch_to_drive(request: Request, book_id: int, patch_id: int):
    # Mirrors worker.py's convention of holding db_lock for the full duration of a Google
    # API call (see the youtube upload in _process_book_job) - simpler than fine-grained
    # locking, acceptable for a single-user local app.
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=400, detail="cannot export a patch that is currently processing")
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        try:
            account = google_drive.pick_export_account(conn)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Compute the folder name up front so the same name is baked into the notebook and
        # used for the actual Drive folder (folder_name_for_patch has a timestamp, so it must
        # only be called once).
        folder_name = drive_export.folder_name_for_patch(book.title, patch)
        package_dir = _build_or_400(drive_export.build_export_package, conn, patch, drive_folder_name=folder_name, hf_token=settings.hf_token)
        try:
            service = google_drive.get_drive_service(conn, account["id"])
            root_id = google_drive.get_or_create_root_folder(service)
            folder = google_drive.create_folder(service, folder_name, parent_id=root_id)
            for f in sorted(package_dir.iterdir()):
                google_drive.upload_file(service, folder["id"], str(f))
            chunk_count = sum(1 for f in package_dir.iterdir() if f.name.startswith("chunk_") and f.suffix == ".txt")
            repository.create_patch_export(conn, patch_id, folder["id"], folder["link"], chunk_count, drive_account_id=account["id"])
        except Exception as exc:
            logger.exception(
                "export to Google Drive failed for patch %s (account %s)",
                patch_id, account.get("account_email") or account["id"],
            )
            raise HTTPException(status_code=500, detail=f"Drive export failed: {exc}")
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
def export_batch_to_drive(request: Request, book_id: int, patch_ids: list[int] = Form(...)):
    with locked_conn(request) as conn:
        book, patches = _load_batch_patches(conn, book_id, patch_ids)
        # One account per batch: a notebook run mounts exactly one Drive, so the whole
        # batch must land in a single account; rotation happens across batches/exports.
        try:
            account = google_drive.pick_export_account(conn)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        folder_name = drive_export.folder_name_for_batch(book.title, patches)
        package_dir, batch_manifest = _build_or_400(
            drive_export.build_batch_export_package,
            conn, patches, drive_folder_name=folder_name, hf_token=settings.hf_token,
        )
        try:
            service = google_drive.get_drive_service(conn, account["id"])
            root_id = google_drive.get_or_create_root_folder(service)
            batch_folder = google_drive.create_folder(service, folder_name, parent_id=root_id)
            folder_map = google_drive.upload_directory(service, batch_folder["id"], str(package_dir))
            # Only record the exports after every upload succeeded, so a mid-upload
            # failure can't leave patch_export rows pointing at a half-filled folder.
            # Each patch points at its own subfolder, which is exactly the layout the
            # existing per-patch "Import results from Drive" flow expects.
            for entry in batch_manifest["patches"]:
                info = folder_map[entry["folder"]]
                repository.create_patch_export(
                    conn, entry["patch_id"], info["id"], info["link"], entry["chunk_count"],
                    drive_account_id=account["id"],
                )
        except Exception as exc:
            logger.exception(
                "batch export to Google Drive failed for book %s (account %s)",
                book_id, account.get("account_email") or account["id"],
            )
            raise HTTPException(status_code=500, detail=f"Drive export failed: {exc}")
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
            raise HTTPException(status_code=400, detail="this patch has never been exported to Drive")
        # Resolve the account before the try-block below: a disconnected account is a
        # user-fixable 400, not something that should mark the export as failed.
        try:
            account = google_drive.resolve_import_account(conn, export.drive_account_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        text = repository.build_patch_text(conn, patch)
        max_chars = patch.max_chars or settings.tts_max_chars
        expected_chunk_count = len(split_into_tts_chunks(text, max_chars=max_chars))

        chunk_dir = Path(settings.data_root) / "books" / str(book_id) / "patches" / f"{patch_id}_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        try:
            service = google_drive.get_drive_service(conn, account["id"])
            # The notebook writes chunk_NNN.wav into an "output" subfolder of the exported
            # folder (see colab_kaggle_tts_template.ipynb) - look there first, falling back
            # to the export folder's top level for exports made before this existed.
            output_folder_id = google_drive.find_subfolder(service, export.drive_folder_id, "output")
            list_from_id = output_folder_id or export.drive_folder_id
            drive_files = {f["name"]: f["id"] for f in google_drive.list_files(service, list_from_id)}

            imported = 0
            for i in range(expected_chunk_count):
                name = f"chunk_{i:03d}.wav"
                local_path = chunk_dir / name
                if local_path.exists():
                    imported += 1
                    continue
                if name not in drive_files:
                    break  # first missing chunk: stop here, contiguous prefix ends
                google_drive.download_file(service, drive_files[name], str(local_path))
                imported += 1

            if imported >= expected_chunk_count:
                book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
                audio_path = str(book_dir / f"{patch_id}.wav")
                chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(expected_chunk_count)]
                audio_merge.merge_chunk_files_to_patch(chunk_paths, audio_path)
                # Chunk files (downloaded from Drive) are intentionally kept on disk, same as
                # the local synthesis path in worker.py - not auto-deleted after merge.
                repository.mark_patch_done(conn, patch_id, audio_path)
                repository.update_patch_export(conn, export.id, status="imported", imported_chunk_count=imported)
            else:
                repository.update_patch_chunk_progress(conn, patch_id, imported)
                repository.update_patch_export(conn, export.id, status="partially_imported", imported_chunk_count=imported)
        except Exception as exc:
            logger.exception("import from Google Drive failed for patch %s", patch_id)
            repository.update_patch_export(conn, export.id, status="failed", error_message=str(exc))
            raise HTTPException(status_code=500, detail=f"Drive import failed: {exc}")

    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)


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
            audio_merge.merge_chunk_files_to_patch(chunk_paths, audio_path)
            repository.mark_patch_done(conn, patch_id, audio_path)
        else:
            repository.update_patch_chunk_progress(conn, patch_id, imported)

    return RedirectResponse(url=f"/books/{book_id}/patches/{patch_id}/chunks", status_code=303)

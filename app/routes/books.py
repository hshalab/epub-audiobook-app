from __future__ import annotations

import logging
import shutil
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import google_drive, repository, video_gen
from app.config import settings
from app.deps import locked_conn
from app.epub_parser import parse_epub
from app.normalization import NormalizationOptions, normalize_text

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
# Book backgrounds may be a still image or a looping video clip.
ALLOWED_BACKGROUND_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | video_gen.VIDEO_BACKGROUND_EXTENSIONS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/books", response_class=HTMLResponse)
def list_books(request: Request, page: int = Query(default=1, ge=1)):
    per_page = settings.default_page_size
    with locked_conn(request) as conn:
        books, total, total_pages = repository.list_books(conn, page=page, per_page=per_page)
        patch_counts = {
            b.id: {
                "total": len(repository.list_patches(conn, b.id)),
                "done": sum(1 for p in repository.list_patches(conn, b.id) if p.status == "done"),
                "pending": sum(1 for p in repository.list_patches(conn, b.id) if p.status == "pending"),
            }
            for b in books
        }
    return templates.TemplateResponse(
        request, "book_list.html", {
            "books": books,
            "patch_counts": patch_counts,
            "page": page,
            "total_pages": total_pages,
        }
    )


@router.get("/books/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@router.post("/books/parse-epub")
async def parse_epub_preview(request: Request, epub_file: UploadFile = File(...)):
    """Parse an EPUB and return chapter list as JSON without creating a book."""
    uploads_dir = Path(settings.data_root) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = uploads_dir / f"_tmp_preview_{epub_file.filename}"
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(epub_file.file, f)

        chapters = parse_epub(str(tmp_path))
        return JSONResponse({
            "filename": epub_file.filename,
            "title": Path(epub_file.filename).stem,
            "chapters": [
                {
                    "index": idx,
                    "title": ch.title,
                    "char_count": ch.char_count,
                    "text_excerpt": ch.text[:300],
                }
                for idx, ch in enumerate(chapters)
            ],
        })
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/books/upload")
async def upload_book(
    request: Request,
    epub_file: UploadFile = File(...),
    patch_size: int = Form(default=10),
    excluded_chapters: str | None = Form(default=None),
):
    """Upload only handles what's needed to start parsing a book: the EPUB
    file, chapter selection, and patch size. Background image and voice
    clone reference are configured afterwards on the book detail page
    (Studio), which already offers a richer, library-backed picker for both."""
    uploads_dir = Path(settings.data_root) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    tmp_epub_path = uploads_dir / f"_tmp_{epub_file.filename}"
    with open(tmp_epub_path, "wb") as f:
        shutil.copyfileobj(epub_file.file, f)

    chapters = parse_epub(str(tmp_epub_path))
    title = Path(epub_file.filename).stem

    with locked_conn(request) as conn:
        book = repository.create_book(
            conn,
            title=title,
            original_filename=epub_file.filename,
            epub_path="",  # finalized below once the book id (and thus its folder name) is known
            patch_size=patch_size,
            chapters=chapters,
            background_image_path=None,
        )

        if excluded_chapters:
            for idx_str in excluded_chapters.split(","):
                idx_str = idx_str.strip()
                if idx_str.isdigit():
                    repository.set_chapter_excluded(conn, book.id, int(idx_str), True)

        final_epub_path = uploads_dir / f"{book.id}.epub"
        tmp_epub_path.rename(final_epub_path)

        conn.execute(
            "UPDATE book SET epub_path = ? WHERE id = ?",
            (str(final_epub_path), book.id),
        )
        conn.commit()

    return RedirectResponse(url=f"/books/{book.id}", status_code=303)


@router.get("/books/{book_id}", response_class=HTMLResponse)
def book_detail(request: Request, book_id: int):
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        patch_list = repository.list_patches(conn, book_id)
        rules = repository.list_replace_rules(conn, book_id)
        chapters = repository.list_chapters(conn, book_id)
        last_error = repository.get_last_error_for_book(conn, book_id)
        sync_targets = repository.list_drive_sync_targets(conn)
        drive_accounts = google_drive.list_accounts(conn)
        music_list = repository.list_music(conn)
        current_music = repository.get_music(conn, book.music_id) if book and book.music_id else None
    has_active_patches = any(p.status in ("pending", "processing") for p in patch_list)

    # Which patches already have a rendered MP4 on disk (server-side or uploaded
    # from Colab/Kaggle) — so the row shows video-ready state on first load.
    patch_videos_dir = Path(settings.data_root) / "books" / str(book_id) / "patch_videos"
    patch_video_ids = {
        p.id for p in patch_list
        if (patch_videos_dir / f"{p.id}.mp4").exists()
    }

    from app import youtube
    youtube_configured = youtube.is_configured()

    from app import image_overlay
    overlay_cfg = image_overlay.parse_overlay_config(book.overlay_config) if book else image_overlay.get_default_overlay_config()

    from app.routes.voices import ALLOWED_AUDIO_EXTENSIONS as _voice_exts, _voices_dir
    voices = [
        f.name for f in sorted(_voices_dir().iterdir())
        if f.is_file() and f.suffix.lower() in _voice_exts
    ]
    current_voice_name = Path(book.voice_clip_path).name if book and book.voice_clip_path else None

    return templates.TemplateResponse(
        request, "book_detail.html", {
            "book": book,
            "patches": patch_list,
            "rules": rules,
            "chapters": chapters,
            "last_error": last_error,
            "has_active_patches": has_active_patches,
            "sync_targets": sync_targets,
            "drive_accounts": drive_accounts,
            "music_list": music_list,
            "current_music": current_music,
            "backgrounds": _list_backgrounds(),
            "overlay_cfg": overlay_cfg,
            "voices": voices,
            "current_voice_name": current_voice_name,
            "default_max_chars": settings.tts_max_chars,
            "patch_video_ids": patch_video_ids,
            "youtube_configured": youtube_configured,
            "youtube_auto_upload": settings.youtube_auto_upload,
            "youtube_default_privacy": settings.youtube_default_privacy,
        }
    )


@router.post("/books/{book_id}/video-settings")
def update_video_settings(
    request: Request, book_id: int,
    video_resolution: str = Form(default=""),
    video_fps: str = Form(default=""),
    default_image_animation: str = Form(default=""),
):
    """Persist the book-wide video config (resolution / fps / default animation)
    used by per-patch video generation. Returns JSON for the async config modal."""
    valid_res = {"1920x1080", "1280x720", "854x480"}
    valid_fps = {24, 30, 60}
    valid_anim = {"none", "static", "zoom-in", "zoom-out", "pan-left", "pan-right"}

    res = video_resolution if video_resolution in valid_res else None
    fps: int | None = None
    if video_fps.strip():
        try:
            fps_val = int(video_fps)
        except ValueError:
            raise HTTPException(status_code=400, detail="fps không hợp lệ")
        fps = fps_val if fps_val in valid_fps else None
    anim = default_image_animation if default_image_animation in valid_anim else None

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        repository.update_book_video_settings(
            conn, book_id,
            video_resolution=res, video_fps=fps, default_image_animation=anim,
        )
    return JSONResponse({
        "status": "saved",
        "video_resolution": res or (book.video_resolution or "1920x1080"),
        "video_fps": fps or (book.video_fps or 30),
    })


@router.get("/books/{book_id}/status")
def book_status(request: Request, book_id: int):
    """Lightweight JSON endpoint for polling status without reloading the page."""
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        patch_list = repository.list_patches(conn, book_id)
    worker = request.app.state.worker
    live_chunk_index = (
        worker.current_chunk_index
        if getattr(worker, "current_patch_id", None) is not None
        else 0
    )
    live_chunk_count = getattr(worker, "current_chunk_count", 0)
    return JSONResponse({
        "book_status": book.status,
        "has_final_audio": bool(book.final_audio_path),
        "has_active_patches": any(p.status in ("pending", "processing") for p in patch_list),
        "patches": [
            {
                "id": p.id,
                "status": p.status,
                "error_message": p.error_message,
                "chunk_count": p.chunk_count,
                "next_chunk_index": (
                    live_chunk_index
                    if (p.status == "processing"
                        and getattr(worker, "current_patch_id", None) == p.id)
                    else p.next_chunk_index
                ),
            }
            for p in patch_list
        ],
        "current_chunk_count": live_chunk_count,
    })


@router.post("/books/{book_id}/video")
def trigger_video(request: Request, book_id: int):
    """Enqueue a video book_job. Video generation is now handled by the worker
    (background, non-blocking). If the book has no final audio yet, or a video
    book_job already exists in any status, this is a no-op that just redirects."""
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None or not book.final_audio_path:
            return RedirectResponse(url=f"/books/{book_id}", status_code=303)
        if not book.background_image_path:
            return RedirectResponse(url=f"/books/{book_id}", status_code=303)
        existing = repository.get_book_job(conn, book_id, "video")
        if existing is not None:
            return RedirectResponse(url=f"/books/{book_id}", status_code=303)
        repository.enqueue_book_job(conn, book_id, "video")

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/music")
def update_book_music(
    request: Request,
    book_id: int,
    music_id: str = Form(default=""),
    music_volume: int = Form(default=15),
):
    from app import image_overlay
    mid: int | None = None
    if music_id.strip().isdigit():
        mid = int(music_id.strip())
    vol = max(0, min(100, music_volume)) / 100.0
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        repository.set_book_music(conn, book_id, mid, vol)
        book = repository.get_book(conn, book_id)
        patches = repository.list_patches(conn, book_id)
    font_path = settings.default_font_path or None
    for patch in patches:
        try:
            image_overlay.ensure_patch_overlay(book, patch, font_path)
        except Exception:
            pass
    if request.headers.get("X-Requested-With") == "autosave":
        return {"status": "ok"}
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/rename")
def rename_book(request: Request, book_id: int, title: str = Form(...)):
    new_title = title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Tên không được để trống")
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy sách")
        repository.rename_book(conn, book_id, new_title)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/overlay-config")
async def update_overlay_config(request: Request, book_id: int):
    from app import image_overlay

    values = await request.form()
    cfg = image_overlay.overlay_cfg_from_values(values)

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        conn.execute(
            "UPDATE book SET overlay_config = ?, updated_at = ? WHERE id = ?",
            (json.dumps(cfg, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), book_id),
        )
        conn.commit()
        book = repository.get_book(conn, book_id)
        patches = repository.list_patches(conn, book_id)

    for patch in patches:
        try:
            image_overlay.render_patch_overlay(book, patch, cfg, None)
        except Exception as exc:
            logger.warning("overlay-config: re-render failed for patch %s: %s", patch.id, exc)
    if request.headers.get("X-Requested-With") == "autosave":
        return {"status": "ok"}
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.get("/books/{book_id}/overlay-preview")
def overlay_preview(request: Request, book_id: int):
    """Render the overlay preview PNG.

    Without params it renders the saved config. With `live=1` the remaining
    query params (same names as the overlay form fields) override the saved
    config, so the studio can preview unsaved edits. `background_path` (must
    be a known background) previews a different image before saving it.

    The response carries an `X-Overlay-Rect` header with the drawn text-block
    rect so the studio can place its drag handle exactly on the text.
    """
    from app import image_overlay
    from io import BytesIO
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        patches = repository.list_patches(conn, book_id)
    sample_patch = next((p for p in patches if p.audio_path), None) or (patches[0] if patches else None)
    patch_label = (sample_patch.name or str(sample_patch.patch_index)) if sample_patch else "Patch 1"

    params = request.query_params
    if params.get("live"):
        cfg = image_overlay.overlay_cfg_from_values(params)
    else:
        cfg = image_overlay.parse_overlay_config(book.overlay_config)

    bg = None
    requested_bg = params.get("background_path", "").strip()
    if requested_bg:
        allowed = {item["path"] for item in _list_backgrounds()}
        if requested_bg in allowed and Path(requested_bg).exists():
            bg = Path(requested_bg)
    if bg is None:
        bg = image_overlay._resolve_background(book)
    if bg is None:
        raise HTTPException(status_code=400, detail="chưa có background image")

    from PIL import Image
    img = Image.open(str(bg)).convert("RGB")
    text = cfg.get("text") or f"{book.title} - {patch_label}"
    lines = image_overlay.build_overlay_lines(img, text, cfg)
    img, rect = image_overlay.render_overlay_with_rect(img, lines, cfg)
    marquee_cfg = cfg.get("marquee") or {}
    if marquee_cfg.get("enabled"):
        img = image_overlay.composite_marquee_preview(img, text, marquee_cfg)
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    rect_header = json.dumps({
        "x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3],
        "img_w": img.size[0], "img_h": img.size[1],
    })
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"X-Overlay-Rect": rect_header, "Cache-Control": "no-store"},
    )


@router.post("/books/{book_id}/delete")
def delete_book(request: Request, book_id: int):
    with locked_conn(request) as conn:
        ok = repository.delete_book(conn, book_id, settings.data_root)
    if not ok:
        raise HTTPException(status_code=404, detail=f"book {book_id} not found")
    return RedirectResponse(url="/books", status_code=303)


@router.post("/books/{book_id}/voice-select")
def select_voice(
    request: Request, book_id: int,
    voice_name: str = Form(default=""),
    voice_transcript: str = Form(default=""),
):
    """Set the book's TTS reference voice clone from the /voices library.

    This is the same file the studio's mix preview plays — picking it there
    both previews it and sets book.voice_clip_path, which the worker passes
    to the TTS engine as reference_wav_path. voice_transcript is the exact
    words spoken in that clip, which improves cloning accuracy.
    """
    from app.routes.voices import ALLOWED_AUDIO_EXTENSIONS as _voice_exts, _voices_dir

    name = voice_name.strip()
    path: str | None = None
    if name:
        candidate = _voices_dir() / name
        if "/" in name or "\\" in name or ".." in name or candidate.suffix.lower() not in _voice_exts:
            raise HTTPException(status_code=400, detail="Tên voice không hợp lệ")
        if not candidate.exists():
            raise HTTPException(status_code=400, detail="Không tìm thấy voice")
        path = str(candidate)

    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        repository.set_book_voice_clip(conn, book_id, path, voice_transcript.strip() or None)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/background-image-select")
def select_background_image(
    request: Request, book_id: int,
    background_path: str = Form(default=""),
):
    from app import image_overlay

    path: str | None = background_path.strip() or None
    if path and not Path(path).exists():
        raise HTTPException(status_code=400, detail="File ảnh không tồn tại")

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        conn.execute(
            "UPDATE book SET background_image_path = ?, updated_at = ? WHERE id = ?",
            (path, datetime.now(timezone.utc).isoformat(), book_id),
        )
        conn.commit()
        book = repository.get_book(conn, book_id)
        patches = repository.list_patches(conn, book_id)

    font_path = settings.default_font_path or None
    for patch in patches:
        try:
            image_overlay.ensure_patch_overlay(book, patch, font_path)
        except Exception:
            pass
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/background-image")
async def upload_background_image(
    request: Request, book_id: int,
    image: UploadFile = File(...),
):
    ext = Path(image.filename or "").suffix.lower()
    if ext not in ALLOWED_BACKGROUND_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Định dạng không hỗ trợ: {ext}")

    from app import image_overlay

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")

        uploads_dir = Path(settings.data_root) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        if book.background_image_path:
            Path(book.background_image_path).unlink(missing_ok=True)

        filename = f"{book_id}_bg_{uuid.uuid4().hex[:8]}{ext}"
        dest = uploads_dir / filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(image.file, f)

        conn.execute(
            "UPDATE book SET background_image_path = ?, updated_at = ? WHERE id = ?",
            (str(dest), datetime.now(timezone.utc).isoformat(), book_id),
        )
        conn.commit()
        book = repository.get_book(conn, book_id)

        patches = repository.list_patches(conn, book_id)

    # A video background is a plain looping backdrop with no baked-in text, so
    # there are no per-patch overlays to pre-render.
    if not video_gen.is_video_background(dest):
        font_path = settings.default_font_path or None
        for patch in patches:
            try:
                image_overlay.ensure_patch_overlay(book, patch, font_path)
            except Exception:
                pass

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


def _parse_ids(raw: str | None) -> list[int]:
    """Parse a comma-separated list of integer ids, ignoring empty / non-integer tokens."""
    if not raw:
        return []
    out: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


@router.get("/books/{book_id}/chapters/preview")
def preview_chapters(
    request: Request,
    book_id: int,
    ids: str | None = Query(default=None, description="Comma-separated chapter_index values"),
    preview_chars: int = Query(default=500, ge=1, le=100_000),
):
    """Return a JSON list of {chapter_index, title, char_count, text_excerpt} for the
    requested chapters. Unknown indices are silently skipped."""
    if ids is None or ids.strip() == "":
        raise HTTPException(status_code=400, detail="'ids' query parameter is required")

    indices = _parse_ids(ids)
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        chapters = repository.get_chapters_by_indices(conn, book_id, indices)

    return JSONResponse([
        {
            "chapter_index": ch.chapter_index,
            "title": ch.title,
            "char_count": ch.char_count,
            "text_excerpt": ch.text[:preview_chars],
        }
        for ch in chapters
    ])


@router.get("/books/{book_id}/chapters/{chapter_index}/text", response_class=PlainTextResponse)
def get_chapter_text(request: Request, book_id: int, chapter_index: int):
    """Return the full text of a single chapter as text/plain."""
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        text = repository.get_chapter_text(conn, book_id, chapter_index)
    if text is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_index} not found")
    return PlainTextResponse(text)


@router.get("/books/{book_id}/chapters/preview-ui", response_class=HTMLResponse)
def preview_chapters_ui(
    request: Request,
    book_id: int,
    ids: str | None = Query(default=None),
    range_start: int | None = Query(default=None),
    range_end: int | None = Query(default=None),
):
    """Server-rendered preview page. Selection sources, in priority order:
    1. `ids` (comma-separated chapter_index values, possibly with a range to expand)
    2. `range_start` + `range_end` (inclusive indices)
    3. Individual checkboxes submitted as repeated `ids` values
    """
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        all_chapters = repository.list_chapters(conn, book_id)

    requested: list[int] = []
    if ids:
        requested.extend(_parse_ids(ids))
    if range_start is not None and range_end is not None and range_end >= range_start:
        requested.extend(range(range_start, range_end + 1))

    seen: set[int] = set()
    selected_indices: list[int] = []
    for idx in requested:
        if idx not in seen:
            seen.add(idx)
            selected_indices.append(idx)
    selected_indices.sort()

    previewed: list = []
    if selected_indices:
        with locked_conn(request) as conn:
            previewed = repository.get_chapters_by_indices(conn, book_id, selected_indices)

    return templates.TemplateResponse(
        request,
        "chapter_preview.html",
        {
            "book": book,
            "all_chapters": all_chapters,
            "previewed": previewed,
            "selected_indices": selected_indices,
            "range_start": range_start,
            "range_end": range_end,
        },
    )


# ---------------------------------------------------------------------------
# Chapter exclude
# ---------------------------------------------------------------------------


@router.post("/books/{book_id}/chapters/{chapter_index}/exclude")
def toggle_chapter_exclude(
    request: Request,
    book_id: int,
    chapter_index: int,
    excluded: str = Form(default="true"),
):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        repository.set_chapter_excluded(
            conn, book_id, chapter_index, excluded.lower() != "false"
        )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Replace rules
# ---------------------------------------------------------------------------


@router.get("/books/{book_id}/replace-rules")
def list_rules(request: Request, book_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        rules = repository.list_replace_rules(conn, book_id)
    return JSONResponse([
        {"id": r.id, "book_id": r.book_id, "find": r.find, "replace": r.replace,
         "is_regex": r.is_regex, "position": r.position}
        for r in rules
    ])


@router.post("/books/{book_id}/replace-rules")
def create_rule(
    request: Request,
    book_id: int,
    find: str = Form(...),
    replace: str = Form(default=""),
    is_regex: str = Form(default="false"),
    position: int = Form(default=0),
):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        try:
            rule = repository.create_replace_rule(
                conn, book_id, find, replace, is_regex.lower() == "true", position
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        repository.reset_done_patches_for_book(conn, book_id)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/replace-rules/{rule_id}/edit")
def edit_rule(
    request: Request,
    book_id: int,
    rule_id: int,
    find: str = Form(...),
    replace: str = Form(default=""),
    is_regex: str = Form(default="false"),
    position: int = Form(default=0),
):
    with locked_conn(request) as conn:
        try:
            updated = repository.update_replace_rule(
                conn, rule_id, find=find, replace=replace,
                is_regex=is_regex.lower() == "true", position=position,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
        repository.reset_done_patches_for_book(conn, book_id)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.post("/books/{book_id}/replace-rules/{rule_id}/delete")
def delete_rule(request: Request, book_id: int, rule_id: int):
    with locked_conn(request) as conn:
        if repository.delete_replace_rule(conn, rule_id):
            repository.reset_done_patches_for_book(conn, book_id)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


# ---------------------------------------------------------------------------
# TTS normalization settings + preview
# ---------------------------------------------------------------------------


@router.post("/books/{book_id}/normalization")
def update_normalization(
    request: Request,
    book_id: int,
    numbers: str = Form(default=""),
    junk: str = Form(default=""),
    spellcheck: str = Form(default=""),
    dictionary: str = Form(default=""),
    transliteration: str = Form(default=""),
):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        repository.update_book_normalization(
            conn,
            book_id,
            numbers=numbers.lower() == "on",
            junk=junk.lower() == "on",
            spellcheck=spellcheck.lower() == "on",
            dictionary=dictionary.lower() == "on",
            transliteration=transliteration.lower() == "on",
        )
        repository.reset_done_patches_for_book(conn, book_id)
    if request.headers.get("X-Requested-With") == "autosave":
        return {"status": "ok"}
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.get("/books/{book_id}/normalization/preview", response_class=PlainTextResponse)
def preview_normalization(
    request: Request,
    book_id: int,
    chapter_index: int = Query(default=0),
):
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        text = repository.get_chapter_text(conn, book_id, chapter_index)
        if text is None:
            raise HTTPException(status_code=404, detail=f"chapter {chapter_index} not found")
        opts = NormalizationOptions(
            numbers=bool(book.normalize_numbers_enabled),
            junk=bool(book.normalize_junk_enabled),
            spellcheck=bool(book.normalize_spellcheck_enabled),
            dictionary=bool(book.normalize_dictionary_enabled),
            transliteration=bool(book.normalize_transliteration_enabled),
        )
        normalized = normalize_text(text, opts)
    return PlainTextResponse(normalized)


# ---------------------------------------------------------------------------
# Patch rebuild + preview actions
# ---------------------------------------------------------------------------


@router.post("/books/{book_id}/patches/rebuild")
async def rebuild_patches(request: Request, book_id: int):
    body = await request.json()
    ranges_raw = body.get("ranges", [])
    reset_done = body.get("reset_done", True)
    ranges: list[tuple[int, int]] = []
    for item in ranges_raw:
        if isinstance(item, list) and len(item) == 2:
            ranges.append((item[0], item[1]))
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        try:
            patches = repository.rebuild_patches(conn, book_id, ranges, reset_done)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse([
        {"patch_index": p.patch_index, "chapter_start": p.chapter_start,
         "chapter_end": p.chapter_end, "name": p.name, "chunk_count": p.chunk_count,
         "status": p.status}
        for p in patches
    ])


@router.post("/books/{book_id}/patches/auto-build")
async def auto_build_patches(
    request: Request,
    book_id: int,
):
    body = await request.form()
    start_chapter_str = body.get("start_chapter")
    end_chapter_str = body.get("end_chapter")
    patch_size_str = body.get("patch_size")

    try:
        start_chapter = int(start_chapter_str) if start_chapter_str else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="start_chapter is required and must be an integer")
    if start_chapter is None:
        raise HTTPException(status_code=400, detail="start_chapter is required")
    end_chapter = None
    if end_chapter_str is not None and end_chapter_str.strip() != "":
        try:
            end_chapter = int(end_chapter_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="end_chapter must be an integer")
    patch_size = None
    if patch_size_str is not None and patch_size_str.strip() != "":
        try:
            patch_size = int(patch_size_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="patch_size must be an integer")

    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        try:
            repository.auto_build_patches(conn, book_id, start_chapter, end_chapter, patch_size)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.get("/books/{book_id}/patches/auto-build/preview")
def preview_auto_build(
    request: Request,
    book_id: int,
    start_chapter: int = Query(...),
    end_chapter: int | None = Query(default=None),
    patch_size: int | None = Query(default=None),
):
    """Return planned patches as JSON without creating them."""
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        try:
            planned = repository.preview_auto_build(
                conn, book_id, start_chapter, end_chapter, patch_size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"patches": planned})


@router.get("/books/{book_id}/patches/{patch_id}/text", response_class=PlainTextResponse)
def get_patch_text(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.build_patch_text(conn, patch)
    return PlainTextResponse(text)


@router.get("/books/{book_id}/patches/{patch_id}/audio")
def get_patch_audio(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status != "done" or not patch.audio_path:
            raise HTTPException(status_code=404, detail="audio not available")
        path = patch.audio_path
    return FileResponse(path, media_type="audio/wav")


@router.get("/books/{book_id}/patches/build", response_class=HTMLResponse)
def patch_builder_page(request: Request, book_id: int):
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        chapters = repository.list_chapters(conn, book_id)
        patches = repository.list_patches(conn, book_id)
        patch_video_ids = {
            p.id for p in patches
            if (Path(settings.data_root) / "books" / str(book_id) / "patch_videos" / f"{p.id}.mp4").exists()
        }
    return templates.TemplateResponse(
        request, "patch_builder.html",
        {"book": book, "chapters": chapters, "patches": patches, "patch_video_ids": patch_video_ids},
    )


@router.post("/books/{book_id}/patches/build")
async def patch_builder_submit(request: Request, book_id: int):
    body = await request.form()
    excluded_list = body.getlist("excluded")
    excluded_set = {int(x) for x in excluded_list if x.isdigit()}
    range_starts = body.getlist("range_start")
    range_ends = body.getlist("range_end")
    ranges: list[tuple[int, int]] = []
    for rs, re_ in zip(range_starts, range_ends):
        try:
            s, e = int(rs), int(re_)
            if s <= e:
                ranges.append((s, e))
        except ValueError:
            continue

    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        for ch in repository.list_chapters(conn, book_id):
            new_excluded = ch.chapter_index in excluded_set
            if new_excluded != ch.is_excluded:
                repository.set_chapter_excluded(
                    conn, book_id, ch.chapter_index, new_excluded
                )
        if ranges:
            try:
                repository.rebuild_patches(conn, book_id, ranges, reset_done=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=f"/books/{book_id}", status_code=303)


@router.get("/books/{book_id}/youtube-description")
def get_youtube_description(request: Request, book_id: int):
    """Return the enriched YouTube description + tags for the Copy button."""
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        result = repository.build_youtube_description(conn, book_id)
    return JSONResponse(result)


def _list_backgrounds() -> list[dict]:
    """Shared helper: list backgrounds (default + user-uploaded images/videos)."""
    from app.routes.video import _BACKGROUNDS_DIR, ALLOWED_BACKGROUND_EXTENSIONS
    items: list[dict] = []
    default = settings.default_background_image
    if Path(default).exists():
        items.append({"name": "__default__", "path": default, "is_default": True,
                      "is_video": video_gen.is_video_background(default)})
    _BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(_BACKGROUNDS_DIR.iterdir()):
        if f.suffix.lower() in ALLOWED_BACKGROUND_EXTENSIONS:
            items.append({"name": f.name, "path": str(f), "is_default": False,
                          "is_video": video_gen.is_video_background(f)})
    return items

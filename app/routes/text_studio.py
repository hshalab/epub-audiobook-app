"""Text Studio routes: edit patch text, search/replace, spell check, effect markers."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import repository, text_analysis
from app.config import settings
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/books/{book_id}/text-studio", response_class=HTMLResponse)
def text_studio_page(request: Request, book_id: int, patch_id: int | None = None):
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        patches = repository.list_patches(conn, book_id)
        if patch_id is None and patches:
            patch_id = patches[0].id
        patch = repository.get_patch(conn, patch_id) if patch_id else None
        warnings = repository.list_patch_warnings(conn, patch_id) if patch_id else []
        clean_text = None
        if patch:
            clean_text = repository.get_effective_patch_text(conn, patch)
    return templates.TemplateResponse(
        request, "text_studio.html",
        {
            "book": book, "patches": patches, "patch": patch,
            "clean_text": clean_text, "warnings": warnings,
        },
    )


@router.get("/books/{book_id}/text-studio/patches/{patch_id}")
def get_patch_text(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)
        warnings = repository.list_patch_warnings(conn, patch_id)
    return JSONResponse({"text": text, "warnings": warnings, "is_edited": patch.clean_text is not None})


@router.put("/books/{book_id}/text-studio/patches/{patch_id}")
async def save_patch_text(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    text = body.get("text", "")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.save_patch_clean_text(conn, patch_id, text)
    return JSONResponse({"ok": True})


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/analyze")
async def analyze_patch_text(request: Request, book_id: int, patch_id: int):
    client_text = None
    try:
        body = await request.json()
        client_text = body.get("text")
    except Exception:
        pass
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = client_text if client_text is not None else repository.get_effective_patch_text(conn, patch)
        warnings = text_analysis.analyze_text(text)
        repository.save_patch_warnings(conn, patch_id, warnings)
    return JSONResponse({"warnings": warnings, "count": len(warnings)})


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/apply-warning")
async def apply_warning(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    warning_id = body.get("warning_id")
    action = body.get("action")  # "accept" or "dismiss"
    if warning_id is None or action not in ("accept", "dismiss"):
        raise HTTPException(status_code=400, detail="warning_id and action required")
    status = 1 if action == "accept" else 2
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.update_patch_warning_status(conn, warning_id, status)
    return JSONResponse({"ok": True})


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/replace")
async def search_replace(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    search = body.get("search", "")
    replace = body.get("replace", "")
    is_regex = body.get("is_regex", False)
    if not search:
        raise HTTPException(status_code=400, detail="search text required")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)
        if is_regex:
            try:
                new_text, count = re.subn(search, replace, text)
            except re.error as e:
                raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
        else:
            count = text.count(search)
            new_text = text.replace(search, replace)
        repository.save_patch_clean_text(conn, patch_id, new_text)
    return JSONResponse({"text": new_text, "replacements": count})


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/reset")
def reset_patch_text(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        repository.reset_patch_clean_text(conn, patch_id)
        text = repository.build_patch_text(conn, patch)
    return JSONResponse({"text": text})



@router.get("/books/{book_id}/text-studio/patches/{patch_id}/audio")
def serve_patch_audio(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status != "done" or not patch.audio_path:
            raise HTTPException(status_code=404, detail="audio not available")
        path = patch.audio_path
    return FileResponse(path, media_type="audio/wav")


@router.get("/books/{book_id}/text-studio/patches/{patch_id}/video")
def serve_patch_video(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
    video_path = Path(settings.data_root) / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="video not available")
    return FileResponse(str(video_path), media_type="video/mp4")


@router.post("/books/{book_id}/text-studio/normalize-preview")
async def normalize_preview(request: Request, book_id: int):
    form = await request.form()
    text = form.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    from app.normalization import NormalizationOptions, normalize_text
    opts = NormalizationOptions(
        numbers=form.get("numbers") == "on",
        junk=form.get("junk") == "on",
        spellcheck=form.get("spellcheck") == "on",
        dictionary=form.get("dictionary") == "on",
        transliteration=form.get("transliteration") == "on",
    )
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        rules = repository.list_replace_rules(conn, book_id)
    result = normalize_text(text, opts)
    result = repository.apply_replace_rules(result, rules)
    return JSONResponse({"text": result})

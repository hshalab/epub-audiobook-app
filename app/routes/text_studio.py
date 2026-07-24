"""Text Studio routes: edit patch text, search/replace, spell check, effect markers."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import threading
import uuid as _uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app import repository, text_analysis
from app.config import settings
from app.deps import locked_conn
from app.light_tts import LightTTSEngine

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_light_tts_engine: LightTTSEngine | None = None


def _get_light_engine() -> LightTTSEngine:
    global _light_tts_engine
    if _light_tts_engine is None:
        _light_tts_engine = LightTTSEngine(
            backend=settings.light_tts_backend,
            voice=settings.light_tts_voice,
        )
    return _light_tts_engine


def _mix_effects(wav_bytes: bytes, text: str, conn) -> bytes:
    markers_found: list[tuple[int, str]] = []
    for pattern in text_analysis._EFFECT_PATTERNS:
        for m in pattern.finditer(text):
            markers_found.append((m.start(), m.group(1 if m.lastindex else 0)))
    if not markers_found:
        return wav_bytes

    effects = repository.list_sound_effects(conn)
    effect_map: dict[str, dict] = {}
    for e in effects:
        marker_lower = e["marker"].strip().lower()
        if marker_lower not in effect_map:
            effect_map[marker_lower] = e
    if not effect_map:
        return wav_bytes

    try:
        tts_data, tts_sr = sf.read(io.BytesIO(wav_bytes))
    except Exception:
        return wav_bytes
    if tts_data.ndim > 1:
        tts_data = tts_data.mean(axis=1)
    mixed = tts_data.astype(np.float64)
    text_len = len(text)

    for pos, raw_marker in markers_found:
        key = raw_marker.strip().lower()
        if key not in effect_map:
            continue
        effect_info = effect_map[key]
        effect_path = effect_info.get("file_path", "")
        if not effect_path or not Path(effect_path).exists():
            continue
        try:
            eff_data, eff_sr = sf.read(effect_path)
        except Exception:
            continue
        if eff_sr != tts_sr:
            continue
        if eff_data.ndim > 1:
            eff_data = eff_data.mean(axis=1)
        ratio = pos / text_len if text_len > 0 else 0
        start_sample = int(ratio * len(mixed))
        end_sample = min(start_sample + len(eff_data), len(mixed))
        eff_len = end_sample - start_sample
        if eff_len <= 0:
            continue
        mixed[start_sample:end_sample] += eff_data[:eff_len]

    peak = np.max(np.abs(mixed))
    if peak > 1.0:
        mixed = mixed / peak

    out_buf = io.BytesIO()
    sf.write(out_buf, mixed, tts_sr, format="WAV")
    return out_buf.getvalue()


def _light_synthesize_patch(
    patch_id: int,
    book_id: int,
    backend: str,
    voice: str | None,
    with_effects: bool,
    conn,
    db_lock: threading.Lock,
) -> str:
    """Synthesize a patch using LightTTS, chunk-by-chunk. Returns audio_path."""
    from app.chunker import split_into_tts_chunks
    from app import audio_merge, repository

    with db_lock:
        patch = repository.get_patch(conn, patch_id)
        text = repository.get_effective_patch_text(conn, patch)

    chunks = split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars)

    engine = LightTTSEngine(backend=backend, voice=voice)

    book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
    book_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = book_dir / f"{patch_id}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        wav_bytes, _ = engine.synthesize_to_wav_bytes(chunk_text)
        chunk_path = chunk_dir / f"chunk_{i:03d}.wav"
        chunk_path.write_bytes(wav_bytes)
        chunk_paths.append(str(chunk_path))

    audio_path = str(book_dir / f"{patch_id}.wav")
    audio_merge.merge_chunk_files_to_patch(chunk_paths, audio_path)

    if with_effects:
        merged = Path(audio_path).read_bytes()
        with db_lock:
            mixed = _mix_effects(merged, text, conn)
        Path(audio_path).write_bytes(mixed)

    with db_lock:
        repository.mark_patch_done(conn, patch_id, audio_path)

    return audio_path


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


@router.get("/text-studio/light-tts/backends")
def list_backends(request: Request):
    from app.light_tts import _BACKENDS, _check_backend
    result = []
    for name, info in _BACKENDS.items():
        available = True
        try:
            _check_backend(name)
        except RuntimeError:
            available = False
        result.append({"id": name, "label": info["description"], "available": available})
    return JSONResponse({"backends": result})


@router.get("/text-studio/light-tts/voices")
def list_voices_endpoint(backend: str):
    from app.light_tts import _BACKENDS, list_voices
    if backend not in _BACKENDS:
        raise HTTPException(status_code=400, detail="unknown backend")
    return JSONResponse({"voices": list_voices(backend)})


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/preview-paragraph")
async def preview_paragraph(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    text = body.get("text", "").strip()
    with_effects = body.get("with_effects", False)
    backend = body.get("backend")
    voice = body.get("voice")

    if not text:
        raise HTTPException(status_code=400, detail="text required")

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")

        try:
            if backend:
                engine = LightTTSEngine(backend=backend)
            else:
                engine = _get_light_engine()
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        try:
            wav_bytes, _ = await asyncio.to_thread(engine.synthesize_to_wav_bytes, text, voice)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {e}")

        if with_effects:
            wav_bytes = _mix_effects(wav_bytes, text, conn)

    return Response(content=wav_bytes, media_type="audio/wav")


@router.post("/books/{book_id}/text-studio/patches/{patch_id}/preview-patch")
async def preview_patch(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    with_effects = body.get("with_effects", False)
    backend = body.get("backend")
    voice = body.get("voice")

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)

        try:
            if backend:
                engine = LightTTSEngine(backend=backend)
            else:
                engine = _get_light_engine()
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        try:
            wav_bytes, _ = await asyncio.to_thread(engine.synthesize_to_wav_bytes, text, voice)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {e}")

        if with_effects:
            wav_bytes = _mix_effects(wav_bytes, text, conn)

    return Response(content=wav_bytes, media_type="audio/wav")


@router.post("/books/{book_id}/patches/{patch_id}/light-tts-generate")
async def light_tts_generate(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    backend = body.get("backend") or settings.light_tts_backend
    voice = body.get("voice") or settings.light_tts_voice
    with_effects = bool(body.get("with_effects", False))

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=409, detail="patch is currently processing")

    db_lock = request.app.state.db_lock
    conn = request.app.state.conn

    try:
        audio_path = await asyncio.to_thread(
            _light_synthesize_patch,
            patch_id, book_id, backend, voice, with_effects, conn, db_lock,
        )
    except Exception as exc:
        logger.exception("light_tts_generate failed for patch %s", patch_id)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"status": "done", "patch_id": patch_id, "audio_path": audio_path})


@router.post("/books/{book_id}/light-tts-generate-all")
async def light_tts_generate_all(request: Request, book_id: int):
    body = await request.json()
    backend = body.get("backend") or settings.light_tts_backend
    voice = body.get("voice") or settings.light_tts_voice
    with_effects = bool(body.get("with_effects", False))
    patch_ids: list[int] | None = body.get("patch_ids")

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        all_patches = repository.list_patches(conn, book_id)

    if patch_ids is not None:
        targets = [p for p in all_patches if p.id in set(patch_ids)]
    else:
        targets = [p for p in all_patches if p.status in ("pending", "failed")]

    db_lock = request.app.state.db_lock
    conn_ref = request.app.state.conn

    results = []
    for patch in targets:
        if patch.status == "processing":
            results.append({"patch_id": patch.id, "status": "skipped", "detail": "currently processing"})
            continue
        try:
            await asyncio.to_thread(
                _light_synthesize_patch,
                patch.id, book_id, backend, voice, with_effects, conn_ref, db_lock,
            )
            results.append({"patch_id": patch.id, "status": "done"})
        except Exception as exc:
            logger.exception("light_tts_generate_all failed for patch %s", patch.id)
            results.append({"patch_id": patch.id, "status": "error", "detail": str(exc)})

    return JSONResponse({"results": results})


_SAFE_PREVIEW_NAME = re.compile(r"^[\w\-]+\.wav$")


@router.get("/preview-tmp/{filename}")
def serve_preview_tmp(filename: str):
    if not _SAFE_PREVIEW_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    p = Path(settings.data_root) / "preview_tmp" / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), media_type="audio/wav")


@router.get("/books/{book_id}/text-studio/patches/{patch_id}/preview-stream")
async def preview_stream(
    request: Request,
    book_id: int,
    patch_id: int,
    backend: str = "",
    voice: str = "",
    with_effects: int = 0,
):
    from starlette.responses import StreamingResponse

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)

    _backend = backend or settings.light_tts_backend
    _voice = voice or settings.light_tts_voice
    _with_effects = bool(with_effects)
    db_lock = request.app.state.db_lock
    conn_ref = request.app.state.conn

    async def _generate():
        from app import audio_merge
        from app.chunker import split_into_tts_chunks

        tmp_dir = Path(settings.data_root) / "preview_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        for old in tmp_dir.glob(f"{patch_id}_*.wav"):
            old.unlink(missing_ok=True)

        session_token = _uuid.uuid4().hex[:8]
        chunks = split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars)
        total = len(chunks)
        tmp_paths = []

        try:
            engine = LightTTSEngine(backend=_backend, voice=_voice or None)
        except RuntimeError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            return

        for i, chunk_text in enumerate(chunks):
            try:
                wav_bytes, _ = await asyncio.to_thread(
                    engine.synthesize_to_wav_bytes, chunk_text, _voice or None
                )
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': f'chunk {i} failed: {exc}'})}\n\n"
                return

            tmp_name = f"{patch_id}_{session_token}_{i}.wav"
            tmp_path = tmp_dir / tmp_name
            tmp_path.write_bytes(wav_bytes)
            tmp_paths.append(str(tmp_path))

            yield f"data: {json.dumps({'type': 'chunk', 'index': i, 'total': total, 'url': f'/preview-tmp/{tmp_name}'})}\n\n"

        try:
            book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
            book_dir.mkdir(parents=True, exist_ok=True)
            audio_path = str(book_dir / f"{patch_id}.wav")
            await asyncio.to_thread(audio_merge.merge_chunk_files_to_patch, tmp_paths, audio_path)

            if _with_effects:
                merged_bytes = Path(audio_path).read_bytes()
                with db_lock:
                    mixed = _mix_effects(merged_bytes, text, conn_ref)
                Path(audio_path).write_bytes(mixed)

            with db_lock:
                repository.mark_patch_done(conn_ref, patch_id, audio_path)

            yield f"data: {json.dumps({'type': 'done', 'saved': True})}\n\n"
        except Exception as exc:
            logger.exception("preview_stream merge/save failed for patch %s", patch_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")

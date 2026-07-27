"""Endpoints shared by the Book Detail LightTTS controls."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app import repository
from app.chunker import split_into_tts_chunks
from app.config import settings
from app.deps import locked_conn
from app.light_tts import _BACKENDS, _check_backend, list_voices

router = APIRouter()


@router.get("/text-studio/light-tts/backends")
def list_light_tts_backends():
    backends = []
    for name, info in _BACKENDS.items():
        try:
            _check_backend(name)
            available = True
        except RuntimeError:
            available = False
        backends.append({"id": name, "label": info["description"], "available": available})
    return {"backends": backends}


@router.get("/text-studio/light-tts/voices")
def list_light_tts_voices(backend: str):
    if backend not in _BACKENDS:
        raise HTTPException(status_code=400, detail="unknown backend")
    return {"voices": list_voices(backend)}


@router.post("/books/{book_id}/patches/reconcile-chunk-counts")
def reconcile_chunk_counts(request: Request, book_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        updated = []
        for patch_id in repository.list_stale_chunk_count_patch_ids(conn, book_id):
            patch = repository.get_patch(conn, patch_id)
            if patch is None:
                continue
            text = repository.get_effective_patch_text(conn, patch)
            chunk_count = len(split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars))
            repository.update_patch_chunk_count(conn, patch_id, chunk_count)
            updated.append({"id": patch_id, "chunk_count": chunk_count})
    return {"updated": updated}

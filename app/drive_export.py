"""Build the exportable package (manifest + chunk texts + notebook template) for a
patch, so it can be synthesized on Google Colab/Kaggle and the resulting audio
re-imported. See app/google_drive.py for the Drive API calls and
app/routes/patches.py for the export/import routes that tie it together.

Two package shapes exist:
- single patch: chunk_NNN.txt + manifest.json + notebook at the package root
  (build_export_package / build_export_zip), and
- batch: several patches under patches/patch_NNN/ with a batch_manifest.json and
  a batch notebook at the root (build_batch_export_package / build_batch_export_zip),
  so one Colab/Kaggle run can synthesize and merge every selected patch.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import repository
from app.chunker import split_into_tts_chunks
from app.config import settings
from app.models import Book, Patch

_NOTEBOOK_TEMPLATE = Path(__file__).parent / "assets" / "colab_kaggle_tts_template.ipynb"
_BATCH_NOTEBOOK_TEMPLATE = Path(__file__).parent / "assets" / "colab_kaggle_batch_tts_template.ipynb"
_TMP_DIR = Path(settings.data_root) / "tmp" / "patch_export"


def _sanitize_name(name: str) -> str:
    """Strip characters that are unsafe in Drive/Windows file and folder names."""
    return re.sub(r"[^\w\- ]", "", name).strip()


def _write_patch_files(
    conn: sqlite3.Connection,
    book: Book,
    patch: Patch,
    dest_dir: Path,
    reference_rel: str | None,
) -> dict:
    """Write chunk_NNN.txt files + manifest.json for one patch into dest_dir and
    return the manifest dict. ``reference_rel`` is the path (relative to dest_dir)
    recorded in the manifest for the voice reference clip, or None when the book
    has no voice clip."""
    text = repository.build_patch_text(conn, patch)
    max_chars = patch.max_chars or settings.tts_max_chars
    chunks = split_into_tts_chunks(text, max_chars=max_chars)
    if not chunks:
        raise ValueError(f"patch {patch.id} has no text to export")

    dest_dir.mkdir(parents=True, exist_ok=True)

    chunk_filenames = []
    for i, chunk_text in enumerate(chunks):
        filename = f"chunk_{i:03d}.txt"
        (dest_dir / filename).write_text(chunk_text, encoding="utf-8")
        chunk_filenames.append(filename)

    manifest = {
        "patch_id": patch.id,
        "book_id": patch.book_id,
        "book_title": book.title,
        "patch_name": patch.name or str(patch.patch_index),
        "chapter_start": patch.chapter_start,
        "chapter_end": patch.chapter_end,
        "max_chars": max_chars,
        "chunk_count": len(chunks),
        "chunks": chunk_filenames,
        "reference_wav": reference_rel,
        "reference_transcript": book.voice_transcript or None,
        "voxcpm_model_id": "openbmb/VoxCPM2",
        "expected_outputs": [f"chunk_{i:03d}.wav" for i in range(len(chunks))],
    }
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def build_export_package(
    conn: sqlite3.Connection,
    patch: Patch,
    drive_folder_name: str | None = None,
    hf_token: str | None = None,
) -> Path:
    """Write manifest.json + chunk_NNN.txt + (optional) voice reference + the notebook
    template into a fresh temp directory. Caller is responsible for deleting it.

    ``drive_folder_name`` is baked into the notebook so its Colab cell can locate the
    exported folder automatically. If not given (e.g. the plain local-download path),
    the deterministic per-patch name is used as the fallback default.
    """
    book = repository.get_book(conn, patch.book_id)
    if book is None:
        raise ValueError(f"book {patch.book_id} not found")

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = _TMP_DIR / f"patch_{patch.id}_{uuid.uuid4().hex[:8]}"
    package_dir.mkdir(parents=True, exist_ok=True)

    reference_wav_name = None
    if book.voice_clip_path and Path(book.voice_clip_path).exists():
        reference_wav_name = "reference" + Path(book.voice_clip_path).suffix
        shutil.copyfile(book.voice_clip_path, package_dir / reference_wav_name)

    _write_patch_files(conn, book, patch, package_dir, reference_wav_name)

    # Bake the patch id + folder name + HF token into the notebook so its cells can find
    # the exported folder and authenticate automatically. Kept as simple placeholder
    # substitutions rather than parsing/rewriting nbformat cells.
    folder_name = drive_folder_name or folder_name_for_patch(book.title, patch)
    notebook_src = _NOTEBOOK_TEMPLATE.read_text(encoding="utf-8")
    notebook_src = notebook_src.replace("__PATCH_ID__", str(patch.id))
    notebook_src = notebook_src.replace(
        "__DEFAULT_FOLDER_NAME__", json.dumps(folder_name)[1:-1]  # escape for JSON string literal
    )
    notebook_src = notebook_src.replace("__HF_TOKEN__", (hf_token or settings.hf_token) or "")
    (package_dir / "colab_kaggle_tts_template.ipynb").write_text(notebook_src, encoding="utf-8")

    return package_dir


def build_export_zip(
    conn: sqlite3.Connection,
    patch: Patch,
    hf_token: str | None = None,
) -> Path:
    """Same package as build_export_package, zipped up for local download (the safety
    net that works even without connecting Google Drive)."""
    package_dir = build_export_package(conn, patch, hf_token=hf_token)
    try:
        zip_path = shutil.make_archive(str(package_dir), "zip", root_dir=package_dir)
    finally:
        shutil.rmtree(package_dir, ignore_errors=True)
    return Path(zip_path)


def folder_name_for_patch(book_title: str, patch: Patch) -> str:
    safe_title = _sanitize_name(book_title) or "book"
    label = patch.name or str(patch.patch_index)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{safe_title} - patch {label} - {timestamp}"


def folder_name_for_batch(book_title: str, patches: list[Patch]) -> str:
    safe_title = _sanitize_name(book_title) or "book"
    indices = [p.patch_index for p in patches]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return (
        f"{safe_title} - batch {min(indices):03d}-{max(indices):03d} "
        f"({len(patches)} patches) - {timestamp}"
    )


def result_wav_name(patch: Patch) -> str:
    """Filename of the merged per-patch wav the batch notebook writes into result/.
    Prefixed with the zero-padded patch index so duplicate patch names cannot
    collide and the files sort in reading order."""
    label = _sanitize_name(patch.name or str(patch.patch_index)) or "patch"
    return f"{patch.patch_index:03d} - {label}.wav"


def build_batch_export_package(
    conn: sqlite3.Connection,
    patches: list[Patch],
    drive_folder_name: str | None = None,
    hf_token: str | None = None,
) -> tuple[Path, dict]:
    """Write a multi-patch package: batch_manifest.json + the batch notebook at the
    root, one shared voice reference clip, and per-patch subfolders under patches/
    (each with the same manifest.json + chunk_NNN.txt layout as a single export).
    Returns (package_dir, batch_manifest); caller is responsible for deleting the
    directory."""
    if not patches:
        raise ValueError("no patches to export")
    book_ids = {p.book_id for p in patches}
    if len(book_ids) != 1:
        raise ValueError("all patches in a batch must belong to the same book")
    book = repository.get_book(conn, patches[0].book_id)
    if book is None:
        raise ValueError(f"book {patches[0].book_id} not found")

    patches = sorted(patches, key=lambda p: p.patch_index)

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = _TMP_DIR / f"batch_{uuid.uuid4().hex[:8]}"
    package_dir.mkdir(parents=True, exist_ok=True)

    # The reference clip is book-level, so it is stored once at the batch root and
    # each per-patch manifest points at it with a relative path.
    reference_wav_name = None
    if book.voice_clip_path and Path(book.voice_clip_path).exists():
        reference_wav_name = "reference" + Path(book.voice_clip_path).suffix
        shutil.copyfile(book.voice_clip_path, package_dir / reference_wav_name)

    patch_entries = []
    for patch in patches:
        folder_rel = f"patches/patch_{patch.patch_index:03d}"
        reference_rel = f"../../{reference_wav_name}" if reference_wav_name else None
        manifest = _write_patch_files(conn, book, patch, package_dir / folder_rel, reference_rel)
        patch_entries.append({
            "patch_id": patch.id,
            "patch_index": patch.patch_index,
            "folder": folder_rel,
            "patch_name": manifest["patch_name"],
            "chapter_start": patch.chapter_start,
            "chapter_end": patch.chapter_end,
            "max_chars": manifest["max_chars"],
            "chunk_count": manifest["chunk_count"],
            "result_wav": f"result/{result_wav_name(patch)}",
        })

    timestamp = datetime.now(timezone.utc)
    batch_id = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    batch_manifest = {
        "format": "epub-audiobook-batch-v1",
        "batch_id": batch_id,
        "book_id": book.id,
        "book_title": book.title,
        "created_at": timestamp.isoformat(),
        "voxcpm_model_id": "openbmb/VoxCPM2",
        "reference_wav": reference_wav_name,
        "reference_transcript": book.voice_transcript or None,
        "patch_count": len(patch_entries),
        "patches": patch_entries,
    }
    (package_dir / "batch_manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Same placeholder substitution as the single-patch notebook. Note __BATCH_ID__
    # sits inside string quotes in the template (batch ids are strings, unlike the
    # bare-int __PATCH_ID__).
    folder_name = drive_folder_name or folder_name_for_batch(book.title, patches)
    notebook_src = _BATCH_NOTEBOOK_TEMPLATE.read_text(encoding="utf-8")
    notebook_src = notebook_src.replace("__BATCH_ID__", batch_id)
    notebook_src = notebook_src.replace(
        "__DEFAULT_FOLDER_NAME__", json.dumps(folder_name)[1:-1]  # escape for JSON string literal
    )
    notebook_src = notebook_src.replace("__HF_TOKEN__", (hf_token or settings.hf_token) or "")
    (package_dir / "colab_kaggle_batch_tts_template.ipynb").write_text(notebook_src, encoding="utf-8")

    return package_dir, batch_manifest


def build_batch_export_zip(
    conn: sqlite3.Connection,
    patches: list[Patch],
    drive_folder_name: str | None = None,
    hf_token: str | None = None,
) -> Path:
    """Same package as build_batch_export_package, zipped up for local download
    (works without connecting Google Drive; also what gets uploaded to Kaggle
    as a dataset)."""
    package_dir, _ = build_batch_export_package(
        conn, patches, drive_folder_name=drive_folder_name, hf_token=hf_token
    )
    try:
        zip_path = shutil.make_archive(str(package_dir), "zip", root_dir=package_dir)
    finally:
        shutil.rmtree(package_dir, ignore_errors=True)
    return Path(zip_path)

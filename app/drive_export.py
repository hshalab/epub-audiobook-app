"""Build the exportable package (manifest + chunk texts + notebook template) for a
patch, so it can be synthesized on Google Colab/Kaggle and the resulting audio
re-imported. See app/google_drive.py for the Drive API calls and
app/routes/patches.py for the export/import routes that tie it together.

Two package shapes exist:
- single patch: manifest.json (chunk text inlined in chunk_metadata) + notebook at
  the package root (build_export_package / build_export_zip), and
- batch: several patches under patches/patch_NNN/ with a batch_manifest.json and
  a batch notebook at the root (build_batch_export_package / build_batch_export_zip),
  so one Colab/Kaggle run can synthesize and merge every selected patch.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import repository
from app.config import settings
from app.models import Book, Patch

_NOTEBOOK_TEMPLATE = Path(__file__).parent / "assets" / "colab_kaggle_tts_template.ipynb"
_BATCH_NOTEBOOK_TEMPLATE = Path(__file__).parent / "assets" / "colab_kaggle_batch_tts_template.ipynb"
_TMP_DIR = Path(settings.data_root) / "tmp" / "patch_export"


def validate_sync_folder(folder_path: str) -> Path:
    raw = Path(folder_path.strip()).expanduser()
    if not raw.is_absolute():
        raise ValueError("Folder path must be absolute")
    path = raw.resolve()
    if not path.exists():
        raise ValueError("Folder path does not exist")
    if not path.is_dir():
        raise ValueError("Folder path is not a directory")
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".epub-audiobook-write-test-", delete=True):
            pass
    except OSError as exc:
        raise ValueError(f"Folder is not writable: {exc}") from exc
    return path


def publish_package(package_dir: Path, target_folder: str, folder_name: str) -> Path:
    target = validate_sync_folder(target_folder)
    final = target / folder_name
    if final.exists():
        raise FileExistsError(f"Export folder already exists: {final}")
    temp = Path(tempfile.mkdtemp(prefix=".epub-audiobook-export-", dir=target))
    try:
        shutil.copytree(package_dir, temp, dirs_exist_ok=True)
        temp.rename(final)
        return final
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _sanitize_name(name: str) -> str:
    """Strip characters that are unsafe in Drive/Windows file and folder names."""
    return re.sub(r"[^\w\- ]", "", name).strip()


def _voice_clip_or_raise(book: Book) -> Path:
    """The voice reference clip is mandatory for Colab/Kaggle exports: without it
    VoxCPM picks a different random voice per chunk/session, so chunks synthesized
    remotely would not match each other (or audio already generated locally).
    Called before any package files are written so a refused export leaves nothing
    behind."""
    if not book.voice_clip_path or not Path(book.voice_clip_path).exists():
        raise ValueError(
            f"book '{book.title}' has no voice reference clip - upload one on the "
            "book page before exporting, so every chunk is synthesized with the "
            "same voice"
        )
    return Path(book.voice_clip_path)


def _write_patch_files(
    conn: sqlite3.Connection,
    book: Book,
    patch: Patch,
    dest_dir: Path,
    reference_rel: str | None,
    overlay_image_path: str | None = None,
) -> dict:
    """Write manifest.json (with chunk text inlined) + background image for one patch into
    dest_dir and return the manifest dict. overlay_image_path is the pre-rendered
    background image (text already baked in by Pillow); falls back to raw background."""
    plan = repository.build_patch_chunk_plan(conn, patch)
    if not plan:
        raise ValueError(f"patch {patch.id} has no text to export")

    dest_dir.mkdir(parents=True, exist_ok=True)

    chunk_filenames = []
    chunk_metadata = []
    for i, item in enumerate(plan):
        # chunk_NNN.txt is a logical id only - the text itself travels in the manifest,
        # so a whole patch is one file to download instead of one per chunk.
        filename = f"chunk_{i:03d}.txt"
        chunk_filenames.append(filename)
        chunk_metadata.append(
            {
                "filename": filename,
                "chapter_index": item["chapter_index"],
                "chapter_title": item["chapter_title"],
                "is_chapter_start": item["is_chapter_start"],
                "text": item["text"],
            }
        )

    bg_filename: str | None = None
    bg_source = overlay_image_path
    if not bg_source and book.background_image_path and Path(book.background_image_path).exists():
        bg_source = book.background_image_path
    if not bg_source:
        default = settings.default_background_image
        if Path(default).exists():
            bg_source = default
    if bg_source:
        ext = Path(bg_source).suffix.lower() or ".jpg"
        bg_filename = f"background{ext}"
        shutil.copyfile(bg_source, dest_dir / bg_filename)

    manifest = {
        "patch_id": patch.id,
        "book_id": patch.book_id,
        "book_title": book.title,
        "patch_name": patch.name or str(patch.patch_index),
        "chapter_start": patch.chapter_start,
        "chapter_end": patch.chapter_end,
        "max_chars": patch.max_chars or settings.tts_max_chars,
        "chunk_count": len(plan),
        "chunks": chunk_filenames,
        "chunk_metadata": chunk_metadata,
        "reference_wav": reference_rel,
        "reference_transcript": book.voice_transcript or None,
        "voxcpm_model_id": "openbmb/VoxCPM2",
        "expected_outputs": [f"chunk_{i:03d}.wav" for i in range(len(plan))],
        "background_image": bg_filename,
    }
    (dest_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def package_chunk_count(package_dir: Path) -> int:
    """Chunk count of a built single-patch package, read from its manifest.

    Chunk texts no longer exist as separate files, so callers cannot count
    chunk_NNN.txt to learn how big a package is."""
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    return int(manifest["chunk_count"])


def _copy_music_to_package(book: Book, conn: sqlite3.Connection, package_dir: Path) -> str | None:
    """Copy the book's music file into package_dir/music/ and return the relative path,
    or None if the book has no music assigned."""
    if not book.music_id:
        return None
    music = repository.get_music(conn, book.music_id)
    if music is None or not Path(music.file_path).exists():
        return None
    music_dir = package_dir / "music"
    music_dir.mkdir(exist_ok=True)
    dest_name = Path(music.file_path).name
    shutil.copyfile(music.file_path, music_dir / dest_name)
    return f"music/{dest_name}"


def build_export_package(
    conn: sqlite3.Connection,
    patch: Patch,
    drive_folder_name: str | None = None,
    hf_token: str | None = None,
) -> Path:
    """Write manifest.json + background image + optional music + notebook
    template into a fresh temp directory. Caller is responsible for deleting it."""
    from app import image_overlay

    book = repository.get_book(conn, patch.book_id)
    if book is None:
        raise ValueError(f"book {patch.book_id} not found")
    voice_clip = _voice_clip_or_raise(book)

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = _TMP_DIR / f"patch_{patch.id}_{uuid.uuid4().hex[:8]}"
    package_dir.mkdir(parents=True, exist_ok=True)

    reference_wav_name = "reference" + voice_clip.suffix
    shutil.copyfile(voice_clip, package_dir / reference_wav_name)

    overlay_path = image_overlay.ensure_patch_overlay(
        book, patch, settings.default_font_path or None
    )
    _write_patch_files(conn, book, patch, package_dir, reference_wav_name, overlay_path)

    music_rel = _copy_music_to_package(book, conn, package_dir)

    w, h = (book.video_resolution or "1920x1080").split("x")
    video_config = {
        "resolution": book.video_resolution or "1920x1080",
        "fps": book.video_fps or 30,
        "music_file": music_rel,
        "music_volume": book.music_volume,
        "youtube_privacy": settings.youtube_default_privacy,
    }

    folder_name = drive_folder_name or folder_name_for_patch(book.title, patch)
    notebook_src = _NOTEBOOK_TEMPLATE.read_text(encoding="utf-8")
    notebook_src = notebook_src.replace("__PATCH_ID__", str(patch.id))
    notebook_src = notebook_src.replace(
        "__DEFAULT_FOLDER_NAME__", json.dumps(folder_name)[1:-1]
    )
    notebook_src = notebook_src.replace("__HF_TOKEN__", (hf_token or settings.hf_token) or "")
    notebook_src = notebook_src.replace(
        "__VIDEO_CONFIG__", json.dumps(video_config, ensure_ascii=False)
    )
    (package_dir / "colab_kaggle_tts_template.ipynb").write_text(notebook_src, encoding="utf-8")

    return package_dir


def build_export_zip(
    conn: sqlite3.Connection,
    patch: Patch,
    hf_token: str | None = None,
) -> Path:
    """Same package as build_export_package, zipped up for local download."""
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
    """Filename of the merged per-patch wav the batch notebook writes into result/."""
    label = _sanitize_name(patch.name or str(patch.patch_index)) or "patch"
    return f"{patch.patch_index:03d} - {label}.wav"


def result_mp4_name(patch: Patch) -> str:
    """Filename of the rendered per-patch MP4 the batch notebook writes into result/."""
    label = _sanitize_name(patch.name or str(patch.patch_index)) or "patch"
    return f"{patch.patch_index:03d} - {label}.mp4"


def build_batch_export_package(
    conn: sqlite3.Connection,
    patches: list[Patch],
    drive_folder_name: str | None = None,
    hf_token: str | None = None,
) -> tuple[Path, dict]:
    """Write a multi-patch package: batch_manifest.json + the batch notebook at the
    root, one shared voice reference clip, per-patch background images (overlays),
    optional music, and per-patch subfolders under patches/.
    Returns (package_dir, batch_manifest); caller is responsible for deleting the directory."""
    from app import image_overlay

    if not patches:
        raise ValueError("no patches to export")
    book_ids = {p.book_id for p in patches}
    if len(book_ids) != 1:
        raise ValueError("all patches in a batch must belong to the same book")
    book = repository.get_book(conn, patches[0].book_id)
    if book is None:
        raise ValueError(f"book {patches[0].book_id} not found")
    voice_clip = _voice_clip_or_raise(book)

    patches = sorted(patches, key=lambda p: p.patch_index)

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = _TMP_DIR / f"batch_{uuid.uuid4().hex[:8]}"
    package_dir.mkdir(parents=True, exist_ok=True)

    reference_wav_name = "reference" + voice_clip.suffix
    shutil.copyfile(voice_clip, package_dir / reference_wav_name)

    music_rel = _copy_music_to_package(book, conn, package_dir)

    font_path = settings.default_font_path or None
    patch_entries = []
    for patch in patches:
        folder_rel = f"patches/patch_{patch.patch_index:03d}"
        reference_rel = f"../../{reference_wav_name}"
        overlay_path = image_overlay.ensure_patch_overlay(book, patch, font_path)
        manifest = _write_patch_files(
            conn, book, patch, package_dir / folder_rel, reference_rel, overlay_path
        )
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
            "result_mp4": f"result/{result_mp4_name(patch)}",
            "background_image": f"{folder_rel}/{manifest['background_image']}" if manifest.get("background_image") else None,
        })

    timestamp = datetime.now(timezone.utc)
    batch_id = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    video_config = {
        "resolution": book.video_resolution or "1920x1080",
        "fps": book.video_fps or 30,
        "music_file": music_rel,
        "music_volume": book.music_volume,
        "youtube_privacy": settings.youtube_default_privacy,
    }
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
        "video_config": video_config,
    }
    (package_dir / "batch_manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    folder_name = drive_folder_name or folder_name_for_batch(book.title, patches)
    notebook_src = _BATCH_NOTEBOOK_TEMPLATE.read_text(encoding="utf-8")
    notebook_src = notebook_src.replace("__BATCH_ID__", batch_id)
    notebook_src = notebook_src.replace(
        "__DEFAULT_FOLDER_NAME__", json.dumps(folder_name)[1:-1]
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
    """Same package as build_batch_export_package, zipped up for local download."""
    package_dir, _ = build_batch_export_package(
        conn, patches, drive_folder_name=drive_folder_name, hf_token=hf_token
    )
    try:
        zip_path = shutil.make_archive(str(package_dir), "zip", root_dir=package_dir)
    finally:
        shutil.rmtree(package_dir, ignore_errors=True)
    return Path(zip_path)

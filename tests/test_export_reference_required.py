"""Batch export requires a reference clip for reference-based models."""
from __future__ import annotations

import sqlite3

import pytest

from app import db as app_db, drive_export, repository


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    app_db.init_schema(c)
    yield c
    c.close()


def _seed_book_and_patch(conn, voice_clip_path=None):
    now = "2026-01-01T00:00:00+00:00"
    cur = conn.execute(
        "INSERT INTO book (title, original_filename, epub_path, patch_size, status, "
        "voice_clip_path, created_at, updated_at) VALUES ('test', 't.epub', 't.epub', "
        "10, 'ready', ?, ?, ?)",
        (voice_clip_path, now, now),
    )
    book_id = cur.lastrowid
    conn.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) "
        "VALUES (?, 0, 'Chapter 1', 'Some chapter text to synthesize.', 32)",
        (book_id,),
    )
    cur = conn.execute(
        "INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, "
        "status, created_at, updated_at) VALUES (?, 0, 0, 0, 'pending', ?, ?)",
        (book_id, now, now),
    )
    conn.commit()
    return repository.get_patch(conn, cur.lastrowid)


def test_batch_export_requires_voice_reference(conn):
    patch = _seed_book_and_patch(conn, voice_clip_path=None)
    with pytest.raises(ValueError, match="voice reference"):
        drive_export.build_batch_export_package(conn, [patch])


def test_batch_export_bundles_reference(conn, tmp_path, monkeypatch):
    from app import image_overlay

    clip = tmp_path / "voice.wav"
    clip.write_bytes(b"RIFFfakewav")
    monkeypatch.setattr(drive_export, "_TMP_DIR", tmp_path / "export_tmp")
    monkeypatch.setattr(image_overlay, "ensure_patch_overlay", lambda *a, **k: None)
    patch = _seed_book_and_patch(conn, voice_clip_path=str(clip))

    package_dir, batch_manifest = drive_export.build_batch_export_package(conn, [patch])
    try:
        assert batch_manifest["reference_wav"] == "reference.wav"
        assert (package_dir / "reference.wav").exists()
    finally:
        import shutil

        shutil.rmtree(package_dir, ignore_errors=True)

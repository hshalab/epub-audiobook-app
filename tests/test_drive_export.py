from __future__ import annotations

import json
import sqlite3

import pytest

from app import db, drive_export, repository


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_schema(connection)
    now = "2026-01-01T00:00:00+00:00"
    book_id = connection.execute(
        "INSERT INTO book (title, original_filename, epub_path, patch_size, status, created_at, updated_at) "
        "VALUES ('Book', 'book.epub', 'book.epub', 10, 'ready', ?, ?)",
        (now, now),
    ).lastrowid
    connection.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) VALUES (?, 0, 'One', ?, ?)",
        (book_id, "Alpha one. Alpha two. Alpha three.", 34),
    )
    connection.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) VALUES (?, 1, 'Two', ?, ?)",
        (book_id, "Beta one. Beta two.", 19),
    )
    connection.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count, is_excluded) VALUES (?, 2, 'Excluded', 'Never export.', 13, 1)",
        (book_id,),
    )
    connection.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) VALUES (?, 3, 'Whitespace', '   ', 3)",
        (book_id,),
    )
    connection.execute(
        "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) VALUES (?, 4, 'Punctuation', '...!!!', 6)",
        (book_id,),
    )
    patch_id = connection.execute(
        "INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status, max_chars, created_at, updated_at) "
        "VALUES (?, 0, 0, 4, 'pending', 18, ?, ?)",
        (book_id, now, now),
    ).lastrowid
    connection.commit()
    yield connection, repository.get_book(connection, book_id), repository.get_patch(connection, patch_id)
    connection.close()


def test_write_patch_files_exports_chunk_metadata_at_chapter_boundaries(conn, tmp_path):
    connection, book, patch = conn
    manifest = drive_export._write_patch_files(connection, book, patch, tmp_path, None)

    metadata = manifest["chunk_metadata"]
    assert all(
        list(item.keys()) == ["filename", "chapter_index", "chapter_title", "is_chapter_start"]
        for item in metadata
    )
    assert manifest["chunks"] == [f"chunk_{i:03d}.txt" for i in range(5)]
    assert manifest["expected_outputs"] == [f"chunk_{i:03d}.wav" for i in range(5)]
    assert all(isinstance(name, str) for name in manifest["chunks"] + manifest["expected_outputs"])
    assert [item["chapter_index"] for item in metadata] == [0, 0, 0, 1, 1]
    assert [item["is_chapter_start"] for item in metadata] == [True, False, False, True, False]
    assert [
        (tmp_path / name).read_text(encoding="utf-8") for name in manifest["chunks"]
    ] == [
        "Alpha one.",
        "Alpha two.",
        "Alpha three.",
        "Beta one.",
        "Beta two.",
    ]
    assert all("Never export" not in text for text in manifest["chunks"])
    assert [manifest[key] for key in ("patch_id", "book_id", "book_title", "patch_name", "chapter_start", "chapter_end", "max_chars", "chunk_count", "reference_wav", "reference_transcript", "voxcpm_model_id", "background_image")] == [
        patch.id, patch.book_id, book.title, str(patch.patch_index), 0, 4, 18, 5, None, None, "openbmb/VoxCPM2", "background.jpg"
    ]
    saved_manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest == manifest


def test_write_patch_files_rejects_empty_plan(conn, tmp_path):
    connection, book, patch = conn
    connection.execute("UPDATE patch SET chapter_start = 2, chapter_end = 4 WHERE id = ?", (patch.id,))
    connection.commit()
    patch = repository.get_patch(connection, patch.id)
    with pytest.raises(ValueError, match=f"patch {patch.id} has no text to export"):
        drive_export._write_patch_files(connection, book, patch, tmp_path, None)

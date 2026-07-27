from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_book_detail_tts_backends_endpoint_returns_available_backends(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "enable_worker", False)
    with TestClient(app) as client:
        response = client.get("/text-studio/light-tts/backends")

    assert response.status_code == 200
    assert any(item["id"] == "edge-tts" for item in response.json()["backends"])


def test_book_detail_tts_voices_endpoint_returns_backend_voices(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "enable_worker", False)
    with TestClient(app) as client:
        response = client.get("/text-studio/light-tts/voices?backend=edge-tts")

    assert response.status_code == 200
    assert response.json()["voices"]


def test_book_detail_reconciles_estimated_chunk_count(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()
    with TestClient(app) as client:
        conn = client.app.state.conn
        conn.execute(
            """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status, created_at, updated_at)
               VALUES (1, 'Book', 'book.epub', 'book.epub', 10, 'ready', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status, chunk_count, chunk_count_exact, created_at, updated_at)
               VALUES (1, 1, 0, 1, 1, 'pending', 99, 0, ?, ?)""",
            (now, now),
        )
        conn.execute(
            "INSERT INTO chapter (book_id, chapter_index, title, text, char_count) VALUES (1, 1, 'One', 'Short text.', 11)",
        )
        conn.commit()
        response = client.post("/books/1/patches/reconcile-chunk-counts")

    assert response.status_code == 200
    assert response.json()["updated"] == [{"id": 1, "chunk_count": 1}]

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "default_background_image", str(tmp_path / "default.png"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_book(client):
    now = "2026-07-26T00:00:00+00:00"
    conn = client.app.state.conn
    book_id = conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) "
        "VALUES ('B','b.epub','b.epub',1,'ready',?,?)",
        (now, now),
    ).lastrowid
    conn.commit()
    return book_id


@pytest.fixture
def seeded_book_with_patches(client, seeded_book):
    conn = client.app.state.conn
    now = "2026-07-26T00:00:00+00:00"
    for idx in range(3):
        conn.execute(
            "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,chunk_count,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'done','audio.wav',1,?,?)",
            (seeded_book, idx, idx, idx, f"Patch {idx}", now, now),
        )
    conn.commit()
    return seeded_book


def test_enqueue_book_missing_book_returns_404(client, seeded_book):
    response = client.post("/books/999/automation/enqueue")
    assert response.status_code == 404


def test_enqueue_book_creates_one_pipeline_per_patch_and_is_idempotent(client, seeded_book_with_patches):
    first = client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    assert first.status_code == 200
    data = first.json()
    assert len(data["pipeline_ids"]) == 3
    assert all(isinstance(pid, int) for pid in data["pipeline_ids"])

    second = client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    assert second.status_code == 200
    assert second.json()["pipeline_ids"] == data["pipeline_ids"]


def test_enqueue_respects_automation_config_defaults(client, seeded_book_with_patches):
    conn = client.app.state.conn
    conn.execute(
        "INSERT INTO automation_settings (id,schema_version,config_json,created_at,updated_at) "
        "VALUES (1,1,'{\"video\":{\"fps\":60}}','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    response = client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    data = response.json()
    conn = client.app.state.conn
    for pid in data["pipeline_ids"]:
        row = conn.execute("SELECT config_snapshot FROM patch_pipeline WHERE id=?", (pid,)).fetchone()
        import json
        snap = json.loads(row[0])
        assert snap["automation"]["video"]["fps"] == 60


def test_retry_unknown_book_returns_404(client, seeded_book_with_patches):
    response = client.post("/books/999/automation/retry/1")
    assert response.status_code == 404


def test_retry_unknown_patch_returns_404(client, seeded_book):
    response = client.post(f"/books/{seeded_book}/automation/retry/999")
    assert response.status_code == 404


def test_retry_marks_failed_stage_pending_and_clears_error(client, seeded_book_with_patches):
    client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    conn = client.app.state.conn
    pipeline = conn.execute("SELECT * FROM patch_pipeline WHERE patch_id=1").fetchone()
    conn.execute(
        "UPDATE patch_pipeline SET stage='video', video_status='failed', last_error='boom', next_retry_at='2099-01-01T00:00:00+00:00' WHERE id=?",
        (pipeline["id"],),
    )
    conn.commit()

    response = client.post(f"/books/{seeded_book_with_patches}/automation/retry/1")
    assert response.status_code == 200
    data = response.json()
    assert data["stage"] == "video"
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (data["id"],)).fetchone()
    assert row["video_status"] == "pending"
    assert row["last_error"] is None
    assert row["next_retry_at"] is None


def test_retry_keeps_completed_thumbnail_for_video_retry(client, seeded_book_with_patches):
    client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    conn = client.app.state.conn
    pipeline = conn.execute("SELECT * FROM patch_pipeline WHERE patch_id=1").fetchone()
    conn.execute(
        "UPDATE patch_pipeline SET stage='video', thumbnail_status='done', video_status='failed', last_error='boom' WHERE id=?",
        (pipeline["id"],),
    )
    conn.commit()

    response = client.post(f"/books/{seeded_book_with_patches}/automation/retry/1")
    assert response.status_code == 200
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (response.json()["id"],)).fetchone()
    assert row["thumbnail_status"] == "done"
    assert row["video_status"] == "pending"

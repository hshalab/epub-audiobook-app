import asyncio
import sqlite3
import threading
import pytest

from app import db, youtube
from app.upload_worker import UploadWorker
from app.video_integrity import ValidationFacts, ValidationResult


@pytest.fixture(autouse=True)
def _valid_preflight(monkeypatch):
    monkeypatch.setattr(youtube, "validate_upload_file", lambda *a: ValidationResult(
        True, None, "", (), ValidationFacts(), 0))


class TrackingLock:
    def __init__(self): self.held = False
    def __enter__(self): self.held = True; return self
    def __exit__(self, *args): self.held = False


def test_upload_network_runs_on_isolated_connection_without_shared_lock(tmp_path, monkeypatch):
    path = tmp_path / "worker.db"
    conn = db.connect(str(path)); db.init_schema(conn)
    upload_id = youtube.enqueue_upload(conn, "video.mp4", "Title")
    lock = TrackingLock()
    seen = []
    def process(upload_conn, current_id):
        seen.append((upload_conn is conn, lock.held))
        upload_conn.execute("UPDATE youtube_uploads SET status='done', youtube_video_id='yt' WHERE id=?", (current_id,)); upload_conn.commit()
        return {"status": "done", "youtube_video_id": "yt"}
    monkeypatch.setattr(youtube, "process_upload", process)
    monkeypatch.setattr(youtube, "publish_completed_upload", lambda upload_conn, current_id: seen.append((upload_conn is conn, lock.held)))
    worker = UploadWorker(conn, lock)
    asyncio.run(worker._process_upload({"id": upload_id, "video_id": None}))
    assert seen == [(False, False), (False, False)]
    assert conn.execute("SELECT status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()[0] == "done"


def test_in_memory_upload_is_rejected_without_network(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    upload_id = youtube.enqueue_upload(conn, "video.mp4", "Title")
    lock = TrackingLock(); calls = []
    monkeypatch.setattr(youtube, "process_upload", lambda *args: calls.append("process"))
    monkeypatch.setattr(youtube, "publish_completed_upload", lambda *args: calls.append("publish"))
    asyncio.run(UploadWorker(conn, lock)._process_upload({"id": upload_id, "video_id": None}))
    row = conn.execute("SELECT status, error_message FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert calls == []
    assert row[0] == "failed" and "in-memory" in row[1]
    assert not lock.held


def test_in_memory_rejection_restores_video_from_uploading(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO videos (file_path, filename, title, created_at, updated_at) VALUES ('v', 'v.mp4', 'Title', 'now', 'now')")
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    upload_id = youtube.enqueue_upload(conn, "v", "Title", video_id=video_id)
    asyncio.run(UploadWorker(conn, TrackingLock())._process_upload({"id": upload_id, "video_id": video_id}))
    row = conn.execute("SELECT upload_status, error_message FROM videos WHERE id=?", (video_id,)).fetchone()
    assert row[0] != "uploading" and "in-memory" in row[1]


def test_failed_upload_does_not_mark_video_uploaded(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "worker.db")); db.init_schema(conn)
    conn.execute("INSERT INTO videos (file_path, filename, title, created_at, updated_at) VALUES ('v', 'v.mp4', 'Title', 'now', 'now')")
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    upload_id = youtube.enqueue_upload(conn, "v", "Title", video_id=video_id)
    monkeypatch.setattr(youtube, "process_upload", lambda *args: {"status": "failed", "youtube_video_id": ""})
    asyncio.run(UploadWorker(conn, TrackingLock())._process_upload({"id": upload_id, "video_id": video_id}))
    assert conn.execute("SELECT upload_status FROM videos WHERE id=?", (video_id,)).fetchone()[0] != "uploaded"


def test_successful_upload_marks_video_uploaded(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "worker.db")); db.init_schema(conn)
    conn.execute("INSERT INTO videos (file_path, filename, title, created_at, updated_at) VALUES ('v', 'v.mp4', 'Title', 'now', 'now')")
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    upload_id = youtube.enqueue_upload(conn, "v", "Title", video_id=video_id)
    monkeypatch.setattr(youtube, "process_upload", lambda *args: {"status": "done", "youtube_video_id": "yt"})
    monkeypatch.setattr(youtube, "publish_completed_upload", lambda *args: None)
    asyncio.run(UploadWorker(conn, TrackingLock())._process_upload({"id": upload_id, "video_id": video_id}))
    assert conn.execute("SELECT upload_status FROM videos WHERE id=?", (video_id,)).fetchone()[0] == "uploaded"


def test_postprocess_auth_keeps_upload_done_and_syncs_pipeline(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "worker.db")); db.init_schema(conn)
    conn.execute("INSERT INTO videos (file_path, filename, title, created_at, updated_at) VALUES ('v', 'v.mp4', 'Title', 'now', 'now')")
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    upload_id = youtube.enqueue_upload(conn, "v", "Title", video_id=video_id)
    conn.execute("UPDATE youtube_uploads SET status='done', youtube_video_id='yt' WHERE id=?", (upload_id,)); conn.commit()
    monkeypatch.setattr(youtube, "process_upload", lambda *args: {"status": "done", "youtube_video_id": "yt"})
    monkeypatch.setattr(youtube, "publish_completed_upload", lambda *args: {"status": "auth_required", "error": "auth"})
    monkeypatch.setattr("app.upload_worker.sync_pipeline_from_upload", lambda conn, upload_id: conn.execute("UPDATE patch_pipeline SET stage='auth_required' WHERE youtube_upload_id=?", (upload_id,)))
    asyncio.run(UploadWorker(conn, TrackingLock())._process_upload({"id": upload_id, "video_id": video_id}))
    assert conn.execute("SELECT status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()[0] == "done"
    assert conn.execute("SELECT upload_status FROM videos WHERE id=?", (video_id,)).fetchone()[0] == "uploaded"

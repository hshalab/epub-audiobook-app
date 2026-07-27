import json
from pathlib import Path

import pytest

from app import db, youtube


@pytest.fixture
def db_conn(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_schema(conn)
    return conn


class FakeYouTubeService:
    def videos(self):
        return self

    def insert(self, *args, **kwargs):
        return self

    def next_chunk(self):
        return None, {"id": "youtube_video_id"}


@pytest.fixture(autouse=True)
def fake_service(monkeypatch):
    monkeypatch.setattr(
        "app.youtube.get_youtube_service",
        lambda conn: FakeYouTubeService(),
    )


def test_pending_upload_updates_same_row(db_conn, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    upload_id = youtube.enqueue_upload(db_conn, str(video), "Title")
    result = youtube.process_upload(db_conn, upload_id)
    rows = db_conn.execute("SELECT * FROM youtube_uploads").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == upload_id
    assert rows[0]["youtube_video_id"] == result["youtube_video_id"]

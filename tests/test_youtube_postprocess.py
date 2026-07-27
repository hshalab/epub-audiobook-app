import json
from pathlib import Path

import pytest

from app import automation_repository, db, youtube


@pytest.fixture
def db_conn(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_schema(conn)
    return conn


class _FakeRequest:
    def __init__(self, response):
        self._response = response
    def execute(self):
        return self._response


class _FakeThumbnails:
    def __init__(self, parent):
        self._parent = parent
    def set(self, *args, **kwargs):
        self._parent.thumbnails_set_calls += 1
        return _FakeRequest({"kind": "youtube#thumbnail"})


class _FakePlaylists:
    def __init__(self, parent):
        self._parent = parent
    def list(self, *args, **kwargs):
        return _FakeRequest({"items": [{"id": "pl_existing"}]})
    def insert(self, *args, **kwargs):
        self._parent.playlists_insert_calls += 1
        return _FakeRequest({"id": "pl_new", "snippet": {"title": ""}})


class _FakePlaylistItems:
    def __init__(self, parent):
        self._parent = parent
    def list(self, *args, **kwargs):
        return _FakeRequest({"items": []})
    def insert(self, *args, **kwargs):
        self._parent.playlist_items_insert_calls += 1
        return _FakeRequest({"id": "item_fake"})


class FakeYouTubeService:
    def __init__(self):
        self.thumbnails_set_calls = 0
        self.playlists_insert_calls = 0
        self.playlist_items_insert_calls = 0

    def thumbnails(self):
        return _FakeThumbnails(self)
    def playlists(self):
        return _FakePlaylists(self)
    def playlistItems(self):
        return _FakePlaylistItems(self)


@pytest.fixture
def fake_service(monkeypatch):
    service = FakeYouTubeService()
    monkeypatch.setattr("app.youtube.get_youtube_service", lambda conn: service)
    return service


@pytest.fixture
def uploaded_pipeline(db_conn, tmp_path):
    now = "2026-07-26T00:00:00+00:00"
    book_id = db_conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) VALUES ('B','b.epub','b.epub',1,'ready',?,?)",
        (now, now),
    ).lastrowid
    patch_id = db_conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) VALUES (?,0,0,0,'P','done','audio.wav',?,?)",
        (book_id, now, now),
    ).lastrowid
    thumbnail = tmp_path / "thumb.png"
    thumbnail.write_bytes(b"png")
    db_conn.execute(
        "INSERT INTO youtube_credentials (access_token,refresh_token,token_expiry,channel_id,channel_name,created_at,updated_at) VALUES ('tok','ref','2099-01-01T00:00:00+00:00','ch_fake','Chan',?,?)",
        (now, now),
    )
    upload_id = db_conn.execute(
        "INSERT INTO youtube_uploads (video_path,youtube_video_id,title,status,thumbnail_status,playlist_status,created_at) VALUES (?,?,?,'done','pending','pending',?)",
        (str(tmp_path / "v.mp4"), "yt_video_id", "Title", now),
    ).lastrowid
    # Create automation_settings row with playlist config
    db_conn.execute(
        "INSERT INTO automation_settings (id,schema_version,config_json,created_at,updated_at) VALUES (1,1,?,?,?)",
        (json.dumps({"youtube": {"playlist_mode": "auto-create", "playlist_title_template": "{book_title}", "playlist_privacy": "private"}}), now, now),
    )
    # Store metadata_snapshot on upload with rendered playlist values
    db_conn.execute(
        "UPDATE youtube_uploads SET metadata_snapshot=? WHERE id=?",
        (json.dumps({
            "automation": {
                "youtube": {
                    "playlist_mode": "auto-create",
                    "playlist_title_template": "{book_title}",
                    "playlist_description_template": "",
                    "playlist_privacy": "private",
                },
            },
            "background_fallback": str(thumbnail),
        }), upload_id),
    )
    db_conn.commit()
    # Enqueue pipeline and link it to the upload
    pipeline = automation_repository.enqueue_patch_pipeline(db_conn, patch_id)
    assert pipeline is not None, "enqueue_patch_pipeline returned None"
    db_conn.execute(
        "UPDATE patch_pipeline SET stage='upload', upload_status='done', youtube_upload_id=?, thumbnail_status='done', thumbnail_path=?, video_status='done' WHERE id=?",
        (upload_id, str(thumbnail), pipeline["id"]),
    )
    db_conn.commit()
    return type("obj", (), {"upload_id": upload_id, "book_id": book_id, "youtube_video_id": "yt_video_id", "failed_stage": "playlist"})()


def test_postprocess_sets_thumbnail_and_reuses_playlist(db_conn, fake_service, uploaded_pipeline):
    first = youtube.postprocess_upload(db_conn, uploaded_pipeline.upload_id)
    second = youtube.postprocess_upload(db_conn, uploaded_pipeline.upload_id)
    assert first["status"] == second["status"] == "published"
    assert fake_service.thumbnails_set_calls == 1
    assert fake_service.playlists_insert_calls == 1
    assert fake_service.playlist_items_insert_calls == 1


def test_postprocess_no_playlist_mode(db_conn, fake_service, tmp_path):
    now = "2026-07-26T00:00:00+00:00"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    upload_id = youtube.enqueue_upload(db_conn, str(video), "Title")
    db_conn.execute(
        "UPDATE youtube_uploads SET youtube_video_id='yt_id', status='done', metadata_snapshot=? WHERE id=?",
        (json.dumps({"automation": {"youtube": {"playlist_mode": "none"}}}), upload_id),
    )
    db_conn.commit()
    result = youtube.postprocess_upload(db_conn, upload_id)
    assert result["status"] == "published"
    assert fake_service.playlists_insert_calls == 0
    assert fake_service.playlist_items_insert_calls == 0
    row = db_conn.execute("SELECT playlist_status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert row["playlist_status"] == "done"


def test_postprocess_existing_playlist_mode(db_conn, fake_service, tmp_path):
    now = "2026-07-26T00:00:00+00:00"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    upload_id = youtube.enqueue_upload(db_conn, str(video), "Title")
    db_conn.execute(
        "UPDATE youtube_uploads SET youtube_video_id='yt_id', status='done', metadata_snapshot=? WHERE id=?",
        (json.dumps({"automation": {"youtube": {"playlist_mode": "existing", "playlist_id": "pl_fixed"}}}), upload_id),
    )
    db_conn.commit()
    result = youtube.postprocess_upload(db_conn, upload_id)
    assert result["status"] == "published"
    assert fake_service.playlists_insert_calls == 0
    assert fake_service.playlist_items_insert_calls == 1
    row = db_conn.execute("SELECT playlist_status, playlist_id FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert row["playlist_status"] == "done"
    assert row["playlist_id"] == "pl_fixed"


def test_postprocess_thumbnail_retry_after_failure(db_conn, fake_service, tmp_path):
    now = "2026-07-26T00:00:00+00:00"
    upload_id = db_conn.execute(
        "INSERT INTO youtube_uploads (video_path,youtube_video_id,title,status,thumbnail_status,playlist_status,created_at) VALUES (?,?,?,'done','failed','failed',?)",
        (str(tmp_path / "v.mp4"), "yt_id", "Title", now),
    ).lastrowid
    db_conn.commit()
    result = youtube.postprocess_upload(db_conn, upload_id)
    assert result["status"] == "published"
    # Failed status means no thumbnail was set, no playlist was created
    assert fake_service.thumbnails_set_calls == 0
    assert fake_service.playlists_insert_calls == 0

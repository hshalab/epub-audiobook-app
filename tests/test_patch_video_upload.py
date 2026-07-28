import json
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes import patches


def test_upload_patch_video_saves_where_preview_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(
        patches.repository, "get_patch",
        lambda conn, patch_id: SimpleNamespace(
            id=patch_id, book_id=7, audio_path="audio.wav", image_path="image.jpg", patch_index=0,
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            "/books/7/patches/11/video",
            files={"video": ("patch.mp4", b"video-data", "video/mp4")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert (tmp_path / "books" / "7" / "patch_videos" / "11.mp4").read_bytes() == b"video-data"
    with TestClient(app) as client:
        library = client.get("/video/api/videos")
    assert any(v["filename"] == "patch_7_11.mp4" for v in library.json()["videos"])


def test_upload_patch_audio_marks_patch_done(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
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
            """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status, created_at, updated_at)
               VALUES (1, 1, 0, 1, 1, 'pending', ?, ?)""",
            (now, now),
        )
        conn.commit()
        response = client.post(
            "/books/1/patches/1/upload-audio",
            files={"audio": ("result.wav", b"audio-data", "audio/wav")},
        )
        row = conn.execute("SELECT status, audio_path FROM patch WHERE id = 1").fetchone()

    assert response.status_code == 200
    assert row["status"] == "done"
    assert Path(row["audio_path"]).read_bytes() == b"audio-data"


def test_generate_patch_video_mixes_book_background_music(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()
    audio = tmp_path / "narration.wav"
    image = tmp_path / "background.jpg"
    music = tmp_path / "music.mp3"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")
    music.write_bytes(b"music")
    captured = {}

    def render(*args, **kwargs):
        captured.update(kwargs)
        Path(args[2]).write_bytes(b"video")

    monkeypatch.setattr(patches.video_gen, "generate_segment", render)
    monkeypatch.setattr(patches.image_overlay, "ensure_patch_overlay", lambda *args, **kwargs: str(image))

    with TestClient(app) as client:
        conn = client.app.state.conn
        conn.execute(
            "INSERT INTO music (id, name, file_path, created_at) VALUES (1, 'Music', ?, ?)",
            (str(music), now),
        )
        conn.execute(
            """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                                   music_id, music_volume, created_at, updated_at)
               VALUES (1, 'Book', 'book.epub', 'book.epub', 10, 'done', 1, 0.3, ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status,
                                    audio_path, image_path, created_at, updated_at)
               VALUES (1, 1, 0, 1, 1, 'done', ?, ?, ?, ?)""",
            (str(audio), str(image), now, now),
        )
        conn.commit()
        response = client.post("/books/1/patches/1/generate-video?ajax=1")

    assert response.status_code == 200
    assert captured["music_path"] == str(music)
    assert captured["music_volume"] == 0.3


def test_generate_patch_video_appends_intro_and_outro(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()
    audio = tmp_path / "narration.wav"
    image = tmp_path / "background.jpg"
    audio.write_bytes(b"audio")
    image.write_bytes(b"image")
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    intro = voices_dir / "intro.mp3"
    outro = voices_dir / "outro.mp3"
    intro.write_bytes(b"intro")
    outro.write_bytes(b"outro")
    segment_calls = []
    concat_calls = []

    def render(*args, **kwargs):
        segment_calls.append((args[0], args[1], args[2]))
        Path(args[2]).write_bytes(b"video")

    def concat(segments, out_path, **kwargs):
        concat_calls.append((list(segments), out_path))
        Path(out_path).write_bytes(b"final")

    monkeypatch.setattr(patches.video_gen, "generate_segment", render)
    monkeypatch.setattr(patches.video_gen, "concat_segments", concat)
    monkeypatch.setattr(patches.image_overlay, "ensure_patch_overlay", lambda *args, **kwargs: str(image))

    with TestClient(app) as client:
        conn = client.app.state.conn
        conn.execute(
            """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                                   automation_config, created_at, updated_at)
               VALUES (1, 'Book', 'book.epub', 'book.epub', 10, 'done', ?, ?, ?)""",
            (json.dumps({"video": {"intro_voice": "intro.mp3", "outro_voice": "outro.mp3"}}), now, now),
        )
        conn.execute(
            """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status,
                                    audio_path, image_path, created_at, updated_at)
               VALUES (1, 1, 0, 1, 1, 'done', ?, ?, ?, ?)""",
            (str(audio), str(image), now, now),
        )
        conn.commit()
        response = client.post("/books/1/patches/1/generate-video?ajax=1")

    assert response.status_code == 200
    audios = [call[1] for call in segment_calls]
    assert audios == [str(intro), str(audio), str(outro)]
    assert len(concat_calls) == 1
    segments, out_path = concat_calls[0]
    assert len(segments) == 3
    assert out_path == str(tmp_path / "books" / "1" / "patch_videos" / "1.mp4")
    assert Path(out_path).read_bytes() == b"final"


def _seed_book_with_patch_video(conn, tmp_path) -> Path:
    """Insert a book + done patch and put an MP4 where the patch video routes read."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status, created_at, updated_at)
           VALUES (1, 'Book', 'book.epub', 'book.epub', 10, 'done', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status,
                                audio_path, created_at, updated_at)
           VALUES (1, 1, 0, 1, 1, 'done', 'narration.wav', ?, ?)""",
        (now, now),
    )
    video_path = tmp_path / "books" / "1" / "patch_videos" / "1.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    conn.execute(
        """INSERT INTO videos (id, filename, file_path, file_size_bytes, created_at, updated_at)
           VALUES (1, 'patch_1_1.mp4', ?, 5, ?, ?)""",
        (str(video_path), now, now),
    )
    conn.commit()
    return video_path


def test_delete_patch_video_removes_file_and_library_row(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)

    with TestClient(app) as client:
        conn = client.app.state.conn
        video_path = _seed_book_with_patch_video(conn, tmp_path)
        response = client.post("/books/1/patches/1/video/delete?ajax=1")
        remaining = conn.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert not video_path.exists()
    assert remaining == 0


def test_delete_patch_video_resets_publish_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()

    with TestClient(app) as client:
        conn = client.app.state.conn
        video_path = _seed_book_with_patch_video(conn, tmp_path)
        conn.execute(
            """INSERT INTO patch_pipeline (patch_id, stage, video_status, upload_status,
                                             video_id, video_path, config_snapshot, media_snapshot,
                                             created_at, updated_at)
               VALUES (1, 'upload', 'done', 'pending', 1, ?, '{}', '{}', ?, ?)""",
            (str(video_path), now, now),
        )
        conn.commit()
        client.post("/books/1/patches/1/video/delete?ajax=1")
        row = conn.execute("SELECT * FROM patch_pipeline WHERE patch_id = 1").fetchone()

    assert row["video_status"] == "pending"
    assert row["stage"] == "video"
    assert row["video_id"] is None
    assert row["video_path"] is None


def test_delete_patch_video_keeps_stage_when_already_uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()

    with TestClient(app) as client:
        conn = client.app.state.conn
        video_path = _seed_book_with_patch_video(conn, tmp_path)
        conn.execute(
            """INSERT INTO patch_pipeline (patch_id, stage, video_status, upload_status,
                                             video_id, video_path, config_snapshot, media_snapshot,
                                             created_at, updated_at)
               VALUES (1, 'playlist', 'done', 'done', 1, ?, '{}', '{}', ?, ?)""",
            (str(video_path), now, now),
        )
        conn.commit()
        client.post("/books/1/patches/1/video/delete?ajax=1")
        row = conn.execute("SELECT * FROM patch_pipeline WHERE patch_id = 1").fetchone()

    assert row["stage"] == "playlist"
    assert row["upload_status"] == "done"
    assert row["video_path"] is None


def test_delete_patch_video_rejects_patch_from_another_book(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)

    with TestClient(app) as client:
        conn = client.app.state.conn
        video_path = _seed_book_with_patch_video(conn, tmp_path)
        response = client.post("/books/99/patches/1/video/delete?ajax=1")

    assert response.status_code == 404
    assert video_path.exists()


def test_generate_patch_video_uses_saved_shared_video_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)
    now = datetime.now(timezone.utc).isoformat()
    audio = tmp_path / "narration.wav"
    fallback = tmp_path / "fallback.jpg"
    bg1 = tmp_path / "bg1.jpg"
    bg2 = tmp_path / "bg2.jpg"
    for path in (audio, fallback, bg1, bg2):
        path.write_bytes(b"x")
    captured = {}

    def render_sequence(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        Path(args[2]).write_bytes(b"video")

    monkeypatch.setattr(patches.video_gen, "generate_background_sequence", render_sequence)
    monkeypatch.setattr(patches.video_gen, "generate_segment", lambda *args, **kwargs: Path(args[2]).write_bytes(b"video"))
    monkeypatch.setattr(patches.image_overlay, "ensure_patch_overlay", lambda *args, **kwargs: str(fallback))

    with TestClient(app) as client:
        conn = client.app.state.conn
        conn.execute(
            """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                                   background_image_path, automation_config, created_at, updated_at)
               VALUES (1, 'Book', 'book.epub', 'book.epub', 10, 'done', ?, ?, ?, ?)""",
            (str(fallback), json.dumps({"video": {
                "backgrounds": [str(bg1), str(bg2)],
                "background_mode": "random",
                "image_duration_seconds": 7,
                "crossfade_enabled": True,
                "crossfade_seconds": 1.5,
                "ken_burns_enabled": True,
                "progress_bar_enabled": True,
            }}), now, now),
        )
        conn.execute(
            """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end, status,
                                    audio_path, created_at, updated_at)
               VALUES (1, 1, 0, 1, 1, 'done', ?, ?, ?)""",
            (str(audio), now, now),
        )
        conn.commit()
        response = client.post("/books/1/patches/1/generate-video?ajax=1")

    assert response.status_code == 200
    assert captured["args"][:2] == ([str(bg1), str(bg2)], str(audio))
    assert captured["image_duration"] == 7
    assert captured["mode"] == "random"
    assert captured["crossfade"] is True
    assert captured["crossfade_seconds"] == 1.5
    assert captured["ken_burns"] is True
    assert captured["progress_bar"] is True

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
    monkeypatch.setattr(patches.image_overlay, "ensure_patch_overlay", lambda *args: str(image))

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

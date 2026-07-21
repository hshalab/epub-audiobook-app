from types import SimpleNamespace

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

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings
    from app.routes import video

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "default_background_image", str(tmp_path / "default.png"))
    monkeypatch.setattr(video, "_BACKGROUNDS_DIR", tmp_path / "backgrounds")
    with TestClient(app) as test_client:
        yield test_client


_SAFE_FIELDS = [
    "resolution",
    "fps",
    "encoder",
    "quality",
    "preset",
    "audio_bitrate",
    "background_duration_seconds",
    "position",
    "playlist_mode",
    "playlist_privacy",
]


def test_automation_settings_page_contains_safe_fields_only(client):
    response = client.get("/automation/settings-page")
    assert response.status_code == 200
    html = response.text.lower()
    for field in _SAFE_FIELDS:
        assert field in html, f"expected '{field}' to appear in settings page HTML"
    assert "ffmpeg_args" not in html


def test_youtube_page_contains_postprocess_columns(client):
    response = client.get("/youtube")
    assert response.status_code == 200
    html = response.text.lower()
    assert "thumbnail" in html
    assert "playlist" in html

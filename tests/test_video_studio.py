"""Tests for the Video Creator studio: live overlay preview + batch audio serving."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import settings
    from app.routes import video as video_routes

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(video_routes, "_TMP_DIR", tmp_path / "tmp" / "video_creator")
    monkeypatch.setattr(video_routes, "_VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(video_routes, "_BACKGROUNDS_DIR", tmp_path / "backgrounds")
    with TestClient(app) as c:
        yield c


def _make_png(path: Path, size=(640, 360), color=(20, 20, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(str(path), "PNG")
    return path


def _rect(resp) -> dict:
    assert "X-Overlay-Rect" in resp.headers
    return json.loads(resp.headers["X-Overlay-Rect"])


# ---------------------------------------------------------------------------
# Overlay preview
# ---------------------------------------------------------------------------


def test_overlay_preview_requires_a_background(client, tmp_path):
    from app.config import settings

    # default_background_image points nowhere and backgrounds dir is empty.
    settings_default = settings.default_background_image
    resp = client.get("/video/overlay-preview")
    if Path(settings_default).exists():
        pytest.skip("a real default background is configured in this environment")
    assert resp.status_code == 400


def test_overlay_preview_uses_whitelisted_background(client, tmp_path):
    from app.routes import video as video_routes

    bg = _make_png(video_routes._BACKGROUNDS_DIR / "alt.png", size=(320, 200))
    resp = client.get("/video/overlay-preview", params={"background_path": str(bg)})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    rect = _rect(resp)
    assert (rect["img_w"], rect["img_h"]) == (320, 200)
    assert rect["w"] > 0 and rect["h"] > 0


def test_overlay_preview_rejects_path_outside_whitelist(client, tmp_path):
    from app.routes import video as video_routes

    _make_png(video_routes._BACKGROUNDS_DIR / "alt.png", size=(320, 200))
    sneaky = _make_png(tmp_path / "secret" / "sneaky.png", size=(111, 99))
    resp = client.get("/video/overlay-preview", params={"background_path": str(sneaky)})
    # Falls back to a valid background rather than reading the arbitrary path;
    # here there's no default configured for the test env, so it 400s instead
    # of returning the sneaky image's dimensions.
    if resp.status_code == 200:
        rect = _rect(resp)
        assert (rect["img_w"], rect["img_h"]) != (111, 99)
    else:
        assert resp.status_code == 400


def test_overlay_preview_offset_shifts_rect(client, tmp_path):
    from app.routes import video as video_routes

    # 640x360 keeps the default placeholder text on one line, matching the
    # book-studio tests' background size so the rect math is predictable.
    bg = _make_png(video_routes._BACKGROUNDS_DIR / "alt.png", size=(640, 360))
    base = _rect(client.get("/video/overlay-preview", params={
        "background_path": str(bg), "position": "top",
    }))
    shifted = _rect(client.get("/video/overlay-preview", params={
        "background_path": str(bg), "position": "top", "offset_x": "30", "offset_y": "12",
    }))
    assert shifted["x"] == base["x"] + 30
    assert shifted["y"] == base["y"] + 12


def test_overlay_preview_position_changes_rect(client, tmp_path):
    from app.routes import video as video_routes

    bg = _make_png(video_routes._BACKGROUNDS_DIR / "alt.png", size=(640, 360))
    top = _rect(client.get("/video/overlay-preview", params={
        "background_path": str(bg), "position": "top",
    }))
    bottom = _rect(client.get("/video/overlay-preview", params={
        "background_path": str(bg), "position": "bottom",
    }))
    assert bottom["y"] > top["y"]


def test_overlay_preview_offset_clamped_inside_image(client, tmp_path):
    from app.routes import video as video_routes

    bg = _make_png(video_routes._BACKGROUNDS_DIR / "alt.png", size=(320, 200))
    rect = _rect(client.get("/video/overlay-preview", params={
        "background_path": str(bg), "offset_x": "5000", "offset_y": "5000",
    }))
    assert rect["x"] + rect["w"] <= rect["img_w"]
    assert rect["y"] + rect["h"] <= rect["img_h"]


# ---------------------------------------------------------------------------
# Batch audio serving
# ---------------------------------------------------------------------------


def _seed_batch(client, tmp_path) -> str:
    from app.routes import video as video_routes

    batch_id = "testbatch1"
    batch_dir = video_routes._TMP_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    audio_path = batch_dir / "audio_0.wav"
    audio_path.write_bytes(b"RIFFfake")
    meta = {
        "batch_id": batch_id,
        "files": [{
            "index": 0, "original_name": "chuong1.wav", "saved_name": "audio_0.wav",
            "size_bytes": audio_path.stat().st_size, "path": str(audio_path),
        }],
        "created_at": 0,
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return batch_id


def test_serve_batch_audio_returns_file(client, tmp_path):
    batch_id = _seed_batch(client, tmp_path)
    resp = client.get(f"/video/batch/{batch_id}/audio/0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"RIFFfake"


def test_serve_batch_audio_404_unknown_batch(client, tmp_path):
    resp = client.get("/video/batch/doesnotexist/audio/0")
    assert resp.status_code == 404


def test_serve_batch_audio_404_unknown_index(client, tmp_path):
    batch_id = _seed_batch(client, tmp_path)
    resp = client.get(f"/video/batch/{batch_id}/audio/9")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Overlay rendering with drag offset (final render must match preview)
# ---------------------------------------------------------------------------


def test_render_overlay_for_batch_applies_offset(tmp_path):
    from app.routes import video as video_routes
    from app import image_overlay

    bg = _make_png(tmp_path / "bg.png", size=(640, 360))
    out_a = tmp_path / "a.png"
    out_b = tmp_path / "b.png"
    video_routes._render_overlay_for_batch(bg, "Sample", {"position": "top"}, out_a)
    video_routes._render_overlay_for_batch(
        bg, "Sample", {"position": "top", "offset_x": 50, "offset_y": 5}, out_b,
    )
    assert Image.open(out_a).tobytes() != Image.open(out_b).tobytes()


def test_render_overlay_for_batch_keeps_shadow_enabled_by_default(tmp_path):
    """Video Creator has no shadow toggle in its UI - it must stay on by
    default even though overlay_cfg_from_values() defaults shadow to off."""
    from app.routes import video as video_routes
    from app import image_overlay

    values = video_routes._overlay_values_with_defaults({"position": "top"})
    cfg = image_overlay.overlay_cfg_from_values(values)
    assert cfg["shadow"]["enabled"] is True


def test_video_creator_page_renders_studio(client):
    resp = client.get("/video")
    assert resp.status_code == 200
    assert 'id="ov-drag-rect"' in resp.text
    assert 'id="mix-play"' in resp.text

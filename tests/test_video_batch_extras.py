"""Overlay rendering helper for the Video Creator batch route."""
from pathlib import Path

from PIL import Image

from app.routes import video as video_routes


def _make_bg(tmp_path: Path) -> Path:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (640, 360), (10, 30, 60)).save(bg)
    return bg


def test_render_overlay_creates_png(tmp_path):
    bg = _make_bg(tmp_path)
    out = tmp_path / "out.png"
    result = video_routes._render_overlay_for_batch(
        bg, "Xin chào Việt Nam",
        {"position": "bottom", "font_size": 40, "text_color": "#FFDD00"},
        out,
    )
    assert result == out
    assert out.exists()
    img = Image.open(out)
    assert img.size == (640, 360)


def test_render_overlay_returns_none_on_bad_background(tmp_path):
    result = video_routes._render_overlay_for_batch(
        tmp_path / "missing.png", "text", {}, tmp_path / "out.png",
    )
    assert result is None

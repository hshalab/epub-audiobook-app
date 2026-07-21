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


def test_convert_overlay_config_to_flat_maps_nested_shadow_box_marquee():
    """A full nested overlay config (the shape the frontend must always
    send, whether for a per-file override or the batch-wide default) maps
    every field through to the flat shape the renderer consumes."""
    cfg = {
        "position": "bottom", "alignment": "left", "font_size": 44,
        "text_color": "#00FF00", "margin": 10, "offset_x": 5, "offset_y": -5,
        "shadow": {"enabled": True, "color": "#111111", "offset": 4},
        "box": {"enabled": True, "color": "#222222", "opacity": 70,
                "padding_x": 12, "padding_y": 6, "radius": 4},
        "marquee": {"enabled": True, "height": 50, "font_size": 30,
                    "text_color": "#333333", "bg_color": "#444444",
                    "bg_opacity": 90, "speed_px_per_sec": 80},
    }
    flat = video_routes._convert_overlay_config_to_flat(cfg)
    assert flat["position"] == "bottom"
    assert flat["shadow_enabled"] == "on"
    assert flat["shadow_color"] == "#111111"
    assert flat["shadow_offset"] == 4
    assert flat["box_enabled"] == "on"
    assert flat["box_opacity"] == 70
    assert flat["marquee_enabled"] == "on"
    assert flat["marquee_speed"] == 80


def test_convert_overlay_config_to_flat_bare_text_only_loses_shadow_box():
    """Guards the historical bug: a payload shaped like {"text": ...} (no
    nested shadow/box/marquee) silently disables all three. This is why the
    frontend must always send the FULL nested shape as the batch-wide
    `overlay` fallback, not just {text}."""
    flat = video_routes._convert_overlay_config_to_flat({"text": "hello"})
    assert flat["shadow_enabled"] == "off"
    assert flat["box_enabled"] == "off"
    assert flat["marquee_enabled"] == "off"

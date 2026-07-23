"""Looping video backgrounds: generate_segment must stream-loop the clip,
drop its audio, and skip Ken Burns / marquee; generate_full_video must skip
text overlays for a video background."""
from unittest.mock import patch

import pytest

from app import video_gen
from app.models import Book, Patch


def _fake_run(captured):
    def run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            stdout = ""
            returncode = 0

        return R()

    return run


# ---------------------------------------------------------------------------
# is_video_background
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("clip.mp4", True),
    ("clip.MP4", True),
    ("clip.webm", True),
    ("movie.mov", True),
    ("photo.jpg", False),
    ("photo.png", False),
    ("photo.webp", False),
    ("", False),
    (None, False),
])
def test_is_video_background(path, expected):
    assert video_gen.is_video_background(path) is expected


# ---------------------------------------------------------------------------
# generate_segment with a video background
# ---------------------------------------------------------------------------

def test_video_background_stream_loops_and_drops_its_audio(tmp_path, monkeypatch):
    vid = tmp_path / "bg.mp4"
    vid.write_bytes(b"x")
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"x")

    captured = {}
    monkeypatch.setattr(video_gen.subprocess, "run", _fake_run(captured))
    video_gen.generate_segment(str(vid), str(aud), str(tmp_path / "o.mp4"))
    cmd = captured["cmd"]

    # Looped as a video, never as a still image.
    assert "-stream_loop" in cmd
    assert cmd[cmd.index("-stream_loop") + 1] == "-1"
    assert "-loop" not in cmd
    # No still-image tuning for real motion.
    assert "stillimage" not in cmd
    # Narration audio is kept, the background clip's own audio is dropped.
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["0:v", "1:a"]
    assert "0:a" not in maps


def test_video_background_ignores_ken_burns(tmp_path, monkeypatch):
    vid = tmp_path / "bg.mp4"
    vid.write_bytes(b"x")
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"x")

    captured = {}
    monkeypatch.setattr(video_gen.subprocess, "run", _fake_run(captured))
    video_gen.generate_segment(
        str(vid), str(aud), str(tmp_path / "o.mp4"), image_type="zoom-in",
    )
    cmd = captured["cmd"]
    vf = cmd[cmd.index("-vf") + 1]
    assert "zoompan" not in vf
    assert "scale=" in vf and "pad=" in vf


def test_video_background_ignores_marquee(tmp_path, monkeypatch):
    vid = tmp_path / "bg.mp4"
    vid.write_bytes(b"x")
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"x")
    band = tmp_path / "band.png"
    band.write_bytes(b"x")

    captured = {}
    monkeypatch.setattr(video_gen.subprocess, "run", _fake_run(captured))
    video_gen.generate_segment(
        str(vid), str(aud), str(tmp_path / "o.mp4"),
        marquee_path=str(band), marquee_meta={"marquee_height": 60},
    )
    cmd = captured["cmd"]
    # Marquee band is never fed in or overlaid for a video background.
    assert "-filter_complex" not in cmd
    assert str(band) not in cmd


def test_still_image_background_still_loops_as_image(tmp_path, monkeypatch):
    img = tmp_path / "bg.png"
    img.write_bytes(b"x")
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"x")

    captured = {}
    monkeypatch.setattr(video_gen.subprocess, "run", _fake_run(captured))
    video_gen.generate_segment(str(img), str(aud), str(tmp_path / "o.mp4"))
    cmd = captured["cmd"]
    assert "-loop" in cmd
    assert "-stream_loop" not in cmd
    assert "stillimage" in cmd


# ---------------------------------------------------------------------------
# generate_full_video skips overlays for a video background
# ---------------------------------------------------------------------------

def _book(**over):
    base = dict(
        id=1, title="t", original_filename="f.epub", epub_path="f.epub",
        patch_size=1, status="ready", final_audio_path=None, final_video_path=None,
        background_image_path=None, voice_clip_path=None, voice_transcript=None,
        created_at="", updated_at="",
    )
    base.update(over)
    return Book(**base)


def _patch(**over):
    base = dict(
        id=10, book_id=1, patch_index=0, chapter_start=0, chapter_end=0,
        status="done", audio_path="a.wav", error_message=None, attempt_count=0,
        created_at="", updated_at="",
    )
    base.update(over)
    return Patch(**base)


def test_full_video_skips_overlay_for_video_background(tmp_path):
    vid = tmp_path / "bg.mp4"
    vid.write_bytes(b"x")
    book = _book(background_image_path=str(vid))
    p = _patch(image_path=str(vid), image_type="zoom-in")

    with patch.object(video_gen, "generate_segment") as seg, \
         patch.object(video_gen, "concat_segments"), \
         patch("app.image_overlay.ensure_patch_overlay") as overlay:
        video_gen.generate_full_video(
            [p], book, str(tmp_path / "out.mp4"), default_image=str(vid),
        )

    overlay.assert_not_called()
    # The raw video path is passed straight through with no Ken Burns.
    assert seg.call_args.args[0] == str(vid)
    assert seg.call_args.kwargs["image_type"] == "none"

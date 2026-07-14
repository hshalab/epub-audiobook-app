"""generate_standalone_video must forward music params to generate_segment."""
from unittest.mock import patch

from app import video_gen


def test_music_branch_labels_video_output(tmp_path, monkeypatch):
    """ffmpeg 5+ rejects -map 0:v when 0:v is consumed by a complex filtergraph.

    The music branch must label the video chain ([vout]) and map the label.
    """
    img = tmp_path / "i.png"
    img.write_bytes(b"x")
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"x")
    mus = tmp_path / "m.mp3"
    mus.write_bytes(b"x")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            stdout = ""
            returncode = 0

        return R()

    monkeypatch.setattr(video_gen.subprocess, "run", fake_run)
    video_gen.generate_segment(
        str(img), str(aud), str(tmp_path / "o.mp4"), music_path=str(mus),
    )
    cmd = captured["cmd"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[vout]" in fc
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert "[vout]" in maps
    assert "0:v" not in maps


def test_standalone_forwards_music_params():
    with patch.object(video_gen, "generate_segment") as seg:
        video_gen.generate_standalone_video(
            "a.mp3", "i.jpg", "o.mp4",
            music_path="m.mp3", music_volume=0.25,
        )
    kwargs = seg.call_args.kwargs
    assert kwargs["music_path"] == "m.mp3"
    assert kwargs["music_volume"] == 0.25


def test_standalone_defaults_no_music():
    with patch.object(video_gen, "generate_segment") as seg:
        video_gen.generate_standalone_video("a.mp3", "i.jpg", "o.mp4")
    kwargs = seg.call_args.kwargs
    assert kwargs["music_path"] is None
    assert kwargs["music_volume"] == 0.15

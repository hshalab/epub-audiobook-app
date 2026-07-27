import subprocess
from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from app.automation_config import merge_automation_config
from app.video_compositor import (
    build_composite_command,
    probe_media,
    render_composite,
    validate_ffmpeg_capabilities,
)


def _config(**override):
    return merge_automation_config({}, override)


def _require_ffmpeg():
    from app.ffmpeg import get_ffmpeg_path
    try:
        subprocess.run([get_ffmpeg_path(), "-version"], check=True,
                       capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"configured FFmpeg cannot execute -version: {exc}")
    return get_ffmpeg_path()


def _probe(path):
    if path.endswith((".wav", ".mp3", ".m4a", ".ogg")):
        return {"duration": 31.5, "streams": [{"codec_type": "audio"}]}
    kind = "image" if path.endswith((".jpg", ".png")) else "video"
    return {"duration": None if kind == "image" else 2.0,
            "streams": [{"codec_type": "video"}], "kind": kind}


def test_mixed_background_and_webcam_command_normalizes_and_drops_source_audio(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", _probe)
    cmd = build_composite_command(
        "voice.wav",
        [{"file_path": "a.jpg", "kind": "image"}, {"file_path": "b.mp4", "kind": "video"}],
        [{"file_path": "cam.mp4", "kind": "video"}],
        "out.mp4",
        _config(video={"background_duration_seconds": 12}, webcam={"enabled": True}),
    )

    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=0" in graph
    assert f"loop=loop=-1:size={24 * 30}:start=0" in graph
    assert f"loop=loop=-1:size={12 * 30}:start=0" in graph
    assert "overlay=W-w-24:H-h-24" in graph
    assert "trim=duration=12" in graph
    assert graph.count("setsar=1") == 3
    assert graph.count("fps=30") == 3
    assert [cmd[i + 1] for i, value in enumerate(cmd[:-1]) if value == "-map"] == ["[video]", "0:a"]
    assert cmd[cmd.index("-t") + 1] == "31.5"
    assert cmd.count("-stream_loop") == 2
    assert cmd[cmd.index("-loop") + 1] == "1"
    assert "-shortest" in cmd


def test_webcam_border_and_rounding_are_in_the_filter_graph(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", _probe)
    cmd = build_composite_command(
        "voice.wav", [{"file_path": "bg.jpg", "kind": "image"}],
        [{"file_path": "cam.mp4", "kind": "video"}], "out.mp4",
        _config(webcam={"enabled": True, "border_width": 3,
                        "border_color": "#112233", "corner_radius": 8}),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "pad=iw+6:ih+6:3:3:color=#112233" in graph
    assert "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a=" in graph


def test_music_is_looped_and_mixed_with_narration(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", _probe)
    cmd = build_composite_command(
        "voice.wav", [{"file_path": "a.jpg", "kind": "image"}], [], "out.mp4",
        _config(video={"music_volume": 0.2}), music_path="music.mp3",
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "volume=0.2" in graph
    assert "amix=inputs=2:duration=first" in graph
    assert [cmd[i + 1] for i, value in enumerate(cmd[:-1]) if value == "-map"] == ["[video]", "[audio]"]


def test_webcam_rejects_non_video_sources(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", _probe)
    with pytest.raises(ValueError, match="Webcam sources must be video"):
        build_composite_command(
            "voice.wav", [{"file_path": "bg.jpg", "kind": "image"}],
            [{"file_path": "cam.jpg", "kind": "image"}], "out.mp4",
            _config(webcam={"enabled": True}),
        )


def test_nvenc_capability_is_rejected_without_fallback(monkeypatch):
    from app import video_compositor
    video_compositor._ffmpeg_capabilities.cache_clear()
    calls = []
    monkeypatch.setattr(
        "app.video_compositor.subprocess.run",
        lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, " V..... libx264\n TS geq V->V", ""),
    )
    with pytest.raises(RuntimeError, match="h264_nvenc"):
        validate_ffmpeg_capabilities(_config(video={"encoder": "h264_nvenc"}))
    assert [args[-1] for args in calls] == ["-encoders", "-filters"]


@pytest.mark.parametrize("stdout,error", [
    ("not json", "invalid probe output"),
    ('{"streams": []}', "no video stream"),
    ('{"streams":[{"codec_type":"video"}],"format":{}}', "missing duration"),
])
def test_probe_media_reports_contextual_errors(monkeypatch, tmp_path, stdout, error):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"x")
    monkeypatch.setattr("app.video_compositor.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout, ""))
    with pytest.raises(ValueError, match=error):
        probe_media(str(broken))


def test_visual_sources_are_probed_and_kind_checked(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", lambda path: {
        "duration": 2.0, "streams": [{"codec_type": "video"}], "kind": "video"
    } if path != "voice.wav" else {"duration": 1.0, "streams": [{"codec_type": "audio"}]})
    with pytest.raises(ValueError, match="background.*declared image.*video"):
        build_composite_command("voice.wav", [{"file_path": "wrong.jpg", "kind": "image"}], [], "out.mp4", _config())


def test_probe_rejects_unsupported_visual_extension(tmp_path):
    source = tmp_path / "background.bmp"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match=r"unsupported media extension.*\.bmp"):
        probe_media(str(source))


@pytest.mark.parametrize("role,path", [("narration", "voice.mp4"), ("music", "music.mp4")])
def test_audio_roles_require_audio_stream(monkeypatch, role, path):
    def fake_probe(value):
        if value == "voice.wav":
            return {"duration": 1.0, "streams": [{"codec_type": "audio"}], "kind": "audio"}
        if value.endswith(".jpg"):
            return {"duration": None, "streams": [{"codec_type": "video"}], "kind": "image"}
        return {"duration": 1.0, "streams": [{"codec_type": "video"}], "kind": "video"}
    monkeypatch.setattr("app.video_compositor.probe_media", fake_probe)
    kwargs = {"music_path": path} if role == "music" else {}
    audio = "voice.wav" if role == "music" else path
    with pytest.raises(ValueError, match=f"{role}.*audio stream"):
        build_composite_command(audio, [{"file_path": "bg.jpg", "kind": "image"}], [], "out.mp4", _config(), **kwargs)


def test_narration_requires_positive_duration(monkeypatch):
    monkeypatch.setattr("app.video_compositor.probe_media", lambda path: {
        "duration": 0.0, "streams": [{"codec_type": "audio"}], "kind": "audio"
    })
    with pytest.raises(ValueError, match="narration.*positive duration"):
        build_composite_command("voice.wav", [{"file_path": "bg.jpg", "kind": "image"}], [], "out.mp4", _config())


def test_missing_music_is_contextual(monkeypatch):
    def fake_probe(path):
        if path == "missing.mp3":
            raise ValueError("media source does not exist: missing.mp3")
        return _probe(path)
    monkeypatch.setattr("app.video_compositor.probe_media", fake_probe)
    with pytest.raises(ValueError, match="music.*missing.mp3"):
        build_composite_command("voice.wav", [{"file_path": "bg.jpg", "kind": "image"}], [],
                                "out.mp4", _config(), music_path="missing.mp3")


def test_standalone_delegates_only_when_compositor_arguments_are_present(monkeypatch):
    from app import video_gen

    calls = []
    monkeypatch.setattr("app.video_compositor.render_composite", lambda *a, **k: calls.append((a, k)))
    config = _config()
    video_gen.generate_standalone_video(
        "voice.wav", "fallback.jpg", "out.mp4",
        backgrounds=[{"file_path": "bg.jpg", "kind": "image"}],
        webcam_sources=[], automation_config=config,
    )
    assert calls[0][0][:4] == (
        "voice.wav", [{"file_path": "bg.jpg", "kind": "image"}], [], "out.mp4",
    )


def test_empty_webcam_alone_uses_legacy_standalone_path(monkeypatch):
    from app import video_gen
    legacy = []
    monkeypatch.setattr(video_gen, "generate_segment", lambda *a, **k: legacy.append(a))
    video_gen.generate_standalone_video("voice.wav", "fallback.jpg", "out.mp4", webcam_sources=[])
    assert legacy


def test_full_video_delegates_each_patch_to_compositor(monkeypatch, tmp_path):
    from app import video_gen
    backgrounds = [{"file_path": "first.jpg", "kind": "image"},
                   {"file_path": "second.mp4", "kind": "video"}]
    webcams = [{"file_path": "cam.mp4", "kind": "video"}]
    patches = [SimpleNamespace(id=1, patch_index=0, audio_path="one.wav"),
               SimpleNamespace(id=2, patch_index=1, audio_path="two.wav")]
    book = SimpleNamespace(video_resolution="1920x1080", video_fps=30,
                           default_image_animation="none")
    calls = []
    monkeypatch.setattr("app.video_compositor.render_composite", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(video_gen, "concat_segments", lambda *a, **k: None)

    video_gen.generate_full_video(
        patches, book, str(tmp_path / "out.mp4"), default_image="fallback.jpg",
        backgrounds=backgrounds, webcam_sources=webcams, automation_config=_config(),
    )

    assert [(call[0][0], call[0][1], call[0][2]) for call in calls] == [
        ("one.wav", backgrounds, webcams), ("two.wav", backgrounds, webcams)
    ]


def test_full_video_without_composite_args_uses_legacy_segment(monkeypatch, tmp_path):
    from app import video_gen
    patch = SimpleNamespace(id=1, patch_index=0, audio_path="one.wav",
                            image_path=None, image_type="static")
    book = SimpleNamespace(id=1, video_resolution="1920x1080", video_fps=30,
                           default_image_animation="none", background_image_path=None)
    fallback = tmp_path / "fallback.jpg"
    fallback.write_bytes(b"x")
    legacy = []
    monkeypatch.setattr(video_gen, "generate_segment", lambda *a, **k: legacy.append(a))
    monkeypatch.setattr(video_gen, "concat_segments", lambda *a, **k: None)
    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", lambda *a: None)

    video_gen.generate_full_video([patch], book, str(tmp_path / "out.mp4"), default_image=str(fallback))

    assert legacy[0][1] == "one.wav"


def test_full_video_removes_partial_segment_after_render_failure(monkeypatch, tmp_path):
    from app import video_gen
    patch = SimpleNamespace(id=1, patch_index=0, audio_path="one.wav")
    book = SimpleNamespace(video_resolution="1920x1080", video_fps=30,
                           default_image_animation="none")

    def fail(*args, **kwargs):
        Path(args[3]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr("app.video_compositor.render_composite", fail)
    with pytest.raises(subprocess.CalledProcessError):
        video_gen.generate_full_video(
            [patch], book, str(tmp_path / "out.mp4"), default_image="unused.jpg",
            backgrounds=[{"file_path": "bg.jpg", "kind": "image"}],
            automation_config=_config(),
        )
    assert not (tmp_path / "_segments").exists()


def test_real_ffmpeg_renders_rounded_multi_source_to_narration_duration(tmp_path):
    from app.ffmpeg import get_ffmpeg_path, get_ffprobe_path
    from app import video_compositor

    ffmpeg = _require_ffmpeg()
    fixtures = {
        "voice.wav": ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.2"],
        "music.wav": ["-f", "lavfi", "-i", "sine=frequency=220:duration=0.2"],
        "bg.png": ["-f", "lavfi", "-i", "color=blue:size=64x36", "-frames:v", "1"],
        "bg.mp4": ["-f", "lavfi", "-i", "color=green:size=64x36:duration=0.2"],
        "cam1.mp4": ["-f", "lavfi", "-i", "color=red:size=32x18:duration=0.2"],
        "cam2.mp4": ["-f", "lavfi", "-i", "color=yellow:size=32x18:duration=0.2"],
    }
    for name, args in fixtures.items():
        subprocess.run([ffmpeg, "-y", *args, str(tmp_path / name)], check=True,
                       capture_output=True, text=True)

    video_compositor._ffmpeg_capabilities.cache_clear()
    config = _config(
        video={"resolution": "1280x720", "fps": 24,
               "background_duration_seconds": 3, "music_volume": 0.1},
        webcam={"enabled": True, "corner_radius": 6, "border_width": 2,
                "border_color": "#ffffff", "width_percent": 10},
    )
    output = tmp_path / "out.mp4"
    render_composite(
        str(tmp_path / "voice.wav"),
        [{"file_path": str(tmp_path / "bg.png"), "kind": "image"},
         {"file_path": str(tmp_path / "bg.mp4"), "kind": "video"}],
        [{"file_path": str(tmp_path / "cam1.mp4"), "kind": "video"},
         {"file_path": str(tmp_path / "cam2.mp4"), "kind": "video"}],
        str(output), config, music_path=str(tmp_path / "music.wav"),
    )

    result = subprocess.run(
        [get_ffprobe_path(), "-v", "error", "-show_entries",
         "stream=codec_type:format=duration", "-of", "json", str(output)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    assert {stream["codec_type"] for stream in data["streams"]} == {"video", "audio"}
    assert float(data["format"]["duration"]) == pytest.approx(1.2, abs=0.05)


def test_full_video_real_composite_two_patches_cleans_segments(tmp_path):
    from app import video_gen
    from app.ffmpeg import get_ffprobe_path

    ffmpeg = _require_ffmpeg()
    background = tmp_path / "bg.png"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=navy:size=64x36",
         "-frames:v", "1", str(background)],
        check=True, capture_output=True, text=True,
    )
    patches = []
    for index, duration in enumerate((0.3, 0.4)):
        audio = tmp_path / f"voice{index}.wav"
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i",
             f"sine=frequency={440 + index * 110}:duration={duration}", str(audio)],
            check=True, capture_output=True, text=True,
        )
        patches.append(SimpleNamespace(id=index + 1, patch_index=index,
                                       audio_path=str(audio)))

    output = tmp_path / "full.mp4"
    book = SimpleNamespace(video_resolution="1280x720", video_fps=24,
                           default_image_animation="none")
    video_gen.generate_full_video(
        patches, book, str(output), default_image="unused.jpg",
        backgrounds=[{"file_path": str(background), "kind": "image"}],
        automation_config=_config(video={"resolution": "1280x720", "fps": 24}),
    )

    result = subprocess.run(
        [get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
        check=True, capture_output=True, text=True,
    )
    assert output.exists()
    assert float(result.stdout) == pytest.approx(0.7, abs=0.1)
    assert not (tmp_path / "_segments").exists()

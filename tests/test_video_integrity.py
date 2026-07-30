import json
import subprocess
from types import SimpleNamespace

import pytest

from app import video_integrity


def _probe(*, video_duration="10", audio_duration="10", video_codec="h264",
           audio_codec="aac", container="mov,mp4,m4a,3gp,3g2,mj2"):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": video_codec,
                 "duration": video_duration, "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": audio_codec,
                 "duration": audio_duration},
            ],
            "format": {"format_name": container, "duration": video_duration},
        }),
        stderr="",
    )


def _run_with_probe(monkeypatch, probe, decode=None):
    calls = []
    decode = decode or SimpleNamespace(returncode=0, stdout="", stderr="")

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return probe if "ffprobe" in str(cmd[0]).lower() else decode

    monkeypatch.setattr(video_integrity.subprocess, "run", run)
    monkeypatch.setattr(video_integrity.Settings, "get_ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr(video_integrity.Settings, "get_ffmpeg_path", lambda: "ffmpeg")
    return calls


def test_decode_timeout_has_duration_aware_bounds():
    assert video_integrity.decode_timeout(0) == 300
    assert video_integrity.decode_timeout(60) == 300
    assert video_integrity.decode_timeout(3600) == 7320
    assert video_integrity.decode_timeout(999999) == 21600


def test_missing_and_empty_files_fail_before_probe(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_integrity.subprocess, "run", lambda *a, **k: calls.append(a))
    missing = video_integrity.validate_video(tmp_path / "missing.mp4")
    empty_path = tmp_path / "empty.mp4"
    empty_path.touch()
    empty = video_integrity.validate_video(empty_path)
    assert (missing.valid, missing.error_code) == (False, "file_missing")
    assert (empty.valid, empty.error_code) == (False, "file_empty")
    assert calls == []


@pytest.mark.parametrize(("payload", "code"), [
    ({"streams": [], "format": {"format_name": "mp4", "duration": "10"}},
     "missing_video_stream"),
    ({"streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10"}],
      "format": {"format_name": "mp4", "duration": "10"}}, "missing_audio_stream"),
])
def test_probe_requires_audio_and_video(tmp_path, monkeypatch, payload, code):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""))
    result = video_integrity.validate_video(path)
    assert (result.valid, result.error_code) == (False, code)


@pytest.mark.parametrize("duration", ["0", "nan", "inf", "bad"])
def test_probe_rejects_invalid_durations(tmp_path, monkeypatch, duration):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, _probe(video_duration=duration))
    assert video_integrity.validate_video(path).error_code == "invalid_duration"


@pytest.mark.parametrize(("video_codec", "audio_codec", "container"), [
    ("vp9", "aac", "mp4"), ("h264", "opus", "mp4"), ("h264", "aac", "matroska"),
])
def test_probe_rejects_unsupported_output(tmp_path, monkeypatch, video_codec, audio_codec, container):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, _probe(video_codec=video_codec, audio_codec=audio_codec,
                                        container=container))
    assert video_integrity.validate_video(path).error_code == "unsupported_format"


def test_probe_failure_and_invalid_json_are_classified(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, SimpleNamespace(returncode=1, stdout="", stderr="bad"))
    assert video_integrity.validate_video(path).error_code == "probe_failed"
    _run_with_probe(monkeypatch, SimpleNamespace(returncode=0, stdout="{", stderr=""))
    assert video_integrity.validate_video(path).error_code == "probe_failed"


@pytest.mark.parametrize(("drift", "valid", "code", "warned"), [
    (0.9, True, None, False), (1.0, True, None, True), (5.0, False, "av_drift", False),
])
def test_av_drift_thresholds(tmp_path, monkeypatch, drift, valid, code, warned):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, _probe(video_duration=str(10 + drift), audio_duration="10"))
    result = video_integrity.validate_video(path)
    assert (result.valid, result.error_code, bool(result.warnings)) == (valid, code, warned)


def test_full_decode_maps_streams_uses_xerror_and_timeout(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    calls = _run_with_probe(monkeypatch, _probe())
    result = video_integrity.validate_video(path)
    cmd, kwargs = calls[1]
    assert result.valid
    assert [cmd[i + 1] for i, value in enumerate(cmd) if value == "-map"] == ["0:v:0", "0:a:0"]
    assert "-xerror" in cmd
    assert cmd[-2:] == ["null", "-"]
    assert kwargs["timeout"] == video_integrity.decode_timeout(10)


def test_decode_failure_timeout_and_bounded_stderr(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    _run_with_probe(monkeypatch, _probe(), SimpleNamespace(returncode=1, stdout="", stderr="x" * 3000))
    failed = video_integrity.validate_video(path)
    assert failed.error_code == "decode_failed"
    assert len(failed.message) == 2000

    def timeout(cmd, **kwargs):
        if "ffprobe" in str(cmd[0]).lower():
            return _probe()
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    monkeypatch.setattr(video_integrity.subprocess, "run", timeout)
    assert video_integrity.validate_video(path).error_code == "validation_timeout"


def test_missing_tools_are_classified(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    monkeypatch.setattr(video_integrity.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert video_integrity.validate_video(path).error_code == "tool_unavailable"

import importlib.util
from pathlib import Path

from app.video_integrity import ValidationFacts, ValidationResult


def _script():
    path = Path(__file__).parents[1] / "scripts" / "check_video_integrity.py"
    spec = importlib.util.spec_from_file_location("check_video_integrity", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_inspect_delegates_to_shared_validator(tmp_path, monkeypatch):
    script = _script(); video = tmp_path / "v.mp4"; video.write_bytes(b"x"); calls = []
    monkeypatch.setattr(script, "validate_video", lambda p: calls.append(Path(p)) or ValidationResult(False, "decode_failed", "broken", (), ValidationFacts(), 0))
    row = script.inspect(video)
    assert calls == [video]
    assert row["verdict"] == "broken"
    assert row["reasons"] == ["decode_failed: broken"]

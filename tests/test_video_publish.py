from pathlib import Path

import pytest

from app.video_integrity import ValidationFacts, ValidationResult
from app.video_publish import VideoValidationError, publish_validated_video


def _result(valid=True, code=None):
    return ValidationResult(valid, code, "broken" if code else "", (), ValidationFacts(), 0)


def test_success_validates_temp_then_atomically_replaces_final(tmp_path):
    final = tmp_path / "video.mp4"
    final.write_bytes(b"old")
    seen = {}

    def render(temp):
        seen["temp"] = Path(temp)
        Path(temp).write_bytes(b"new")

    result = publish_validated_video(final, render, validator=lambda p: _result())
    assert result.valid
    assert final.read_bytes() == b"new"
    assert seen["temp"].parent == final.parent
    assert seen["temp"] != final
    assert not seen["temp"].exists()


def test_failed_validation_preserves_existing_final_and_cleans_temp(tmp_path):
    final = tmp_path / "video.mp4"
    final.write_bytes(b"old")
    with pytest.raises(VideoValidationError) as raised:
        publish_validated_video(
            final, lambda temp: Path(temp).write_bytes(b"broken"),
            validator=lambda p: _result(False, "decode_failed"),
        )
    assert raised.value.result.error_code == "decode_failed"
    assert final.read_bytes() == b"old"
    assert list(tmp_path.glob("*.rendering-*.mp4")) == []


@pytest.mark.parametrize("error", [RuntimeError("render failed"), KeyboardInterrupt()])
def test_render_exception_preserves_final_and_cleans_temp(tmp_path, error):
    final = tmp_path / "video.mp4"
    final.write_bytes(b"old")

    def render(temp):
        Path(temp).write_bytes(b"partial")
        raise error

    with pytest.raises(type(error)):
        publish_validated_video(final, render, validator=lambda p: _result())
    assert final.read_bytes() == b"old"
    assert list(tmp_path.glob("*.rendering-*.mp4")) == []

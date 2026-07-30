"""Publish a rendered video only after validating its complete temporary output."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable

from app.video_integrity import ValidationResult, validate_video


class VideoValidationError(RuntimeError):
    def __init__(self, result: ValidationResult):
        super().__init__(f"{result.error_code}: {result.message}")
        self.result = result


def publish_validated_video(
    final_path: str | Path,
    render: Callable[[str], None],
    *,
    validator: Callable[[str | Path], ValidationResult] = validate_video,
) -> ValidationResult:
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = final.with_name(f"{final.stem}.rendering-{uuid.uuid4().hex}{final.suffix}")
    try:
        render(str(temp))
        result = validator(temp)
        if not result.valid:
            raise VideoValidationError(result)
        temp.replace(final)
        return result
    finally:
        temp.unlink(missing_ok=True)

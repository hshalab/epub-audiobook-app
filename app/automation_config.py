from __future__ import annotations

from copy import deepcopy
from string import Formatter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TEMPLATE_FIELDS = {
    "book_title",
    "patch_name",
    "patch_index",
    "chapter_start",
    "chapter_end",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VideoBase(StrictModel):
    resolution: Literal["1280x720", "1920x1080", "2560x1440", "3840x2160"] = (
        "1920x1080"
    )
    fps: Literal[24, 25, 30, 50, 60] = 30
    audio_bitrate: Literal["128k", "192k", "256k", "320k"] = "192k"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    background_duration_seconds: int = Field(default=10, ge=3, le=300)
    music_id: int | None = None
    music_volume: float = Field(default=0.0, ge=0.0, le=1.0)



class CpuVideoConfig(VideoBase):
    encoder: Literal["libx264"] = "libx264"
    crf: int = Field(default=23, ge=0, le=51)
    preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"


class NvencVideoConfig(VideoBase):
    encoder: Literal["h264_nvenc"]
    cq: int = Field(default=23, ge=0, le=51)
    preset: Literal["p1", "p2", "p3", "p4", "p5", "p6", "p7"] = "p4"


VideoConfig = Annotated[
    CpuVideoConfig | NvencVideoConfig, Field(discriminator="encoder")
]


class WebcamConfig(StrictModel):
    enabled: bool = False
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = (
        "bottom-right"
    )
    width_percent: int = Field(default=25, ge=10, le=50)
    margin: int = Field(default=24, ge=0)
    border_width: int = Field(default=0, ge=0)
    border_color: str = "#ffffff"
    corner_radius: int = Field(default=0, ge=0)


class YouTubeConfig(StrictModel):
    privacy: Literal["private", "unlisted", "public"] = "private"
    category_id: str = "22"
    tags: list[str] = Field(default_factory=list)
    title_template: str = "{book_title} - {patch_name}"
    description_template: str = ""
    made_for_kids: bool = False
    notify_subscribers: bool = False
    default_audio_language: str | None = None
    default_video_language: str | None = None
    license: Literal["youtube", "creativeCommon"] = "youtube"
    embeddable: bool = True
    public_statistics: bool = True
    playlist_mode: Literal["none", "existing", "auto-create"] = "none"
    playlist_id: str | None = None
    playlist_title_template: str = "{book_title}"
    playlist_description_template: str = ""
    playlist_privacy: Literal["private", "unlisted", "public"] = "private"

    @field_validator(
        "title_template",
        "description_template",
        "playlist_title_template",
        "playlist_description_template",
    )
    @classmethod
    def validate_template(cls, template: str) -> str:
        _template_fields(template)
        return template


class AutomationConfig(StrictModel):
    enabled: bool = False
    youtube_auto_upload: bool = False
    generate_missing_thumbnails: bool = True
    continue_after_patch_failure: bool = True
    video: VideoConfig = Field(default_factory=CpuVideoConfig)
    webcam: WebcamConfig = Field(default_factory=WebcamConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


def merge_automation_config(
    system: dict, override: dict | None = None
) -> AutomationConfig:
    def merge(base: dict, changes: dict) -> dict:
        result = deepcopy(base)
        if changes.get("encoder") != result.get("encoder") and "encoder" in changes:
            for field in ("crf", "cq", "preset"):
                result.pop(field, None)
        for key, value in changes.items():
            if isinstance(result.get(key), dict) and isinstance(value, dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    resolved = merge(AutomationConfig().model_dump(), system)
    return AutomationConfig.model_validate(merge(resolved, override or {}))


def render_metadata_template(template: str, values: dict[str, object]) -> str:
    _template_fields(template)
    return template.format_map(values)


def _template_fields(template: str) -> set[str]:
    parsed = list(Formatter().parse(template))
    if any(
        name == ""
        or (name and (conversion or format_spec or "." in name or "[" in name))
        for _, name, format_spec, conversion in parsed
    ):
        raise ValueError("only simple template fields are supported")
    fields = {name for _, name, _, _ in parsed if name}
    unknown = fields - TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"unknown template field: {sorted(unknown)[0]}")
    return fields

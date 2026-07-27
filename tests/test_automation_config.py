import pytest
from pydantic import ValidationError

from app.automation_config import merge_automation_config, render_metadata_template


def test_override_inherits_defaults():
    cfg = merge_automation_config(
        {"video": {"fps": 25, "resolution": "1280x720"}},
        {"video": {"fps": 30}},
    )
    assert cfg.video.resolution == "1280x720"
    assert cfg.video.fps == 30
    assert cfg.video.encoder == "libx264"


def test_rejects_raw_ffmpeg_and_invalid_slot_duration():
    with pytest.raises(ValidationError):
        merge_automation_config(
            {},
            {"video": {"ffmpeg_args": "-f lavfi", "background_duration_seconds": 2}},
        )


def test_rejects_wrong_encoder_preset():
    with pytest.raises(ValidationError):
        merge_automation_config(
            {}, {"video": {"encoder": "h264_nvenc", "preset": "medium"}}
        )


@pytest.mark.parametrize(
    ("section", "values"),
    [
        ("video", {"crf": 52}),
        ("video", {"background_duration_seconds": 301}),
        ("webcam", {"width_percent": 9}),
        ("webcam", {"width_percent": 51}),
        ("webcam", {"margin": -1}),
        ("webcam", {"border_width": -1}),
    ],
)
def test_rejects_values_outside_bounds(section, values):
    with pytest.raises(ValidationError):
        merge_automation_config({}, {section: values})


def test_nvenc_accepts_its_quality_and_preset():
    cfg = merge_automation_config(
        {}, {"video": {"encoder": "h264_nvenc", "preset": "p4", "cq": 20}}
    )
    assert cfg.video.preset == "p4"
    assert cfg.video.cq == 20


def test_default_values_are_exact():
    cfg = merge_automation_config({}, None)
    assert cfg.model_dump() == {
        "enabled": False,
        "youtube_auto_upload": False,
        "generate_missing_thumbnails": True,
        "continue_after_patch_failure": True,
        "video": {
            "resolution": "1920x1080",
            "fps": 30,
            "encoder": "libx264",
            "crf": 23,
            "preset": "medium",
            "audio_bitrate": "192k",
            "pixel_format": "yuv420p",
            "background_duration_seconds": 10,
            "music_id": None,
            "music_volume": 0.0,
        },
        "webcam": {
            "enabled": False,
            "position": "bottom-right",
            "width_percent": 25,
            "margin": 24,
            "border_width": 0,
            "border_color": "#ffffff",
            "corner_radius": 0,
        },
        "youtube": {
            "privacy": "private",
            "category_id": "22",
            "tags": [],
            "title_template": "{book_title} - {patch_name}",
            "description_template": "",
            "made_for_kids": False,
            "notify_subscribers": False,
            "default_audio_language": None,
            "default_video_language": None,
            "license": "youtube",
            "embeddable": True,
            "public_statistics": True,
            "playlist_mode": "none",
            "playlist_id": None,
            "playlist_title_template": "{book_title}",
            "playlist_description_template": "",
            "playlist_privacy": "private",
        },
    }


@pytest.mark.parametrize(
    "video",
    [
        {"encoder": "libx264", "preset": "medium", "cq": 20},
        {"encoder": "h264_nvenc", "preset": "p4", "cq": 20, "crf": 23},
        {"encoder": "h264_nvenc", "preset": "p4", "cq": 52},
    ],
)
def test_rejects_inapplicable_or_invalid_encoder_quality(video):
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"video": video})


def test_encoder_serialization_contains_only_applicable_quality():
    cpu = merge_automation_config({}, None).video.model_dump()
    nvenc = merge_automation_config(
        {}, {"video": {"encoder": "h264_nvenc", "preset": "p4", "cq": 20}}
    ).video.model_dump()
    assert "crf" in cpu and "cq" not in cpu
    assert "cq" in nvenc and "crf" not in nvenc


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        (
            "resolution",
            ["1280x720", "1920x1080", "2560x1440", "3840x2160"],
            "640x480",
        ),
        ("fps", [24, 25, 30, 50, 60], 29),
        ("audio_bitrate", ["128k", "192k", "256k", "320k"], "96k"),
    ],
)
def test_video_allowlists(field, accepted, rejected):
    for value in accepted:
        assert getattr(merge_automation_config({}, {"video": {field: value}}).video, field) == value
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"video": {field: rejected}})


@pytest.mark.parametrize(
    "preset",
    [
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ],
)
def test_cpu_preset_allowlist(preset):
    assert merge_automation_config({}, {"video": {"preset": preset}}).video.preset == preset


def test_rejects_unlisted_cpu_preset():
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"video": {"preset": "placebo"}})


@pytest.mark.parametrize("preset", ["p1", "p2", "p3", "p4", "p5", "p6", "p7"])
def test_nvenc_preset_allowlist(preset):
    video = {"encoder": "h264_nvenc", "preset": preset}
    assert merge_automation_config({}, {"video": video}).video.preset == preset


def test_rejects_unlisted_nvenc_preset():
    with pytest.raises(ValidationError):
        merge_automation_config(
            {}, {"video": {"encoder": "h264_nvenc", "preset": "p8"}}
        )


@pytest.mark.parametrize(
    ("section", "field", "accepted", "rejected"),
    [
        (
            "webcam",
            "position",
            ["top-left", "top-right", "bottom-left", "bottom-right"],
            "center",
        ),
        ("youtube", "privacy", ["private", "unlisted", "public"], "friends"),
        (
            "youtube",
            "playlist_mode",
            ["none", "existing", "auto-create"],
            "append",
        ),
        ("youtube", "license", ["youtube", "creativeCommon"], "standard"),
        (
            "youtube",
            "playlist_privacy",
            ["private", "unlisted", "public"],
            "friends",
        ),
    ],
)
def test_webcam_and_youtube_allowlists(section, field, accepted, rejected):
    for value in accepted:
        cfg = merge_automation_config({}, {section: {field: value}})
        assert getattr(getattr(cfg, section), field) == value
    with pytest.raises(ValidationError):
        merge_automation_config({}, {section: {field: rejected}})


def test_rejects_extra_fields_at_every_level():
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"unexpected": True})
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"youtube": {"unexpected": True}})
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"webcam": {"media_ids": [1]}})


def test_nested_override_does_not_mutate_inputs():
    system = {"webcam": {"position": "top-left", "margin": 12}}
    override = {"webcam": {"margin": 20}}
    cfg = merge_automation_config(system, override)
    assert cfg.webcam.position == "top-left"
    assert cfg.webcam.margin == 20
    assert system == {"webcam": {"position": "top-left", "margin": 12}}


def test_metadata_template_allowlist():
    assert render_metadata_template(
        "{book_title} - {patch_index}", {"book_title": "Book", "patch_index": 2}
    ) == "Book - 2"
    with pytest.raises(ValueError, match="unknown template field"):
        render_metadata_template("{secret}", {})


@pytest.mark.parametrize(
    "field",
    [
        "title_template",
        "description_template",
        "playlist_title_template",
        "playlist_description_template",
    ],
)
def test_rejects_unknown_fields_in_youtube_templates_at_config_load(field):
    with pytest.raises(ValidationError, match="unknown template field"):
        merge_automation_config({}, {"youtube": {field: "{secret}"}})


@pytest.mark.parametrize(
    "field",
    [
        "title_template",
        "description_template",
        "playlist_title_template",
        "playlist_description_template",
    ],
)
def test_rejects_automatic_positional_field_in_youtube_templates(field):
    with pytest.raises(ValidationError, match="simple template fields"):
        merge_automation_config({}, {"youtube": {field: "{}"}})


def test_renderer_rejects_automatic_positional_field_before_formatting():
    with pytest.raises(ValueError, match="simple template fields"):
        render_metadata_template("{}", {})


@pytest.mark.parametrize(
    "template",
    ["{book_title.name}", "{book_title[0]}", "{book_title!r}", "{patch_index:03d}"],
)
def test_rejects_non_simple_template_substitutions(template):
    with pytest.raises(ValueError, match="simple template fields"):
        render_metadata_template(template, {"book_title": "Book", "patch_index": 2})

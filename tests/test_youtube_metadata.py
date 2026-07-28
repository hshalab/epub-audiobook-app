import json
from types import SimpleNamespace
import pytest
import soundfile as sf
import numpy as np

from app import db
from app.youtube_metadata import (get_book_youtube_config, get_patch_youtube_override,
                                  resolve_patch_youtube_metadata, save_book_youtube_config,
                                  save_patch_youtube_override, validate_book_youtube_config,
                                  load_timeline)


def test_load_timeline_returns_valid_sidecar(tmp_path):
    audio = tmp_path / "result.wav"
    sf.write(audio, np.zeros(40), 1)
    timeline = {"version": 1, "sample_rate": 1, "total_frames": 40,
                "chapters": [{"chapter_index": 1, "start_frame": 0, "start_seconds": 0.0, "title": "One"},
                             {"chapter_index": 2, "start_frame": 10, "start_seconds": 10.0, "title": "Two"},
                             {"chapter_index": 3, "start_frame": 20, "start_seconds": 20.0, "title": "Three"}]}
    audio.with_suffix(".timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    assert load_timeline(audio) == timeline


@pytest.mark.parametrize("chapter_change", [
    lambda chapters: chapters[1].update(start_frame=5),
    lambda chapters: chapters[1].pop("chapter_index"),
])
def test_load_timeline_rejects_invalid_chapter_order_and_schema(tmp_path, chapter_change):
    audio = tmp_path / "invalid.wav"
    sf.write(audio, np.zeros(20), 10)
    chapters = [
        {"chapter_index": 1, "title": "One", "start_frame": 0, "start_seconds": 0},
        {"chapter_index": 2, "title": "Two", "start_frame": 10, "start_seconds": 1},
    ]
    chapter_change(chapters)
    audio.with_suffix(".timeline.json").write_text(json.dumps({
        "version": 1, "sample_rate": 10, "total_frames": 20, "chapters": chapters,
    }), encoding="utf-8")
    assert load_timeline(audio) is None


@pytest.mark.parametrize("count", [1, 2])
def test_load_timeline_accepts_valid_short_chapter_lists(tmp_path, count):
    audio = tmp_path / "short-structural.wav"
    sf.write(audio, np.zeros(20), 10)
    chapters = [{"chapter_index": i + 1, "title": f"Chapter {i + 1}",
                 "start_frame": i * 10, "start_seconds": i}
                for i in range(count)]
    audio.with_suffix(".timeline.json").write_text(json.dumps({
        "version": 1, "sample_rate": 10, "total_frames": 20, "chapters": chapters,
    }), encoding="utf-8")
    assert load_timeline(audio) is not None


def test_load_timeline_accepts_gapped_source_indexes(tmp_path):
    audio = tmp_path / "gapped.wav"
    sf.write(audio, np.zeros(20), 10)
    timeline = {"version": 1, "sample_rate": 10, "total_frames": 20, "chapters": [
        {"chapter_index": 10, "title": "Ten", "start_frame": 0, "start_seconds": 0},
        {"chapter_index": 12, "title": "Twelve", "start_frame": 10, "start_seconds": 1},
    ]}
    audio.with_suffix(".timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    assert load_timeline(audio) == timeline


@pytest.mark.parametrize("indexes", [[10, 10], [12, 10]])
def test_load_timeline_rejects_duplicate_or_regressing_indexes(tmp_path, indexes):
    audio = tmp_path / "bad-indexes.wav"
    sf.write(audio, np.zeros(20), 10)
    timeline = {"version": 1, "sample_rate": 10, "total_frames": 20, "chapters": [
        {"chapter_index": indexes[0], "title": "One", "start_frame": 0, "start_seconds": 0},
        {"chapter_index": indexes[1], "title": "Two", "start_frame": 10, "start_seconds": 1},
    ]}
    audio.with_suffix(".timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    assert load_timeline(audio) is None


def test_valid_short_timeline_loads_but_is_not_added_to_description(tmp_path):
    audio = tmp_path / "short.wav"
    sf.write(audio, np.zeros(20), 10)
    timeline = {"version": 1, "sample_rate": 10, "total_frames": 20,
                 "chapters": [{"chapter_index": 1, "start_frame": 0, "start_seconds": 0.0, "title": "Only"}]}
    audio.with_suffix(".timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    from app.youtube_metadata import load_timeline, resolve_patch_youtube_metadata
    assert load_timeline(audio) == timeline
    assert "Only" not in resolve_patch_youtube_metadata(_book(), _patch(str(audio)), {})["description"]


def _book():
    return SimpleNamespace(title="Nha Tro", automation_config=json.dumps({"youtube": {
        "description": "book description",
        "genre_tags": "kinh di, huyen huyen",
    }}))


def _patch(audio_path=None):
    return SimpleNamespace(name="Mua", chapter_start=1, chapter_end=8, patch_index=3, audio_path=audio_path)


def _timeline_audio(tmp_path, *, frames=30 * 10, sample_rate=10, chapters=None):
    audio = tmp_path / "episode.wav"
    sf.write(audio, np.zeros(frames), sample_rate)
    sidecar = audio.with_suffix(".timeline.json")
    sidecar.write_text(json.dumps({"version": 1, "sample_rate": sample_rate,
                                   "total_frames": frames,
                                   "chapters": chapters or [
                                       {"chapter_index": 1, "start_frame": 0, "start_seconds": 0, "title": "Intro"},
                                       {"chapter_index": 2, "start_frame": 100, "start_seconds": 10, "title": "Chapter 1"},
                                       {"chapter_index": 3, "start_frame": 200, "start_seconds": 20, "title": "Chapter 2"},
                                   ]}))
    return audio


def _write_timeline(audio, **values):
    timeline = {"version": 1, "sample_rate": 10, "total_frames": 300,
                "chapters": [{"start_frame": 0, "title": "Intro"},
                              {"start_frame": 100, "title": "One"},
                              {"start_frame": 200, "title": "Two"}]}
    timeline.update(values)
    audio.with_suffix(".timeline.json").write_text(json.dumps(timeline))


def test_valid_timeline_is_appended_once_with_floor_and_hour_formatting(tmp_path):
    audio = _timeline_audio(tmp_path, frames=72000, sample_rate=10, chapters=[
        {"chapter_index": 1, "start_frame": 0, "start_seconds": 0, "title": "Intro"},
        {"chapter_index": 2, "start_frame": 100, "start_seconds": 10, "title": "Chapter 1"},
        {"chapter_index": 3, "start_frame": 36000, "start_seconds": 3600, "title": "Chapter 2"},
    ])
    book = _book()
    patch = _patch(str(audio))
    result = resolve_patch_youtube_metadata(book, patch, None)
    assert result["description"] == "book description\n\n00:00 Intro\n00:10 Chapter 1\n1:00:00 Chapter 2"
    assert result["description"].count("Intro") == 1


@pytest.mark.parametrize("chapters", [
    [{"start_frame": 0, "title": "a"}, {"start_frame": 100, "title": "b"}],
    [{"start_frame": 1, "title": "a"}, {"start_frame": 101, "title": "b"}, {"start_frame": 201, "title": "c"}],
    [{"start_frame": 0, "title": "a"}, {"start_frame": 99, "title": "b"}, {"start_frame": 200, "title": "c"}],
    [{"start_frame": 0, "title": "a"}, {"start_frame": 100, "title": "b"}, {"start_frame": 250, "title": "c"}],
    [{"start_frame": 0, "title": " "}, {"start_frame": 100, "title": "b"}, {"start_frame": 200, "title": "c"}],
    [{"start_frame": 0, "title": "a"}, {"start_frame": 100, "title": "b"}, {"start_frame": 100, "title": "c"}],
])
def test_invalid_timeline_preserves_description(tmp_path, chapters):
    audio = _timeline_audio(tmp_path, chapters=chapters)
    result = resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)
    assert result["description"] == "book description"


def test_missing_or_stale_timeline_preserves_description(tmp_path):
    audio = _timeline_audio(tmp_path)
    audio.with_suffix(".timeline.json").unlink()
    assert resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)["description"] == "book description"


def test_invalid_utf8_timeline_preserves_description(tmp_path):
    audio = _timeline_audio(tmp_path)
    audio.with_suffix(".timeline.json").write_bytes(b"{\xff")
    assert resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)["description"] == "book description"


def test_existing_timeline_after_prose_and_blank_line_is_unchanged(tmp_path):
    audio = _timeline_audio(tmp_path)
    _write_timeline(audio, chapters=[{"start_frame": 0, "title": "Intro"},
                                     {"start_frame": 100, "title": "One"},
                                     {"start_frame": 200, "title": "Two"}])
    block = "00:00 Intro\n00:10 One\n00:20 Two"
    book = _book()
    book.automation_config = json.dumps({"youtube": {"description": f"Prose\n\n{block}"}})
    description = resolve_patch_youtube_metadata(book, _patch(str(audio)), None)["description"]
    assert description == f"Prose\n\n{block}"
    assert description.count("00:00 Intro") == 1


@pytest.mark.parametrize("values", [
    {"sample_rate": 0}, {"sample_rate": 11}, {"total_frames": 299},
    {"sample_rate": True}, {"total_frames": False}, {"total_frames": 301},
    {"chapters": [{"start_frame": 0, "title": "a"}, {"start_frame": 100, "title": "b"}, {"start_frame": 301, "title": "c"}]},
])
def test_invalid_timeline_numbers_preserve_description(tmp_path, values):
    audio = _timeline_audio(tmp_path)
    _write_timeline(audio, **values)
    assert resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)["description"] == "book description"


@pytest.mark.parametrize("start_seconds", [None, True, "10", float("nan"), 10.000000002])
def test_mismatched_or_invalid_start_seconds_preserves_description(tmp_path, start_seconds):
    audio = _timeline_audio(tmp_path, chapters=[
        {"start_frame": 0, "start_seconds": 0, "title": "Intro"},
        {"start_frame": 100, "start_seconds": start_seconds, "title": "Chapter 1"},
        {"start_frame": 200, "start_seconds": 20, "title": "Chapter 2"},
    ])
    assert resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)["description"] == "book description"


def test_start_seconds_must_match_frame_position_tightly(tmp_path):
    audio = _timeline_audio(tmp_path, chapters=[
            {"chapter_index": 1, "start_frame": 0, "start_seconds": 0.0, "title": "Intro"},
            {"chapter_index": 2, "start_frame": 100, "start_seconds": 10.0, "title": "Chapter 1"},
            {"chapter_index": 3, "start_frame": 200, "start_seconds": 20.0, "title": "Chapter 2"},
    ])
    assert "00:10 Chapter 1" in resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)["description"]


def test_timeline_append_is_idempotent_for_exact_existing_block(tmp_path):
    audio = _timeline_audio(tmp_path)
    _write_timeline(audio, chapters=[{"start_frame": 0, "title": "Intro"},
                                     {"start_frame": 100, "title": "One"},
                                     {"start_frame": 200, "title": "Two"}])
    book = _book()
    book.automation_config = json.dumps({"youtube": {"description": "00:00 Intro\n00:10 One\n00:20 Two"}})
    result = resolve_patch_youtube_metadata(book, _patch(str(audio)), None)
    assert result["description"].count("00:00 Intro") == 1


@pytest.mark.parametrize("chapters,frames,valid", [
        ([{"chapter_index": 1, "start_frame": 0, "start_seconds": 0, "title": "a"}, {"chapter_index": 2, "start_frame": 100, "start_seconds": 10, "title": "b"}, {"chapter_index": 3, "start_frame": 200, "start_seconds": 20, "title": "c"}], 300, True),
    ([{"start_frame": 0, "start_seconds": 0, "title": "a"}, {"start_frame": 99, "start_seconds": 9.9, "title": "b"}, {"start_frame": 200, "start_seconds": 20, "title": "c"}], 300, False),
    ([{"start_frame": 0, "start_seconds": 0, "title": "a"}, {"start_frame": 100, "start_seconds": 10, "title": "b"}, {"start_frame": 200, "start_seconds": 20, "title": "c"}], 299, False),
])
def test_timeline_ten_second_boundaries(tmp_path, chapters, frames, valid):
    audio = _timeline_audio(tmp_path, frames=frames, chapters=chapters)
    result = resolve_patch_youtube_metadata(_book(), _patch(str(audio)), None)
    assert ("00:00 a" in result["description"]) is valid


def test_default_patch_title_and_tags():
    result = resolve_patch_youtube_metadata(_book(), _patch(), None)
    assert result["title"] == "Nha Tro - Tap 4 - Chuong 1-8: Mua | kinh di, huyen huyen"
    assert result["tags"] == ["kinh di", "huyen huyen"]


def test_episode_number_comes_from_patch_index():
    assert "Tap 4" in resolve_patch_youtube_metadata(_book(), _patch(), None)["title"]


def test_optional_title_segments_are_omitted():
    patch = _patch()
    patch.name = ""
    book = _book()
    book.automation_config = json.dumps({"youtube": {"genre_tags": ""}})
    assert resolve_patch_youtube_metadata(book, patch, None)["title"] == "Nha Tro - Tap 4 - Chuong 1-8"


def test_empty_patch_name_keeps_non_empty_genre_suffix():
    patch = _patch()
    patch.name = ""
    result = resolve_patch_youtube_metadata(_book(), patch, None)
    assert result["title"] == "Nha Tro - Tap 4 - Chuong 1-8 | kinh di, huyen huyen"


def test_patch_override_wins_and_empty_field_inherits():
    result = resolve_patch_youtube_metadata(_book(), _patch(), {"title": "Custom", "description": ""})
    assert result["title"] == "Custom"
    assert result["description"] == "book description"


def test_patch_genre_override_drives_title_and_tags():
    result = resolve_patch_youtube_metadata(_book(), _patch(), {"genre_tags": " mystery, mystery, fantasy "})
    assert result["title"].endswith("| mystery, fantasy")
    assert result["tags"] == ["mystery", "fantasy"]


def test_list_tags_drive_title_and_returned_tags():
    result = resolve_patch_youtube_metadata(_book(), _patch(), {"tags": [" mystery ", "mystery", "fantasy"]})
    assert result["title"].endswith("| mystery, fantasy")
    assert result["tags"] == ["mystery", "fantasy"]


@pytest.mark.parametrize("empty_field", ["patch_name", "genre_tags"])
def test_custom_template_removes_empty_optional_fragment(empty_field):
    book = _book()
    book.automation_config = json.dumps({"youtube": {
        "title_template": "{book_title}: {patch_name} | {genre_tags}",
        "genre_tags": "genres" if empty_field == "patch_name" else "",
    }})
    patch = _patch()
    if empty_field == "patch_name":
        patch.name = ""
    else:
        patch.name = "Name"
    result = resolve_patch_youtube_metadata(book, patch, None)
    assert ":" not in result["title"] if empty_field == "patch_name" else "|" not in result["title"]


def test_explicit_title_always_includes_resolved_genre_suffix():
    result = resolve_patch_youtube_metadata(_book(), _patch(), {"title": " Custom | ", "genre_tags": " mystery "})
    assert result["title"] == "Custom |"


def test_description_template_renders_allowed_values_and_snapshot_playlist_shape():
    book = _book()
    book.automation_config = json.dumps({"youtube": {
        "description": "{book_title} episode {episode_number}",
        "genre_tags": "mystery, fantasy",
    }})
    result = resolve_patch_youtube_metadata(book, _patch(), None)
    assert result["description"] == "Nha Tro episode 4"
    assert set(result["youtube"]) >= {"mode", "playlist_id", "title_template"}


def test_playlist_override_inherits_book_destination():
    book = _book()
    book.automation_config = json.dumps({"youtube": {"playlist": {"mode": "create", "title_template": "{book_title}", "description_template": "desc"}}})
    result = resolve_patch_youtube_metadata(book, _patch(), {"playlist": {"mode": "existing", "playlist_id": "p1"}})
    assert result["youtube"]["playlist_id"] == "p1"
    with pytest.raises(ValueError):
        validate_book_youtube_config({"description": "{unknown}"})


def test_save_override_validates_values(tmp_path):
    conn = db.connect(str(tmp_path / "override.db"))
    db.init_schema(conn)
    with pytest.raises(ValueError):
        save_patch_youtube_override(conn, 1, {"title": 3})
    with pytest.raises(ValueError):
        save_patch_youtube_override(conn, 1, {"tags": ["ok", 3]})
    with pytest.raises(ValueError):
        save_patch_youtube_override(conn, 1, {"privacy_status": "invalid"})
    with pytest.raises(ValueError):
        save_patch_youtube_override(conn, 1, {"playlist": {"mode": "invalid"}})


@pytest.mark.parametrize("config", [{"description": 3}, {"genre_tags": 3}, {"privacy_status": 3}, {"title_template": 3}, {"playlist": "none"}])
def test_config_types_are_validated(config):
    with pytest.raises(ValueError):
        validate_book_youtube_config(config)


def test_invalid_template_syntax_is_rejected():
    with pytest.raises(ValueError):
        validate_book_youtube_config({"title_template": "{broken"})


@pytest.mark.parametrize("template", [
    "{patch_name}",
    "{patch_name}: {patch_name}",
    "x / {patch_name}",
    "{genre_tags}",
    "{genre_tags} | {genre_tags}",
    "x / {genre_tags}",
])
def test_optional_placeholders_require_exact_single_fragments(template):
    with pytest.raises(ValueError):
        validate_book_youtube_config({"title_template": template})


@pytest.mark.parametrize("template", [
    "{book_title}",
    "{book_title}: {patch_name}",
    "{book_title} | {genre_tags}",
    "{book_title}: {patch_name} | {genre_tags}",
])
def test_title_template_accepts_only_valid_suffix_forms(template):
    assert validate_book_youtube_config({"title_template": template})["title_template"] == template


@pytest.mark.parametrize("template", [
    "{patch_name}: {book_title}",
    "{book_title} | {genre_tags} - tail",
    "{book_title}: {patch_name} - {genre_tags}",
    "{book_title} - : {patch_name}",
    "{book_title} /: {patch_name}",
    "{book_title}: {patch_name}: {patch_name}",
])
def test_title_template_rejects_non_suffix_or_empty_separator_forms(template):
    with pytest.raises(ValueError):
        validate_book_youtube_config({"title_template": template})


def test_valid_custom_template_omits_exact_optional_fragments():
    book = _book()
    book.automation_config = json.dumps({"youtube": {
        "title_template": "{book_title}: {patch_name} | {genre_tags}",
        "genre_tags": "",
    }})
    patch = _patch()
    patch.name = ""
    assert resolve_patch_youtube_metadata(book, patch, None)["title"] == "Nha Tro"


def test_resolved_limits_are_validated():
    with pytest.raises(ValueError):
        resolve_patch_youtube_metadata(_book(), _patch(), {"title": "x" * 101})
    with pytest.raises(ValueError):
        resolve_patch_youtube_metadata(_book(), _patch(), {"description": "x" * 5001})


def test_persistence_and_migration(tmp_path):
    conn = db.connect(str(tmp_path / "metadata.db"))
    db.init_schema(conn)
    assert "youtube_override" in {row["name"] for row in conn.execute("PRAGMA table_info(patch)")}
    conn.execute("INSERT INTO book (title, original_filename, epub_path, patch_size, created_at, updated_at) VALUES ('Book', 'x', 'x', 8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    book_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, created_at, updated_at) VALUES (?, 0, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (book_id,))
    patch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    save_book_youtube_config(conn, book_id, {"genre_tags": "a,b"})
    save_patch_youtube_override(conn, patch_id, {"genre_tags": "x", "description": ""})
    assert get_book_youtube_config(conn, book_id)["genre_tags"] == "a,b"
    assert get_patch_youtube_override(conn, patch_id) == {"genre_tags": "x"}
    conn.execute("UPDATE book SET automation_config = '{bad' WHERE id = ?", (book_id,))
    conn.execute("UPDATE patch SET youtube_override = '{bad' WHERE id = ?", (patch_id,))
    conn.commit()
    assert get_book_youtube_config(conn, book_id)["genre_tags"] == ""
    assert get_patch_youtube_override(conn, patch_id) == {}

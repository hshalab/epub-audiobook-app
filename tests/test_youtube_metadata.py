import json
from types import SimpleNamespace
import pytest

from app import db
from app.youtube_metadata import (get_book_youtube_config, get_patch_youtube_override,
                                  resolve_patch_youtube_metadata, save_book_youtube_config,
                                  save_patch_youtube_override, validate_book_youtube_config)


def _book():
    return SimpleNamespace(title="Nha Tro", automation_config=json.dumps({"youtube": {
        "description": "book description",
        "genre_tags": "kinh di, huyen huyen",
    }}))


def _patch():
    return SimpleNamespace(name="Mua", chapter_start=1, chapter_end=8, patch_index=3)


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

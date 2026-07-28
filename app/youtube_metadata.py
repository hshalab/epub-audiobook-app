"""YouTube metadata configuration, validation, and resolution."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
import string

import soundfile as sf

DEFAULT_TITLE_TEMPLATE = "{book_title} - Tap {episode_number} - Chuong {chapter_start}-{chapter_end}: {patch_name} | {genre_tags}"
ALLOWED_FIELDS = {"book_title", "episode_number", "chapter_start", "chapter_end", "patch_name", "genre_tags"}
OVERRIDE_FIELDS = {"title", "description", "genre_tags", "tags", "privacy_status", "playlist"}
DEFAULT_BOOK_YOUTUBE_CONFIG = {"auto_upload": False, "title_template": DEFAULT_TITLE_TEMPLATE, "description": "", "genre_tags": "", "privacy_status": "private", "playlist": {"mode": "none", "playlist_id": "", "title_template": "{book_title}", "description_template": ""}}


def split_tags(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _template_without_empty_optional_parts(template: str, patch_name: str, genre_text: str) -> str:
    if not patch_name:
        template = template.replace(": {patch_name}", "").replace("{patch_name}: ", "")
    if not genre_text:
        template = template.replace(" | {genre_tags}", "").replace("{genre_tags} | ", "")
    return template


def _validate_title_template(template: str) -> None:
    base = template
    if base.endswith(" | {genre_tags}"):
        base = base[:-len(" | {genre_tags}")]
    if base.endswith(": {patch_name}"):
        base = base[:-len(": {patch_name}")]
    if "{patch_name}" in base or "{genre_tags}" in base or base.rstrip()[-1:] in "-:|/":
        raise ValueError("optional title fields must be trailing suffixes")


def _validate_template(template: str, label: str) -> None:
    try:
        for _, name, _, _ in string.Formatter().parse(template):
            if name and name not in ALLOWED_FIELDS:
                raise ValueError(f"unknown {label} field: {name}")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid {label} template") from exc


def _json_object(value, default):
    try:
        parsed = json.loads(value or "{}") if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return default.copy()
    return parsed.copy() if isinstance(parsed, dict) else default.copy()


def validate_book_youtube_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    result = {**DEFAULT_BOOK_YOUTUBE_CONFIG, **config}
    if not isinstance(result["auto_upload"], bool):
        raise ValueError("auto_upload must be a boolean")
    if not isinstance(result["title_template"], str):
        raise ValueError("title template must be a string")
    _validate_template(result["title_template"], "title")
    _validate_template(result["description"], "description")
    template = result["title_template"]
    _validate_title_template(template)
    if not isinstance(result["description"], str) or not isinstance(result["genre_tags"], str):
        raise ValueError("description and genre_tags must be strings")
    if result["privacy_status"] not in {"private", "unlisted", "public"}:
        raise ValueError("invalid privacy status")
    if not isinstance(result["playlist"], dict):
        raise ValueError("playlist must be an object")
    playlist = {**DEFAULT_BOOK_YOUTUBE_CONFIG["playlist"], **result["playlist"]}
    if playlist["mode"] not in {"none", "existing", "create"} or not isinstance(playlist["playlist_id"], str) or not isinstance(playlist.get("title_template", ""), str) or not isinstance(playlist.get("description_template", ""), str):
        raise ValueError("invalid playlist")
    if playlist["mode"] == "existing" and not playlist["playlist_id"]:
        raise ValueError("existing playlist id is required")
    if playlist["mode"] == "create":
        playlist["playlist_id"] = ""
        _validate_template(playlist["title_template"], "playlist title")
        _validate_template(playlist["description_template"], "playlist description")
        if "{" not in playlist["title_template"] and not 1 <= len(playlist["title_template"]) <= 150:
            raise ValueError("playlist title must be 1-150 characters")
        if "{" not in playlist["description_template"] and len(playlist["description_template"]) > 5000:
            raise ValueError("playlist description exceeds 5000 characters")
    if len(result["description"]) > 5000:
        raise ValueError("description exceeds 5000 characters")
    result["playlist"] = playlist
    return result


def _clean_title(title: str) -> str:
    title = re.sub(r"\s*(?:\||:)\s*(?=-|$)", "", title)
    title = re.sub(r"\s*-\s*(?=-|$)", "", title)
    return re.sub(r"\s+", " ", title).strip(" -:|")


def _validate_override(override: dict) -> dict:
    if not isinstance(override, dict) or any(k not in OVERRIDE_FIELDS for k in override):
        raise ValueError("invalid override field")
    for key in ("title", "description", "genre_tags"):
        if key in override and not isinstance(override[key], str):
            raise ValueError(f"{key} must be a string")
    if "tags" in override and (not isinstance(override["tags"], (str, list)) or (isinstance(override["tags"], list) and not all(isinstance(v, str) for v in override["tags"]))):
        raise ValueError("tags must be a string or list of strings")
    if "privacy_status" in override and override["privacy_status"] not in {"private", "unlisted", "public"}:
        raise ValueError("invalid privacy status")
    if "playlist" in override:
        playlist = override["playlist"]
        if not isinstance(playlist, dict) or playlist.get("mode", "none") not in {"none", "existing", "create"} or not isinstance(playlist.get("playlist_id", ""), str):
            raise ValueError("invalid playlist")
    return override


def _timeline_description(patch) -> str:
    audio_path = getattr(patch, "audio_path", None)
    if not audio_path:
        return ""
    try:
        info = sf.info(audio_path)
        timeline_path = Path(audio_path).with_suffix(".timeline.json")
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        if not isinstance(timeline, dict) or timeline.get("version") != 1:
            return ""
        sample_rate = timeline["sample_rate"]
        total_frames = timeline["total_frames"]
        if (isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0 or sample_rate != info.samplerate or
                isinstance(total_frames, bool) or not isinstance(total_frames, int) or total_frames < 0 or total_frames != info.frames):
            return ""
        chapters = timeline["chapters"]
        if not isinstance(chapters, list) or len(chapters) < 3:
            return ""
        starts = []
        titles = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                return ""
            start = chapter["start_frame"]
            start_seconds = chapter["start_seconds"]
            title = chapter["title"]
            if (isinstance(start, bool) or not isinstance(start, int) or start < 0 or start > total_frames or
                    isinstance(start_seconds, bool) or not isinstance(start_seconds, (int, float)) or
                    not math.isfinite(start_seconds) or not math.isclose(start_seconds, start / sample_rate, rel_tol=0, abs_tol=1e-9) or
                    not isinstance(title, str) or not title.strip()):
                return ""
            starts.append(start)
            titles.append(title.strip())
        if starts[0] != 0 or any(b - a < sample_rate * 10 for a, b in zip(starts, starts[1:])):
            return ""
        if total_frames - starts[-1] < sample_rate * 10:
            return ""

        def format_time(frame):
            seconds = frame // sample_rate
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

        return "\n".join(f"{format_time(start)} {title}" for start, title in zip(starts, titles))
    except (OSError, TypeError, ValueError, UnicodeDecodeError, KeyError, json.JSONDecodeError, sf.SoundFileError):
        return ""


def resolve_patch_youtube_metadata(book, patch, override: dict | None) -> dict:
    raw = _json_object(book.automation_config, {})
    config = validate_book_youtube_config(raw.get("youtube", {}))
    override = _validate_override({k: v for k, v in _json_object(override, {}).items() if k in OVERRIDE_FIELDS})
    genre_value = override.get("genre_tags") or override.get("tags") or config["genre_tags"]
    if isinstance(genre_value, list):
        genre_value = ",".join(genre_value)
    if not isinstance(genre_value, str):
        raise ValueError("genre_tags override must be a string")
    genre_text = ", ".join(split_tags(genre_value))
    values = {"book_title": book.title, "episode_number": patch.patch_index + 1, "chapter_start": patch.chapter_start, "chapter_end": patch.chapter_end, "patch_name": patch.name or "", "genre_tags": genre_text}
    try:
        if config["title_template"] == DEFAULT_TITLE_TEMPLATE:
            title = f"{book.title} - Tap {values['episode_number']} - Chuong {patch.chapter_start}-{patch.chapter_end}"
            if values["patch_name"]:
                title += f": {values['patch_name']}"
            if genre_text:
                title += f" | {genre_text}"
        else:
            template = config["title_template"]
            if not values["patch_name"]:
                template = template.replace(": {patch_name}", "")
            if not genre_text:
                template = template.replace(" | {genre_tags}", "")
            title = _clean_title(template.format(**values))
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError("invalid title template") from exc
    explicit_title = bool(override.get("title"))
    title = override.get("title") or title
    title = _clean_title(title) if not explicit_title else title.strip()
    if genre_text and not explicit_title:
        suffix = f" | {genre_text}"
        if not title.endswith(suffix):
            title = title.rstrip(" |:") + suffix
    description_template = override.get("description") or config["description"]
    try:
        description = description_template.format(**values)
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError("invalid description template") from exc
    timeline = _timeline_description(patch)
    if timeline:
        if description.rstrip() == timeline or description.endswith(f"\n\n{timeline}"):
            candidate = description
        else:
            candidate = f"{description}\n\n{timeline}" if description else timeline
        if len(candidate) <= 5000:
            description = candidate
    privacy = override.get("privacy_status") or config["privacy_status"]
    if not isinstance(title, str) or not title or len(title) > 100:
        raise ValueError("title must be 1-100 characters")
    if not isinstance(description, str) or len(description) > 5000:
        raise ValueError("description exceeds 5000 characters")
    if privacy not in {"private", "unlisted", "public"}:
        raise ValueError("invalid privacy status")
    playlist = {**config["playlist"], **(override.get("playlist") or {})}
    validated = validate_book_youtube_config({**config, "playlist": playlist})
    playlist = validated["playlist"]
    return {"title": title, "description": description, "tags": split_tags(genre_value), "privacy_status": privacy, "youtube": playlist}


def get_book_youtube_config(conn, book_id: int) -> dict:
    row = conn.execute("SELECT automation_config FROM book WHERE id = ?", (book_id,)).fetchone()
    return validate_book_youtube_config(_json_object((row[0] if row else None), {}).get("youtube", {}))


def save_book_youtube_config(conn, book_id: int, config: dict) -> None:
    validated = validate_book_youtube_config(config)
    row = conn.execute("SELECT automation_config FROM book WHERE id = ?", (book_id,)).fetchone()
    raw = _json_object(row[0] if row else None, {})
    raw["youtube"] = validated
    conn.execute("UPDATE book SET automation_config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(raw), book_id))
    conn.commit()


def get_patch_youtube_override(conn, patch_id: int) -> dict:
    row = conn.execute("SELECT youtube_override FROM patch WHERE id = ?", (patch_id,)).fetchone()
    return _json_object(row[0] if row else None, {})


def save_patch_youtube_override(conn, patch_id: int, override: dict) -> None:
    _validate_override(override)
    normalized = {key: value for key, value in override.items() if value != ""}
    conn.execute("UPDATE patch SET youtube_override = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(normalized), patch_id))
    conn.commit()

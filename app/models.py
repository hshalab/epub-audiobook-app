"""Typed row representations for the SQLite tables."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str
    original_filename: str
    epub_path: str
    patch_size: int
    status: str  # parsing | ready | processing | done | failed
    final_audio_path: str | None
    final_video_path: str | None
    background_image_path: str | None
    voice_clip_path: str | None
    voice_transcript: str | None
    created_at: str
    updated_at: str
    video_resolution: str = "1920x1080"
    video_fps: int = 30
    default_image_animation: str = "none"
    normalize_numbers_enabled: int = 1
    normalize_junk_enabled: int = 1
    normalize_spellcheck_enabled: int = 1
    music_id: int | None = None
    music_volume: float = 0.15
    overlay_config: str | None = None
    text_position: str = "top"
    text_align: str = "center"
    text_font_size: int = 52
    text_color: str = "#FFFFFF"
    text_shadow: int = 1
    text_box_enabled: int = 0
    text_box_color: str = "#000000"
    text_box_opacity: float = 0.6
    text_box_padding: int = 12
    text_box_radius: int = 12


@dataclass
class Chapter:
    id: int
    book_id: int
    chapter_index: int
    title: str
    text: str
    char_count: int
    is_excluded: bool = False


@dataclass
class Patch:
    id: int
    book_id: int
    patch_index: int
    chapter_start: int
    chapter_end: int
    status: str  # pending | processing | done | failed
    audio_path: str | None
    error_message: str | None
    attempt_count: int
    created_at: str
    updated_at: str
    image_path: str | None = None
    image_type: str = "static"
    name: str = ""
    chunk_count: int = 0
    next_chunk_index: int = 0
    max_chars: int | None = None


@dataclass
class Music:
    id: int
    name: str
    file_path: str
    duration_sec: float | None
    created_at: str


@dataclass
class PatchExport:
    id: int
    patch_id: int
    drive_folder_id: str
    drive_folder_link: str
    status: str  # exported | partially_imported | imported | failed
    exported_chunk_count: int
    imported_chunk_count: int
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass
class BookJob:
    id: int
    book_id: int
    job_type: str  # 'video' for now
    status: str  # pending | processing | done | failed
    attempt_count: int
    error_message: str | None
    output_path: str | None
    created_at: str
    updated_at: str


@dataclass
class TextReplaceRule:
    id: int
    book_id: int
    find: str
    replace: str
    is_regex: bool
    position: int

"""SQLite connection helper and schema initialization."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS book (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    epub_path       TEXT NOT NULL,
    patch_size      INTEGER NOT NULL DEFAULT 10,
    status          TEXT NOT NULL DEFAULT 'parsing',
    final_audio_path TEXT,
    final_video_path TEXT,
    background_image_path TEXT,
    voice_clip_path TEXT,
    voice_transcript TEXT,
    normalize_numbers_enabled INTEGER NOT NULL DEFAULT 1,
    normalize_junk_enabled INTEGER NOT NULL DEFAULT 1,
    normalize_spellcheck_enabled INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapter (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
    chapter_index   INTEGER NOT NULL,
    title           TEXT,
    text            TEXT NOT NULL,
    char_count      INTEGER NOT NULL,
    UNIQUE(book_id, chapter_index)
);

CREATE TABLE IF NOT EXISTS patch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
    patch_index     INTEGER NOT NULL,
    chapter_start   INTEGER NOT NULL,
    chapter_end     INTEGER NOT NULL,
    name            TEXT,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    next_chunk_index INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    audio_path      TEXT,
    error_message   TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(book_id, patch_index)
);

CREATE INDEX IF NOT EXISTS idx_patch_status ON patch(status);
CREATE INDEX IF NOT EXISTS idx_patch_book_order ON patch(book_id, patch_index);
CREATE INDEX IF NOT EXISTS idx_patch_status_updated ON patch(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS book_job (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
    job_type        TEXT NOT NULL DEFAULT 'video',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    output_path     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(book_id, job_type)
);

CREATE INDEX IF NOT EXISTS idx_book_job_status ON book_job(status, book_id, id);
CREATE INDEX IF NOT EXISTS idx_book_job_book_type ON book_job(book_id, job_type);

CREATE TABLE IF NOT EXISTS drive_oauth_client (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    client_secret   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key             TEXT PRIMARY KEY,
    value           TEXT
);

CREATE TABLE IF NOT EXISTS text_replace_rule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id         INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
    find            TEXT NOT NULL,
    replace         TEXT NOT NULL DEFAULT '',
    is_regex        INTEGER NOT NULL DEFAULT 0,
    position        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS youtube_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_expiry    TEXT NOT NULL,
    channel_id      TEXT,
    channel_name    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS youtube_uploads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_path      TEXT NOT NULL,
    youtube_video_id TEXT,
    title           TEXT,
    description     TEXT,
    tags            TEXT,
    privacy_status  TEXT NOT NULL DEFAULT 'private',
    status          TEXT NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    uploaded_at     TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS google_drive_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_expiry    TEXT NOT NULL,
    account_email   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS music (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    duration_sec    REAL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patch_export (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    patch_id                INTEGER NOT NULL REFERENCES patch(id) ON DELETE CASCADE,
    -- google_drive_credentials.id of the account the export went to; NULL = legacy
    -- export from before multi-account. Deliberately not a FK: disconnecting an
    -- account must neither be blocked by export history nor erase it.
    drive_account_id        INTEGER,
    drive_folder_id         TEXT NOT NULL,
    drive_folder_link       TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'exported',
    exported_chunk_count    INTEGER NOT NULL DEFAULT 0,
    imported_chunk_count    INTEGER NOT NULL DEFAULT 0,
    error_message           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_patch_export_patch ON patch_export(patch_id, id DESC);

CREATE TABLE IF NOT EXISTS voice_meta (
    filename    TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a book table already existed on disk."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(book)")}
    if "voice_clip_path" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN voice_clip_path TEXT")
    if "voice_transcript" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN voice_transcript TEXT")
    # book_job and app_state are CREATE TABLE IF NOT EXISTS, so they're picked up by
    # init_schema on a fresh DB and are a no-op on an existing DB; no per-column migration
    # is needed for them.
    chapter_existing = {row["name"] for row in conn.execute("PRAGMA table_info(chapter)")}
    if "is_excluded" not in chapter_existing:
        conn.execute("ALTER TABLE chapter ADD COLUMN is_excluded INTEGER NOT NULL DEFAULT 0")
    patch_existing = {row["name"] for row in conn.execute("PRAGMA table_info(patch)")}
    if "image_path" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN image_path TEXT")
    if "image_type" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN image_type TEXT NOT NULL DEFAULT 'static'")
    if "name" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN name TEXT")
    if "chunk_count" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 0")
    if "next_chunk_index" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN next_chunk_index INTEGER NOT NULL DEFAULT 0")
    if "video_resolution" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN video_resolution TEXT NOT NULL DEFAULT '1920x1080'")
    if "video_fps" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN video_fps INTEGER NOT NULL DEFAULT 30")
    if "default_image_animation" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN default_image_animation TEXT NOT NULL DEFAULT 'none'")
    if "max_chars" not in patch_existing:
        conn.execute("ALTER TABLE patch ADD COLUMN max_chars INTEGER")
    if "normalize_numbers_enabled" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN normalize_numbers_enabled INTEGER NOT NULL DEFAULT 1")
    if "normalize_junk_enabled" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN normalize_junk_enabled INTEGER NOT NULL DEFAULT 1")
    if "normalize_spellcheck_enabled" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN normalize_spellcheck_enabled INTEGER NOT NULL DEFAULT 1")
    if "music_id" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN music_id INTEGER REFERENCES music(id)")
    if "music_volume" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN music_volume REAL NOT NULL DEFAULT 0.15")
    if "overlay_config" not in existing:
        conn.execute("ALTER TABLE book ADD COLUMN overlay_config TEXT")
    export_existing = {row["name"] for row in conn.execute("PRAGMA table_info(patch_export)")}
    if "drive_account_id" not in export_existing:
        conn.execute("ALTER TABLE patch_export ADD COLUMN drive_account_id INTEGER")
    gdc_existing = {row["name"] for row in conn.execute("PRAGMA table_info(google_drive_credentials)")}
    if "oauth_client_id" not in gdc_existing:
        conn.execute("ALTER TABLE google_drive_credentials ADD COLUMN oauth_client_id INTEGER")
    from app.config import settings
    if settings.google_drive_client_id:
        row = conn.execute("SELECT 1 FROM drive_oauth_client LIMIT 1").fetchone()
        if row is None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO drive_oauth_client (name, client_id, client_secret, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("Default OAuth Client", settings.google_drive_client_id, settings.google_drive_client_secret, now, now),
            )

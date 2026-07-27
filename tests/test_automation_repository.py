import json
import sqlite3
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import automation_repository, db, models


def _seed(conn):
    now = "2026-07-26T00:00:00+00:00"
    book_id = conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) "
        "VALUES ('B','b.epub','b.epub',1,'ready',?,?)",
        (now, now),
    ).lastrowid
    patch_id = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,status,created_at,updated_at) "
        "VALUES (?,0,0,0,'done',?,?)",
        (book_id, now, now),
    ).lastrowid
    conn.commit()
    return book_id, patch_id


def test_settings_override_media_order_and_idempotent_enqueue(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    book_id, patch_id = _seed(conn)

    automation_repository.save_system_config(conn, {"video": {"fps": 25}})
    automation_repository.save_book_override(conn, book_id, {"video": {"fps": 30}})
    assert automation_repository.get_system_config(conn).video.fps == 25
    assert automation_repository.get_effective_config(conn, book_id).video.fps == 30

    a = automation_repository.upsert_media_asset(conn, "/tmp/a.jpg", "a.jpg", "image")
    b = automation_repository.upsert_media_asset(conn, "/tmp/b.mp4", "b.mp4", "video")
    c = automation_repository.upsert_media_asset(conn, "/tmp/c.mp4", "c.mp4", "video")
    assert automation_repository.upsert_media_asset(conn, "/tmp/a.jpg", "renamed.jpg", "image")["id"] == a["id"]
    automation_repository.set_book_media(conn, book_id, "background", [b["id"], a["id"]])
    automation_repository.set_book_media(conn, book_id, "webcam", [c["id"]])
    assert [x["id"] for x in automation_repository.list_book_media(conn, book_id, "background")] == [b["id"], a["id"]]

    first = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    automation_repository.set_book_media(conn, book_id, "background", [a["id"]])
    second = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    assert first["id"] == second["id"]
    assert json.loads(first["config_snapshot"])["automation"]["video"]["fps"] == 30
    expected_config = automation_repository.get_effective_config(conn, book_id).model_dump()
    config_snapshot = json.loads(first["config_snapshot"])
    assert config_snapshot["automation"] == expected_config
    assert config_snapshot["overlay_config"] is None
    assert config_snapshot["background_fallback"] is None
    assert json.loads(second["media_snapshot"]) == {
        "background": [
            {"id": b["id"], "file_path": "/tmp/b.mp4", "media_type": "video"},
            {"id": a["id"], "file_path": "/tmp/a.jpg", "media_type": "image"},
        ],
        "webcam": [{"id": c["id"], "file_path": "/tmp/c.mp4", "media_type": "video"}],
    }


def test_claim_pipeline_stage_claims_only_requested_row(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    _, first_patch = _seed(conn)
    first = automation_repository.enqueue_patch_pipeline(conn, first_patch)
    now = "2026-07-26T00:00:00+00:00"
    second_patch = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,status,created_at,updated_at) "
        "SELECT book_id,1,0,0,'done',?,? FROM patch WHERE id=?",
        (now, now, first_patch),
    ).lastrowid
    second = automation_repository.enqueue_patch_pipeline(conn, second_patch)

    claimed = automation_repository.claim_pipeline_stage(conn, second["id"], "thumbnail")

    assert claimed["id"] == second["id"]
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (first["id"],)).fetchone()[0] == "pending"
    assert automation_repository.claim_pipeline_stage(conn, second["id"], "thumbnail") is None


def test_patch_video_upsert_is_single_statement_and_safe_across_connections(tmp_path, monkeypatch):
    from app import video_repository

    path = str(tmp_path / "shared.db")
    first_conn = db.connect(path)
    db.init_schema(first_conn)
    book_id, patch_id = _seed(first_conn)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"mp4")
    statements = []
    first_conn.set_trace_callback(statements.append)
    first = video_repository.upsert_patch_video(first_conn, book_id=book_id, patch_id=patch_id, file_path=str(video), resolution="1280x720")
    writes = [sql for sql in statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE"))]
    assert len(writes) == 1
    assert "ON CONFLICT" in writes[0].upper() and "RETURNING" in writes[0].upper()

    second_conn = db.connect(path)
    second = video_repository.upsert_patch_video(second_conn, book_id=book_id, patch_id=patch_id, file_path=str(video), resolution="1920x1080")
    assert second["id"] == first["id"]
    assert second["resolution"] == "1920x1080"


def test_clearing_book_override_returns_inherited_config(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    book_id, _ = _seed(conn)
    automation_repository.save_system_config(conn, {"video": {"fps": 25}})
    automation_repository.save_book_override(conn, book_id, {"video": {"fps": 30}})

    cleared = automation_repository.save_book_override(conn, book_id, None)

    assert cleared.video.fps == 25
    assert conn.execute("SELECT automation_config FROM book WHERE id=?", (book_id,)).fetchone()[0] is None


def test_claim_update_and_playlist_map(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    book_id, patch_id = _seed(conn)
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)

    claimed = automation_repository.claim_next_pipeline_stage(conn, "thumbnail")
    assert claimed["id"] == pipeline["id"]
    assert claimed["thumbnail_status"] == "processing"
    assert automation_repository.claim_next_pipeline_stage(conn, "thumbnail") is None

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    automation_repository.update_pipeline_stage(conn, pipeline["id"], "thumbnail", "failed", error="boom", next_retry_at=future)
    assert automation_repository.claim_next_pipeline_stage(conn, "thumbnail") is None
    automation_repository.update_pipeline_stage(conn, pipeline["id"], "thumbnail", "pending", next_retry_at=None)
    assert automation_repository.claim_next_pipeline_stage(conn, "thumbnail")["attempt_count"] == 2

    first = automation_repository.get_or_create_playlist_map(conn, book_id, "channel", "playlist", "auto-create")
    second = automation_repository.get_or_create_playlist_map(conn, book_id, "channel", "other", "existing")
    assert first["id"] == second["id"]
    assert second["playlist_id"] == "playlist"


def test_claim_requires_current_stage(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    _, patch_id = _seed(conn)
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)

    assert automation_repository.claim_next_pipeline_stage(conn, "video") is None
    advanced = automation_repository.advance_pipeline_stage(
        conn, pipeline["id"], "thumbnail", "video"
    )
    assert advanced["stage"] == "video"
    assert advanced["thumbnail_status"] == "done"
    assert advanced["last_error"] is None
    assert advanced["next_retry_at"] is None
    assert automation_repository.claim_next_pipeline_stage(conn, "video")["video_status"] == "processing"


def test_advance_pipeline_stage_validates_sequence(tmp_path):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    _, patch_id = _seed(conn)
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)

    with pytest.raises(ValueError, match="invalid pipeline transition"):
        automation_repository.advance_pipeline_stage(conn, pipeline["id"], "thumbnail", "upload")
    with pytest.raises(ValueError, match="unknown pipeline stage"):
        automation_repository.advance_pipeline_stage(conn, pipeline["id"], "bogus", "video")


def test_claim_is_atomic_across_connections(tmp_path):
    path = str(tmp_path / "race.db")
    setup = db.connect(path)
    db.init_schema(setup)
    _, patch_id = _seed(setup)
    automation_repository.enqueue_patch_pipeline(setup, patch_id)
    setup.close()
    barrier = Barrier(2)

    def claim():
        conn = db.connect(path)
        barrier.wait()
        try:
            row = automation_repository.claim_next_pipeline_stage(conn, "thumbnail")
            return row["id"] if row else None
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    assert sum(result is not None for result in results) == 1


def test_retry_offsets_are_canonical_and_compared_as_instants(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    _, patch_id = _seed(conn)
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    monkeypatch.setattr(automation_repository, "_now", lambda: "2026-07-26T12:00:00+00:00")

    due = automation_repository.update_pipeline_stage(
        conn, pipeline["id"], "thumbnail", "pending", next_retry_at="2026-07-26T13:00:00+01:00"
    )
    assert due["next_retry_at"] == "2026-07-26T12:00:00+00:00"
    assert automation_repository.claim_next_pipeline_stage(conn, "thumbnail") is not None

    automation_repository.update_pipeline_stage(
        conn, pipeline["id"], "thumbnail", "pending", next_retry_at="2026-07-26T08:30:00-04:00"
    )
    assert automation_repository.claim_next_pipeline_stage(conn, "thumbnail") is None


def test_schema_migrates_existing_tables(tmp_path):
    conn = db.connect(str(tmp_path / "legacy.db"))
    conn.executescript(
        "CREATE TABLE book (id INTEGER PRIMARY KEY);"
        "CREATE TABLE videos (id INTEGER PRIMARY KEY, upload_status TEXT, batch_id TEXT, created_at TEXT);"
        "CREATE TABLE youtube_uploads (id INTEGER PRIMARY KEY);"
    )
    db.init_schema(conn)
    assert "automation_config" in {row["name"] for row in conn.execute("PRAGMA table_info(book)")}
    assert {"book_id", "patch_id"} <= {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    upload_columns = {row["name"] for row in conn.execute("PRAGMA table_info(youtube_uploads)")}
    assert {"upload_progress", "thumbnail_status", "playlist_status", "metadata_snapshot", "next_retry_at"} <= upload_columns
    db.init_schema(conn)


def test_fresh_schema_constraints_and_foreign_keys(tmp_path):
    conn = db.connect(str(tmp_path / "fresh.db"))
    db.init_schema(conn)
    db.init_schema(conn)

    pipeline_fks = {row["from"]: (row["table"], row["to"], row["on_delete"]) for row in conn.execute("PRAGMA foreign_key_list(patch_pipeline)")}
    assert pipeline_fks == {
        "youtube_upload_id": ("youtube_uploads", "id", "SET NULL"),
        "video_id": ("videos", "id", "SET NULL"),
        "patch_id": ("patch", "id", "CASCADE"),
    }
    selection_fks = {row["from"]: (row["table"], row["on_delete"]) for row in conn.execute("PRAGMA foreign_key_list(book_media_selection)")}
    assert selection_fks == {"media_asset_id": ("media_assets", "CASCADE"), "book_id": ("book", "CASCADE")}

    index = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_videos_patch_id'").fetchone()[0]
    assert "WHERE patch_id IS NOT NULL" in index
    assert any(row["unique"] for row in conn.execute("PRAGMA index_list(patch_pipeline)"))
    assert any(row["unique"] for row in conn.execute("PRAGMA index_list(book_media_selection)"))
    assert any(row["unique"] for row in conn.execute("PRAGMA index_list(youtube_playlist_map)"))
    claim_index = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_patch_pipeline_claim'").fetchone()[0]
    assert "stage, next_retry_at, id" in claim_index


def test_init_replaces_prechange_claim_index(tmp_path):
    conn = db.connect(str(tmp_path / "upgrade.db"))
    db.init_schema(conn)
    conn.execute("DROP INDEX idx_patch_pipeline_claim")
    conn.execute("CREATE INDEX idx_patch_pipeline_claim ON patch_pipeline(stage, next_retry_at)")
    conn.commit()

    db.init_schema(conn)

    index = conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_patch_pipeline_claim'").fetchone()[0]
    assert "stage, next_retry_at, id" in index


def test_schema_unique_contracts(tmp_path):
    conn = db.connect(str(tmp_path / "fresh.db"))
    db.init_schema(conn)
    book_id, patch_id = _seed(conn)
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO patch_pipeline (patch_id,config_snapshot,media_snapshot,created_at,updated_at) VALUES (?,?,?,?,?)",
            (patch_id, "{}", "{}", pipeline["created_at"], pipeline["updated_at"]),
        )
    asset = automation_repository.upsert_media_asset(conn, "/tmp/a", "a", "image")
    conn.execute("INSERT INTO book_media_selection VALUES (?,?,?,?)", (book_id, "background", asset["id"], 0))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO book_media_selection VALUES (?,?,?,?)", (book_id, "background", asset["id"], 1))
    automation_repository.get_or_create_playlist_map(conn, book_id, "channel", "one", "existing")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO youtube_playlist_map (book_id,channel_id,playlist_id,mode,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (book_id, "channel", "two", "existing", pipeline["created_at"], pipeline["updated_at"]),
        )


def test_patch_pipeline_model_matches_schema(tmp_path):
    conn = db.connect(str(tmp_path / "fresh.db"))
    db.init_schema(conn)
    schema_names = [row["name"] for row in conn.execute("PRAGMA table_info(patch_pipeline)")]
    assert [field.name for field in fields(models.PatchPipeline)] == schema_names

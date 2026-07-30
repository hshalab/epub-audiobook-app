import json

from app import db, youtube
from app.video_repository import insert_video, update_video


EXPECTED_UPLOAD_COLUMNS = {
    "validation_status", "validation_error_code", "validation_error_message",
    "validated_at", "integrity_retry_count", "render_source_type", "render_source_id",
}


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    return conn


def test_schema_adds_integrity_provenance_and_render_config(tmp_path):
    conn = _conn(tmp_path)
    upload_columns = {row["name"] for row in conn.execute("PRAGMA table_info(youtube_uploads)")}
    video_columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    assert EXPECTED_UPLOAD_COLUMNS <= upload_columns
    assert "render_config_json" in video_columns


def test_upload_defaults_do_not_change_upload_lifecycle(tmp_path):
    conn = _conn(tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v.mp4", "T")
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert (row["status"], row["validation_status"], row["integrity_retry_count"],
            row["render_source_type"]) == ("pending", "pending", 0, "external")


def test_enqueue_upload_persists_explicit_provenance(tmp_path):
    conn = _conn(tmp_path)
    upload_id = youtube.enqueue_upload(
        conn, "v.mp4", "T", render_source_type="book", render_source_id=7,
    )
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert (row["render_source_type"], row["render_source_id"]) == ("book", 7)


def test_enqueue_rejects_unknown_source_type(tmp_path):
    conn = _conn(tmp_path)
    try:
        youtube.enqueue_upload(conn, "v.mp4", "T", render_source_type="filename_guess")
    except ValueError as exc:
        assert "render_source_type" in str(exc)
    else:
        raise AssertionError("unknown source accepted")


def test_validation_helpers_clear_stale_errors_and_bound_messages(tmp_path):
    conn = _conn(tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v.mp4", "T")
    youtube.mark_validation_failed(conn, upload_id, "decode_failed", "x" * 3000)
    failed = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert failed["validation_status"] == "failed"
    assert len(failed["validation_error_message"]) == 2000
    assert failed["validated_at"]
    youtube.mark_validation_started(conn, upload_id)
    started = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert started["validation_status"] == "validating"
    assert started["validation_error_code"] is None
    youtube.mark_validation_valid(conn, upload_id)
    valid = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert valid["validation_status"] == "valid"
    assert valid["validation_error_message"] is None


def test_standalone_video_persists_reproducible_render_config(tmp_path):
    conn = _conn(tmp_path)
    config = {"fps": 30, "codec": "libx264", "music_path": None}
    video = insert_video(
        conn, filename="v.mp4", original_name="v.mp4", file_path="v.mp4",
        source_audio="a.wav", background_path="b.jpg", render_config=config,
    )
    assert json.loads(video["render_config_json"]) == config
    updated = update_video(conn, video["id"], file_path="new.mp4", source_audio="new.wav",
                           background_path="new.jpg", render_config_json='{"fps":24}')
    assert (updated["file_path"], updated["source_audio"], updated["background_path"]) == (
        "new.mp4", "new.wav", "new.jpg")
    assert json.loads(updated["render_config_json"]) == {"fps": 24}

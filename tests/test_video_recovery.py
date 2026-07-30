from datetime import datetime, timezone

from app import db, youtube
from app.jobqueue import store
from app.video_integrity import ValidationFacts, ValidationResult
from app.video_recovery import infer_render_source, resume_upload_after_render, schedule_rerender


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    return conn


def _invalid(code="decode_failed"):
    return ValidationResult(False, code, "broken", (), ValidationFacts(), 0)


def _book_source(conn, tmp_path, job_id=7):
    audio = tmp_path / f"book-{job_id}.wav"; audio.write_bytes(b"audio")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO book (id,title,original_filename,epub_path,patch_size,status,final_audio_path,created_at,updated_at) VALUES (?,?, 'b','b',1,'done',?,?,?)", (job_id, f"B{job_id}", str(audio), now, now))
    conn.execute("INSERT INTO book_job (id,book_id,job_type,status,created_at,updated_at) VALUES (?,?, 'video','done',?,?)", (job_id, job_id, now, now)); conn.commit()


def test_explicit_source_wins_and_unlinked_stays_external(tmp_path):
    conn = _conn(tmp_path)
    explicit = youtube.enqueue_upload(conn, "v", "T", render_source_type="book", render_source_id=7)
    external = youtube.enqueue_upload(conn, "other", "T")
    assert infer_render_source(conn, dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (explicit,)).fetchone())) == ("book", 7)
    assert infer_render_source(conn, dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (external,)).fetchone())) == ("external", None)


def test_linked_reproducible_video_infers_standalone(tmp_path):
    conn = _conn(tmp_path); now = datetime.now(timezone.utc).isoformat()
    video_id = conn.execute("""INSERT INTO videos (filename,file_path,source_audio,background_path,render_config_json,created_at,updated_at)
        VALUES ('v','v','a','b','{}',?,?)""", (now, now)).lastrowid; conn.commit()
    upload_id = youtube.enqueue_upload(conn, "v", "T", video_id=video_id)
    upload = dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone())
    assert infer_render_source(conn, upload) == ("standalone", video_id)


def test_recoverable_failure_persists_count_and_enqueues_generation(tmp_path):
    conn = _conn(tmp_path)
    _book_source(conn, tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v", "T", render_source_type="book", render_source_id=7)
    decision = schedule_rerender(conn, upload_id, _invalid())
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    job = store.list_jobs(conn, job_type="video")[0]
    assert (decision.action, decision.retry_count) == ("rerender", 1)
    assert (row["status"], row["validation_status"], row["integrity_retry_count"]) == ("waiting_for_rerender", "waiting_for_rerender", 1)
    assert job.payload == {"book_job_id": 7, "recovery_upload_id": upload_id}
    assert job.dedupe_key.endswith("integrity_retry=1")


def test_second_retry_is_allowed_then_next_failure_is_terminal(tmp_path):
    conn = _conn(tmp_path)
    _book_source(conn, tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v", "T", render_source_type="book", render_source_id=7)
    conn.execute("UPDATE youtube_uploads SET integrity_retry_count=1 WHERE id=?", (upload_id,)); conn.commit()
    assert schedule_rerender(conn, upload_id, _invalid()).retry_count == 2
    store.finish(conn, store.list_jobs(conn, job_type="video")[0].id, None)
    terminal = schedule_rerender(conn, upload_id, _invalid())
    row = conn.execute("SELECT status,integrity_retry_count FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert terminal.action == "failed"
    assert tuple(row) == ("failed", 2)


def test_external_and_infrastructure_failures_do_not_consume_retry(tmp_path):
    conn = _conn(tmp_path)
    external = youtube.enqueue_upload(conn, "v", "T")
    assert schedule_rerender(conn, external, _invalid()).action == "failed"
    infra = youtube.enqueue_upload(conn, "x", "T", render_source_type="book", render_source_id=8)
    decision = schedule_rerender(conn, infra, _invalid("tool_unavailable"))
    row = conn.execute("SELECT integrity_retry_count FROM youtube_uploads WHERE id=?", (infra,)).fetchone()
    assert decision.action == "retry_validation"
    assert row[0] == 0


def test_missing_application_source_fails_before_retry_count_increments(tmp_path):
    conn = _conn(tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v", "T", render_source_type="book", render_source_id=999)
    decision = schedule_rerender(conn, upload_id, _invalid())
    row = conn.execute("SELECT status,validation_error_code,integrity_retry_count FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert decision.action == "failed"
    assert tuple(row) == ("failed", "source_unavailable", 0)


def test_resume_reuses_same_upload_and_enqueues_once(tmp_path):
    conn = _conn(tmp_path)
    upload_id = youtube.enqueue_upload(conn, "v", "T", render_source_type="book", render_source_id=7)
    conn.execute("UPDATE youtube_uploads SET status='waiting_for_rerender',validation_status='waiting_for_rerender',integrity_retry_count=1 WHERE id=?", (upload_id,)); conn.commit()
    assert resume_upload_after_render(conn, upload_id)
    row = conn.execute("SELECT status,validation_status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert tuple(row) == ("pending", "pending")
    assert [j.payload["upload_id"] for j in store.list_jobs(conn, job_type="youtube_upload")] == [upload_id]
    assert resume_upload_after_render(conn, upload_id) is None

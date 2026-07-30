"""Handler video: gọi video_gen, nối on_progress vào ctx.progress, nối chuỗi upload."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db, repository
from app.jobqueue import store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.handlers import video as video_handler
from app.jobqueue.models import JobFatalError
from app.video_integrity import ValidationFacts, ValidationResult


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(video_handler, "validate_video", lambda path: ValidationResult(True, None, "", (), ValidationFacts(), 0))
    yield


def _book_job(conn, *, final_audio="/tmp/final.wav"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                              final_audio_path, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?, ?)""",
        (final_audio, now, now))
    conn.commit()
    return repository.enqueue_book_job(conn, 1, "video")


def _ctx(conn, book_job_id):
    job_id = store.enqueue(conn, "video", payload={"book_job_id": book_job_id}, book_id=1)
    job = store.claim(conn, "video", "video#1")
    return JobContext(job, conn, JobLogger(job_id, "video"), lambda: False), job_id


def test_missing_book_job_is_fatal(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    ctx, _ = _ctx(conn, 999)
    with pytest.raises(JobFatalError):
        video_handler.handle(ctx)


def test_book_without_final_audio_is_fatal(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn, final_audio=None)
    ctx, _ = _ctx(conn, bj.id)
    with pytest.raises(JobFatalError):
        video_handler.handle(ctx)
    assert repository.get_book_job(conn, 1, "video").status == "failed"


def test_successful_render_marks_the_book_job_and_book(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)
    calls = {}

    def fake_generate(patches, book, out_path, **kw):
        calls["out_path"] = out_path
        calls["on_progress"] = kw.get("on_progress")
        kw["on_progress"]("segment.start", {"path": out_path})
        kw["on_progress"]("concat.done", {"count": 3})
        open(out_path, "wb").close()

    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", fake_generate)
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", False)
    ctx, job_id = _ctx(conn, bj.id)

    result = video_handler.handle(ctx)

    assert result["output_path"].endswith(f"video_{bj.id}.mp4")
    assert repository.get_book_job(conn, 1, "video").status == "done"
    assert repository.get_book(conn, 1).final_video_path == result["output_path"]
    queue_job = store.get(conn, job_id)
    assert (queue_job.progress_current, queue_job.progress_total, queue_job.phase) == (1, 1, "done")
    assert calls["out_path"] != result["output_path"]


def test_auto_upload_records_book_render_source(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn); bj = _book_job(conn)
    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", lambda p, b, out, **kw: open(out, "wb").write(b"video"))
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", True)
    monkeypatch.setattr(video_handler, "_youtube_ready", lambda: True)
    monkeypatch.setattr(video_handler.repository, "build_youtube_description", lambda *a: {"description": "", "tags": []})
    ctx, _ = _ctx(conn, bj.id); video_handler.handle(ctx)
    row = conn.execute("SELECT render_source_type,render_source_id FROM youtube_uploads").fetchone()
    assert tuple(row) == ("book", bj.id)


def test_progress_events_are_logged_and_phase_is_set(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)

    def fake_generate(patches, book, out_path, **kw):
        kw["on_progress"]("segment.ffmpeg_start", {"path": out_path})
        open(out_path, "wb").close()

    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", fake_generate)
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", False)
    ctx, job_id = _ctx(conn, bj.id)
    video_handler.handle(ctx)
    ctx.flush()

    from app.jobqueue import joblog
    assert "segment.ffmpeg_start" in joblog.tail(job_id)
    assert store.get(conn, job_id).phase == "done"


def test_a_render_failure_marks_the_book_job_failed_and_reraises(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg exit 1")

    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", boom)
    ctx, _ = _ctx(conn, bj.id)
    with pytest.raises(RuntimeError):
        video_handler.handle(ctx)
    assert repository.get_book_job(conn, 1, "video").status == "failed"


def test_auto_upload_enqueues_a_youtube_job(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)

    def fake_generate(patches, book, out_path, **kw):
        open(out_path, "wb").close()

    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", fake_generate)
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", True)
    monkeypatch.setattr(video_handler, "_youtube_ready", lambda: True)
    monkeypatch.setattr(
        video_handler.repository, "build_youtube_description",
        lambda conn, book_id: {"description": "mô tả", "tags": ["a"]})
    monkeypatch.setattr(video_handler.youtube, "enqueue_upload", lambda *a, **kw: 55)
    ctx, _ = _ctx(conn, bj.id)

    video_handler.handle(ctx)

    jobs = store.list_jobs(conn, job_type="youtube_upload")
    assert len(jobs) == 1
    assert jobs[0].payload["upload_id"] == 55


def test_auto_upload_is_skipped_when_youtube_is_not_configured(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)
    monkeypatch.setattr(
        video_handler.video_gen, "generate_full_video",
        lambda p, b, out, **kw: open(out, "wb").close())
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", True)
    monkeypatch.setattr(video_handler, "_youtube_ready", lambda: False)
    ctx, _ = _ctx(conn, bj.id)
    video_handler.handle(ctx)
    assert store.list_jobs(conn, job_type="youtube_upload") == []


def test_a_failing_auto_upload_does_not_fail_the_video_job(tmp_path, monkeypatch):
    """Video đã render xong rồi — lỗi ở bước xếp hàng upload chỉ được ghi log."""
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)
    monkeypatch.setattr(
        video_handler.video_gen, "generate_full_video",
        lambda p, b, out, **kw: open(out, "wb").close())
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", True)
    monkeypatch.setattr(video_handler, "_youtube_ready", lambda: True)
    monkeypatch.setattr(
        video_handler.repository, "build_youtube_description",
        lambda conn, book_id: (_ for _ in ()).throw(RuntimeError("hỏng")))
    ctx, _ = _ctx(conn, bj.id)

    result = video_handler.handle(ctx)

    assert result["output_path"]
    assert repository.get_book_job(conn, 1, "video").status == "done"

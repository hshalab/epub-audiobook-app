"""In-memory progress store for the Video Creator batch page."""
import time

from app.routes import video as video_routes


def setup_function():
    video_routes._progress_store.clear()


def test_record_step_appends_and_tracks_status():
    video_routes._record_step("b1:0", "segment.start", {"path": "o.mp4"})
    video_routes._record_step("b1:0", "segment.ffmpeg_start", {})
    entry = video_routes._progress_store["b1:0"]
    assert entry["status"] == "running"
    assert len(entry["steps"]) == 2
    assert entry["steps"][0]["event"] == "segment.start"
    assert "path=o.mp4" in entry["steps"][0]["detail"]


def test_record_step_failed_sets_error_status():
    video_routes._record_step("b1:1", "segment.failed", {"returncode": 1})
    assert video_routes._progress_store["b1:1"]["status"] == "error"


def test_record_step_job_done_sets_done_status():
    video_routes._record_step("b1:2", "job.done", {})
    assert video_routes._progress_store["b1:2"]["status"] == "done"


def test_progress_logger_records_when_job_key_given():
    cb = video_routes._make_progress_logger("video_creator.batch", job_key="b2:0", batch_id="b2")
    cb("segment.start", {"path": "x.mp4"})
    assert "b2:0" in video_routes._progress_store


def test_cleanup_purges_old_entries():
    video_routes._record_step("old:0", "segment.start", {})
    video_routes._progress_store["old:0"]["updated_at"] = time.time() - 7200
    video_routes._record_step("new:0", "segment.start", {})
    video_routes._cleanup_progress_store(max_age_seconds=3600)
    assert "old:0" not in video_routes._progress_store
    assert "new:0" in video_routes._progress_store

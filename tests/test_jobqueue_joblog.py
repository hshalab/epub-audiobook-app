"""File log theo job: ghi, tail, @@EVENT round-trip, mirror ERROR sang app.log, retention."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.jobqueue import joblog, store


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    """Mỗi test một data_root riêng — không đụng vào data/ thật của repo."""
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


def test_log_path_lives_under_data_root(tmp_path):
    path = joblog.job_log_path(42)
    assert path.parent == tmp_path / "logs" / "jobs"
    assert path.name == "42.log"


def test_file_is_not_created_until_something_is_written():
    logger = joblog.JobLogger(1, "video")
    assert not joblog.job_log_path(1).exists()
    logger.log("bắt đầu")
    logger.close()
    assert joblog.job_log_path(1).exists()


def test_log_line_carries_level_and_phase():
    logger = joblog.JobLogger(2, "video")
    logger.log("ffmpeg pass 1/2", phase="encoding")
    logger.close()
    line = joblog.tail(2).strip()
    assert "[INFO ]" in line
    assert "phase=encoding" in line
    assert line.endswith("ffmpeg pass 1/2")


def test_emit_round_trips_as_json():
    logger = joblog.JobLogger(3, "light_tts")
    logger.emit({"type": "chunk", "index": 7, "total": 42})
    logger.close()
    events, next_line = joblog.read_events(3)
    assert events == [{"type": "chunk", "index": 7, "total": 42}]
    assert next_line == 1


def test_read_events_resumes_from_a_line_offset():
    logger = joblog.JobLogger(4, "light_tts")
    logger.emit({"type": "chunk", "index": 0})
    logger.log("dòng chữ thường, không phải event")
    logger.emit({"type": "chunk", "index": 1})
    logger.close()
    first, cursor = joblog.read_events(4)
    assert [e["index"] for e in first] == [0, 1]
    logger = joblog.JobLogger(4, "light_tts")
    logger.emit({"type": "done"})
    logger.close()
    second, _ = joblog.read_events(4, from_line=cursor)
    assert second == [{"type": "done"}]


def test_a_half_written_trailing_event_is_not_lost():
    """Cầu SSE tail file này trong lúc job còn ghi. Nếu lần đọc rơi đúng vào lúc dòng
    @@EVENT cuối chưa xong, dòng đó phải bị bỏ qua lượt này rồi giao ở lượt sau —
    không được đếm vào cursor rồi biến mất vĩnh viễn."""
    logger = joblog.JobLogger(11, "light_tts")
    logger.emit({"type": "chunk", "index": 0})
    logger.close()

    path = joblog.job_log_path(11)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('@@EVENT {"type":"chunk","index":1')   # cụt, chưa có newline

    events, cursor = joblog.read_events(11)
    assert [e["index"] for e in events] == [0]
    assert cursor == 1

    with path.open("a", encoding="utf-8") as fh:
        fh.write("}\n")                                   # ghi nốt phần còn lại

    events, _ = joblog.read_events(11, from_line=cursor)
    assert [e["index"] for e in events] == [1]


def test_errors_are_mirrored_to_the_app_logger(caplog):
    with caplog.at_level(logging.WARNING, logger="app.jobqueue.joblog"):
        logger = joblog.JobLogger(5, "video")
        logger.log("ffmpeg exit 1", level=logging.ERROR, phase="encoding")
        logger.close()
    assert any("job_id=5" in r.message and "job_type=video" in r.message
               for r in caplog.records)


def test_info_lines_are_not_mirrored(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.jobqueue.joblog"):
        logger = joblog.JobLogger(6, "video")
        logger.log("chuyện thường ngày")
        logger.close()
    assert caplog.records == []


def test_tail_returns_only_the_last_n_lines():
    logger = joblog.JobLogger(7, "video")
    for i in range(50):
        logger.log(f"dòng {i}")
    logger.close()
    assert joblog.tail(7, lines=3).count("\n") == 3
    assert "dòng 49" in joblog.tail(7, lines=3)


def test_tail_of_a_job_with_no_log_is_empty():
    assert joblog.tail(999) == ""


def test_tail_with_zero_lines_returns_nothing():
    """all_lines[-0:] là cả file, không phải rỗng — cái bẫy slice âm quen thuộc."""
    logger = joblog.JobLogger(12, "video")
    logger.log("một dòng")
    logger.close()
    assert joblog.tail(12, lines=0) == ""


def test_purge_deletes_logs_of_old_finished_jobs():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    old_id = store.enqueue(conn, "video")
    fresh_id = store.enqueue(conn, "video")
    running_id = store.enqueue(conn, "video")
    store.finish(conn, old_id, None)
    store.finish(conn, fresh_id, None)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn.execute("UPDATE job SET finished_at=? WHERE id=?", (long_ago, old_id))
    conn.commit()
    for job_id in (old_id, fresh_id, running_id):
        lg = joblog.JobLogger(job_id, "video")
        lg.log("x")
        lg.close()

    assert joblog.purge_old_logs(conn, retention_days=7) == 1
    assert not joblog.job_log_path(old_id).exists()
    assert joblog.job_log_path(fresh_id).exists()
    assert joblog.job_log_path(running_id).exists()

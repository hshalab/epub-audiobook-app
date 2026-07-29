"""Job dataclass: map từ sqlite3.Row, parse payload/result JSON an toàn."""
from __future__ import annotations

from datetime import datetime, timezone

from app import db
from app.jobqueue.models import (
    CANCELLED, DONE, FAILED, PENDING, TERMINAL_STATUSES, HandlerSpec, Job, JobFatalError,
)


def _row(**over):
    conn = db.connect(":memory:")
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    cols = {"job_type": "video", "payload_json": '{"book_job_id": 7}',
            "created_at": now, "updated_at": now}
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO job ({names}) VALUES ({marks})", list(cols.values()))
    conn.commit()
    return conn.execute("SELECT * FROM job WHERE id=?", (cur.lastrowid,)).fetchone()


def test_from_row_maps_every_column():
    job = Job.from_row(_row())
    assert job.job_type == "video"
    assert job.status == PENDING
    assert job.priority == 100
    assert job.max_attempts == 3
    assert job.book_id is None


def test_payload_parses_json():
    assert Job.from_row(_row()).payload == {"book_job_id": 7}


def test_payload_of_empty_string_is_a_dict_not_a_crash():
    assert Job.from_row(_row(payload_json="")).payload == {}


def test_result_is_none_until_set():
    assert Job.from_row(_row()).result is None
    assert Job.from_row(_row(result_json='{"path": "/x.mp4"}')).result == {"path": "/x.mp4"}


def test_terminal_statuses():
    assert TERMINAL_STATUSES == frozenset({DONE, FAILED, CANCELLED})


def test_handler_spec_defaults():
    spec = HandlerSpec(job_type="video", fn=lambda ctx: {}, concurrency=2)
    assert spec.max_attempts == 3
    assert spec.cancellable is True


def test_job_fatal_error_is_an_exception():
    assert issubclass(JobFatalError, Exception)

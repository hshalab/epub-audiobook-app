# Parallel Job Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gom VoxCPM TTS, LightTTS, render video và upload YouTube vào một queue duy nhất chạy song song với giới hạn riêng theo từng loại (mặc định 10), có tiến độ trong DB và log riêng cho từng job.

**Architecture:** Một bảng `job` giữ vòng đời mọi job; các bảng nghiệp vụ (`patch`, `book_job`, `youtube_uploads`) không đổi và vẫn là nguồn sự thật. `JobQueue` chạy một dispatcher loop cho mỗi `job_type`, mỗi loop có semaphore riêng, claim job bằng một câu `UPDATE ... RETURNING` nguyên tử. Handler là hàm sync chạy trong ThreadPoolExecutor riêng của queue, mỗi job một connection sqlite riêng (WAL + busy_timeout), không đi qua `db_lock` toàn cục của routes.

**Tech Stack:** Python 3.10–3.12, FastAPI, sqlite3 (WAL, sqlite 3.45), asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-07-29-parallel-job-queue-design.md`

## Global Constraints

- Chạy test bằng `pytest tests/` — `pytest` trần sẽ lội vào `build/` và `.venv/` rồi chết trước khi chạy được test nào.
- Package tên `app.jobqueue`, **không** đặt tên `app.queue` (che module chuẩn `queue` của Python).
- Không thêm hàm mới vào `app/repository.py` (đã 72KB). Mọi SQL của queue nằm trong `app/jobqueue/store.py`.
- Routes HTTP giữ nguyên `app/deps.py::locked_conn` + `app.state.db_lock`. Queue **không** dùng `db_lock`.
- `JobQueue` nhận `conn_factory: Callable[[], sqlite3.Connection]`, **không** nhận `db_path` — test dùng `:memory:`, mỗi `db.connect(":memory:")` là một DB rỗng khác nhau.
- Mọi khóa cũ của `/health` phải giữ nguyên: `status`, `worker_state`, `current_patch_id`, `current_chunk_index`, `current_chunk_count`, `queue_depth`, `last_heartbeat_at`.
- Giá trị mặc định, copy đúng từ spec:
  - `QUEUE_CONCURRENCY="voxcpm_tts=1,video=2,youtube_upload=1"`
  - `QUEUE_DEFAULT_CONCURRENCY=10`
  - `QUEUE_LOG_RETENTION_DAYS=7`
  - `QUEUE_REAP_AFTER_SECONDS=120`
  - backoff retry: `min(30 * 2**attempt, 600)` giây, `max_attempts=3`
  - throttle ghi tiến độ: 1.0 giây
- Dispatcher dùng lại `settings.worker_poll_interval` (2.0), không thêm setting poll mới — `/health` tính ngưỡng heartbeat bằng `3 × worker_poll_interval`.
- Tiếng Việt trong log/UI giữ nguyên phong cách hiện có của repo.

## File Structure

| File | Trách nhiệm |
|---|---|
| `app/db.py` (sửa) | thêm DDL bảng `job` vào `_SCHEMA` |
| `app/config.py` (sửa) | 4 setting mới của queue |
| `app/jobqueue/__init__.py` | export `JobQueue`, `JobFatalError` |
| `app/jobqueue/models.py` | `Job`, `HandlerSpec`, `JobFatalError`, hằng trạng thái |
| `app/jobqueue/store.py` | toàn bộ SQL: enqueue/claim/progress/finish/fail/cancel/retry/reap/list |
| `app/jobqueue/joblog.py` | file log theo job, `@@EVENT`, tail, retention |
| `app/jobqueue/context.py` | `JobContext` — throttle tiến độ, heartbeat, should_cancel |
| `app/jobqueue/runner.py` | `JobQueue` — registry, dispatcher/loại, executor, reaper, shutdown, compat props |
| `app/jobqueue/handlers/voxcpm_tts.py` | port từ `PatchWorker._synthesize` |
| `app/jobqueue/handlers/video.py` | port từ `PatchWorker._run_video_job` |
| `app/jobqueue/handlers/youtube_upload.py` | port từ `UploadWorker._process_upload` |
| `app/jobqueue/handlers/light_tts.py` | port từ `preview_stream._generate()` |
| `app/jobqueue/backfill.py` | enqueue job cho công việc tồn đọng lúc boot |
| `app/main.py` (sửa) | dựng `JobQueue` trong lifespan thay cho hai worker cũ |
| `app/routes/queue.py` (sửa) | API job + `/health` mở rộng |
| `app/templates/queue.html` | trang theo dõi |
| `app/routes/text_studio.py` (sửa) | `preview-stream` thành cầu SSE đọc log job |
| `app/worker.py` (sửa) | xóa vòng lặp, giữ phần synthesize/video làm hàm thuần |
| `app/upload_worker.py` (sửa) | xóa vòng lặp, giữ `_process_upload` làm hàm thuần |
| `pyproject.toml` (sửa) | `packages` phải liệt kê sub-package mới |

---

### Task 1: Bảng `job` trong schema

**Files:**
- Modify: `app/db.py` (thêm vào chuỗi `_SCHEMA`)
- Modify: `pyproject.toml` (mục `[tool.setuptools]`)
- Test: `tests/test_job_schema.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_schema` (đã có)
- Produces: bảng `job` với các cột đúng như DDL dưới; index `idx_job_claim`, `idx_job_book`, unique một phần `idx_job_dedupe`

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_job_schema.py`:

```python
"""Schema của bảng job: cột, index, và partial unique index trên dedupe_key."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app import db


def _conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


def _insert(conn, **over):
    now = datetime.now(timezone.utc).isoformat()
    cols = {
        "job_type": "video", "status": "pending", "payload_json": "{}",
        "dedupe_key": None, "created_at": now, "updated_at": now,
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO job ({names}) VALUES ({marks})", list(cols.values()))
    conn.commit()
    return cur.lastrowid


def test_job_table_has_expected_columns():
    conn = _conn()
    names = {r["name"] for r in conn.execute("PRAGMA table_info(job)")}
    assert names == {
        "id", "job_type", "status", "priority", "book_id", "payload_json", "dedupe_key",
        "phase", "progress_current", "progress_total", "result_json", "error_message",
        "attempt_count", "max_attempts", "next_retry_at", "worker_id", "heartbeat_at",
        "created_at", "started_at", "finished_at", "updated_at",
    }


def test_defaults_are_applied():
    conn = _conn()
    job_id = _insert(conn)
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["priority"] == 100
    assert row["progress_current"] == 0
    assert row["progress_total"] == 0
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 3


def test_dedupe_key_blocks_a_second_live_job():
    conn = _conn()
    _insert(conn, dedupe_key="video:book_job=1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, dedupe_key="video:book_job=1")


def test_dedupe_key_is_free_again_once_the_first_job_is_terminal():
    """Partial index chỉ phủ pending/running — job xong rồi thì khóa được tái sử dụng."""
    conn = _conn()
    first = _insert(conn, dedupe_key="video:book_job=1")
    conn.execute("UPDATE job SET status='done' WHERE id=?", (first,))
    conn.commit()
    second = _insert(conn, dedupe_key="video:book_job=1")
    assert second != first


def test_null_dedupe_keys_do_not_collide():
    conn = _conn()
    assert _insert(conn, dedupe_key=None) != _insert(conn, dedupe_key=None)


def test_claim_index_exists():
    conn = _conn()
    names = {r["name"] for r in conn.execute("PRAGMA index_list(job)")}
    assert {"idx_job_claim", "idx_job_book", "idx_job_dedupe"} <= names
```

- [ ] **Step 2: Chạy test, xác nhận nó fail**

```bash
pytest tests/test_job_schema.py -v
```

Kỳ vọng: FAIL với `sqlite3.OperationalError: no such table: job`.

- [ ] **Step 3: Thêm DDL vào `_SCHEMA` trong `app/db.py`**

Chèn vào cuối chuỗi `_SCHEMA` (trước dấu đóng chuỗi), giữ nguyên phong cách các bảng khác:

```sql
CREATE TABLE IF NOT EXISTS job (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    priority         INTEGER NOT NULL DEFAULT 100,
    book_id          INTEGER,
    payload_json     TEXT NOT NULL DEFAULT '{}',
    dedupe_key       TEXT,
    phase            TEXT,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    result_json      TEXT,
    error_message    TEXT,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    next_retry_at    TEXT,
    worker_id        TEXT,
    heartbeat_at     TEXT,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_claim ON job(status, job_type, priority, id);
CREATE INDEX IF NOT EXISTS idx_job_book  ON job(book_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_dedupe ON job(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending','running');
```

Chú ý: bảng `job` **không** có `REFERENCES book(id) ON DELETE CASCADE` trên `book_id`. Cố ý — xóa sách không được làm bốc hơi lịch sử job, và `book_id` ở đây chỉ để lọc trên UI.

- [ ] **Step 4: Sửa `pyproject.toml`**

```toml
[tool.setuptools]
packages = ["app", "app.jobqueue", "app.jobqueue.handlers"]
```

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_job_schema.py -v
```

Kỳ vọng: 6 passed.

- [ ] **Step 6: Chạy cả suite để chắc schema mới không phá gì**

```bash
pytest tests/ -q
```

Kỳ vọng: không có failure mới so với trước Task 1. (`test_heartbeat_keeps_long_create_claim_alive` đôi khi flaky do timing — chạy lại riêng nó trước khi đổ lỗi cho thay đổi này.)

- [ ] **Step 7: Commit**

```bash
git add app/db.py pyproject.toml tests/test_job_schema.py
git commit -m "feat(queue): add job table with partial unique dedupe index"
```

---

### Task 2: `models.py` — Job, HandlerSpec, JobFatalError

**Files:**
- Create: `app/jobqueue/__init__.py`
- Create: `app/jobqueue/models.py`
- Test: `tests/test_jobqueue_models.py`

**Interfaces:**
- Consumes: bảng `job` (Task 1)
- Produces:
  - `PENDING/RUNNING/DONE/FAILED/CANCELLING/CANCELLED: str`, `TERMINAL_STATUSES: frozenset[str]`
  - `class JobFatalError(Exception)`
  - `@dataclass Job` với mọi cột của bảng; `Job.payload -> dict`; `Job.result -> dict`; `Job.from_row(row) -> Job`
  - `@dataclass HandlerSpec(job_type, fn, concurrency, max_attempts=3, cancellable=True)`

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_models.py`:

```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_models.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue'`.

- [ ] **Step 3: Tạo `app/jobqueue/__init__.py`**

```python
"""Queue job chạy nền, song song có giới hạn theo từng loại task."""
from app.jobqueue.models import HandlerSpec, Job, JobFatalError

__all__ = ["HandlerSpec", "Job", "JobFatalError"]
```

- [ ] **Step 4: Tạo `app/jobqueue/models.py`**

```python
"""Kiểu dữ liệu của queue. Không import store/runner để tránh vòng lặp import."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLING = "cancelling"
CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({DONE, FAILED, CANCELLED})


class JobFatalError(Exception):
    """Lỗi không đáng retry: payload sai, file nguồn không tồn tại, quota đã hết.
    Handler raise cái này thì job đi thẳng sang 'failed', bỏ qua backoff."""


def _loads(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@dataclass
class Job:
    id: int
    job_type: str
    status: str
    priority: int
    book_id: int | None
    payload_json: str
    dedupe_key: str | None
    phase: str | None
    progress_current: int
    progress_total: int
    result_json: str | None
    error_message: str | None
    attempt_count: int
    max_attempts: int
    next_retry_at: str | None
    worker_id: str | None
    heartbeat_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str

    @property
    def payload(self) -> dict[str, Any]:
        return _loads(self.payload_json) or {}

    @property
    def result(self) -> dict[str, Any] | None:
        return _loads(self.result_json)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        return cls(**{k: row[k] for k in row.keys()})


@dataclass
class HandlerSpec:
    job_type: str
    fn: Callable[[Any], dict[str, Any] | None]   # Callable[[JobContext], ...]
    concurrency: int
    max_attempts: int = 3
    cancellable: bool = True
```

Chú thích cho người triển khai: `fn` được gõ là `Callable[[Any], ...]` chứ không phải
`Callable[[JobContext], ...]` vì `context.py` import `models.py` — gõ ngược lại sẽ tạo
vòng import. Kiểu thật của tham số là `JobContext` (Task 5).

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_models.py -v
```

Kỳ vọng: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add app/jobqueue/__init__.py app/jobqueue/models.py tests/test_jobqueue_models.py
git commit -m "feat(queue): add Job/HandlerSpec models and JobFatalError"
```

---

### Task 3: `store.py` — toàn bộ SQL của queue

**Files:**
- Create: `app/jobqueue/store.py`
- Test: `tests/test_jobqueue_store.py`

**Interfaces:**
- Consumes: `Job`, hằng trạng thái, `TERMINAL_STATUSES` (Task 2)
- Produces (mọi task sau đều gọi qua đúng các chữ ký này):

```python
def backoff_seconds(attempt: int) -> int
def enqueue(conn, job_type: str, *, payload: dict | None = None, book_id: int | None = None,
            dedupe_key: str | None = None, priority: int = 100,
            max_attempts: int = 3) -> int | None          # None = đã có job sống cùng dedupe_key
def get(conn, job_id: int) -> Job | None
def find_live_by_dedupe(conn, dedupe_key: str) -> Job | None
def claim(conn, job_type: str, worker_id: str, *, now: str | None = None) -> Job | None

# Năm hàm dưới nhận worker_id tùy chọn. Khi được truyền, câu UPDATE mang thêm
# `AND worker_id=?` và hàm trả về False/None nếu không khớp dòng nào — xem mục
# "Fencing" bên dưới. Bỏ trống worker_id = ghi không rào, dành cho route admin.
def write_progress(conn, job_id: int, *, current: int, total: int, phase: str | None,
                   now: str | None = None, worker_id: str | None = None) -> bool
def heartbeat(conn, job_id: int, *, now: str | None = None,
              worker_id: str | None = None) -> bool
def finish(conn, job_id: int, result: dict | None = None, *,
           worker_id: str | None = None) -> bool
def fail(conn, job_id: int, error: str, *, fatal: bool = False,
         max_attempts: int | None = None,
         worker_id: str | None = None) -> str | None      # None = bị rào chặn
def request_cancel(conn, job_id: int) -> str | None      # 'cancelled' | 'cancelling' | None
def mark_cancelled(conn, job_id: int, *, worker_id: str | None = None) -> bool
def retry(conn, job_id: int) -> bool
def reap_stale(conn, *, older_than_seconds: int, now: str | None = None) -> list[int]
def list_jobs(conn, *, job_type=None, status=None, book_id=None, limit: int = 100) -> list[Job]
def counts(conn) -> dict[str, dict[str, int]]            # {job_type: {status: n}}
def pending_count(conn, job_type: str) -> int
```

#### Fencing: vì sao năm hàm kia cần `worker_id`

`claim()` là hàm duy nhất có guard chống race. Không có gì khác phân biệt được "tiến
trình đã chết" với "tiến trình còn sống nhưng im lặng", nên kịch bản này xảy ra thật:

```
worker A claim job 5 → chạy ffmpeg 40 phút, không báo tiến độ lần nào
reaper (120s) tưởng A đã chết → job 5 quay về 'pending'
worker B claim job 5 → cùng một job chạy hai lần
A xong → finish(5) ghi đè lên lượt chạy đang dở của B
```

Với `QUEUE_REAP_AFTER_SECONDS=120`, cả `video` (khoảng lặng giữa `ffmpeg_start` và
`ffmpeg_done` của một sách dài là hàng chục phút) lẫn `youtube_upload`
(`youtube.process_upload` là cả lần transfer, không có heartbeat bên trong) đều rơi
vào đây — nghĩa là upload trùng một video lên YouTube.

Hai lớp phòng thủ, cả hai đều cần:

1. **Rào ghi (task này).** Khi `worker_id` được truyền, câu UPDATE mang thêm
   `AND worker_id=?`. Worker đã bị reap ghi vào là no-op, không phá được lượt chạy
   của worker mới. `JobContext` (Task 5) truyền `job.worker_id` vào mọi lời gọi.
2. **Heartbeat trong lúc chạy dài (Task 5, 8, 9).** `ctx.keep_alive()` giữ nhịp tim
   trong suốt một bước dài, để job không bị reap ngay từ đầu.

Rào ghi là chốt chặn đúng đắn; heartbeat là thứ tránh lãng phí một lần encode/upload.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_store.py`:

```python
"""store.py: enqueue/dedupe, claim nguyên tử dưới nhiều thread, backoff, reaper."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.jobqueue import store
from app.jobqueue.models import CANCELLED, CANCELLING, DONE, FAILED, PENDING, RUNNING


def _conn(tmp_path=None):
    conn = db.connect(str(tmp_path / "app.db") if tmp_path else ":memory:")
    db.init_schema(conn)
    return conn


def _iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def test_enqueue_returns_a_job_id():
    conn = _conn()
    job_id = store.enqueue(conn, "video", payload={"book_job_id": 3}, book_id=9)
    job = store.get(conn, job_id)
    assert job.job_type == "video"
    assert job.payload == {"book_job_id": 3}
    assert job.book_id == 9
    assert job.status == PENDING


def test_enqueue_with_a_live_dedupe_key_returns_none():
    conn = _conn()
    first = store.enqueue(conn, "video", dedupe_key="video:book_job=3")
    assert store.enqueue(conn, "video", dedupe_key="video:book_job=3") is None
    assert store.find_live_by_dedupe(conn, "video:book_job=3").id == first


def test_enqueue_reuses_a_dedupe_key_after_the_job_finished():
    conn = _conn()
    first = store.enqueue(conn, "video", dedupe_key="k")
    store.finish(conn, first, {"ok": True})
    second = store.enqueue(conn, "video", dedupe_key="k")
    assert second is not None and second != first


def test_claim_moves_the_job_to_running_and_bumps_attempt_count():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#0")
    assert job.id == job_id
    assert job.status == RUNNING
    assert job.attempt_count == 1
    assert job.worker_id == "video#0"
    assert job.started_at is not None


def test_claim_only_returns_jobs_of_the_requested_type():
    conn = _conn()
    store.enqueue(conn, "video")
    assert store.claim(conn, "light_tts", "w") is None


def test_claim_respects_priority_then_id():
    conn = _conn()
    low = store.enqueue(conn, "video", priority=100)
    high = store.enqueue(conn, "video", priority=10)
    assert store.claim(conn, "video", "w").id == high
    assert store.claim(conn, "video", "w").id == low


def test_claim_skips_a_job_whose_retry_time_has_not_arrived():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    store.fail(conn, job_id, "boom")          # -> pending, next_retry_at ở tương lai
    assert store.claim(conn, "video", "w") is None


def test_claim_is_atomic_across_threads(tmp_path):
    """20 thread cùng claim 5 job — không job nào được giao hai lần."""
    conn = _conn(tmp_path)
    for _ in range(5):
        store.enqueue(conn, "video")
    conn.close()

    claimed: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def worker(n: int):
        c = db.connect(str(tmp_path / "app.db"))
        start.wait()
        job = store.claim(c, "video", f"video#{n}")
        if job is not None:
            with lock:
                claimed.append(job.id)
        c.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 5
    assert len(set(claimed)) == 5


def test_backoff_grows_and_is_capped():
    assert store.backoff_seconds(1) == 60
    assert store.backoff_seconds(2) == 120
    assert store.backoff_seconds(3) == 240
    assert store.backoff_seconds(99) == 600


def test_fail_reschedules_while_attempts_remain():
    conn = _conn()
    job_id = store.enqueue(conn, "video", max_attempts=3)
    store.claim(conn, "video", "w")
    assert store.fail(conn, job_id, "boom") == PENDING
    job = store.get(conn, job_id)
    assert job.error_message == "boom"
    assert job.next_retry_at > _iso()


def test_fail_gives_up_once_attempts_are_exhausted():
    conn = _conn()
    job_id = store.enqueue(conn, "video", max_attempts=2)
    store.claim(conn, "video", "w")
    store.fail(conn, job_id, "one")
    conn.execute("UPDATE job SET next_retry_at=NULL WHERE id=?", (job_id,))
    conn.commit()
    store.claim(conn, "video", "w")
    assert store.fail(conn, job_id, "two") == FAILED
    assert store.get(conn, job_id).finished_at is not None


def test_an_explicit_max_attempts_overrides_the_stored_one():
    """Runner áp số của HandlerSpec: job enqueue với max_attempts=5 nhưng handler đăng ký
    max_attempts=1 thì hỏng một lần là bỏ."""
    conn = _conn()
    job_id = store.enqueue(conn, "video", max_attempts=5)
    store.claim(conn, "video", "w")
    assert store.fail(conn, job_id, "boom", max_attempts=1) == FAILED


def test_fatal_failure_skips_retry_entirely():
    conn = _conn()
    job_id = store.enqueue(conn, "video", max_attempts=5)
    store.claim(conn, "video", "w")
    assert store.fail(conn, job_id, "file missing", fatal=True) == FAILED


def test_write_progress_updates_counters_and_heartbeat():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    store.write_progress(conn, job_id, current=3, total=10, phase="encoding")
    job = store.get(conn, job_id)
    assert (job.progress_current, job.progress_total, job.phase) == (3, 10, "encoding")
    assert job.heartbeat_at is not None


def test_finish_stores_the_result():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    store.finish(conn, job_id, {"output_path": "/x.mp4"})
    job = store.get(conn, job_id)
    assert job.status == DONE
    assert job.result == {"output_path": "/x.mp4"}
    assert job.finished_at is not None


def test_cancel_a_pending_job_is_immediate():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    assert store.request_cancel(conn, job_id) == CANCELLED
    assert store.get(conn, job_id).status == CANCELLED


def test_cancel_a_running_job_asks_politely():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    assert store.request_cancel(conn, job_id) == CANCELLING
    store.mark_cancelled(conn, job_id)
    assert store.get(conn, job_id).status == CANCELLED


def test_cancel_a_finished_job_does_nothing():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.finish(conn, job_id, None)
    assert store.request_cancel(conn, job_id) is None


def test_retry_resets_a_failed_job():
    conn = _conn()
    job_id = store.enqueue(conn, "video", max_attempts=1)
    store.claim(conn, "video", "w")
    store.fail(conn, job_id, "boom")
    assert store.retry(conn, job_id) is True
    job = store.get(conn, job_id)
    assert job.status == PENDING
    assert job.attempt_count == 0
    assert job.error_message is None
    assert job.next_retry_at is None


def test_retry_refuses_a_running_job():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    assert store.retry(conn, job_id) is False


def test_a_reaped_worker_cannot_finish_a_job_someone_else_now_owns():
    """Kịch bản zombie: A bị reap giữa chừng, B claim lại, rồi A mới xong. Lần ghi
    muộn của A phải là no-op, không được đè lên lượt chạy của B."""
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    a = store.claim(conn, "video", "video#A")
    conn.execute("UPDATE job SET heartbeat_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    store.reap_stale(conn, older_than_seconds=120)
    b = store.claim(conn, "video", "video#B")
    assert b.worker_id == "video#B"

    assert store.finish(conn, job_id, {"from": "A"}, worker_id=a.worker_id) is False
    job = store.get(conn, job_id)
    assert job.status == RUNNING
    assert job.worker_id == "video#B"
    assert job.result is None

    assert store.finish(conn, job_id, {"from": "B"}, worker_id=b.worker_id) is True
    assert store.get(conn, job_id).result == {"from": "B"}


def test_a_reaped_worker_cannot_fail_a_job_someone_else_now_owns():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    a = store.claim(conn, "video", "video#A")
    conn.execute("UPDATE job SET heartbeat_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    store.reap_stale(conn, older_than_seconds=120)
    store.claim(conn, "video", "video#B")

    assert store.fail(conn, job_id, "A nói hỏng", worker_id=a.worker_id) is None
    job = store.get(conn, job_id)
    assert job.status == RUNNING
    assert job.error_message is None


def test_a_reaped_worker_cannot_move_progress_or_heartbeat():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    a = store.claim(conn, "video", "video#A")
    conn.execute("UPDATE job SET heartbeat_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    store.reap_stale(conn, older_than_seconds=120)
    store.claim(conn, "video", "video#B")

    assert store.write_progress(
        conn, job_id, current=99, total=99, phase="ma", worker_id=a.worker_id) is False
    assert store.heartbeat(conn, job_id, worker_id=a.worker_id) is False
    job = store.get(conn, job_id)
    assert job.progress_current == 0
    assert job.phase is None


def test_a_reaped_worker_cannot_mark_cancelled():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    a = store.claim(conn, "video", "video#A")
    conn.execute("UPDATE job SET heartbeat_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    store.reap_stale(conn, older_than_seconds=120)
    store.claim(conn, "video", "video#B")
    assert store.mark_cancelled(conn, job_id, worker_id=a.worker_id) is False
    assert store.get(conn, job_id).status == RUNNING


def test_the_owning_worker_writes_normally():
    """Rào chỉ chặn kẻ lạ — chủ sở hữu thật vẫn ghi được như thường."""
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#A")
    assert store.write_progress(
        conn, job_id, current=2, total=5, phase="encoding", worker_id=job.worker_id) is True
    assert store.heartbeat(conn, job_id, worker_id=job.worker_id) is True
    assert store.finish(conn, job_id, {"ok": True}, worker_id=job.worker_id) is True
    assert store.get(conn, job_id).status == DONE


def test_writes_without_a_worker_id_are_unfenced():
    """Route admin gọi không kèm worker_id và vẫn phải ghi được."""
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "video#A")
    assert store.finish(conn, job_id, {"by": "admin"}) is True
    assert store.get(conn, job_id).status == DONE


def test_fail_on_a_missing_job_returns_none():
    conn = _conn()
    assert store.fail(conn, 4242, "không tồn tại") is None


def test_reap_returns_a_stale_running_job_to_pending():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    conn.execute("UPDATE job SET heartbeat_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    assert store.reap_stale(conn, older_than_seconds=120) == [job_id]
    job = store.get(conn, job_id)
    assert job.status == PENDING
    assert job.worker_id is None


def test_reap_leaves_a_freshly_heartbeating_job_alone():
    conn = _conn()
    store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    assert store.reap_stale(conn, older_than_seconds=120) == []


def test_reap_uses_started_at_when_heartbeat_is_still_null():
    conn = _conn()
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    conn.execute("UPDATE job SET heartbeat_at=NULL, started_at=? WHERE id=?", (_iso(-3600), job_id))
    conn.commit()
    assert store.reap_stale(conn, older_than_seconds=120) == [job_id]


def test_list_jobs_filters_and_orders_newest_first():
    conn = _conn()
    a = store.enqueue(conn, "video", book_id=1)
    b = store.enqueue(conn, "light_tts", book_id=1)
    store.enqueue(conn, "video", book_id=2)
    assert [j.id for j in store.list_jobs(conn, book_id=1)] == [b, a]
    assert [j.id for j in store.list_jobs(conn, job_type="light_tts")] == [b]
    assert [j.id for j in store.list_jobs(conn, status=PENDING, limit=1)] == [
        store.list_jobs(conn)[0].id
    ]


def test_counts_group_by_type_and_status():
    conn = _conn()
    store.enqueue(conn, "video")
    store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    store.enqueue(conn, "light_tts")
    counts = store.counts(conn)
    assert counts["video"] == {"pending": 1, "running": 1}
    assert counts["light_tts"] == {"pending": 1}


def test_pending_count_is_per_type():
    conn = _conn()
    store.enqueue(conn, "video")
    store.enqueue(conn, "light_tts")
    assert store.pending_count(conn, "video") == 1
    assert store.pending_count(conn, "youtube_upload") == 0
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_store.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.store'`.

- [ ] **Step 3: Viết `app/jobqueue/store.py`**

```python
"""Mọi câu SQL của queue. Không phụ thuộc asyncio, không phụ thuộc FastAPI —
gọi được từ bất kỳ thread nào, với bất kỳ connection nào."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.jobqueue.models import (
    CANCELLED, CANCELLING, DONE, FAILED, PENDING, RUNNING, TERMINAL_STATUSES, Job,
)

_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 600

_COLUMNS = (
    "id, job_type, status, priority, book_id, payload_json, dedupe_key, phase, "
    "progress_current, progress_total, result_json, error_message, attempt_count, "
    "max_attempts, next_retry_at, worker_id, heartbeat_at, created_at, started_at, "
    "finished_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def backoff_seconds(attempt: int) -> int:
    """30·2^attempt, chặn trên ở 600s. attempt là attempt_count sau khi đã tăng,
    nên lần hỏng đầu tiên (attempt=1) đợi 60 giây."""
    return min(_BASE_BACKOFF_SECONDS * (2 ** max(1, attempt)), _MAX_BACKOFF_SECONDS)


# ---------------------------------------------------------------- enqueue / đọc

def enqueue(
    conn: sqlite3.Connection,
    job_type: str,
    *,
    payload: dict | None = None,
    book_id: int | None = None,
    dedupe_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> int | None:
    """Trả về id job mới, hoặc None nếu đã có job cùng dedupe_key đang pending/running.

    Không tự kiểm tra trước rồi mới insert — chạy thẳng INSERT và bắt IntegrityError,
    vì partial unique index mới là thứ duy nhất đúng khi có nhiều tiến trình cùng gọi."""
    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO job (job_type, status, priority, book_id, payload_json,
                                dedupe_key, max_attempts, created_at, updated_at)
               VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
            (job_type, priority, book_id, json.dumps(payload or {}), dedupe_key,
             max_attempts, now, now),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    conn.commit()
    return cur.lastrowid


def get(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM job WHERE id=?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def find_live_by_dedupe(conn: sqlite3.Connection, dedupe_key: str) -> Job | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM job WHERE dedupe_key=? AND status IN ('pending','running')",
        (dedupe_key,),
    ).fetchone()
    return Job.from_row(row) if row else None


# ---------------------------------------------------------------------- claim

def claim(
    conn: sqlite3.Connection, job_type: str, worker_id: str, *, now: str | None = None
) -> Job | None:
    """Một câu UPDATE nguyên tử. Điều kiện `AND status='pending'` ở ngoài là chốt chặn
    thứ hai: nếu một claim khác đã đổi trạng thái giữa lúc subquery chọn id và lúc
    UPDATE ghi, câu này khớp 0 dòng và trả None thay vì cướp job."""
    stamp = now or _now()
    row = conn.execute(
        f"""UPDATE job
               SET status='running', worker_id=?, started_at=COALESCE(started_at, ?),
                   heartbeat_at=?, attempt_count=attempt_count+1, error_message=NULL,
                   next_retry_at=NULL, updated_at=?
             WHERE id=(SELECT id FROM job
                        WHERE status='pending' AND job_type=?
                          AND (next_retry_at IS NULL OR next_retry_at<=?)
                        ORDER BY priority, id LIMIT 1)
               AND status='pending'
         RETURNING {_COLUMNS}""",
        (worker_id, stamp, stamp, stamp, job_type, stamp),
    ).fetchone()
    conn.commit()
    return Job.from_row(row) if row else None


# ------------------------------------------------------------ tiến độ / nhịp tim

def _fence(worker_id: str | None) -> tuple[str, list]:
    """Mảnh WHERE tùy chọn rào theo chủ sở hữu. Truyền worker_id thì câu UPDATE chỉ
    khớp khi job vẫn thuộc về worker đó — worker đã bị reap ghi vào là no-op."""
    return (" AND worker_id=?", [worker_id]) if worker_id is not None else ("", [])


def write_progress(
    conn: sqlite3.Connection, job_id: int, *, current: int, total: int,
    phase: str | None, now: str | None = None, worker_id: str | None = None,
) -> bool:
    stamp = now or _now()
    guard, extra = _fence(worker_id)
    cur = conn.execute(
        f"""UPDATE job SET progress_current=?, progress_total=?, phase=?,
                           heartbeat_at=?, updated_at=? WHERE id=?{guard}""",
        [current, total, phase, stamp, stamp, job_id] + extra,
    )
    conn.commit()
    return cur.rowcount > 0


def heartbeat(
    conn: sqlite3.Connection, job_id: int, *, now: str | None = None,
    worker_id: str | None = None,
) -> bool:
    stamp = now or _now()
    guard, extra = _fence(worker_id)
    cur = conn.execute(
        f"UPDATE job SET heartbeat_at=?, updated_at=? WHERE id=?{guard}",
        [stamp, stamp, job_id] + extra,
    )
    conn.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------- kết thúc

def finish(
    conn: sqlite3.Connection, job_id: int, result: dict | None = None, *,
    worker_id: str | None = None,
) -> bool:
    now = _now()
    guard, extra = _fence(worker_id)
    cur = conn.execute(
        f"""UPDATE job SET status='done', result_json=?, error_message=NULL,
                           finished_at=?, heartbeat_at=?, updated_at=? WHERE id=?{guard}""",
        [json.dumps(result) if result is not None else None, now, now, now, job_id] + extra,
    )
    conn.commit()
    return cur.rowcount > 0


def fail(
    conn: sqlite3.Connection, job_id: int, error: str, *, fatal: bool = False,
    max_attempts: int | None = None, worker_id: str | None = None,
) -> str | None:
    """Trả về trạng thái mới: 'pending' nếu còn lượt retry, 'failed' nếu hết
    (hoặc fatal=True). Trả về None khi bị rào chặn — job đã không còn thuộc về
    worker_id truyền vào, nên lần ghi này bị bỏ qua. Cắt error về 4000 ký tự:
    traceback của ffmpeg có thể rất dài và cột này được đọc trên mọi trang danh sách.

    `max_attempts` cho phép runner áp số của HandlerSpec, đè lên giá trị đã lưu trên
    dòng job. Cần thiết vì job có thể được enqueue trước khi handler đăng ký (backfill,
    hoặc một bản cũ của app), và số đúng phải là số của handler đang chạy."""
    now = _now()
    job = get(conn, job_id)
    if job is None:
        return None
    if worker_id is not None and job.worker_id != worker_id:
        return None
    message = (error or "")[:4000]
    limit = job.max_attempts if max_attempts is None else max_attempts
    guard, extra = _fence(worker_id)
    if not fatal and job.attempt_count < limit:
        retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(job.attempt_count))
        ).isoformat()
        cur = conn.execute(
            f"""UPDATE job SET status='pending', error_message=?, next_retry_at=?,
                               worker_id=NULL, updated_at=? WHERE id=?{guard}""",
            [message, retry_at, now, job_id] + extra,
        )
        conn.commit()
        return PENDING if cur.rowcount > 0 else None
    cur = conn.execute(
        f"""UPDATE job SET status='failed', error_message=?, finished_at=?,
                           worker_id=NULL, updated_at=? WHERE id=?{guard}""",
        [message, now, now, job_id] + extra,
    )
    conn.commit()
    return FAILED if cur.rowcount > 0 else None


# ---------------------------------------------------------------- hủy / retry

def request_cancel(conn: sqlite3.Connection, job_id: int) -> str | None:
    """pending → cancelled ngay. running → cancelling (handler tự dừng).
    Job đã kết thúc → None, không đụng vào."""
    job = get(conn, job_id)
    if job is None or job.status in TERMINAL_STATUSES:
        return None
    now = _now()
    if job.status == PENDING:
        conn.execute(
            "UPDATE job SET status='cancelled', finished_at=?, updated_at=? WHERE id=?",
            (now, now, job_id),
        )
        conn.commit()
        return CANCELLED
    conn.execute("UPDATE job SET status='cancelling', updated_at=? WHERE id=?", (now, job_id))
    conn.commit()
    return CANCELLING


def mark_cancelled(
    conn: sqlite3.Connection, job_id: int, *, worker_id: str | None = None
) -> bool:
    now = _now()
    guard, extra = _fence(worker_id)
    cur = conn.execute(
        f"""UPDATE job SET status='cancelled', finished_at=?, worker_id=NULL,
                           updated_at=? WHERE id=?{guard}""",
        [now, now, job_id] + extra,
    )
    conn.commit()
    return cur.rowcount > 0


def retry(conn: sqlite3.Connection, job_id: int) -> bool:
    """Đưa một job đã kết thúc về pending với bộ đếm sạch. Từ chối job đang chạy."""
    job = get(conn, job_id)
    if job is None or job.status not in TERMINAL_STATUSES:
        return False
    now = _now()
    conn.execute(
        """UPDATE job SET status='pending', attempt_count=0, error_message=NULL,
                          next_retry_at=NULL, worker_id=NULL, started_at=NULL,
                          finished_at=NULL, updated_at=? WHERE id=?""",
        (now, job_id),
    )
    conn.commit()
    return True


def reap_stale(
    conn: sqlite3.Connection, *, older_than_seconds: int, now: str | None = None
) -> list[int]:
    """Job 'running' mà nhịp tim đã ngừng (tiến trình chết, hoặc shutdown hết giờ chờ)
    được trả về 'pending'. attempt_count giữ nguyên nên nó vẫn tiêu một lượt retry —
    một job làm treo worker không được phép quay vòng vô hạn.

    COALESCE(heartbeat_at, started_at): job vừa claim xong đã chết trước lần
    write_progress đầu tiên thì heartbeat_at vẫn là giá trị đặt lúc claim, nhưng phòng
    khi có bản ghi cũ để NULL."""
    stamp = _parse(now) or datetime.now(timezone.utc)
    cutoff = (stamp - timedelta(seconds=older_than_seconds)).isoformat()
    rows = conn.execute(
        """UPDATE job SET status='pending', worker_id=NULL, updated_at=?
            WHERE status='running'
              AND COALESCE(heartbeat_at, started_at, created_at) < ?
        RETURNING id""",
        (stamp.isoformat(), cutoff),
    ).fetchall()
    conn.commit()
    return [r["id"] for r in rows]


# -------------------------------------------------------------------- đọc list

def list_jobs(
    conn: sqlite3.Connection, *, job_type: str | None = None, status: str | None = None,
    book_id: int | None = None, limit: int = 100,
) -> list[Job]:
    where, params = [], []
    if job_type:
        where.append("job_type=?")
        params.append(job_type)
    if status:
        where.append("status=?")
        params.append(status)
    if book_id is not None:
        where.append("book_id=?")
        params.append(book_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(limit, 1000)))
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM job {clause} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    return [Job.from_row(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT job_type, status, COUNT(*) AS c FROM job GROUP BY job_type, status"
    ):
        out.setdefault(row["job_type"], {})[row["status"]] = row["c"]
    return out


def pending_count(conn: sqlite3.Connection, job_type: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM job WHERE job_type=? AND status='pending'", (job_type,)
    ).fetchone()
    return row["c"]
```

- [ ] **Step 4: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_store.py -v
```

Kỳ vọng: 33 passed. Hai bài quan trọng nhất của task này là
`test_claim_is_atomic_across_threads` và nhóm `test_a_reaped_worker_cannot_*` — nếu chúng
fail thì sửa code, đừng nới lỏng assert để cho qua.

- [ ] **Step 5: Commit**

```bash
git add app/jobqueue/store.py tests/test_jobqueue_store.py
git commit -m "feat(queue): add job store with atomic claim, backoff retry and reaper"
```

---

### Task 4: `joblog.py` — log riêng cho từng job

**Files:**
- Create: `app/jobqueue/joblog.py`
- Modify: `app/config.py` (thêm setting queue)
- Test: `tests/test_jobqueue_joblog.py`

**Interfaces:**
- Consumes: `settings.data_root`, `settings.queue_log_retention_days`
- Produces:

```python
EVENT_PREFIX = "@@EVENT "
def job_log_path(job_id: int) -> Path                      # <data_root>/logs/jobs/<id>.log
class JobLogger:
    def __init__(self, job_id: int, job_type: str)
    def log(self, message: str, level: int = logging.INFO, phase: str | None = None) -> None
    def emit(self, event: dict) -> None
    def close(self) -> None
def tail(job_id: int, lines: int = 500) -> str
def read_events(job_id: int, *, from_line: int = 0) -> tuple[list[dict], int]
def purge_old_logs(conn, *, retention_days: int) -> int
```

- [ ] **Step 1: Thêm setting vào `app/config.py`**

Chèn sau khối `light_tts_chunk_retries`:

```python
    # Queue job chạy nền
    # Loại nào không liệt kê ở đây nhận queue_default_concurrency.
    queue_concurrency: str = "voxcpm_tts=1,video=2,youtube_upload=1"
    queue_default_concurrency: int = 10
    queue_log_retention_days: int = 7
    # Job 'running' im lặng quá lâu bị coi là chết và trả về 'pending'.
    queue_reap_after_seconds: int = 120
```

- [ ] **Step 2: Viết test thất bại**

Tạo `tests/test_jobqueue_joblog.py`:

```python
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
```

- [ ] **Step 3: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_joblog.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.joblog'`.

- [ ] **Step 4: Viết `app/jobqueue/joblog.py`**

```python
"""Một file log cho mỗi job, dưới <data_root>/logs/jobs/<job_id>.log.

Hai loại dòng sống chung trong cùng file:

    2026-07-29T10:22:31Z [INFO ] phase=encoding | ffmpeg pass 1/2, 34%
    @@EVENT {"type":"chunk","index":7,"total":42}

Dòng thường để người đọc; dòng @@EVENT là dữ liệu có cấu trúc cho cầu SSE
(app/routes/text_studio.py). Một file, một cơ chế — không cần stream thứ hai.

Dòng WARNING trở lên được nhân bản sang logger chính nên app.log vẫn là cái nhìn
toàn cục; dòng INFO/DEBUG chỉ nằm ở file riêng để app.log không bị ngập."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from app.config import settings

logger = logging.getLogger(__name__)

EVENT_PREFIX = "@@EVENT "

_LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO ",
    logging.WARNING: "WARN ",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "FATAL",
}


def jobs_log_dir() -> Path:
    return Path(settings.data_root) / "logs" / "jobs"


def job_log_path(job_id: int) -> Path:
    return jobs_log_dir() / f"{job_id}.log"


class JobLogger:
    """Mở file lười ở lần ghi đầu tiên, nối tiếp nếu file đã có (job retry thì log
    của lần chạy trước vẫn còn — cố ý, để so được hai lần chạy)."""

    def __init__(self, job_id: int, job_type: str):
        self.job_id = job_id
        self.job_type = job_type
        self._fh: TextIO | None = None

    def _handle(self) -> TextIO:
        if self._fh is None:
            path = job_log_path(self.job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")
        return self._fh

    def _write(self, line: str) -> None:
        fh = self._handle()
        fh.write(line + "\n")
        fh.flush()   # cầu SSE tail file này realtime; buffer sẽ làm tiến độ đứng hình

    def log(self, message: str, level: int = logging.INFO, phase: str | None = None) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        label = _LEVEL_NAMES.get(level, "INFO ")
        prefix = f"phase={phase} | " if phase else ""
        self._write(f"{stamp} [{label}] {prefix}{message}")
        if level >= logging.WARNING:
            logger.log(
                level,
                "event=job.log job_id=%s job_type=%s phase=%s message=%s",
                self.job_id, self.job_type, phase or "", message,
            )

    def emit(self, event: dict[str, Any]) -> None:
        """Ghi một event có cấu trúc. Dùng separators gọn và ensure_ascii=False để
        tiếng Việt trong message lỗi đọc được bằng mắt ngay trong file log."""
        self._write(EVENT_PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")))

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def tail(job_id: int, lines: int = 500) -> str:
    path = job_log_path(job_id)
    if not path.is_file():
        return ""
    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(all_lines[-lines:]) + "\n" if all_lines else ""


def read_events(job_id: int, *, from_line: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Đọc các dòng @@EVENT kể từ dòng thứ `from_line` (đếm theo dòng @@EVENT, không
    phải dòng file). Trả về (events, cursor mới) để cầu SSE poll tiếp mà không đọc lại.

    Đếm theo dòng event chứ không theo byte offset: file có cả dòng chữ thường xen
    kẽ, và một cursor byte sẽ vỡ nếu dòng cuối được ghi dở lúc đang đọc."""
    path = job_log_path(job_id)
    if not path.is_file():
        return [], from_line
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], from_line
    events: list[dict[str, Any]] = []
    seen = 0
    for line in raw:
        if not line.startswith(EVENT_PREFIX):
            continue
        seen += 1
        if seen <= from_line:
            continue
        try:
            parsed = json.loads(line[len(EVENT_PREFIX):])
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events, seen


def purge_old_logs(conn: sqlite3.Connection, *, retention_days: int | None = None) -> int:
    """Xóa file log của job đã kết thúc quá hạn. Trả về số file đã xóa.
    Job chưa kết thúc (finished_at IS NULL) không bao giờ bị đụng vào."""
    days = settings.queue_log_retention_days if retention_days is None else retention_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT id FROM job WHERE finished_at IS NOT NULL AND finished_at < ?", (cutoff,)
    ).fetchall()
    removed = 0
    for row in rows:
        path = job_log_path(row["id"])
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("không xóa được log job %s: %s", row["id"], exc)
    return removed
```

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_joblog.py -v
```

Kỳ vọng: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add app/jobqueue/joblog.py app/config.py tests/test_jobqueue_joblog.py
git commit -m "feat(queue): add per-job log files with structured @@EVENT lines"
```

---

### Task 5: `context.py` — JobContext với throttle tiến độ

**Files:**
- Create: `app/jobqueue/context.py`
- Test: `tests/test_jobqueue_context.py`

**Interfaces:**
- Consumes: `Job` (Task 2), `store.write_progress`/`store.heartbeat` (Task 3), `JobLogger` (Task 4)
- Produces:

```python
class JobContext:
    def __init__(self, job: Job, conn, logger: JobLogger,
                 cancel_check: Callable[[], bool], *,
                 flush_interval: float = 1.0, clock: Callable[[], float] = time.monotonic,
                 on_write: Callable[[int, int, str | None], None] | None = None,
                 conn_factory: Callable[[], sqlite3.Connection] | None = None)
    job: Job
    conn: sqlite3.Connection
    def progress(self, current: int, total: int | None = None, phase: str | None = None) -> None
    def heartbeat(self) -> None
    def flush(self, *, force: bool = True) -> None
    def log(self, message: str, level: int = logging.INFO) -> None
    def emit(self, event: dict) -> None
    def should_cancel(self) -> bool
    def lost_ownership(self) -> bool
    @contextmanager
    def keep_alive(self, interval: float = 30.0)   # giữ nhịp tim quanh một bước dài
```

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_context.py`:

```python
"""JobContext: ghi DB có throttle, nhưng đổi phase và lúc kết thúc thì luôn ghi."""
from __future__ import annotations

import logging

import pytest

from app import db
from app.jobqueue import store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


def _ctx(clock, *, cancel=lambda: False):
    conn = db.connect(":memory:")
    db.init_schema(conn)
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#0")
    ctx = JobContext(job, conn, JobLogger(job_id, "video"), cancel, clock=clock)
    return ctx, conn, job_id


def test_first_progress_call_writes_immediately():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(1, 10, phase="encoding")
    row = store.get(conn, job_id)
    assert (row.progress_current, row.progress_total, row.phase) == (1, 10, "encoding")


def test_rapid_progress_calls_are_throttled_to_one_write_per_second():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(1, 100, phase="encoding")     # ghi lần 1
    for i in range(2, 100):
        clock.advance(0.001)
        ctx.progress(i, 100, phase="encoding") # bị chặn hết
    assert store.get(conn, job_id).progress_current == 1
    clock.advance(1.0)
    ctx.progress(100, 100, phase="encoding")   # ghi lần 2
    assert store.get(conn, job_id).progress_current == 100


def test_a_phase_change_always_writes_even_inside_the_throttle_window():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(1, 10, phase="synthesizing")
    clock.advance(0.01)
    ctx.progress(2, 10, phase="encoding")
    row = store.get(conn, job_id)
    assert row.phase == "encoding"
    assert row.progress_current == 2


def test_flush_forces_a_write_of_the_pending_value():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(1, 10, phase="encoding")
    clock.advance(0.01)
    ctx.progress(9, 10, phase="encoding")      # bị chặn
    assert store.get(conn, job_id).progress_current == 1
    ctx.flush()
    assert store.get(conn, job_id).progress_current == 9


def test_flush_is_a_no_op_when_nothing_changed():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(5, 10, phase="encoding")
    before = store.get(conn, job_id).updated_at
    ctx.flush()
    assert store.get(conn, job_id).updated_at == before


def test_total_is_remembered_when_omitted():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(1, 42, phase="synthesizing")
    clock.advance(1.0)
    ctx.progress(2)
    assert store.get(conn, job_id).progress_total == 42


def test_heartbeat_touches_the_row_without_moving_progress():
    clock = _FakeClock()
    ctx, conn, job_id = _ctx(clock)
    ctx.progress(3, 10, phase="uploading")
    before = store.get(conn, job_id)
    clock.advance(5.0)
    ctx.heartbeat()
    after = store.get(conn, job_id)
    assert after.progress_current == 3
    assert after.heartbeat_at >= before.heartbeat_at


def test_on_write_hook_fires_on_every_db_write_and_only_then():
    """Runner dùng hook này thay cho việc bọc lại _write. Nó phải theo đúng nhịp
    throttle: gọi progress() liên tục không được làm hook nổ liên tục."""
    clock = _FakeClock()
    conn = db.connect(":memory:")
    db.init_schema(conn)
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#0")
    seen = []
    ctx = JobContext(
        job, conn, JobLogger(job_id, "video"), lambda: False, clock=clock,
        on_write=lambda current, total, phase: seen.append((current, total, phase)),
    )

    ctx.progress(1, 10, phase="encoding")      # ghi
    clock.advance(0.01)
    ctx.progress(2, 10, phase="encoding")      # bị chặn
    clock.advance(1.0)
    ctx.progress(3, 10, phase="encoding")      # ghi

    assert seen == [(1, 10, "encoding"), (3, 10, "encoding")]


def test_should_cancel_reflects_the_supplied_check():
    clock = _FakeClock()
    flag = {"stop": False}
    ctx, _, _ = _ctx(clock, cancel=lambda: flag["stop"])
    assert ctx.should_cancel() is False
    flag["stop"] = True
    assert ctx.should_cancel() is True


def test_losing_ownership_flips_should_cancel(tmp_path):
    """Job bị reap rồi worker khác claim: lần ghi tiến độ tiếp theo bị rào chặn, và
    handler được báo dừng qua should_cancel()."""
    clock = _FakeClock()
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#A")
    ctx = JobContext(job, conn, JobLogger(job_id, "video"), lambda: False, clock=clock)

    ctx.progress(1, 10, phase="encoding")
    assert ctx.should_cancel() is False
    assert ctx.lost_ownership() is False

    conn.execute("UPDATE job SET worker_id='video#B' WHERE id=?", (job_id,))
    conn.commit()
    clock.advance(1.0)
    ctx.progress(2, 10, phase="encoding")

    assert ctx.lost_ownership() is True
    assert ctx.should_cancel() is True
    assert store.get(conn, job_id).progress_current == 1   # ghi của A không lọt qua


def test_keep_alive_beats_while_a_long_step_runs(tmp_path):
    import time as real_time
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    job_id = store.enqueue(conn, "video")
    job = store.claim(conn, "video", "video#A")
    conn.execute("UPDATE job SET heartbeat_at=NULL WHERE id=?", (job_id,))
    conn.commit()
    ctx = JobContext(
        job, conn, JobLogger(job_id, "video"), lambda: False,
        conn_factory=lambda: db.connect(str(tmp_path / "a.db")),
    )

    with ctx.keep_alive(interval=0.05):
        real_time.sleep(0.25)

    assert store.get(conn, job_id).heartbeat_at is not None


def test_keep_alive_is_a_no_op_without_a_conn_factory(tmp_path):
    """Test nào không quan tâm tới nhịp tim thì không phải dựng connection factory."""
    clock = _FakeClock()
    ctx, _, _ = _ctx(clock)
    with ctx.keep_alive(interval=0.01):
        pass


def test_log_and_emit_reach_the_job_log_file():
    from app.jobqueue import joblog
    clock = _FakeClock()
    ctx, _, job_id = _ctx(clock)
    ctx.progress(1, 3, phase="encoding")
    ctx.log("đang ghép audio")
    ctx.emit({"type": "chunk", "index": 0})
    ctx.close()
    text = joblog.tail(job_id)
    assert "đang ghép audio" in text
    assert "phase=encoding" in text
    events, _ = joblog.read_events(job_id)
    assert events == [{"type": "chunk", "index": 0}]
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_context.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.context'`.

- [ ] **Step 3: Viết `app/jobqueue/context.py`**

```python
"""Thứ duy nhất handler nhìn thấy. Cách ly handler khỏi runner: handler không biết
gì về asyncio, semaphore hay dispatcher — chỉ báo tiến độ, ghi log, và hỏi xem có bị
bảo dừng không."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Callable

from app.jobqueue import store
from app.jobqueue.joblog import JobLogger
from app.jobqueue.models import Job


class JobContext:
    def __init__(
        self,
        job: Job,
        conn: sqlite3.Connection,
        logger: JobLogger,
        cancel_check: Callable[[], bool],
        *,
        flush_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        on_write: Callable[[int, int, str | None], None] | None = None,
        conn_factory: Callable[[], sqlite3.Connection] | None = None,
    ):
        self.job = job
        self.conn = conn
        self._logger = logger
        self._cancel_check = cancel_check
        self._flush_interval = flush_interval
        self._clock = clock
        # Runner dùng hook này để mirror tiến độ vào tracker trong bộ nhớ, tránh cho
        # /health phải đọc DB mỗi lần được gọi. Không có nó, runner sẽ phải thò tay
        # vào thuộc tính private của lớp này.
        self._on_write = on_write
        # keep_alive() cần connection riêng: thread giữ nhịp tim không được dùng chung
        # self.conn với handler đang chạy (sqlite3 connection không an toàn khi hai
        # thread dùng cùng lúc). None => keep_alive thành no-op, dùng trong test.
        self._conn_factory = conn_factory
        self._lost_ownership = False

        self._current = job.progress_current
        self._total = job.progress_total
        self._phase = job.phase
        self._written = (self._current, self._total, self._phase)
        self._last_write_at: float | None = None

    # ------------------------------------------------------------- tiến độ

    def progress(
        self, current: int, total: int | None = None, phase: str | None = None
    ) -> None:
        """Luôn cập nhật trong bộ nhớ; chỉ chạm DB khi đã quá flush_interval kể từ lần
        ghi trước, hoặc khi phase đổi. Gọi cái này mỗi chunk/mỗi frame đều an toàn."""
        self._current = current
        if total is not None:
            self._total = total
        phase_changed = phase is not None and phase != self._phase
        if phase is not None:
            self._phase = phase

        now = self._clock()
        due = self._last_write_at is None or (now - self._last_write_at) >= self._flush_interval
        if phase_changed or due:
            self._write(now)

    def heartbeat(self) -> None:
        """Báo còn sống khi không có tiến độ mới để báo — handler nào chạy một bước
        dài (upload một file lớn) phải gọi cái này, nếu không reaper sẽ tưởng nó chết."""
        if not store.heartbeat(self.conn, self.job.id, worker_id=self.job.worker_id):
            self._lost_ownership = True
        self._last_write_at = self._clock()

    @contextmanager
    def keep_alive(self, interval: float = 30.0):
        """Giữ nhịp tim trong suốt một bước dài không tự báo tiến độ được — encode
        ffmpeg, hay một lần upload YouTube. Không có nó, reaper (mặc định 120s) sẽ
        tưởng worker đã chết, trả job về 'pending', và một worker thứ hai chạy lại
        đúng công việc đó.

        Thread giữ nhịp mở connection riêng mỗi nhịp: sqlite3 connection không an toàn
        khi hai thread dùng cùng lúc, và handler đang giữ self.conn."""
        if self._conn_factory is None:
            yield
            return

        stop = threading.Event()

        def _beat() -> None:
            while not stop.wait(interval):
                try:
                    conn = self._conn_factory()
                    try:
                        alive = store.heartbeat(
                            conn, self.job.id, worker_id=self.job.worker_id)
                    finally:
                        conn.close()
                except sqlite3.Error:
                    continue      # một nhịp lỡ không đáng làm hỏng job
                if not alive:
                    self._lost_ownership = True
                    return        # job không còn của mình: ngừng đập, để handler tự dừng

        thread = threading.Thread(target=_beat, name=f"keepalive-{self.job.id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5.0)

    def flush(self, *, force: bool = True) -> None:
        """Đẩy giá trị đang treo xuống DB. Runner gọi khi job kết thúc, nên con số
        cuối cùng luôn khớp thực tế dù lần progress() cuối rơi vào cửa sổ throttle."""
        if not force:
            return
        if (self._current, self._total, self._phase) != self._written:
            self._write(self._clock())

    def _write(self, now: float) -> None:
        if not store.write_progress(
            self.conn, self.job.id,
            current=self._current, total=self._total, phase=self._phase,
            worker_id=self.job.worker_id,
        ):
            # Job đã bị reap và giao cho worker khác. Ghi tiếp là đè lên lượt chạy của
            # họ, nên dừng lại và để should_cancel() bảo handler thoát.
            self._lost_ownership = True
        self._written = (self._current, self._total, self._phase)
        self._last_write_at = now
        if self._on_write is not None:
            self._on_write(self._current, self._total, self._phase)

    # ----------------------------------------------------------------- log

    def log(self, message: str, level: int = logging.INFO) -> None:
        self._logger.log(message, level=level, phase=self._phase)

    def emit(self, event: dict[str, Any]) -> None:
        self._logger.emit(event)

    def close(self) -> None:
        self._logger.close()

    # ----------------------------------------------------------------- hủy

    def should_cancel(self) -> bool:
        """True khi có người bấm hủy, HOẶC khi job đã không còn thuộc về worker này.
        Gộp hai thứ vào một cờ để handler chỉ phải kiểm một chỗ ở ranh giới chunk."""
        return self._lost_ownership or self._cancel_check()

    def lost_ownership(self) -> bool:
        """Runner dùng để phân biệt 'bị hủy' với 'bị reap rồi cướp mất'."""
        return self._lost_ownership
```

Import thêm ở đầu `context.py`: `import sqlite3`, `import threading`, và
`from contextlib import contextmanager`.

- [ ] **Step 4: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_context.py -v
```

Kỳ vọng: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add app/jobqueue/context.py tests/test_jobqueue_context.py
git commit -m "feat(queue): add JobContext with throttled progress writes"
```

---

### Task 6: `runner.py` — JobQueue, dispatcher theo loại

**Files:**
- Create: `app/jobqueue/runner.py`
- Modify: `app/jobqueue/__init__.py` (export `JobQueue`, `parse_concurrency`)
- Test: `tests/test_jobqueue_runner.py`

**Interfaces:**
- Consumes: `store`, `JobContext`, `JobLogger`, `HandlerSpec`, `JobFatalError`
- Produces:

```python
def parse_concurrency(spec: str, *, default: int) -> dict[str, int]
class JobQueue:
    def __init__(self, conn_factory: Callable[[], sqlite3.Connection], *,
                 concurrency: dict[str, int], default_concurrency: int = 10,
                 poll_interval: float = 2.0, reap_after_seconds: int = 120,
                 is_paused: Callable[[sqlite3.Connection], bool] | None = None)
    def register(self, job_type: str, fn, *, concurrency: int | None = None,
                 max_attempts: int = 3, cancellable: bool = True) -> None
    def capacity(self, job_type: str) -> int
    async def start(self) -> None
    async def stop(self, timeout: float) -> None
    def request_cancel(self, job_id: int) -> None
    def pool_status(self) -> list[dict]      # [{job_type, running, capacity, pending}]
    # thuộc tính tương thích với PatchWorker cũ:
    state: str            # 'idle' | 'busy' | 'paused'
    current_patch_id: int | None
    current_chunk_index: int
    current_chunk_count: int
    last_heartbeat_at: str
```

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_runner.py`:

```python
"""JobQueue: trần song song theo loại, pause, retry, fatal, hủy, shutdown drain."""
from __future__ import annotations

import asyncio
import threading

import pytest

from app import db
from app.jobqueue import store
from app.jobqueue.models import JobFatalError
from app.jobqueue.runner import JobQueue, parse_concurrency


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


@pytest.fixture
def conn_factory(tmp_path):
    """Mỗi lần gọi mở một connection mới tới cùng file DB — đúng như production.
    Không dùng :memory: vì mỗi connect(":memory:") là một DB rỗng khác nhau."""
    path = str(tmp_path / "queue.db")
    setup = db.connect(path)
    db.init_schema(setup)
    setup.close()
    return lambda: db.connect(path)


def _queue(conn_factory, **over):
    kwargs = dict(concurrency={}, default_concurrency=10, poll_interval=0.01,
                  reap_after_seconds=120)
    kwargs.update(over)
    return JobQueue(conn_factory, **kwargs)


async def _drain(conn, *, timeout=10.0):
    """Chờ tới khi không còn job pending/running."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM job WHERE status IN ('pending','running')"
        ).fetchone()
        if row["c"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("queue không rút hết job trong thời gian chờ")


def test_parse_concurrency_reads_the_env_string():
    assert parse_concurrency("voxcpm_tts=1,video=2,youtube_upload=1", default=10) == {
        "voxcpm_tts": 1, "video": 2, "youtube_upload": 1,
    }


def test_parse_concurrency_tolerates_whitespace_and_empty_entries():
    assert parse_concurrency(" video = 2 ,, ", default=10) == {"video": 2}


def test_parse_concurrency_ignores_malformed_entries():
    assert parse_concurrency("video=abc,light_tts=3,broken", default=10) == {"light_tts": 3}


def test_parse_concurrency_of_empty_string_is_empty():
    assert parse_concurrency("", default=10) == {}


def test_capacity_falls_back_to_the_default():
    q = JobQueue(lambda: None, concurrency={"video": 2}, default_concurrency=10)
    q.register("video", lambda ctx: {})
    q.register("light_tts", lambda ctx: {})
    assert q.capacity("video") == 2
    assert q.capacity("light_tts") == 10


def test_explicit_register_concurrency_beats_the_config_map():
    q = JobQueue(lambda: None, concurrency={"video": 2}, default_concurrency=10)
    q.register("video", lambda ctx: {}, concurrency=5)
    assert q.capacity("video") == 5


@pytest.mark.asyncio
async def test_a_job_runs_and_is_marked_done(conn_factory):
    conn = conn_factory()
    store.enqueue(conn, "demo", payload={"x": 2})
    q = _queue(conn_factory)
    q.register("demo", lambda ctx: {"doubled": ctx.job.payload["x"] * 2})
    await q.start()
    await _drain(conn)
    await q.stop(timeout=5)
    job = store.list_jobs(conn)[0]
    assert job.status == "done"
    assert job.result == {"doubled": 4}


@pytest.mark.asyncio
async def test_concurrency_cap_is_never_exceeded(conn_factory):
    """30 job, trần 10 — không lúc nào có quá 10 job chạy cùng lúc."""
    conn = conn_factory()
    for _ in range(30):
        store.enqueue(conn, "demo")

    live = 0
    peak = 0
    lock = threading.Lock()

    def handler(ctx):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        import time as _t
        _t.sleep(0.05)
        with lock:
            live -= 1
        return {}

    q = _queue(conn_factory, default_concurrency=10)
    q.register("demo", handler)
    await q.start()
    await _drain(conn, timeout=20)
    await q.stop(timeout=5)
    assert peak <= 10
    assert peak > 1, "không có song song thật — kiểm tra executor và dispatcher"


@pytest.mark.asyncio
async def test_each_type_gets_its_own_cap(conn_factory):
    conn = conn_factory()
    for _ in range(8):
        store.enqueue(conn, "slow")
        store.enqueue(conn, "fast")

    peaks = {"slow": 0, "fast": 0}
    live = {"slow": 0, "fast": 0}
    lock = threading.Lock()

    def make(kind):
        def handler(ctx):
            with lock:
                live[kind] += 1
                peaks[kind] = max(peaks[kind], live[kind])
            import time as _t
            _t.sleep(0.05)
            with lock:
                live[kind] -= 1
            return {}
        return handler

    q = _queue(conn_factory, concurrency={"slow": 1, "fast": 4})
    q.register("slow", make("slow"))
    q.register("fast", make("fast"))
    await q.start()
    await _drain(conn, timeout=20)
    await q.stop(timeout=5)
    assert peaks["slow"] == 1
    assert peaks["fast"] <= 4


@pytest.mark.asyncio
async def test_a_failing_handler_reschedules_the_job(conn_factory):
    conn = conn_factory()
    job_id = store.enqueue(conn, "demo", max_attempts=3)

    def handler(ctx):
        raise RuntimeError("bùm")

    q = _queue(conn_factory)
    q.register("demo", handler)
    await q.start()
    for _ in range(200):
        if store.get(conn, job_id).attempt_count >= 1:
            break
        await asyncio.sleep(0.02)
    await q.stop(timeout=5)
    job = store.get(conn, job_id)
    assert job.status == "pending"
    assert job.error_message == "bùm"
    assert job.next_retry_at is not None


@pytest.mark.asyncio
async def test_a_fatal_error_skips_retry(conn_factory):
    conn = conn_factory()
    job_id = store.enqueue(conn, "demo", max_attempts=5)

    def handler(ctx):
        raise JobFatalError("thiếu file nguồn")

    q = _queue(conn_factory)
    q.register("demo", handler)
    await q.start()
    await _drain(conn)
    await q.stop(timeout=5)
    job = store.get(conn, job_id)
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.error_message == "thiếu file nguồn"


@pytest.mark.asyncio
async def test_a_type_with_capacity_zero_is_disabled_but_still_queues(conn_factory):
    """Trần 0 = tắt loại đó. Job vẫn được enqueue và nằm chờ, không bị mất."""
    conn = conn_factory()
    store.enqueue(conn, "off")
    store.enqueue(conn, "on")
    ran = []
    q = _queue(conn_factory, concurrency={"off": 0, "on": 2})
    q.register("off", lambda ctx: ran.append("off"))
    q.register("on", lambda ctx: ran.append("on"))
    await q.start()
    await asyncio.sleep(0.3)
    await q.stop(timeout=5)
    assert ran == ["on"]
    assert store.list_jobs(conn, job_type="off")[0].status == "pending"
    assert {p["job_type"]: p["capacity"] for p in q.pool_status()}["off"] == 0


@pytest.mark.asyncio
async def test_pause_stops_claiming(conn_factory):
    conn = conn_factory()
    store.enqueue(conn, "demo")
    q = _queue(conn_factory, is_paused=lambda c: True)
    q.register("demo", lambda ctx: {})
    await q.start()
    await asyncio.sleep(0.2)
    await q.stop(timeout=5)
    assert store.list_jobs(conn)[0].status == "pending"
    assert q.state == "paused"


@pytest.mark.asyncio
async def test_cancelling_a_running_job_is_seen_by_the_handler(conn_factory):
    conn = conn_factory()
    job_id = store.enqueue(conn, "demo")
    started = threading.Event()
    saw_cancel = threading.Event()

    def handler(ctx):
        started.set()
        import time as _t
        for _ in range(200):
            if ctx.should_cancel():
                saw_cancel.set()
                raise asyncio.CancelledError()
            _t.sleep(0.01)
        return {}

    q = _queue(conn_factory)
    q.register("demo", handler)
    await q.start()
    await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
    q.request_cancel(job_id)
    await asyncio.get_running_loop().run_in_executor(None, saw_cancel.wait, 5)
    await q.stop(timeout=5)
    assert saw_cancel.is_set()
    assert store.get(conn, job_id).status == "cancelled"


@pytest.mark.asyncio
async def test_a_stolen_job_is_not_clobbered_by_the_old_worker(conn_factory):
    """Job bị reap giữa chừng rồi worker khác claim. Lượt chạy cũ xong sau, và kết quả
    của nó phải bị bỏ qua thay vì ghi đè lên chủ mới."""
    conn = conn_factory()
    job_id = store.enqueue(conn, "demo")
    started = threading.Event()
    release = threading.Event()

    def handler(ctx):
        started.set()
        release.wait(5)
        return {"from": "old"}

    q = _queue(conn_factory)
    q.register("demo", handler)
    await q.start()
    await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)

    # Mô phỏng reaper + worker mới cướp job trong lúc handler cũ còn chạy.
    conn.execute(
        "UPDATE job SET status='running', worker_id='demo#stolen' WHERE id=?", (job_id,))
    conn.commit()
    release.set()
    await asyncio.sleep(0.3)
    await q.stop(timeout=5)

    job = store.get(conn, job_id)
    assert job.status == "running"
    assert job.worker_id == "demo#stolen"
    assert job.result is None


@pytest.mark.asyncio
async def test_stop_waits_for_a_running_job_to_finish(conn_factory):
    conn = conn_factory()
    store.enqueue(conn, "demo")
    finished = threading.Event()

    def handler(ctx):
        import time as _t
        _t.sleep(0.3)
        finished.set()
        return {}

    q = _queue(conn_factory)
    q.register("demo", handler)
    await q.start()
    await asyncio.sleep(0.1)
    await q.stop(timeout=5)
    assert finished.is_set()
    assert store.list_jobs(conn)[0].status == "done"


@pytest.mark.asyncio
async def test_pool_status_reports_capacity_and_pending(conn_factory):
    conn = conn_factory()
    store.enqueue(conn, "demo")
    store.enqueue(conn, "demo")
    q = _queue(conn_factory, concurrency={"demo": 3})
    q.register("demo", lambda ctx: {})
    status = {p["job_type"]: p for p in q.pool_status()}
    assert status["demo"]["capacity"] == 3
    assert status["demo"]["running"] == 0


@pytest.mark.asyncio
async def test_compat_properties_track_a_running_voxcpm_job(conn_factory):
    conn = conn_factory()
    store.enqueue(conn, "voxcpm_tts", payload={"patch_id": 77})
    seen = {}
    running = threading.Event()

    def handler(ctx):
        ctx.progress(3, 12, phase="synthesizing")
        running.set()
        import time as _t
        _t.sleep(0.3)
        return {}

    q = _queue(conn_factory)
    q.register("voxcpm_tts", handler)
    await q.start()
    await asyncio.get_running_loop().run_in_executor(None, running.wait, 5)
    await asyncio.sleep(0.05)
    seen["patch_id"] = q.current_patch_id
    seen["index"] = q.current_chunk_index
    seen["count"] = q.current_chunk_count
    seen["state"] = q.state
    await q.stop(timeout=5)
    assert seen == {"patch_id": 77, "index": 3, "count": 12, "state": "busy"}
```

Bài test này cần `pytest-asyncio`. Thêm vào `pyproject.toml`, mục `[project.optional-dependencies].dev`:

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
]
```

và bật chế độ auto trong `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"
```

Cài: `pip install -e ".[dev]"`

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_runner.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.runner'`.

- [ ] **Step 3: Viết `app/jobqueue/runner.py`**

```python
"""JobQueue: một dispatcher loop cho mỗi job_type, mỗi loop một trần song song riêng.

Vì sao mỗi loại một loop thay vì một pool chung: bốn loại task ở đây có đặc tính tài
nguyên khác hẳn nhau (GPU / CPU / quota mạng / network I/O). Một pool chung sẽ để 10
tiến trình ffmpeg chạy cùng lúc, hoặc hai VoxCPM tranh VRAM. Loop riêng làm cho trần
của mỗi loại là bất biến, không phải mẹo xếp hàng.

Vì sao có executor riêng: mọi handler đều blocking, và executor mặc định của asyncio
(min(32, cpu+4)) còn phải phục vụ mọi asyncio.to_thread khác trong app. Dùng chung thì
job sẽ xếp hàng vô hình bên trong executor trong khi /queue vẫn báo 'running'."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from app.jobqueue import joblog, store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.models import CANCELLING, HandlerSpec, JobFatalError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_concurrency(spec: str, *, default: int) -> dict[str, int]:
    """'voxcpm_tts=1,video=2' -> {'voxcpm_tts': 1, 'video': 2}.
    Bỏ qua mục hỏng thay vì crash lúc khởi động: một biến env gõ sai không được phép
    làm cả app không boot được. `default` không dùng ở đây nhưng giữ trong chữ ký để
    chỗ gọi đọc ra ý đồ — loại thiếu sẽ nhận default qua JobQueue.capacity()."""
    out: dict[str, int] = {}
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, raw = chunk.partition("=")
        try:
            value = int(raw.strip())
        except ValueError:
            logger.warning("bỏ qua mục QUEUE_CONCURRENCY không hợp lệ: %r", chunk)
            continue
        if value > 0:
            out[name.strip()] = value
    return out


class _RunningJob:
    __slots__ = ("job_id", "job_type", "patch_id", "current", "total")

    def __init__(self, job_id: int, job_type: str, patch_id: int | None):
        self.job_id = job_id
        self.job_type = job_type
        self.patch_id = patch_id
        self.current = 0
        self.total = 0


class JobQueue:
    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        *,
        concurrency: dict[str, int],
        default_concurrency: int = 10,
        poll_interval: float = 2.0,
        reap_after_seconds: int = 120,
        is_paused: Callable[[sqlite3.Connection], bool] | None = None,
    ):
        self._conn_factory = conn_factory
        self._concurrency = dict(concurrency)
        self._default_concurrency = default_concurrency
        self._poll_interval = poll_interval
        self._reap_after_seconds = reap_after_seconds
        self._is_paused = is_paused

        self._specs: dict[str, HandlerSpec] = {}
        self._tasks: list[asyncio.Task] = []
        self._inflight: set[asyncio.Task] = set()
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._stop = asyncio.Event()

        self._lock = threading.Lock()
        self._running: dict[int, _RunningJob] = {}
        self._cancel_requested: set[int] = set()

        self._paused = False
        self.last_heartbeat_at = _now_iso()

    # ------------------------------------------------------------- đăng ký

    def register(
        self, job_type: str, fn: Callable[[JobContext], dict[str, Any] | None],
        *, concurrency: int | None = None, max_attempts: int = 3, cancellable: bool = True,
    ) -> None:
        if concurrency is not None:
            self._concurrency[job_type] = concurrency
        self._specs[job_type] = HandlerSpec(
            job_type=job_type, fn=fn,
            concurrency=self.capacity(job_type),
            max_attempts=max_attempts, cancellable=cancellable,
        )

    def capacity(self, job_type: str) -> int:
        return self._concurrency.get(job_type, self._default_concurrency)

    # ------------------------------------------------------------ vòng đời

    async def start(self) -> None:
        total = sum(self.capacity(t) for t in self._specs) or 1
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=total + 4, thread_name_prefix="jobqueue",
        )
        for job_type in self._specs:
            # Trần 0 = loại này bị tắt (vd voxcpm_tts trên máy không GPU). Không dựng
            # dispatcher cho nó: asyncio.Semaphore(0) sẽ treo vĩnh viễn ở acquire() và
            # loop không bao giờ quay lại kiểm self._stop. Job vẫn xếp hàng bình thường
            # và sẽ chạy khi bật lại.
            if self.capacity(job_type) <= 0:
                logger.info("event=queue.type_disabled job_type=%s", job_type)
                continue
            self._tasks.append(asyncio.create_task(self._dispatch_loop(job_type)))
        self._tasks.append(asyncio.create_task(self._reaper_loop()))
        logger.info(
            "event=queue.started types=%s capacity=%s threads=%s",
            ",".join(sorted(self._specs)), total, total + 4,
        )

    async def stop(self, timeout: float) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._inflight:
            logger.info("event=queue.draining count=%s", len(self._inflight))
            done, pending = await asyncio.wait(set(self._inflight), timeout=timeout)
            if pending:
                # Không cancel: handler đang chạy trong thread, cancel task async chỉ bỏ
                # rơi kết quả chứ không dừng được thread. Để job ở 'running' và cho
                # reaper ở lần boot sau đưa nó về 'pending' — tiến độ trên đĩa còn nguyên.
                logger.warning(
                    "event=queue.shutdown_timeout pending=%s timeout=%s", len(pending), timeout,
                )
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        logger.info("event=queue.stopped")

    def request_cancel(self, job_id: int) -> None:
        """Đường nhanh trong tiến trình. Route vẫn phải gọi store.request_cancel() để
        job pending (chưa ai giữ) cũng dừng được."""
        with self._lock:
            self._cancel_requested.add(job_id)

    # --------------------------------------------------------------- loops

    async def _dispatch_loop(self, job_type: str) -> None:
        spec = self._specs[job_type]
        sem = asyncio.Semaphore(spec.concurrency)
        conn = self._conn_factory()
        worker_seq = 0
        try:
            while not self._stop.is_set():
                self.last_heartbeat_at = _now_iso()
                if self._check_paused(conn):
                    await asyncio.sleep(self._poll_interval)
                    continue
                await sem.acquire()
                job = None
                try:
                    worker_seq += 1
                    job = store.claim(conn, job_type, f"{job_type}#{worker_seq}")
                except sqlite3.Error as exc:
                    logger.warning("event=queue.claim_failed type=%s error=%s", job_type, exc)
                if job is None:
                    sem.release()
                    await asyncio.sleep(self._poll_interval)
                    continue
                task = asyncio.create_task(self._run_job(spec, job, sem))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
        except asyncio.CancelledError:
            pass
        finally:
            conn.close()

    async def _reaper_loop(self) -> None:
        conn = self._conn_factory()
        try:
            while not self._stop.is_set():
                await asyncio.sleep(max(self._poll_interval, 5.0))
                try:
                    revived = store.reap_stale(
                        conn, older_than_seconds=self._reap_after_seconds
                    )
                except sqlite3.Error as exc:
                    logger.warning("event=queue.reap_failed error=%s", exc)
                    continue
                # Job vừa được reap có thể vẫn đang chạy trong tiến trình này nếu handler
                # treo mà không heartbeat. Loại chúng khỏi log để không báo động giả.
                with self._lock:
                    live = set(self._running)
                orphans = [j for j in revived if j not in live]
                if orphans:
                    logger.warning("event=queue.reaped jobs=%s", orphans)
        except asyncio.CancelledError:
            pass
        finally:
            conn.close()

    def _check_paused(self, conn: sqlite3.Connection) -> bool:
        if self._is_paused is None:
            paused = False
        else:
            try:
                paused = bool(self._is_paused(conn))
            except sqlite3.Error:
                paused = False
        if paused and not self._paused:
            logger.warning("event=queue.paused")
        elif not paused and self._paused:
            logger.info("event=queue.resumed")
        self._paused = paused
        return paused

    # ----------------------------------------------------------- chạy job

    async def _run_job(self, spec: HandlerSpec, job, sem: asyncio.Semaphore) -> None:
        tracker = _RunningJob(job.id, job.job_type, job.payload.get("patch_id"))
        with self._lock:
            self._running[job.id] = tracker
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._execute, spec, job, tracker)
        finally:
            with self._lock:
                self._running.pop(job.id, None)
                self._cancel_requested.discard(job.id)
            sem.release()

    def _execute(self, spec: HandlerSpec, job, tracker: _RunningJob) -> None:
        """Chạy trong thread của executor. Mỗi job một connection riêng — đây là điểm
        làm cho song song thật sự xảy ra thay vì tất cả xếp hàng sau db_lock."""
        conn = self._conn_factory()
        job_logger = JobLogger(job.id, job.job_type)

        # Mirror tiến độ vào tracker trong bộ nhớ để /health và pool_status không phải
        # đọc DB mỗi lần được gọi. Hook chạy theo đúng nhịp throttle của JobContext.
        def _track(current: int, total: int, phase: str | None) -> None:
            tracker.current = current
            tracker.total = total

        ctx = JobContext(
            job, conn, job_logger,
            lambda: self._should_cancel(conn, job.id),
            on_write=_track,
            conn_factory=self._conn_factory,
        )

        owner = job.worker_id
        job_logger.log(f"bắt đầu job {job.id} ({job.job_type}), lần thử {job.attempt_count}")
        try:
            result = spec.fn(ctx)
            ctx.flush()
            if ctx.lost_ownership():
                self._log_stolen(job_logger, job)
                return
            if self._should_cancel(conn, job.id):
                store.mark_cancelled(conn, job.id, worker_id=owner)
                job_logger.log("job bị hủy", level=logging.WARNING)
                return
            store.finish(
                conn, job.id, result if isinstance(result, dict) else None, worker_id=owner)
            job_logger.log("job xong")
        except asyncio.CancelledError:
            ctx.flush()
            if ctx.lost_ownership():
                self._log_stolen(job_logger, job)
                return
            store.mark_cancelled(conn, job.id, worker_id=owner)
            job_logger.log("job bị hủy", level=logging.WARNING)
        except JobFatalError as exc:
            ctx.flush()
            if ctx.lost_ownership():
                self._log_stolen(job_logger, job)
                return
            store.fail(conn, job.id, str(exc), fatal=True, worker_id=owner)
            job_logger.log(f"lỗi không retry được: {exc}", level=logging.ERROR)
        except Exception as exc:  # noqa: BLE001 - một job hỏng không được làm chết pool
            ctx.flush()
            if ctx.lost_ownership():
                self._log_stolen(job_logger, job)
                return
            new_status = store.fail(
                conn, job.id, str(exc), max_attempts=spec.max_attempts, worker_id=owner)
            job_logger.log(
                f"job lỗi ({new_status}): {exc!r}", level=logging.ERROR,
            )
            logger.exception("job %s (%s) lỗi", job.id, job.job_type)
        finally:
            job_logger.close()
            conn.close()

    def _log_stolen(self, job_logger: JobLogger, job) -> None:
        """Job này đã bị reaper thu hồi và giao cho worker khác trong lúc ta đang chạy.
        Mọi lần ghi của ta đã bị rào chặn, nên không có gì để dọn — chỉ ghi lại, vì nếu
        chuyện này xảy ra thường xuyên thì QUEUE_REAP_AFTER_SECONDS đang đặt quá thấp
        so với thời gian chạy thật của loại job đó."""
        job_logger.log(
            f"job {job.id} đã bị thu hồi khỏi {job.worker_id} và giao cho worker khác; "
            "kết quả của lượt chạy này bị bỏ qua",
            level=logging.WARNING,
        )
        logger.warning(
            "event=queue.job_stolen job_id=%s job_type=%s worker_id=%s",
            job.id, job.job_type, job.worker_id,
        )

    def _should_cancel(self, conn: sqlite3.Connection, job_id: int) -> bool:
        with self._lock:
            if job_id in self._cancel_requested:
                return True
        # Tiến trình khác (hoặc lần boot khác) có thể đã đặt cờ trong DB.
        try:
            row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        except sqlite3.Error:
            return False
        return bool(row) and row["status"] == CANCELLING

    # ------------------------------------------------------------ quan sát

    def pool_status(self) -> list[dict[str, Any]]:
        with self._lock:
            running = list(self._running.values())
        conn = self._conn_factory()
        try:
            out = []
            for job_type in sorted(self._specs):
                out.append({
                    "job_type": job_type,
                    "capacity": self.capacity(job_type),
                    "running": sum(1 for r in running if r.job_type == job_type),
                    "pending": store.pending_count(conn, job_type),
                })
            return out
        finally:
            conn.close()

    # ---------------------------------- thuộc tính tương thích PatchWorker cũ

    @property
    def state(self) -> str:
        if self._paused:
            return "paused"
        with self._lock:
            return "busy" if self._running else "idle"

    def _voxcpm(self) -> _RunningJob | None:
        with self._lock:
            for tracker in self._running.values():
                if tracker.job_type == "voxcpm_tts":
                    return tracker
        return None

    @property
    def current_patch_id(self) -> int | None:
        tracker = self._voxcpm()
        return tracker.patch_id if tracker else None

    @property
    def current_chunk_index(self) -> int:
        tracker = self._voxcpm()
        return tracker.current if tracker else 0

    @property
    def current_chunk_count(self) -> int:
        tracker = self._voxcpm()
        return tracker.total if tracker else 0
```

Lưu ý cho người triển khai: tracker được cập nhật qua tham số công khai `on_write` của
`JobContext` (Task 5), **không** bằng cách bọc lại `ctx._write`. Không có thuộc tính
`_`-prefix nào của `JobContext` được chạm tới từ `runner.py`, và logic throttle không bị
nhân bản — hook chạy đúng nhịp mà `JobContext` đã quyết định.

- [ ] **Step 4: Cập nhật `app/jobqueue/__init__.py`**

```python
"""Queue job chạy nền, song song có giới hạn theo từng loại task."""
from app.jobqueue.context import JobContext
from app.jobqueue.models import HandlerSpec, Job, JobFatalError
from app.jobqueue.runner import JobQueue, parse_concurrency

__all__ = [
    "HandlerSpec", "Job", "JobContext", "JobFatalError", "JobQueue", "parse_concurrency",
]
```

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_runner.py -v
```

Kỳ vọng: 18 passed. `test_concurrency_cap_is_never_exceeded` là bài quan trọng nhất — nếu
`peak == 1` thì handler đang chạy tuần tự, kiểm tra `run_in_executor` có nhận đúng
`self._executor` không.

- [ ] **Step 6: Chạy cả suite**

```bash
pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add app/jobqueue/runner.py app/jobqueue/__init__.py pyproject.toml tests/test_jobqueue_runner.py
git commit -m "feat(queue): add JobQueue dispatcher with per-type concurrency caps"
```

---

### Task 7: Handler `voxcpm_tts`

Port `PatchWorker._synthesize` + `_maybe_finalize_book` + `_merge_final_audio` thành hàm thuần.
Ba khác biệt so với bản cũ: bỏ mọi `with self.db_lock` (đã có connection riêng),
`self._log_event(...)` → `ctx.log(...)`, và cập nhật chunk tiến độ đi qua `ctx.progress()`
song song với `repository.update_patch_chunk_progress` (cột `next_chunk_index` vẫn phải
ghi — đó là thứ làm resume hoạt động).

**Files:**
- Create: `app/jobqueue/handlers/__init__.py`
- Create: `app/jobqueue/handlers/voxcpm_tts.py`
- Test: `tests/test_jobqueue_handler_voxcpm.py`

**Interfaces:**
- Consumes: `JobContext` (Task 5), `JobFatalError` (Task 2), `store.enqueue` (Task 3)
- Produces:
```python
def handle(ctx) -> dict          # {"audio_path": str, "chunks": int}
def get_engine()                 # singleton VoxCPMEngine, tạo lười
def synthesize_patch(ctx, patch, engine, data_root: Path) -> tuple[str, int]
def finalize_book_if_ready(ctx, book_id: int) -> str | None   # đường dẫn final.wav nếu vừa gộp
```

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_handler_voxcpm.py`:

```python
"""Handler voxcpm_tts: synthesize, ghi tiến độ, resume, và nối chuỗi sang video."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
import soundfile as sf

from app import db, repository
from app.jobqueue import store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.handlers import voxcpm_tts
from app.jobqueue.models import JobFatalError


class _FakeEngine:
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def synthesize_chunk(self, text, reference_wav_path=None, prompt_text=None):
        self.calls.append(text)
        return np.zeros(self.sample_rate // 10, dtype="float32")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


def _book_with_patch(conn, *, text="Câu một. Câu hai. Câu ba."):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size,
                              status, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?)""", (now, now))
    conn.execute(
        """INSERT INTO chapter (book_id, chapter_index, title, text, char_count)
           VALUES (1, 0, 'Chương 1', ?, ?)""", (text, len(text)))
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               attempt_count, created_at, updated_at)
           VALUES (1, 0, 0, 0, 'pending', 0, ?, ?)""", (now, now))
    conn.commit()
    return cur.lastrowid


def _ctx(conn, job_type="voxcpm_tts", **payload):
    job_id = store.enqueue(conn, job_type, payload=payload, book_id=1)
    job = store.claim(conn, job_type, "w")
    return JobContext(job, conn, JobLogger(job_id, job_type), lambda: False), job_id


def test_missing_patch_is_a_fatal_error(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    ctx, _ = _ctx(conn, patch_id=999)
    with pytest.raises(JobFatalError):
        voxcpm_tts.handle(ctx)


def test_patch_with_no_speakable_text_is_fatal(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn, text="   ")
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: _FakeEngine())
    ctx, _ = _ctx(conn, patch_id=patch_id)
    with pytest.raises(JobFatalError):
        voxcpm_tts.handle(ctx)


def test_successful_run_writes_audio_and_marks_the_patch_done(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    engine = _FakeEngine()
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: engine)
    ctx, job_id = _ctx(conn, patch_id=patch_id)

    result = voxcpm_tts.handle(ctx)

    patch = repository.get_patch(conn, patch_id)
    assert patch.status == "done"
    assert patch.audio_path == result["audio_path"]
    assert sf.info(result["audio_path"]).frames > 0
    assert engine.calls, "engine chưa được gọi lần nào"


def test_progress_is_reported_per_chunk(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: _FakeEngine())
    ctx, job_id = _ctx(conn, patch_id=patch_id)

    voxcpm_tts.handle(ctx)
    ctx.flush()

    job = store.get(conn, job_id)
    assert job.progress_total > 0
    assert job.progress_current == job.progress_total
    assert job.phase == "synthesizing"


def test_next_chunk_index_is_persisted_so_a_rerun_resumes(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    engine = _FakeEngine()
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: engine)
    ctx, _ = _ctx(conn, patch_id=patch_id)
    voxcpm_tts.handle(ctx)
    first_calls = len(engine.calls)

    # Chạy lại: chunk file còn trên đĩa, next_chunk_index đã ở cuối -> không synth lại.
    repository._update_status(conn, "patch", patch_id, status="pending")
    ctx2, _ = _ctx(conn, patch_id=patch_id)
    voxcpm_tts.handle(ctx2)
    assert len(engine.calls) == first_calls, "chunk đã có trên đĩa vẫn bị synth lại"


def test_cancel_between_chunks_stops_the_run(tmp_path, monkeypatch):
    import asyncio
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn, text="Một. Hai. Ba. Bốn. Năm. Sáu. Bảy. Tám.")
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: _FakeEngine())
    job_id = store.enqueue(conn, "voxcpm_tts", payload={"patch_id": patch_id}, book_id=1)
    job = store.claim(conn, "voxcpm_tts", "w")
    ctx = JobContext(job, conn, JobLogger(job_id, "voxcpm_tts"), lambda: True)
    with pytest.raises(asyncio.CancelledError):
        voxcpm_tts.handle(ctx)
    assert repository.get_patch(conn, patch_id).status != "done"


def test_finishing_the_last_patch_enqueues_a_video_job(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    conn.execute("UPDATE book SET background_image_path='/tmp/bg.jpg' WHERE id=1")
    conn.commit()
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: _FakeEngine())
    ctx, _ = _ctx(conn, patch_id=patch_id)

    voxcpm_tts.handle(ctx)

    assert repository.get_book(conn, 1).final_audio_path is not None
    video_jobs = store.list_jobs(conn, job_type="video")
    assert len(video_jobs) == 1
    assert video_jobs[0].payload["book_job_id"] == repository.get_book_job(conn, 1, "video").id


def test_no_video_job_when_the_book_has_no_image(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(voxcpm_tts, "get_engine", lambda: _FakeEngine())
    ctx, _ = _ctx(conn, patch_id=patch_id)
    voxcpm_tts.handle(ctx)
    assert store.list_jobs(conn, job_type="video") == []
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_handler_voxcpm.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.handlers'`.

- [ ] **Step 3: Tạo `app/jobqueue/handlers/__init__.py`**

```python
"""Handler cho từng loại job. Mỗi module export một hàm handle(ctx) -> dict."""
```

- [ ] **Step 4: Viết `app/jobqueue/handlers/voxcpm_tts.py`**

Nguồn gốc: `app/worker.py::PatchWorker._synthesize` (dòng 226–302),
`_maybe_finalize_book` (309–314), `_merge_final_audio` (316–343). Đọc cả ba trước khi sửa.

```python
"""Sinh audio cho một patch bằng VoxCPM. Trần song song 1: engine bám VRAM.

Port từ PatchWorker._synthesize. Ba thay đổi so với bản cũ:
  * bỏ hết `with self.db_lock` — job đã có connection riêng;
  * self._log_event(...) -> ctx.log(...) / ctx.progress(...);
  * kiểm ctx.should_cancel() ở đầu mỗi chunk.
Phần còn lại (chunk file, marker .light_tts_meta, timeline sidecar, resume theo
next_chunk_index) giữ nguyên từng dòng — chúng là hợp đồng với LightTTS và với UI."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import soundfile as sf

from app import audio_merge, repository
from app.config import settings
from app.jobqueue import store
from app.jobqueue.models import JobFatalError

logger = logging.getLogger(__name__)

_CHUNK_PAUSE_MS = 300
_engine = None


def get_engine():
    """Tạo lười và dùng lại. Trần song song của loại này là 1 nên không cần khóa:
    chỉ có đúng một thread chạm vào engine tại một thời điểm."""
    global _engine
    if _engine is None:
        from app.tts_engine import VoxCPMEngine
        _engine = VoxCPMEngine()
    return _engine


def handle(ctx) -> dict:
    patch_id = ctx.job.payload.get("patch_id")
    if patch_id is None:
        raise JobFatalError("payload thiếu patch_id")
    patch = repository.get_patch(ctx.conn, patch_id)
    if patch is None:
        raise JobFatalError(f"patch {patch_id} không tồn tại")

    ctx.log(f"synthesize patch {patch_id} (book {patch.book_id})")
    try:
        audio_path, chunk_count = synthesize_patch(
            ctx, patch, get_engine(), Path(settings.data_root)
        )
    except asyncio.CancelledError:
        raise
    except JobFatalError:
        repository.mark_patch_failed(ctx.conn, patch_id, "không có nội dung đọc được")
        raise
    except Exception as exc:
        repository.mark_patch_failed(ctx.conn, patch_id, str(exc))
        raise

    from app.patch_publishing import fetch_thumbnail_inputs, on_patch_audio_ready, warm_patch_thumbnail
    thumbnail_inputs = fetch_thumbnail_inputs(ctx.conn, patch_id)
    warm_patch_thumbnail(thumbnail_inputs)
    repository.mark_patch_done(ctx.conn, patch_id, audio_path)
    on_patch_audio_ready(ctx.conn, patch_id)
    ctx.log(f"patch {patch_id} xong -> {audio_path}")

    final_path = finalize_book_if_ready(ctx, patch.book_id)
    return {"audio_path": audio_path, "chunks": chunk_count, "final_audio_path": final_path}


def synthesize_patch(ctx, patch, engine, data_root: Path) -> tuple[str, int]:
    plan_inputs = repository.fetch_patch_chunk_inputs(ctx.conn, patch)
    book = repository.get_book(ctx.conn, patch.book_id)
    plan = repository.build_chunk_plan_from_inputs(plan_inputs)
    if not plan:
        raise JobFatalError("patch không có nội dung đọc được")

    ref_wav = book.voice_clip_path if book else None
    ref_text = book.voice_transcript if book else None

    book_dir = data_root / "books" / str(patch.book_id) / "patches"
    book_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(book_dir / f"{patch.id}.wav")
    timeline_path = Path(audio_path).with_suffix(".timeline.json")
    total = len(plan)
    ctx.progress(patch.next_chunk_index, total, phase="synthesizing")

    if not settings.tts_write_chunk_files:
        wavs = []
        for index, item in enumerate(plan):
            if ctx.should_cancel():
                raise asyncio.CancelledError()
            wavs.append(engine.synthesize_chunk(
                item["text"], reference_wav_path=ref_wav, prompt_text=ref_text))
            ctx.progress(index + 1, total)
        chapters, _ = audio_merge.build_chapter_marks(
            plan, [len(a) for a in wavs], engine.sample_rate, _CHUNK_PAUSE_MS)
        audio_merge.concat_chunks_to_wav(
            wavs, engine.sample_rate, audio_path, pause_ms=_CHUNK_PAUSE_MS)
        audio_merge.try_write_timeline(
            timeline_path, engine.sample_rate, chapters, sf.info(audio_path).frames)
        return audio_path, total

    chunk_dir = book_dir / f"{patch.id}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    # LightTTS chỉ dùng lại chunk file khi marker của nó khớp; worker ghi audio khác,
    # nên xóa marker để LightTTS không bao giờ gộp nhầm chunk của worker thành của mình.
    (chunk_dir / ".light_tts_meta").unlink(missing_ok=True)

    repository.update_patch_chunk_count(ctx.conn, patch.id, total)
    start_index = max(0, min(patch.next_chunk_index, total))
    if start_index > 0:
        ctx.log(f"resume từ chunk {start_index}/{total}")

    frame_counts = []
    for index, item in enumerate(plan):
        chunk_path = chunk_dir / f"chunk_{index:03d}.wav"
        if index >= start_index:
            if ctx.should_cancel():
                raise asyncio.CancelledError()
            arr = engine.synthesize_chunk(
                item["text"], reference_wav_path=ref_wav, prompt_text=ref_text)
            sf.write(chunk_path, arr, engine.sample_rate)
            repository.update_patch_chunk_progress(ctx.conn, patch.id, index + 1)
            ctx.progress(index + 1, total)
        frame_counts.append(sf.info(str(chunk_path)).frames)

    chapters, _ = audio_merge.build_chapter_marks(
        plan, frame_counts, engine.sample_rate, _CHUNK_PAUSE_MS)
    chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(total)]
    ctx.progress(total, total, phase="merging")
    audio_merge.concat_wavs(chunk_paths, audio_path, pause_ms=_CHUNK_PAUSE_MS)
    audio_merge.try_write_timeline(
        timeline_path, engine.sample_rate, chapters, sf.info(audio_path).frames)
    # Chunk file cố ý để lại trên đĩa sau khi gộp: một lần gộp hỏng không nhất thiết
    # raise, xóa nguồn ngay sẽ làm việc đó không cứu được nếu không chạy lại cả patch.
    ctx.progress(total, total, phase="synthesizing")
    return audio_path, total


def finalize_book_if_ready(ctx, book_id: int) -> str | None:
    """Gộp final.wav khi mọi patch của sách đã done, rồi enqueue job video nếu sách có
    ảnh dùng được. Trả về đường dẫn final.wav nếu lần gọi này là lần gộp."""
    if not repository.all_patches_done(ctx.conn, book_id):
        return None

    patches = repository.list_patches(ctx.conn, book_id)
    book = repository.get_book(ctx.conn, book_id)
    paths = [p.audio_path for p in patches if p.audio_path]
    if len(paths) != len(patches):
        return None    # phòng thủ: all_patches_done đúng nhưng thiếu file

    book_dir = Path(settings.data_root) / "books" / str(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    final_path = str(book_dir / "final.wav")
    ctx.progress(ctx.job.progress_current, ctx.job.progress_total, phase="merging_book")
    audio_merge.concat_wavs(paths, final_path)
    repository.set_book_final_audio(ctx.conn, book_id, final_path)
    ctx.log(f"gộp xong final.wav cho sách {book_id}")

    if book is None:
        return final_path
    has_image = bool(book.background_image_path) or any(p.image_path for p in patches)
    if not has_image:
        ctx.log("sách không có ảnh nào dùng được — bỏ qua bước tạo video")
        return final_path

    book_job = repository.enqueue_book_job(ctx.conn, book_id, "video")
    job_id = store.enqueue(
        ctx.conn, "video",
        payload={"book_job_id": book_job.id},
        book_id=book_id,
        dedupe_key=f"video:book_job={book_job.id}",
    )
    ctx.log(f"đã xếp hàng job video (book_job={book_job.id}, job={job_id})")
    return final_path
```

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_handler_voxcpm.py -v
```

Kỳ vọng: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add app/jobqueue/handlers/ tests/test_jobqueue_handler_voxcpm.py
git commit -m "feat(queue): add voxcpm_tts handler ported from PatchWorker"
```

---

### Task 8: Handler `video`

**Files:**
- Create: `app/jobqueue/handlers/video.py`
- Test: `tests/test_jobqueue_handler_video.py`

**Interfaces:**
- Consumes: `JobContext`, `JobFatalError`, `store.enqueue`
- Produces: `def handle(ctx) -> dict` → `{"output_path": str}`

Nguồn: `app/worker.py::PatchWorker._run_video_job` (dòng 420–473) và nhánh auto-upload
trong `_process_book_job` (dòng 375–405).

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_handler_video.py`:

```python
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


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
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
    ctx, _ = _ctx(conn, bj.id)

    result = video_handler.handle(ctx)

    assert result["output_path"] == calls["out_path"]
    assert result["output_path"].endswith(f"video_{bj.id}.mp4")
    assert repository.get_book_job(conn, 1, "video").status == "done"
    assert repository.get_book(conn, 1).final_video_path == result["output_path"]


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
    assert store.get(conn, job_id).phase == "encoding"


def test_the_render_runs_inside_keep_alive(tmp_path, monkeypatch):
    """Giữa ffmpeg_start và ffmpeg_done của một sách dài có thể là hàng chục phút
    không event nào. Ngoài keep_alive thì reaper sẽ cho worker khác render lại."""
    from contextlib import contextmanager
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    bj = _book_job(conn)
    events = []

    @contextmanager
    def _spy(interval=30.0):
        events.append("enter")
        yield
        events.append("exit")

    ctx, _ = _ctx(conn, bj.id)
    monkeypatch.setattr(ctx, "keep_alive", _spy)
    monkeypatch.setattr(video_handler.settings, "youtube_auto_upload", False)

    def fake_generate(patches, book, out_path, **kw):
        events.append("render")
        open(out_path, "wb").close()

    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", fake_generate)

    video_handler.handle(ctx)

    assert events == ["enter", "render", "exit"]


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
    monkeypatch.setattr(
        video_handler.youtube, "enqueue_upload",
        lambda *a, **kw: 55)
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

    result = video_handler.handle(ctx)     # không raise

    assert result["output_path"]
    assert repository.get_book_job(conn, 1, "video").status == "done"
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_handler_video.py -v
```

Kỳ vọng: FAIL với `ImportError: cannot import name 'video' from 'app.jobqueue.handlers'`.

- [ ] **Step 3: Viết `app/jobqueue/handlers/video.py`**

```python
"""Render video cho một book_job. Trần song song 2: mỗi job là một tiến trình ffmpeg.

Port từ PatchWorker._run_video_job + nhánh auto-upload của _process_book_job.
Khác bản cũ: không giữ db_lock (job có connection riêng), và bước auto-upload chỉ
enqueue chứ không tự upload."""
from __future__ import annotations

import logging
from pathlib import Path

from app import repository, video_gen, youtube
from app.config import settings
from app.jobqueue import store
from app.jobqueue.models import JobFatalError
from app.video_config import get_book_video_config

logger = logging.getLogger(__name__)

# Event của video_gen báo hiệu đã vào giai đoạn ffmpeg thật sự.
_ENCODING_EVENTS = ("segment.ffmpeg_start", "concat.ffmpeg_start")


def _youtube_ready() -> bool:
    """Tách ra thành hàm riêng để test monkeypatch được mà không đụng module youtube."""
    return youtube.is_configured()


def handle(ctx) -> dict:
    book_job_id = ctx.job.payload.get("book_job_id")
    if book_job_id is None:
        raise JobFatalError("payload thiếu book_job_id")
    row = ctx.conn.execute(
        "SELECT id, book_id, job_type FROM book_job WHERE id=?", (book_job_id,)
    ).fetchone()
    if row is None:
        raise JobFatalError(f"book_job {book_job_id} không tồn tại")
    book_id = row["book_id"]

    ctx.progress(0, 1, phase="preparing")
    try:
        output_path = _render(ctx, book_job_id, book_id)
    except JobFatalError as exc:
        repository.mark_book_job_failed(ctx.conn, book_job_id, str(exc))
        raise
    except Exception as exc:
        repository.mark_book_job_failed(ctx.conn, book_job_id, str(exc))
        raise

    repository.mark_book_job_done(ctx.conn, book_job_id, output_path)
    repository.set_book_final_video(ctx.conn, book_id, output_path)
    ctx.progress(1, 1, phase="done")
    ctx.log(f"video xong -> {output_path}")

    _maybe_enqueue_upload(ctx, book_id, output_path)
    return {"output_path": output_path}


def _render(ctx, book_job_id: int, book_id: int) -> str:
    book = repository.get_book(ctx.conn, book_id)
    patches = repository.list_patches(ctx.conn, book_id)
    if book is None or not book.final_audio_path:
        raise JobFatalError(f"sách {book_id} chưa có final_audio_path")

    music_path = None
    video_config = get_book_video_config(ctx.conn, book)
    if book.music_id is not None:
        music = repository.get_music(ctx.conn, book.music_id)
        if music and Path(music.file_path).exists():
            music_path = music.file_path

    voices_dir = Path(settings.data_root) / "voices"
    intro = voices_dir / video_config["intro_voice"] if video_config.get("intro_voice") else None
    outro = voices_dir / video_config["outro_voice"] if video_config.get("outro_voice") else None
    intro_audio = str(intro) if intro and intro.is_file() else None
    outro_audio = str(outro) if outro and outro.is_file() else None

    done_patches = [p for p in patches if p.status == "done" and p.audio_path]
    book_dir = Path(settings.data_root) / "books" / str(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    # Tên file bám theo book_job.id chứ không phải job.id — đường dẫn này đã nằm trong
    # book.final_video_path của dữ liệu cũ, đổi sẽ làm mất link tải về.
    out_path = str(book_dir / f"video_{book_job_id}.mp4")

    def _on_progress(event: str, fields: dict) -> None:
        if event in _ENCODING_EVENTS:
            ctx.progress(0, 1, phase="encoding")
        detail = " ".join(f"{k}={v}" for k, v in fields.items())
        level = logging.ERROR if event.endswith(".failed") else logging.INFO
        ctx.log(f"{event} {detail}".strip(), level=level)
        ctx.heartbeat()

    # keep_alive là bắt buộc, không phải tùy chọn: giữa segment.ffmpeg_start và
    # segment.ffmpeg_done của một sách dài có thể là hàng chục phút không một event nào.
    # Không có nó, reaper (mặc định 120s) sẽ tưởng worker chết và cho worker thứ hai
    # render lại đúng video đó.
    with ctx.keep_alive():
        video_gen.generate_full_video(
            done_patches, book, out_path,
            default_image=settings.default_background_image,
            use_nvenc=settings.use_nvenc,
            music_path=music_path,
            music_volume=book.music_volume,
            codec=video_config["codec"],
            quality=video_config["quality"],
            audio_bitrate=video_config["audio_bitrate"],
            video_config=video_config,
            intro_audio=intro_audio,
            outro_audio=outro_audio,
            font_path=settings.default_font_path or None,
            on_progress=_on_progress,
        )
    return out_path


def _maybe_enqueue_upload(ctx, book_id: int, output_path: str) -> None:
    """Video đã render xong; mọi lỗi ở đây chỉ ghi log chứ không làm hỏng job."""
    if not settings.youtube_auto_upload or not _youtube_ready():
        return
    try:
        book = repository.get_book(ctx.conn, book_id)
        if book is None:
            return
        info = repository.build_youtube_description(ctx.conn, book_id)
        upload_id = youtube.enqueue_upload(
            ctx.conn,
            video_path=output_path,
            title=book.title,
            description=info["description"],
            tags=info["tags"],
            privacy_status=settings.youtube_default_privacy,
        )
        store.enqueue(
            ctx.conn, "youtube_upload",
            payload={"upload_id": upload_id},
            book_id=book_id,
            dedupe_key=f"youtube_upload:upload={upload_id}",
        )
        ctx.log(f"đã xếp hàng upload YouTube (upload_id={upload_id})")
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"không xếp hàng được upload YouTube: {exc}", level=logging.WARNING)
```

- [ ] **Step 4: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_handler_video.py -v
```

Kỳ vọng: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add app/jobqueue/handlers/video.py tests/test_jobqueue_handler_video.py
git commit -m "feat(queue): add video handler ported from PatchWorker._run_video_job"
```

---

### Task 9: Handler `youtube_upload`

**Files:**
- Create: `app/jobqueue/handlers/youtube_upload.py`
- Test: `tests/test_jobqueue_handler_youtube.py`

**Interfaces:**
- Consumes: `JobContext`, `JobFatalError`
- Produces: `def handle(ctx) -> dict` → `{"youtube_video_id": str}`

Nguồn: `app/upload_worker.py::UploadWorker._process_upload` (dòng 86–134).
`_execution_connection` bị bỏ hẳn: nó tồn tại chỉ để tránh giữ `db_lock` suốt lúc
transfer, mà giờ job đã có connection riêng.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_handler_youtube.py`:

```python
"""Handler youtube_upload: thành công, thất bại, quota là lỗi fatal."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db
from app.jobqueue import store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.handlers import youtube_upload as handler
from app.jobqueue.models import JobFatalError


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


def _upload_row(conn, *, video_id=None):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO youtube_uploads (video_path, title, description, tags,
                                         privacy_status, status, created_at)
           VALUES ('/tmp/v.mp4', 'T', 'D', 'a,b', 'private', 'pending', ?)""", (now,))
    conn.commit()
    return cur.lastrowid


def _ctx(conn, upload_id, video_id=None):
    payload = {"upload_id": upload_id}
    if video_id is not None:
        payload["video_id"] = video_id
    job_id = store.enqueue(conn, "youtube_upload", payload=payload)
    job = store.claim(conn, "youtube_upload", "youtube_upload#1")
    return JobContext(job, conn, JobLogger(job_id, "youtube_upload"), lambda: False), job_id


def test_missing_upload_id_is_fatal(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    job_id = store.enqueue(conn, "youtube_upload", payload={})
    job = store.claim(conn, "youtube_upload", "w")
    ctx = JobContext(job, conn, JobLogger(job_id, "youtube_upload"), lambda: False)
    with pytest.raises(JobFatalError):
        handler.handle(ctx)


def test_successful_upload_returns_the_video_id(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_id = _upload_row(conn)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: {"status": "done", "youtube_video_id": "abc123"})
    monkeypatch.setattr(handler.youtube, "publish_completed_upload", lambda c, uid: {"status": "published"})
    monkeypatch.setattr(handler, "sync_pipeline_from_upload", lambda c, uid: None)
    ctx, _ = _ctx(conn, upload_id)

    assert handler.handle(ctx) == {"youtube_video_id": "abc123"}


def test_the_transfer_runs_inside_keep_alive(tmp_path, monkeypatch):
    """process_upload là cả lần transfer nhiều phút. Nếu nó chạy ngoài keep_alive,
    reaper sẽ trả job về pending giữa chừng và worker thứ hai upload trùng video."""
    from contextlib import contextmanager
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_id = _upload_row(conn)
    ctx, _ = _ctx(conn, upload_id)
    events = []

    @contextmanager
    def _spy(interval=30.0):
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(ctx, "keep_alive", _spy)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: events.append("transfer") or {"status": "done", "youtube_video_id": "x"})
    monkeypatch.setattr(handler.youtube, "publish_completed_upload", lambda c, uid: {"status": "published"})
    monkeypatch.setattr(handler, "sync_pipeline_from_upload", lambda c, uid: None)

    handler.handle(ctx)

    assert events == ["enter", "transfer", "exit"]


def test_a_failed_transfer_marks_the_upload_and_raises(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_id = _upload_row(conn)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: {"status": "failed", "error": "mạng lỗi"})
    ctx, _ = _ctx(conn, upload_id)
    with pytest.raises(RuntimeError, match="mạng lỗi"):
        handler.handle(ctx)
    row = conn.execute(
        "SELECT status, error_message FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert row["status"] == "failed"


def test_quota_errors_are_fatal_and_never_retried(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_id = _upload_row(conn)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: {"status": "failed", "error": "quotaExceeded: daily limit"})
    ctx, _ = _ctx(conn, upload_id)
    with pytest.raises(JobFatalError):
        handler.handle(ctx)


def test_a_missing_source_file_is_fatal(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_id = _upload_row(conn)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: {"status": "failed", "error": "FileNotFoundError: /tmp/v.mp4"})
    ctx, _ = _ctx(conn, upload_id)
    with pytest.raises(JobFatalError):
        handler.handle(ctx)


def test_video_row_is_updated_when_the_payload_carries_one(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO videos (filename, file_path, upload_status, created_at, updated_at)
           VALUES ('v.mp4', '/tmp/v.mp4', 'queued', ?, ?)""", (now, now))
    conn.commit()
    video_id = cur.lastrowid
    upload_id = _upload_row(conn)
    monkeypatch.setattr(
        handler.youtube, "process_upload",
        lambda c, uid: {"status": "done", "youtube_video_id": "xyz"})
    monkeypatch.setattr(handler.youtube, "publish_completed_upload", lambda c, uid: {"status": "published"})
    monkeypatch.setattr(handler, "sync_pipeline_from_upload", lambda c, uid: None)
    ctx, _ = _ctx(conn, upload_id, video_id=video_id)

    handler.handle(ctx)

    row = conn.execute("SELECT upload_status, youtube_video_id FROM videos WHERE id=?",
                       (video_id,)).fetchone()
    assert row["upload_status"] == "uploaded"
    assert row["youtube_video_id"] == "xyz"
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_handler_youtube.py -v
```

Kỳ vọng: FAIL với `ImportError: cannot import name 'youtube_upload'`.

- [ ] **Step 3: Viết `app/jobqueue/handlers/youtube_upload.py`**

```python
"""Upload một video lên YouTube. Trần song song 1 — bị chặn bởi quota API, không phải
bởi băng thông: mỗi videos.insert tốn 1600 trong hạn mức 10.000/ngày, tức khoảng 6
video/ngày. Chạy song song không tăng thông lượng mà chỉ làm tăng nguy cơ 429.

Port từ UploadWorker._process_upload. Bỏ hẳn _execution_connection: nó tồn tại chỉ để
tránh giữ db_lock suốt cả lần transfer, mà job giờ đã có connection riêng."""
from __future__ import annotations

import logging

from app import youtube
from app.jobqueue.models import JobFatalError
from app.patch_publishing import sync_pipeline_from_upload
from app.video_repository import update_video

logger = logging.getLogger(__name__)

# Lỗi mà thử lại cũng vô ích: hết hạn mức, sai quyền, hoặc file nguồn không còn.
_FATAL_MARKERS = (
    "quotaexceeded", "dailylimitexceeded", "uploadlimitexceeded",
    "forbidden", "filenotfounderror", "no such file",
    "invalid_grant", "unauthorized",
)


def _is_fatal(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _FATAL_MARKERS)


def handle(ctx) -> dict:
    upload_id = ctx.job.payload.get("upload_id")
    if upload_id is None:
        raise JobFatalError("payload thiếu upload_id")
    video_id = ctx.job.payload.get("video_id")

    ctx.progress(0, 1, phase="uploading")
    if video_id:
        update_video(ctx.conn, video_id, upload_status="uploading")

    ctx.log(f"bắt đầu upload {upload_id}")
    # process_upload là cả lần transfer nhiều phút và không tự báo tiến độ. Không có
    # keep_alive, reaper sẽ trả job về 'pending' giữa chừng và một worker thứ hai
    # upload lại đúng video đó lên YouTube.
    with ctx.keep_alive():
        result = youtube.process_upload(ctx.conn, upload_id)

    if result.get("status") != "done":
        error = result.get("error") or result.get("status") or "upload thất bại"
        youtube.mark_upload_failed(ctx.conn, upload_id, error)
        if video_id:
            update_video(ctx.conn, video_id, upload_status="failed", error_message=error)
        ctx.log(f"upload {upload_id} thất bại: {error}", level=logging.ERROR)
        if _is_fatal(error):
            raise JobFatalError(error)
        raise RuntimeError(error)

    youtube_video_id = result.get("youtube_video_id", "")
    ctx.progress(1, 1, phase="publishing")

    # Sau khi transfer xong: đặt thumbnail, thêm vào playlist. Hỏng ở bước này không
    # làm mất video đã upload nên chỉ ghi vào error_message, không raise.
    postprocess = youtube.publish_completed_upload(ctx.conn, upload_id)
    if video_id:
        update_video(
            ctx.conn, video_id, upload_status="uploaded", youtube_video_id=youtube_video_id,
        )
    sync_pipeline_from_upload(ctx.conn, upload_id)

    status = (postprocess or {}).get("status")
    if status not in (None, "published", "done"):
        message = (postprocess or {}).get("error") or status
        row = ctx.conn.execute(
            "SELECT error_message FROM youtube_uploads WHERE id=?", (upload_id,)
        ).fetchone()
        if row is not None and not row["error_message"]:
            ctx.conn.execute(
                "UPDATE youtube_uploads SET error_message=? WHERE id=?", (message, upload_id))
            ctx.conn.commit()
        ctx.log(f"hậu xử lý chưa trọn vẹn: {message}", level=logging.WARNING)

    ctx.log(f"upload {upload_id} xong -> {youtube_video_id}")
    return {"youtube_video_id": youtube_video_id}
```

- [ ] **Step 4: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_handler_youtube.py -v
```

Kỳ vọng: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/jobqueue/handlers/youtube_upload.py tests/test_jobqueue_handler_youtube.py
git commit -m "feat(queue): add youtube_upload handler with fatal-error classification"
```

---

### Task 10: Handler `light_tts` + cầu SSE

Đây là task rủi ro nhất: endpoint `preview-stream` đang có hợp đồng với JavaScript của
`book_detail.html`. Hợp đồng đó **không được đổi** — handler `emit()` đúng những message
frontend đang chờ, cầu SSE chỉ forward.

**Files:**
- Create: `app/jobqueue/handlers/light_tts.py`
- Modify: `app/routes/text_studio.py` (hàm `preview_stream`, dòng 394–519)
- Test: `tests/test_jobqueue_handler_light_tts.py`
- Test: `tests/test_preview_stream_bridge.py`

**Interfaces:**
- Consumes: `JobContext`, `JobFatalError`, `joblog.read_events`
- Produces:
```python
# app/jobqueue/handlers/light_tts.py
def handle(ctx) -> dict          # {"audio_path": str | None, "ok": int, "failed": int}
def dedupe_key(patch_id: int) -> str        # f"light_tts:patch={patch_id}"
```

- [ ] **Step 1: Viết test cho handler**

Tạo `tests/test_jobqueue_handler_light_tts.py`:

```python
"""Handler light_tts: emit đúng hợp đồng SSE, dùng lại chunk, không gộp khi có lỗ."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app import db, repository
from app.jobqueue import joblog, store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.handlers import light_tts as handler
from app.jobqueue.models import JobFatalError


def _wav_bytes(seconds=0.1, sr=24000):
    import io
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(sr * seconds), dtype="float32"), sr, format="WAV")
    return buf.getvalue()


class _FakeEngine:
    def __init__(self, fail_indices=()):
        self.fail_indices = set(fail_indices)
        self.calls = 0

    def synthesize_to_wav_bytes(self, text, voice=None):
        index = self.calls
        self.calls += 1
        if index in self.fail_indices:
            raise RuntimeError(f"chunk {index} hỏng")
        return _wav_bytes(), 24000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


def _book_with_patch(conn, text="Câu một. Câu hai. Câu ba."):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size,
                              status, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?)""", (now, now))
    conn.execute(
        """INSERT INTO chapter (book_id, chapter_index, title, text, char_count)
           VALUES (1, 0, 'C1', ?, ?)""", (text, len(text)))
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               attempt_count, created_at, updated_at)
           VALUES (1, 0, 0, 0, 'pending', 0, ?, ?)""", (now, now))
    conn.commit()
    return cur.lastrowid


def _ctx(conn, patch_id, **extra):
    payload = {"patch_id": patch_id, "book_id": 1}
    payload.update(extra)
    job_id = store.enqueue(conn, "light_tts", payload=payload, book_id=1)
    job = store.claim(conn, "light_tts", "light_tts#1")
    return JobContext(job, conn, JobLogger(job_id, "light_tts"), lambda: False), job_id


def test_dedupe_key_shape():
    assert handler.dedupe_key(91) == "light_tts:patch=91"


def test_missing_patch_is_fatal(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    ctx, _ = _ctx(conn, 999)
    with pytest.raises(JobFatalError):
        handler.handle(ctx)


def test_emits_one_chunk_event_per_chunk_then_done(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine())
    ctx, job_id = _ctx(conn, patch_id)

    handler.handle(ctx)
    ctx.close()

    events, _ = joblog.read_events(job_id)
    chunks = [e for e in events if e["type"] == "chunk"]
    done = [e for e in events if e["type"] == "done"]
    assert chunks, "không có event chunk nào"
    assert all({"index", "total", "url"} <= set(e) for e in chunks)
    assert len(done) == 1
    assert done[0]["saved"] is True and done[0]["complete"] is True
    assert done[0]["failed"] == 0


def test_a_failed_chunk_blocks_the_merge(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine(fail_indices={1}))
    ctx, job_id = _ctx(conn, patch_id)

    result = handler.handle(ctx)
    ctx.close()

    events, _ = joblog.read_events(job_id)
    assert any(e["type"] == "chunk_error" and e["index"] == 1 for e in events)
    done = [e for e in events if e["type"] == "done"][0]
    assert done["saved"] is False and done["complete"] is False
    assert result["failed"] == 1
    assert repository.get_patch(conn, patch_id).status != "done"


def test_all_chunks_failing_emits_an_error_event(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(
        handler, "_build_engine", lambda b, v: _FakeEngine(fail_indices=range(50)))
    ctx, job_id = _ctx(conn, patch_id)
    handler.handle(ctx)
    ctx.close()
    events, _ = joblog.read_events(job_id)
    assert any(e["type"] == "error" for e in events)


def test_existing_chunks_are_reused_and_flagged(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    engine = _FakeEngine()
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: engine)
    ctx, _ = _ctx(conn, patch_id)
    handler.handle(ctx)
    first = engine.calls

    ctx2, job2 = _ctx(conn, patch_id)
    handler.handle(ctx2)
    ctx2.close()

    assert engine.calls == first, "chunk cũ vẫn bị synth lại"
    events, _ = joblog.read_events(job2)
    assert all(e.get("reused") for e in events if e["type"] == "chunk")


def test_changing_the_voice_invalidates_the_reuse_marker(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    engine = _FakeEngine()
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: engine)
    ctx, _ = _ctx(conn, patch_id, voice="vi-VN-NamMinhNeural")
    handler.handle(ctx)
    first = engine.calls

    ctx2, _ = _ctx(conn, patch_id, voice="vi-VN-HoaiMyNeural")
    handler.handle(ctx2)
    assert engine.calls > first, "đổi voice mà vẫn dùng lại chunk cũ"


def test_progress_tracks_chunks(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine())
    ctx, job_id = _ctx(conn, patch_id)
    handler.handle(ctx)
    ctx.flush()
    job = store.get(conn, job_id)
    assert job.progress_total > 0
    assert job.progress_current == job.progress_total
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_handler_light_tts.py -v
```

Kỳ vọng: FAIL với `ImportError: cannot import name 'light_tts'`.

- [ ] **Step 3: Viết `app/jobqueue/handlers/light_tts.py`**

Đọc `app/routes/text_studio.py:394-549` trước — logic bên dưới là bản port từng dòng của
nó, chỉ thay `yield _sse(...)` bằng `ctx.emit(...)`.

```python
"""Sinh audio cho một patch bằng LightTTS (edge-tts / gTTS). Trần song song 10:
thuần network I/O, không chạm GPU và gần như không chạm CPU.

Port từ routes/text_studio.py::preview_stream._generate(). Mỗi `yield _sse(x)` thành
`ctx.emit(x)` với đúng cùng khóa — cầu SSE ở text_studio.py forward nguyên vẹn nên
JavaScript ở book_detail.html không phải sửa gì.

Giữ nguyên từ bản cũ và đừng đơn giản hóa:
  * marker .light_tts_meta — chỉ dùng lại chunk khi text/split/backend/voice trùng khớp,
    nếu không sẽ gộp lẫn audio từ nhiều nguồn (worker, Drive import) vào một file;
  * ghi next_chunk_index theo tiền tố liên tục — một lần chạy đứt giữa chừng vẫn để lại
    con số khớp với đĩa;
  * không gộp khi còn chunk lỗi — audio có lỗ không bao giờ được lưu."""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

import soundfile as sf

from app import audio_merge, repository
from app.config import settings
from app.jobqueue.models import JobFatalError

logger = logging.getLogger(__name__)

_CHUNK_PAUSE_MS = 300


def dedupe_key(patch_id: int) -> str:
    return f"light_tts:patch={patch_id}"


def _build_engine(backend: str, voice: str):
    """Tách ra để test thay được mà không cần edge-tts thật."""
    from app.light_tts import LightTTSEngine
    return LightTTSEngine(backend=backend, voice=voice or None)


def _synth_with_retries(engine, text: str, voice: str | None) -> bytes:
    attempts = max(1, settings.light_tts_chunk_retries)
    last: Exception | None = None
    for _ in range(attempts):
        try:
            wav_bytes, _sr = engine.synthesize_to_wav_bytes(text, voice)
            return wav_bytes
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last if last else RuntimeError("synthesize thất bại")


def handle(ctx) -> dict:
    payload = ctx.job.payload
    patch_id = payload.get("patch_id")
    if patch_id is None:
        raise JobFatalError("payload thiếu patch_id")
    patch = repository.get_patch(ctx.conn, patch_id)
    if patch is None:
        raise JobFatalError(f"patch {patch_id} không tồn tại")
    book_id = patch.book_id

    backend = payload.get("backend") or settings.light_tts_backend
    voice = payload.get("voice") or settings.light_tts_voice
    max_chars = int(payload.get("max_chars") or 0)
    with_effects = bool(payload.get("with_effects"))
    effective_max_chars = max_chars if max_chars > 0 else (patch.max_chars or settings.tts_max_chars)

    plan_inputs = repository.fetch_patch_chunk_inputs(
        ctx.conn, patch, max_chars=effective_max_chars)
    plan = repository.build_chunk_plan_from_inputs(plan_inputs)
    total = len(plan)
    if total == 0:
        ctx.emit({"type": "error", "message": "Patch này không có chunk nào"})
        raise JobFatalError("patch không có chunk nào")

    data_root = Path(settings.data_root)
    book_dir = data_root / "books" / str(book_id) / "patches"
    book_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = book_dir / f"{patch_id}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    joined = "\n\n".join(item["text"] for item in plan)
    meta_key = hashlib.md5(
        f"{backend}|{voice}|{effective_max_chars}|{joined}".encode("utf-8")
    ).hexdigest()
    meta_path = chunk_dir / ".light_tts_meta"
    try:
        reusable = meta_path.read_text(encoding="utf-8").strip() == meta_key
    except OSError:
        reusable = False
    if not reusable:
        for stale in chunk_dir.glob("chunk_*.wav"):
            stale.unlink(missing_ok=True)
        meta_path.write_text(meta_key, encoding="utf-8")

    if max_chars == 0 and patch.chunk_count != total:
        repository.update_patch_chunk_count(ctx.conn, patch_id, total)

    try:
        engine = _build_engine(backend, voice)
    except RuntimeError as exc:
        ctx.emit({"type": "error", "message": str(exc)})
        raise JobFatalError(str(exc))

    cache_bust = uuid.uuid4().hex[:8]
    ctx.progress(0, total, phase="synthesizing")
    ok_count = 0
    fail_count = 0
    contiguous = 0
    prefix_open = True

    for index, item in enumerate(plan):
        if ctx.should_cancel():
            import asyncio
            raise asyncio.CancelledError()
        chunk_path = chunk_dir / f"chunk_{index:03d}.wav"
        chunk_url = f"/books/{book_id}/patches/{patch_id}/chunk-audio/{index}?v={cache_bust}"
        present = False
        if chunk_path.is_file():
            ok_count += 1
            present = True
            ctx.emit({"type": "chunk", "index": index, "total": total,
                      "url": chunk_url, "reused": True})
        else:
            try:
                wav_bytes = _synth_with_retries(engine, item["text"], voice or None)
            except Exception as exc:  # noqa: BLE001 - một chunk hỏng không được mất cả patch
                fail_count += 1
                ctx.log(f"chunk {index} lỗi: {exc}", level=logging.WARNING)
                ctx.emit({"type": "chunk_error", "index": index, "total": total,
                          "message": str(exc)})
            else:
                chunk_path.write_bytes(wav_bytes)
                ok_count += 1
                present = True
                ctx.emit({"type": "chunk", "index": index, "total": total, "url": chunk_url})

        if prefix_open:
            if present:
                contiguous = index + 1
            else:
                prefix_open = False
            repository.update_patch_chunk_progress(ctx.conn, patch_id, contiguous)
        ctx.progress(index + 1, total)

    if ok_count == 0:
        ctx.emit({"type": "error", "message": "Tất cả chunk đều lỗi, không có audio để lưu"})
        return {"audio_path": None, "ok": 0, "failed": fail_count}

    if fail_count:
        ctx.emit({"type": "done", "saved": False, "complete": False,
                  "ok": ok_count, "failed": fail_count})
        return {"audio_path": None, "ok": ok_count, "failed": fail_count}

    ctx.progress(total, total, phase="merging")
    audio_path = str(book_dir / f"{patch_id}.wav")
    chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(total)]
    audio_merge.concat_wavs(chunk_paths, audio_path, pause_ms=_CHUNK_PAUSE_MS)
    _finish_patch_audio(ctx, plan, chunk_paths, audio_path, patch_id, with_effects)
    ctx.emit({"type": "done", "saved": True, "complete": True, "ok": ok_count, "failed": 0})
    ctx.progress(total, total, phase="done")
    return {"audio_path": audio_path, "ok": ok_count, "failed": 0}


def _finish_patch_audio(ctx, plan, chunk_paths, audio_path, patch_id, with_effects) -> None:
    """Bản port của text_studio._finish_patch_audio, bỏ tham số conn/db_lock."""
    info = sf.info(audio_path)
    chapters, _ = audio_merge.build_chapter_marks(
        plan, [sf.info(p).frames for p in chunk_paths], info.samplerate, _CHUNK_PAUSE_MS)
    audio_merge.try_write_timeline(
        Path(audio_path).with_suffix(".timeline.json"), info.samplerate, chapters, info.frames)

    if with_effects:
        from app.routes.text_studio import _mix_effects
        merged = Path(audio_path).read_bytes()
        mixed = _mix_effects(merged, "\n\n".join(i["text"] for i in plan), ctx.conn)
        Path(audio_path).write_bytes(mixed)

    repository.mark_patch_done(ctx.conn, patch_id, audio_path)
```

- [ ] **Step 4: Chạy test handler, xác nhận pass**

```bash
pytest tests/test_jobqueue_handler_light_tts.py -v
```

Kỳ vọng: 8 passed.

- [ ] **Step 5: Viết test cho cầu SSE**

Tạo `tests/test_preview_stream_bridge.py`:

```python
"""preview-stream giờ enqueue job rồi forward @@EVENT — hợp đồng SSE cũ không đổi."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from app.jobqueue import joblog, store
from app.jobqueue.joblog import JobLogger


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    import threading
    from app.main import app
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size,
                              status, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?)""", (now, now))
    conn.execute(
        """INSERT INTO patch (id, book_id, patch_index, chapter_start, chapter_end,
                               status, attempt_count, created_at, updated_at)
           VALUES (5, 1, 0, 0, 0, 'pending', 0, ?, ?)""", (now, now))
    conn.commit()
    app.state.conn = conn
    app.state.db_lock = threading.Lock()
    app.state.worker = None
    app.state.job_queue = None
    with TestClient(app) as c:
        yield c, conn


def test_preview_stream_enqueues_a_light_tts_job(client):
    c, conn = client
    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream?voice=v1") as resp:
        assert resp.status_code == 200
        next(resp.iter_lines(), None)
    jobs = store.list_jobs(conn, job_type="light_tts")
    assert len(jobs) == 1
    assert jobs[0].payload["patch_id"] == 5
    assert jobs[0].payload["voice"] == "v1"
    assert jobs[0].dedupe_key == "light_tts:patch=5"


def test_a_second_request_attaches_to_the_same_job(client):
    c, conn = client
    for _ in range(2):
        with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp:
            next(resp.iter_lines(), None)
    assert len(store.list_jobs(conn, job_type="light_tts")) == 1


def test_events_written_by_the_handler_reach_the_client(client):
    """Job đã 'done' với event sẵn trong log — stream phải phát lại hết rồi đóng."""
    c, conn = client
    job_id = store.enqueue(
        conn, "light_tts", payload={"patch_id": 5}, book_id=1,
        dedupe_key="light_tts:patch=5")
    lg = JobLogger(job_id, "light_tts")
    lg.emit({"type": "chunk", "index": 0, "total": 2, "url": "/u/0"})
    lg.emit({"type": "chunk", "index": 1, "total": 2, "url": "/u/1"})
    lg.emit({"type": "done", "saved": True, "complete": True, "ok": 2, "failed": 0})
    lg.close()
    store.finish(conn, job_id, {"ok": 2})

    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp:
        payloads = [
            json.loads(line[len("data: "):])
            for line in resp.iter_lines() if line.startswith("data: ")
        ]
    assert [p["type"] for p in payloads] == ["chunk", "chunk", "done"]
    assert payloads[0]["url"] == "/u/0"


def test_a_failed_job_closes_the_stream_with_an_error(client):
    c, conn = client
    job_id = store.enqueue(
        conn, "light_tts", payload={"patch_id": 5}, book_id=1,
        dedupe_key="light_tts:patch=5")
    store.claim(conn, "light_tts", "w")
    store.fail(conn, job_id, "engine không khả dụng", fatal=True)

    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp:
        payloads = [
            json.loads(line[len("data: "):])
            for line in resp.iter_lines() if line.startswith("data: ")
        ]
    assert payloads[-1]["type"] == "error"
    assert "engine không khả dụng" in payloads[-1]["message"]


def test_unknown_patch_still_404s(client):
    c, _ = client
    with c.stream("GET", "/books/1/text-studio/patches/999/preview-stream") as resp:
        assert resp.status_code == 404
```

- [ ] **Step 6: Chạy test cầu SSE, xác nhận fail**

```bash
pytest tests/test_preview_stream_bridge.py -v
```

Kỳ vọng: FAIL — endpoint vẫn đang tự synthesize, chưa tạo job nào.

- [ ] **Step 7: Thay thế `preview_stream` trong `app/routes/text_studio.py`**

Xóa toàn bộ thân hàm cũ (dòng 394–519, tính cả `_generate`) và thay bằng:

```python
@router.get("/books/{book_id}/text-studio/patches/{patch_id}/preview-stream")
async def preview_stream(
    request: Request,
    book_id: int,
    patch_id: int,
    backend: str = "",
    voice: str = "",
    with_effects: int = 0,
    max_chars: int = 0,
):
    """Cửa sổ xem tiến độ của một job light_tts.

    Trước đây endpoint này tự synthesize trong request, nên đóng tab là job chết. Giờ
    nó chỉ enqueue (hoặc gắn vào job đang chạy) rồi tail file log của job, forward mọi
    dòng @@EVENT. Hợp đồng với JavaScript không đổi: vẫn là chuỗi
    {type: chunk|chunk_error|done|error}."""
    from app.jobqueue import joblog, store
    from app.jobqueue.handlers.light_tts import dedupe_key
    from app.jobqueue.models import TERMINAL_STATUSES

    with locked_conn(request) as conn:
        _require_patch(conn, book_id, patch_id)

    key = dedupe_key(patch_id)
    payload = {
        "patch_id": patch_id, "book_id": book_id,
        "backend": backend, "voice": voice,
        "max_chars": max_chars, "with_effects": with_effects,
    }
    with locked_conn(request) as conn:
        existing = store.find_live_by_dedupe(conn, key)
        if existing is not None:
            job_id = existing.id
        else:
            job_id = store.enqueue(
                conn, "light_tts", payload=payload, book_id=book_id, dedupe_key=key)
            if job_id is None:            # thua cuộc đua với một request khác
                live = store.find_live_by_dedupe(conn, key)
                job_id = live.id if live else None

    if job_id is None:
        async def _bail():
            yield _sse({"type": "error", "message": "Không tạo được job LightTTS"})
        return StreamingResponse(_bail(), media_type="text/event-stream")

    async def _bridge():
        cursor = 0
        # Job có thể đã chạy xong trước khi client kết nối (mở lại trang). Vòng lặp
        # phát lại toàn bộ event từ đầu rồi mới xét trạng thái, nên không mất event nào.
        while True:
            events, cursor = await asyncio.to_thread(
                joblog.read_events, job_id, from_line=cursor)
            for event in events:
                yield _sse(event)
            with locked_conn(request) as conn:
                job = store.get(conn, job_id)
            if job is None:
                return
            if job.status in TERMINAL_STATUSES:
                # Đọc nốt event ghi ra giữa lần đọc trước và lúc job kết thúc.
                tail_events, _ = await asyncio.to_thread(
                    joblog.read_events, job_id, from_line=cursor)
                for event in tail_events:
                    yield _sse(event)
                if job.status == "failed":
                    yield _sse({"type": "error",
                                "message": job.error_message or "job thất bại"})
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(_bridge(), media_type="text/event-stream")
```

Giữ nguyên `_sse`, `_require_patch`, `_book_patch_dir`, `_chunk_dir`, `_mix_effects` —
handler và các endpoint khác vẫn dùng. Xóa `_finish_patch_audio` khỏi `text_studio.py`
**chỉ khi** không còn chỗ nào gọi:

```bash
grep -rn "_finish_patch_audio" app/
```

- [ ] **Step 8: Chạy cả hai file test, xác nhận pass**

```bash
pytest tests/test_preview_stream_bridge.py tests/test_jobqueue_handler_light_tts.py tests/test_light_tts.py tests/test_text_studio.py -v
```

Kỳ vọng: tất cả pass. `test_light_tts.py` và `test_text_studio.py` là test có sẵn — nếu
chúng fail thì hợp đồng đã bị phá, sửa code chứ đừng sửa test.

- [ ] **Step 9: Commit**

```bash
git add app/jobqueue/handlers/light_tts.py app/routes/text_studio.py tests/test_jobqueue_handler_light_tts.py tests/test_preview_stream_bridge.py
git commit -m "feat(queue): move LightTTS into the queue, preview-stream becomes an SSE bridge"
```

---

### Task 11: Backfill + dựng queue trong lifespan

**Files:**
- Create: `app/jobqueue/backfill.py`
- Modify: `app/main.py` (hàm `lifespan`, dòng 38–138)
- Test: `tests/test_jobqueue_backfill.py`

**Interfaces:**
- Consumes: `store.enqueue`, `parse_concurrency`, bốn handler
- Produces:
```python
# app/jobqueue/backfill.py
def backfill_pending_jobs(conn) -> dict[str, int]     # {'voxcpm_tts': n, 'video': n, 'youtube_upload': n}
def build_queue(conn_factory) -> JobQueue             # đã register đủ 4 handler
```
- `app.state.job_queue` là `JobQueue`; `app.state.worker` trỏ vào cùng object (tương thích ngược)

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_jobqueue_backfill.py`:

```python
"""Backfill: công việc tồn đọng ở các bảng cũ được xếp vào queue, chạy lại vô hại."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db, repository
from app.jobqueue import store
from app.jobqueue.backfill import backfill_pending_jobs, build_queue


def _conn(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size,
                              status, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', ?, ?)""", (now, now))
    conn.commit()
    return conn


def _patch(conn, status="pending", index=0):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               attempt_count, created_at, updated_at)
           VALUES (1, ?, 0, 0, ?, 0, ?, ?)""", (index, status, now, now))
    conn.commit()
    return cur.lastrowid


def test_pending_patches_become_voxcpm_jobs(tmp_path):
    conn = _conn(tmp_path)
    p1 = _patch(conn, index=0)
    _patch(conn, status="done", index=1)
    counts = backfill_pending_jobs(conn)
    assert counts["voxcpm_tts"] == 1
    jobs = store.list_jobs(conn, job_type="voxcpm_tts")
    assert jobs[0].payload["patch_id"] == p1
    assert jobs[0].dedupe_key == f"voxcpm_tts:patch={p1}"
    assert jobs[0].book_id == 1


def test_pending_book_jobs_become_video_jobs(tmp_path):
    conn = _conn(tmp_path)
    bj = repository.enqueue_book_job(conn, 1, "video")
    counts = backfill_pending_jobs(conn)
    assert counts["video"] == 1
    assert store.list_jobs(conn, job_type="video")[0].payload["book_job_id"] == bj.id


def test_pending_uploads_become_youtube_jobs(tmp_path):
    conn = _conn(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO youtube_uploads (video_path, title, description, tags,
                                         privacy_status, status, created_at)
           VALUES ('/tmp/v.mp4', 'T', 'D', '', 'private', 'pending', ?)""", (now,))
    conn.commit()
    counts = backfill_pending_jobs(conn)
    assert counts["youtube_upload"] == 1
    assert store.list_jobs(conn, job_type="youtube_upload")[0].payload["upload_id"] == cur.lastrowid


def test_running_it_twice_creates_nothing_new(tmp_path):
    conn = _conn(tmp_path)
    _patch(conn)
    repository.enqueue_book_job(conn, 1, "video")
    first = backfill_pending_jobs(conn)
    second = backfill_pending_jobs(conn)
    assert second == {"voxcpm_tts": 0, "video": 0, "youtube_upload": 0}
    assert len(store.list_jobs(conn)) == sum(first.values())


def test_a_finished_job_does_not_block_a_new_backfill(tmp_path):
    """Patch được retry sau khi job cũ đã done — phải xếp job mới."""
    conn = _conn(tmp_path)
    patch_id = _patch(conn)
    backfill_pending_jobs(conn)
    job = store.list_jobs(conn, job_type="voxcpm_tts")[0]
    store.finish(conn, job.id, None)
    assert backfill_pending_jobs(conn)["voxcpm_tts"] == 1


def test_build_queue_registers_all_four_handlers(tmp_path):
    conn = _conn(tmp_path)
    queue = build_queue(lambda: db.connect(str(tmp_path / "a.db")))
    assert queue.capacity("voxcpm_tts") == 1
    assert queue.capacity("video") == 2
    assert queue.capacity("youtube_upload") == 1
    assert queue.capacity("light_tts") == 10
    assert {p["job_type"] for p in queue.pool_status()} == {
        "voxcpm_tts", "video", "youtube_upload", "light_tts"}
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_jobqueue_backfill.py -v
```

Kỳ vọng: FAIL với `ModuleNotFoundError: No module named 'app.jobqueue.backfill'`.

- [ ] **Step 3: Viết `app/jobqueue/backfill.py`**

```python
"""Dựng queue và kéo công việc tồn đọng của các bảng cũ vào bảng job.

backfill là cách duy nhất công việc đang dở chuyển sang hệ mới — không có script
migrate riêng. Nó chạy mọi lần boot và vô hại khi chạy lại: unique index trên
dedupe_key khiến enqueue trùng trả về None thay vì tạo bản sao."""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from app.config import settings
from app.jobqueue import store
from app.jobqueue.handlers import light_tts, video, voxcpm_tts, youtube_upload
from app.jobqueue.runner import JobQueue, parse_concurrency

logger = logging.getLogger(__name__)


def build_queue(
    conn_factory: Callable[[], sqlite3.Connection], *, enable_voxcpm: bool = True
) -> JobQueue:
    """`enable_voxcpm=False` (từ settings.enable_worker) chỉ đặt trần của voxcpm_tts về 0.
    Ba loại còn lại vẫn chạy: cờ đó có nghĩa "máy này không có GPU", chứ không phải
    "đừng chạy gì cả". Trần 0 làm dispatcher của loại đó không bao giờ claim được job,
    nhưng job vẫn xếp hàng bình thường và sẽ chạy khi bật lại."""
    from app import repository

    concurrency = parse_concurrency(
        settings.queue_concurrency, default=settings.queue_default_concurrency)
    if not enable_voxcpm:
        concurrency["voxcpm_tts"] = 0

    queue = JobQueue(
        conn_factory,
        concurrency=concurrency,
        default_concurrency=settings.queue_default_concurrency,
        poll_interval=settings.worker_poll_interval,
        reap_after_seconds=settings.queue_reap_after_seconds,
        is_paused=repository.is_queue_paused,
    )
    queue.register("voxcpm_tts", voxcpm_tts.handle)
    queue.register("video", video.handle)
    queue.register("youtube_upload", youtube_upload.handle, cancellable=False)
    queue.register("light_tts", light_tts.handle)
    return queue


def backfill_pending_jobs(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"voxcpm_tts": 0, "video": 0, "youtube_upload": 0}

    for row in conn.execute(
        "SELECT id, book_id FROM patch WHERE status='pending' ORDER BY book_id, patch_index"
    ).fetchall():
        if store.enqueue(
            conn, "voxcpm_tts",
            payload={"patch_id": row["id"]},
            book_id=row["book_id"],
            dedupe_key=f"voxcpm_tts:patch={row['id']}",
        ) is not None:
            counts["voxcpm_tts"] += 1

    for row in conn.execute(
        "SELECT id, book_id FROM book_job WHERE status='pending' AND job_type='video' ORDER BY id"
    ).fetchall():
        if store.enqueue(
            conn, "video",
            payload={"book_job_id": row["id"]},
            book_id=row["book_id"],
            dedupe_key=f"video:book_job={row['id']}",
        ) is not None:
            counts["video"] += 1

    for row in conn.execute(
        "SELECT id FROM youtube_uploads WHERE status='pending' ORDER BY id"
    ).fetchall():
        if store.enqueue(
            conn, "youtube_upload",
            payload={"upload_id": row["id"]},
            dedupe_key=f"youtube_upload:upload={row['id']}",
        ) is not None:
            counts["youtube_upload"] += 1

    return counts
```

- [ ] **Step 4: Sửa `lifespan` trong `app/main.py`**

Thay khối dựng worker (dòng 79–113 của bản hiện tại) bằng:

```python
    db_lock = threading.Lock()
    app.state.conn = conn
    app.state.db_lock = db_lock

    # Công việc tồn đọng ở patch/book_job/youtube_uploads được kéo vào bảng job.
    # Chạy sau requeue_stuck_* để những dòng vừa được trả về 'pending' cũng được xếp hàng.
    backfilled = backfill_pending_jobs(conn)
    if any(backfilled.values()):
        logging.info(
            "event=queue.backfill voxcpm_tts=%s video=%s youtube_upload=%s",
            backfilled["voxcpm_tts"], backfilled["video"], backfilled["youtube_upload"],
        )

    removed = joblog.purge_old_logs(conn)
    if removed:
        logging.info("event=queue.log_purge removed=%s", removed)

    # Queue LUÔN được dựng. enable_worker chỉ tắt riêng VoxCPM — đó là ý nghĩa gốc của
    # cờ này ("máy dev không có GPU"), và main.py cũ có comment nói rõ nó cố ý không
    # chặn upload queue. Gộp cả hai vào một cờ sẽ tái tạo đúng cái bug từng làm
    # youtube_uploads không bao giờ được rút.
    job_queue = build_queue(
        lambda: db.connect(settings.db_path),
        enable_voxcpm=settings.enable_worker,
    )
    await job_queue.start()
    logging.info(
        "event=queue.config %s",
        " ".join(f"{p['job_type']}={p['capacity']}" for p in job_queue.pool_status()),
    )
    # app.state.worker giữ tên cũ: /health và routes/queue.py đọc qua nó.
    app.state.job_queue = job_queue
    app.state.worker = job_queue
    # Không còn UploadWorker riêng — upload là một loại job như mọi loại khác.
    app.state.upload_worker = None

    try:
        yield
    finally:
        if job_queue is not None:
            await job_queue.stop(timeout=settings.worker_shutdown_timeout_seconds)
        conn.close()
```

Sửa import ở đầu file: bỏ `from app.upload_worker import init_worker`, bỏ
`from app.worker import PatchWorker`, bỏ `from app.tts_engine import VoxCPMEngine`, bỏ
`from app.youtube import is_configured as youtube_is_configured`; thêm:

```python
from app.jobqueue import joblog
from app.jobqueue.backfill import backfill_pending_jobs, build_queue
```

Chú ý: `enable_worker=false` **chỉ** tắt VoxCPM, đúng như ý nghĩa gốc của cờ ("máy dev
không có GPU"). Video, upload YouTube và LightTTS vẫn chạy. Bản `main.py` cũ có comment
nói rõ cờ này cố ý không chặn upload queue — gộp cả hai sẽ tái tạo đúng cái bug từng
làm `youtube_uploads` không bao giờ được rút. Thêm test khẳng định điều này:

```python
def test_enable_worker_false_only_disables_voxcpm(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "enable_worker", False)
    queue = build_queue(lambda: db.connect(str(tmp_path / "a.db")), enable_voxcpm=False)
    caps = {p["job_type"]: p["capacity"] for p in queue.pool_status()}
    assert caps["voxcpm_tts"] == 0
    assert caps["video"] == 2
    assert caps["youtube_upload"] == 1
    assert caps["light_tts"] == 10
```

- [ ] **Step 5: Chạy test, xác nhận pass**

```bash
pytest tests/test_jobqueue_backfill.py -v
```

Kỳ vọng: 6 passed.

- [ ] **Step 6: Chạy các test đụng tới lifespan**

```bash
pytest tests/test_health_endpoint.py tests/test_upload_worker.py tests/test_pause_flag.py tests/test_video_job.py -v
```

Một số sẽ fail vì chúng dựng `PatchWorker`/`UploadWorker` trực tiếp — đó là việc của
Task 13. Ghi lại danh sách fail để đối chiếu.

- [ ] **Step 7: Commit**

```bash
git add app/jobqueue/backfill.py app/main.py tests/test_jobqueue_backfill.py
git commit -m "feat(queue): wire JobQueue into lifespan with startup backfill"
```

---

### Task 12: API job, `/health` mở rộng, trang `/queue`

**Files:**
- Modify: `app/routes/queue.py`
- Create: `app/templates/queue.html`
- Modify: `app/templates/base.html` (thêm link vào menu)
- Test: `tests/test_queue_routes.py`

**Interfaces:**
- Consumes: `store.list_jobs/get/request_cancel/retry/counts`, `joblog.tail/read_events`,
  `app.state.job_queue.pool_status()`
- Produces các route:
  `GET /queue`, `GET /queue/jobs`, `GET /queue/jobs/{id}`, `GET /queue/jobs/{id}/log`,
  `GET /queue/jobs/{id}/stream`, `POST /queue/jobs/{id}/cancel`, `POST /queue/jobs/{id}/retry`

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_queue_routes.py`:

```python
"""API job: list/detail/log/cancel/retry, và /health giữ nguyên khóa cũ."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from app.jobqueue import store
from app.jobqueue.joblog import JobLogger


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


class _FakeQueue:
    state = "idle"
    current_patch_id = None
    current_chunk_index = 0
    current_chunk_count = 0

    def __init__(self):
        self.last_heartbeat_at = datetime.now(timezone.utc).isoformat()
        self.cancelled: list[int] = []

    def pool_status(self):
        return [
            {"job_type": "video", "capacity": 2, "running": 1, "pending": 3},
            {"job_type": "light_tts", "capacity": 10, "running": 0, "pending": 0},
        ]

    def request_cancel(self, job_id):
        self.cancelled.append(job_id)


@pytest.fixture
def client(tmp_path):
    from app.main import app
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    app.state.conn = conn
    app.state.db_lock = threading.Lock()
    queue = _FakeQueue()
    app.state.worker = queue
    app.state.job_queue = queue
    with TestClient(app) as c:
        yield c, conn, queue


def test_list_jobs_returns_json(client):
    c, conn, _ = client
    store.enqueue(conn, "video", payload={"book_job_id": 1}, book_id=7)
    body = c.get("/queue/jobs").json()
    assert body["jobs"][0]["job_type"] == "video"
    assert body["jobs"][0]["book_id"] == 7
    assert body["jobs"][0]["payload"] == {"book_job_id": 1}


def test_list_jobs_filters(client):
    c, conn, _ = client
    store.enqueue(conn, "video", book_id=1)
    store.enqueue(conn, "light_tts", book_id=2)
    assert len(c.get("/queue/jobs?type=video").json()["jobs"]) == 1
    assert len(c.get("/queue/jobs?book_id=2").json()["jobs"]) == 1
    assert len(c.get("/queue/jobs?status=pending").json()["jobs"]) == 2


def test_job_detail(client):
    c, conn, _ = client
    job_id = store.enqueue(conn, "video")
    body = c.get(f"/queue/jobs/{job_id}").json()
    assert body["id"] == job_id
    assert body["status"] == "pending"


def test_unknown_job_is_404(client):
    c, _, _ = client
    assert c.get("/queue/jobs/12345").status_code == 404


def test_job_log_is_plain_text(client):
    c, conn, _ = client
    job_id = store.enqueue(conn, "video")
    lg = JobLogger(job_id, "video")
    lg.log("dòng thứ nhất")
    lg.close()
    resp = c.get(f"/queue/jobs/{job_id}/log")
    assert resp.status_code == 200
    assert "dòng thứ nhất" in resp.text


def test_cancel_a_pending_job(client):
    c, conn, queue = client
    job_id = store.enqueue(conn, "video")
    assert c.post(f"/queue/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert store.get(conn, job_id).status == "cancelled"
    assert queue.cancelled == [job_id]


def test_cancel_a_running_job_reports_cancelling(client):
    c, conn, _ = client
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    assert c.post(f"/queue/jobs/{job_id}/cancel").json()["status"] == "cancelling"


def test_retry_a_failed_job(client):
    c, conn, _ = client
    job_id = store.enqueue(conn, "video", max_attempts=1)
    store.claim(conn, "video", "w")
    store.fail(conn, job_id, "bùm")
    assert c.post(f"/queue/jobs/{job_id}/retry").json()["retried"] is True
    assert store.get(conn, job_id).status == "pending"


def test_retry_a_running_job_is_409(client):
    c, conn, _ = client
    job_id = store.enqueue(conn, "video")
    store.claim(conn, "video", "w")
    assert c.post(f"/queue/jobs/{job_id}/retry").status_code == 409


def test_health_keeps_every_legacy_key(client):
    c, _, _ = client
    body = c.get("/health").json()
    for key in ("status", "worker_state", "current_patch_id", "current_chunk_index",
                "current_chunk_count", "queue_depth", "last_heartbeat_at"):
        assert key in body, f"thiếu khóa cũ: {key}"


def test_health_adds_pool_status(client):
    c, _, _ = client
    pools = {p["job_type"]: p for p in c.get("/health").json()["pools"]}
    assert pools["video"] == {"job_type": "video", "capacity": 2, "running": 1, "pending": 3}


def test_queue_page_renders(client):
    c, conn, _ = client
    store.enqueue(conn, "video", book_id=1)
    resp = c.get("/queue")
    assert resp.status_code == 200
    assert "video" in resp.text


def test_queue_stats_keeps_its_old_shape(client):
    c, _, _ = client
    body = c.get("/queue/stats").json()
    assert "patch" in body and "book_job" in body
    assert "jobs" in body
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_queue_routes.py -v
```

Kỳ vọng: nhiều FAIL với 404 vì các route chưa tồn tại.

- [ ] **Step 3: Thêm route vào `app/routes/queue.py`**

Giữ nguyên mọi route đang có. Thêm import và các route mới:

```python
import asyncio
import json

from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.jobqueue import joblog, store
from app.jobqueue.models import TERMINAL_STATUSES

templates = Jinja2Templates(directory="app/templates")

_JOB_FIELDS = (
    "id", "job_type", "status", "priority", "book_id", "phase", "progress_current",
    "progress_total", "error_message", "attempt_count", "max_attempts", "worker_id",
    "created_at", "started_at", "finished_at", "updated_at",
)


def _job_dict(job) -> dict:
    data = {name: getattr(job, name) for name in _JOB_FIELDS}
    data["payload"] = job.payload
    data["result"] = job.result
    percent = 0
    if job.progress_total:
        percent = min(100, round(job.progress_current * 100 / job.progress_total))
    data["percent"] = percent
    return data


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    queue = getattr(request.app.state, "job_queue", None)
    with locked_conn(request) as conn:
        jobs = [_job_dict(j) for j in store.list_jobs(conn, limit=200)]
    return templates.TemplateResponse(
        request, "queue.html",
        {"jobs": jobs, "pools": queue.pool_status() if queue else []},
    )


@router.get("/queue/jobs")
def list_jobs(
    request: Request, type: str = "", status: str = "", book_id: int | None = None,
    limit: int = 100,
):
    with locked_conn(request) as conn:
        jobs = store.list_jobs(
            conn, job_type=type or None, status=status or None,
            book_id=book_id, limit=limit,
        )
    return {"jobs": [_job_dict(j) for j in jobs]}


@router.get("/queue/jobs/{job_id}")
def job_detail(request: Request, job_id: int):
    with locked_conn(request) as conn:
        job = store.get(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} không tồn tại")
    return _job_dict(job)


@router.get("/queue/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(request: Request, job_id: int, tail: int = 500):
    with locked_conn(request) as conn:
        if store.get(conn, job_id) is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} không tồn tại")
    return joblog.tail(job_id, lines=tail)


@router.get("/queue/jobs/{job_id}/stream")
async def job_stream(request: Request, job_id: int):
    """SSE tiến độ + @@EVENT cho một job bất kỳ. Trang /queue dùng cái này để hiện
    thanh tiến độ realtime mà không phải poll cả danh sách."""
    with locked_conn(request) as conn:
        if store.get(conn, job_id) is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} không tồn tại")

    async def _stream():
        cursor = 0
        while True:
            events, cursor = await asyncio.to_thread(joblog.read_events, job_id, from_line=cursor)
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            with locked_conn(request) as conn:
                job = store.get(conn, job_id)
            if job is None:
                return
            yield f"data: {json.dumps({'type': 'progress', **_job_dict(job)}, ensure_ascii=False, default=str)}\n\n"
            if job.status in TERMINAL_STATUSES or await request.is_disconnected():
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/queue/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: int):
    with locked_conn(request) as conn:
        new_status = store.request_cancel(conn, job_id)
    if new_status is None:
        raise HTTPException(status_code=409, detail="job đã kết thúc hoặc không tồn tại")
    queue = getattr(request.app.state, "job_queue", None)
    if queue is not None:
        queue.request_cancel(job_id)
    logger.info("event=queue.cancel job_id=%s status=%s", job_id, new_status)
    return {"job_id": job_id, "status": new_status}


@router.post("/queue/jobs/{job_id}/retry")
def retry_job(request: Request, job_id: int):
    with locked_conn(request) as conn:
        ok = store.retry(conn, job_id)
    if not ok:
        raise HTTPException(
            status_code=409, detail="chỉ retry được job đã kết thúc")
    logger.info("event=queue.retry job_id=%s", job_id)
    return {"job_id": job_id, "retried": True}
```

Sửa `health()` — thêm `pools` vào **cả hai** nhánh trả về (nhánh 503 và nhánh 200), và
vào nhánh `worker is None`:

```python
def _pools(worker) -> list[dict]:
    if worker is None or not hasattr(worker, "pool_status"):
        return []
    try:
        return worker.pool_status()
    except Exception:      # noqa: BLE001 - /health không được sập vì lỗi phụ
        return []
```

rồi thêm `"pools": _pools(worker)` vào mỗi dict trả về của `health()`.

Sửa `queue_stats()`:

```python
@router.get("/queue/stats")
def queue_stats(request: Request):
    with locked_conn(request) as conn:
        stats = repository.get_queue_stats(conn)
        stats["jobs"] = store.counts(conn)
    return stats
```

- [ ] **Step 4: Tạo `app/templates/queue.html`**

Xem `app/templates/logs.html` để lấy đúng block layout của repo. Nội dung:

```html
{% extends "base.html" %}
{% block content %}
<h1>Hàng đợi job</h1>

<div class="pools">
  {% for pool in pools %}
    <span class="pool">
      <strong>{{ pool.job_type }}</strong>
      {{ pool.running }}/{{ pool.capacity }} đang chạy · {{ pool.pending }} chờ
    </span>
  {% endfor %}
</div>

<div class="filters">
  <label>Loại
    <select id="f-type">
      <option value="">tất cả</option>
      <option value="voxcpm_tts">voxcpm_tts</option>
      <option value="light_tts">light_tts</option>
      <option value="video">video</option>
      <option value="youtube_upload">youtube_upload</option>
    </select>
  </label>
  <label>Trạng thái
    <select id="f-status">
      <option value="">tất cả</option>
      <option value="pending">pending</option>
      <option value="running">running</option>
      <option value="done">done</option>
      <option value="failed">failed</option>
      <option value="cancelled">cancelled</option>
    </select>
  </label>
  <label><input type="checkbox" id="f-auto" checked> Tự cập nhật</label>
</div>

<table class="static-table" id="jobs">
  <thead>
    <tr>
      <th>ID</th><th>Loại</th><th>Sách</th><th>Trạng thái</th>
      <th>Tiến độ</th><th>Lần thử</th><th>Lỗi</th><th></th>
    </tr>
  </thead>
  <tbody></tbody>
</table>

<pre id="log-view" hidden></pre>

<script>
const JOBS = {{ jobs | tojson }};
// youtube_upload không hỗ trợ hủy giữa chừng: transfer đã bắt đầu thì phải chạy hết.
const NOT_CANCELLABLE = new Set(["youtube_upload"]);
const TERMINAL = new Set(["done", "failed", "cancelled"]);

function render(jobs) {
  const body = document.querySelector("#jobs tbody");
  body.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    const bar = job.progress_total
      ? `<progress value="${job.progress_current}" max="${job.progress_total}"></progress> ${job.percent}%`
      : "—";
    const canCancel = !TERMINAL.has(job.status) && !NOT_CANCELLABLE.has(job.job_type);
    const canRetry = TERMINAL.has(job.status);
    tr.innerHTML = `
      <td>${job.id}</td>
      <td>${job.job_type}</td>
      <td>${job.book_id ?? ""}</td>
      <td>${job.status}${job.phase ? " / " + job.phase : ""}</td>
      <td>${bar}</td>
      <td>${job.attempt_count}/${job.max_attempts}</td>
      <td title="${job.error_message ?? ""}">${(job.error_message ?? "").slice(0, 60)}</td>
      <td>
        <button data-log="${job.id}">Log</button>
        <button data-cancel="${job.id}" ${canCancel ? "" : "disabled"}>Hủy</button>
        <button data-retry="${job.id}" ${canRetry ? "" : "disabled"}>Chạy lại</button>
      </td>`;
    body.appendChild(tr);
  }
}

async function refresh() {
  const params = new URLSearchParams();
  const type = document.getElementById("f-type").value;
  const status = document.getElementById("f-status").value;
  if (type) params.set("type", type);
  if (status) params.set("status", status);
  const resp = await fetch("/queue/jobs?" + params.toString());
  render((await resp.json()).jobs);
}

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (target.dataset.log) {
    const view = document.getElementById("log-view");
    view.hidden = false;
    view.textContent = await (await fetch(`/queue/jobs/${target.dataset.log}/log`)).text();
  } else if (target.dataset.cancel) {
    await fetch(`/queue/jobs/${target.dataset.cancel}/cancel`, {method: "POST"});
    refresh();
  } else if (target.dataset.retry) {
    await fetch(`/queue/jobs/${target.dataset.retry}/retry`, {method: "POST"});
    refresh();
  }
});

document.getElementById("f-type").addEventListener("change", refresh);
document.getElementById("f-status").addEventListener("change", refresh);
setInterval(() => {
  if (document.getElementById("f-auto").checked) refresh();
}, 2000);

render(JOBS);
</script>
{% endblock %}
```

Kiểm tra tên block: nếu `base.html` dùng tên khác `content`, sửa cho khớp.

```bash
grep -n "{% block" app/templates/base.html
```

- [ ] **Step 5: Thêm link vào `app/templates/base.html`**

Cạnh link `/logs` đang có, thêm `<a href="/queue">Hàng đợi</a>`.

- [ ] **Step 6: Chạy test, xác nhận pass**

```bash
pytest tests/test_queue_routes.py tests/test_health_endpoint.py tests/test_queue_stats.py -v
```

Kỳ vọng: tất cả pass. Nếu `test_health_endpoint.py` fail vì thiếu khóa, đó là lỗi thật —
sửa `health()` chứ đừng sửa test.

- [ ] **Step 7: Commit**

```bash
git add app/routes/queue.py app/templates/queue.html app/templates/base.html tests/test_queue_routes.py
git commit -m "feat(queue): add job API, /queue page and pool status on /health"
```

---

### Task 13: Nối các nút admin có sẵn vào bảng `job`

Bốn route đang đặt lại bảng nghiệp vụ rồi trông chờ vòng lặp cũ nhặt lên. Vòng lặp đó
sắp biến mất, nên chúng phải tự enqueue job. Không đổi đường dẫn, không đổi kiểu trả về.

**Files:**
- Modify: `app/routes/queue.py` (`requeue_stuck`, `retry_failed_patches`, `regenerate_video`, `reset_all_jobs`)
- Test: `tests/test_admin_routes_enqueue.py`

**Interfaces:**
- Consumes: `backfill_pending_jobs` (Task 11), `store.enqueue`, `repository.*` (đã có)
- Produces: không có API mới — chỉ thêm tác dụng phụ enqueue vào các route sẵn có

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_admin_routes_enqueue.py`:

```python
"""Nút admin phải sinh job, nếu không bấm xong sẽ không có gì chạy."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, repository
from app.jobqueue import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


@pytest.fixture
def client(tmp_path):
    from app.main import app
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                              final_audio_path, background_image_path, created_at, updated_at)
           VALUES (1, 'Sách', 'a.epub', '/tmp/a.epub', 10, 'ready', '/tmp/f.wav',
                   '/tmp/bg.jpg', ?, ?)""", (now, now))
    conn.commit()
    app.state.conn = conn
    app.state.db_lock = threading.Lock()
    app.state.worker = None
    app.state.job_queue = None
    with TestClient(app) as c:
        yield c, conn


def _patch(conn, status, index=0):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               error_message, attempt_count, created_at, updated_at)
           VALUES (1, ?, 0, 0, ?, 'cũ', 0, ?, ?)""", (index, status, now, now))
    conn.commit()
    return cur.lastrowid


def test_requeue_stuck_enqueues_jobs_for_the_revived_patches(client):
    c, conn = client
    patch_id = _patch(conn, "processing")
    resp = c.post("/queue/requeue-stuck")
    assert resp.json()["requeued"] == 1
    jobs = store.list_jobs(conn, job_type="voxcpm_tts")
    assert [j.payload["patch_id"] for j in jobs] == [patch_id]


def test_retry_failed_patches_enqueues_jobs(client):
    c, conn = client
    patch_id = _patch(conn, "failed")
    c.post("/books/1/patches/retry-failed", follow_redirects=False)
    assert repository.get_patch(conn, patch_id).status == "pending"
    assert store.list_jobs(conn, job_type="voxcpm_tts")[0].payload["patch_id"] == patch_id


def test_regenerate_video_enqueues_a_video_job(client):
    c, conn = client
    c.post("/books/1/video/regenerate", follow_redirects=False)
    book_job = repository.get_book_job(conn, 1, "video")
    jobs = store.list_jobs(conn, job_type="video")
    assert len(jobs) == 1
    assert jobs[0].payload["book_job_id"] == book_job.id


def test_regenerate_replaces_a_stale_job_instead_of_being_blocked_by_dedupe(client):
    """book_job cũ bị xóa và tạo lại với id mới, nên dedupe_key cũng khác — nhưng job
    cũ vẫn còn ở 'pending' và sẽ trỏ vào một book_job không còn tồn tại."""
    c, conn = client
    c.post("/books/1/video/regenerate", follow_redirects=False)
    first = store.list_jobs(conn, job_type="video")[0]
    c.post("/books/1/video/regenerate", follow_redirects=False)
    assert store.get(conn, first.id).status == "cancelled"
    live = [j for j in store.list_jobs(conn, job_type="video") if j.status == "pending"]
    assert len(live) == 1
    assert live[0].payload["book_job_id"] == repository.get_book_job(conn, 1, "video").id


def test_reset_all_clears_every_job_row(client):
    c, conn = client
    _patch(conn, "failed")
    store.enqueue(conn, "video", dedupe_key="video:book_job=1")
    summary = c.post("/queue/reset-all").json()
    assert summary["jobs_cleared"] >= 1
    # Sau reset, chỉ còn job vừa được backfill cho các patch pending.
    assert {j.job_type for j in store.list_jobs(conn)} <= {"voxcpm_tts", "video"}
    assert all(j.status == "pending" for j in store.list_jobs(conn))
```

- [ ] **Step 2: Chạy test, xác nhận fail**

```bash
pytest tests/test_admin_routes_enqueue.py -v
```

Kỳ vọng: FAIL — các route đặt lại bảng nghiệp vụ nhưng `store.list_jobs` trả về rỗng.

- [ ] **Step 3: Sửa `requeue_stuck` trong `app/routes/queue.py`**

```python
@router.post("/queue/requeue-stuck")
def requeue_stuck(request: Request):
    """Operator escape hatch: flip every 'processing' patch back to 'pending' without
    discarding next_chunk_index, rồi xếp lại job cho chúng."""
    with locked_conn(request) as conn:
        resumed = repository.requeue_stuck_processing_returning(conn)
        repository.requeue_stuck_book_jobs(conn)
        backfilled = backfill_pending_jobs(conn)
    logger.info(
        "event=queue.requeue_stuck count=%s jobs=%s", len(resumed), sum(backfilled.values()))
    return {"requeued": len(resumed), "patches": resumed}
```

Thêm `from app.jobqueue.backfill import backfill_pending_jobs` vào đầu file.

- [ ] **Step 4: Sửa `retry_failed_patches`**

```python
@router.post("/books/{book_id}/patches/retry-failed")
def retry_failed_patches(request: Request, book_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        n = repository.retry_all_failed_patches_for_book(conn, book_id)
        backfilled = backfill_pending_jobs(conn)
    logger.info("retry_all_failed book_id=%s reset=%s jobs=%s",
                book_id, n, backfilled["voxcpm_tts"])
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)
```

`backfill_pending_jobs` quét toàn bộ bảng chứ không riêng sách này. Cố ý: nó rẻ (một
truy vấn có index), idempotent, và tránh nhân bản logic enqueue ở bốn chỗ.

- [ ] **Step 5: Sửa `regenerate_video`**

```python
@router.post("/books/{book_id}/video/regenerate")
def regenerate_video(request: Request, book_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail=f"book {book_id} not found")
        existing = repository.get_book_job(conn, book_id, "video")
        if existing is not None and existing.status == "processing":
            raise HTTPException(
                status_code=409,
                detail="a video job for this book is already processing; wait for it to finish",
            )
        if existing is not None:
            # Job queue cũ trỏ vào book_job sắp bị xóa — hủy nó, nếu không nó sẽ chạy
            # rồi fail với "book_job không tồn tại" và ăn hết lượt retry.
            stale = store.find_live_by_dedupe(conn, f"video:book_job={existing.id}")
            if stale is not None:
                store.request_cancel(conn, stale.id)
                if store.get(conn, stale.id).status == "cancelling":
                    store.mark_cancelled(conn, stale.id)
            repository.delete_book_job(conn, book_id, "video")
        book_job = repository.enqueue_book_job(conn, book_id, "video")
        store.enqueue(
            conn, "video",
            payload={"book_job_id": book_job.id},
            book_id=book_id,
            dedupe_key=f"video:book_job={book_job.id}",
        )
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)
```

Cần cả `from app.jobqueue import store` ở đầu file (Task 12 đã thêm).

- [ ] **Step 6: Sửa `reset_all_jobs`**

```python
@router.post("/queue/reset-all")
def reset_all_jobs(request: Request):
    """Reset every patch and book_job to pending, every book to 'ready', delete all
    produced audio/video files, và xóa sạch bảng job rồi xếp lại từ đầu."""
    with locked_conn(request) as conn:
        summary = repository.reset_all_jobs(conn)
        cleared = conn.execute("DELETE FROM job").rowcount
        conn.commit()
        summary["jobs_cleared"] = cleared
        summary["jobs_enqueued"] = sum(backfill_pending_jobs(conn).values())
    logger.info(
        "event=queue.reset_all patches_reset=%s book_jobs_reset=%s books_reset=%s "
        "files_deleted=%s jobs_cleared=%s jobs_enqueued=%s",
        summary["patches_reset"], summary["book_jobs_reset"], summary["books_reset"],
        summary["files_deleted"], summary["jobs_cleared"], summary["jobs_enqueued"],
    )
    return summary
```

`DELETE FROM job` xóa cả lịch sử job đã xong — đúng với ý nghĩa của nút này (nó vốn đã
xóa file audio/video trên đĩa). File log trong `data/logs/jobs/` không bị xóa theo; chúng
sẽ tự hết hạn qua `purge_old_logs`.

- [ ] **Step 7: Chạy test, xác nhận pass**

```bash
pytest tests/test_admin_routes_enqueue.py tests/test_retry_failed.py tests/test_reset_all_jobs.py tests/test_pause_flag.py -v
```

Kỳ vọng: tất cả pass. `test_reset_all_jobs.py` là test có sẵn — nếu nó fail vì khóa mới
trong summary, thêm khóa vào assert của nó là đúng; nếu fail vì khóa cũ mất thì sửa code.

- [ ] **Step 8: Commit**

```bash
git add app/routes/queue.py tests/test_admin_routes_enqueue.py
git commit -m "fix(queue): admin routes now enqueue jobs instead of relying on the old loop"
```

---

### Task 14: Dọn worker cũ và kiểm thử tích hợp

**Files:**
- Modify: `app/worker.py` (xóa vòng lặp)
- Modify: `app/upload_worker.py` (xóa vòng lặp)
- Modify: `tests/test_upload_worker.py`, `tests/test_video_job.py`, `tests/test_db_lock_contention.py`
- Create: `tests/test_jobqueue_integration.py`
- Modify: `README.md`

- [ ] **Step 1: Viết test tích hợp**

Tạo `tests/test_jobqueue_integration.py`:

```python
"""Bốn loại job chạy cùng lúc, mỗi loại giữ đúng trần của mình."""
from __future__ import annotations

import asyncio
import threading

import pytest

from app import db
from app.jobqueue import store
from app.jobqueue.runner import JobQueue


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    yield


@pytest.fixture
def conn_factory(tmp_path):
    path = str(tmp_path / "queue.db")
    setup = db.connect(path)
    db.init_schema(setup)
    setup.close()
    return lambda: db.connect(path)


async def _drain(conn, timeout=30.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM job WHERE status IN ('pending','running')").fetchone()
        if row["c"] == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("queue không rút hết job")


async def test_each_type_respects_its_own_cap(conn_factory):
    conn = conn_factory()
    plan = {"voxcpm_tts": 4, "video": 6, "youtube_upload": 3, "light_tts": 30}
    for job_type, count in plan.items():
        for _ in range(count):
            store.enqueue(conn, job_type)

    peaks = {k: 0 for k in plan}
    live = {k: 0 for k in plan}
    lock = threading.Lock()

    def make(job_type):
        def handler(ctx):
            with lock:
                live[job_type] += 1
                peaks[job_type] = max(peaks[job_type], live[job_type])
            import time as _t
            _t.sleep(0.03)
            with lock:
                live[job_type] -= 1
            return {}
        return handler

    queue = JobQueue(
        conn_factory,
        concurrency={"voxcpm_tts": 1, "video": 2, "youtube_upload": 1},
        default_concurrency=10,
        poll_interval=0.01,
        reap_after_seconds=120,
    )
    for job_type in plan:
        queue.register(job_type, make(job_type))
    await queue.start()
    await _drain(conn)
    await queue.stop(timeout=10)

    assert peaks["voxcpm_tts"] == 1
    assert peaks["video"] <= 2
    assert peaks["youtube_upload"] == 1
    assert peaks["light_tts"] <= 10
    assert peaks["light_tts"] > 1, "light_tts không chạy song song thật"
    assert all(j.status == "done" for j in store.list_jobs(conn, limit=100))


async def test_a_crashing_type_does_not_stall_the_others(conn_factory):
    conn = conn_factory()
    for _ in range(3):
        store.enqueue(conn, "bad", max_attempts=1)
    for _ in range(5):
        store.enqueue(conn, "good")

    def bad(ctx):
        raise RuntimeError("luôn hỏng")

    queue = JobQueue(conn_factory, concurrency={}, default_concurrency=4, poll_interval=0.01,
                     reap_after_seconds=120)
    queue.register("bad", bad)
    queue.register("good", lambda ctx: {"ok": True})
    await queue.start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15
    while loop.time() < deadline:
        good = store.list_jobs(conn, job_type="good", status="done")
        if len(good) == 5:
            break
        await asyncio.sleep(0.02)
    await queue.stop(timeout=10)
    assert len(store.list_jobs(conn, job_type="good", status="done")) == 5
```

- [ ] **Step 2: Chạy test tích hợp**

```bash
pytest tests/test_jobqueue_integration.py -v
```

Kỳ vọng: 2 passed.

- [ ] **Step 3: Gỡ vòng lặp khỏi `app/worker.py`**

Xóa: `PatchWorker.run_forever`, `_spawn_book_job`, `_run_book_job_wrapper`,
`_should_exit`, `_process`, `_process_book_job`, `stop`, `log_shutdown_timeout`,
`_log_event`, và thuộc tính `_stop`/`_in_flight`/`state`/`current_*`.

Logic đã được port sang handler ở Task 7 và 8. Nếu sau khi xóa mà file chỉ còn phần
không ai gọi, xóa luôn `app/worker.py` — kiểm tra trước:

```bash
grep -rn "from app.worker\|from app import worker\|import worker" app/ tests/ scripts/
```

Làm tương tự với `app/upload_worker.py`: xóa `_run_loop`, `start`, `stop`,
`_process_upload`, `_execution_connection`, `get_status`, `init_worker`, biến toàn cục
`upload_worker`. Chỉ giữ `enqueue` nếu còn chỗ gọi:

```bash
grep -rn "upload_worker" app/ tests/ scripts/
```

Chú ý: memory ghi lại rằng `UploadWorker` từng không được khởi động suốt nhiều tháng mà
test vẫn xanh, vì test tiêm một singleton giả. Đừng lặp lại: nếu `tests/test_upload_worker.py`
đang tiêm singleton giả, **viết lại nó** thành test chạy qua queue thật với handler thật
(đã có ở `tests/test_jobqueue_handler_youtube.py`), rồi xóa file cũ nếu nó không còn kiểm
được gì mà handler test chưa phủ.

- [ ] **Step 4: Sửa các test bị ảnh hưởng**

```bash
pytest tests/ -q 2>&1 | tail -40
```

Với mỗi test fail, phân loại:

| Triệu chứng | Xử lý |
|---|---|
| Dựng `PatchWorker(...)`/`UploadWorker(...)` trực tiếp | viết lại dùng handler + `JobContext`, theo mẫu ở `tests/test_jobqueue_handler_video.py` |
| Đọc `app.state.upload_worker` | đổi sang `app.state.job_queue` |
| Kiểm `db_lock` không bị giữ suốt lúc I/O (`test_db_lock_contention.py`) | vẫn còn giá trị cho routes; giữ các test nhắm vào route, xóa test nhắm vào hai worker đã bị gỡ, **và thêm** một test khẳng định handler không hề chạm `app.state.db_lock` |
| Kiểm hành vi nghiệp vụ (video, patch, upload) | phải vẫn pass. Nếu fail thì port bị sai — sửa handler, đừng sửa test |

- [ ] **Step 5: Thêm test khẳng định queue không dùng db_lock**

Thêm vào `tests/test_db_lock_contention.py`:

```python
def test_queue_handlers_never_touch_the_shared_db_lock():
    """Điểm mấu chốt của cả thiết kế: nếu handler đi qua db_lock thì 10 worker chỉ là
    con số trên giấy. Handler nhận connection riêng qua ctx.conn — chưa từng có, và
    không được phép có, tham chiếu tới app.state.db_lock trong app/jobqueue/.

    Quét bằng pathlib chứ không gọi grep qua subprocess: repo này chạy trên Windows,
    nơi grep không chắc có trên PATH của tiến trình Python."""
    from pathlib import Path
    import app.jobqueue

    root = Path(app.jobqueue.__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "db_lock" in line
    ]
    assert offenders == [], f"jobqueue tham chiếu db_lock: {offenders}"
```

- [ ] **Step 6: Chạy toàn bộ suite**

```bash
pytest tests/ -q
```

Kỳ vọng: 0 failed. `test_heartbeat_keeps_long_create_claim_alive` flaky theo timing khi
chạy cả suite — chạy lại riêng nó trước khi kết luận:

```bash
pytest tests/ -q -k heartbeat
```

- [ ] **Step 7: Chạy thử app thật**

```bash
python -m uvicorn app.main:app --port 8000
```

Kiểm bằng tay:
1. `/health` trả 200, có `pools` với đủ 4 loại và `capacity` đúng 1/2/1/10.
2. `/queue` hiện bảng, cột tiến độ nhúc nhích khi có job chạy.
3. Bấm LightTTS cho một patch → có job `light_tts`; **đóng tab, mở lại** → job vẫn chạy
   và UI gắn lại được vào nó. Đây là thứ cả task này tồn tại để làm được.
4. `/queue/jobs/<id>/log` trả ra log của đúng job đó.
5. `data/logs/jobs/` có file log; `data/app.log` chỉ chứa dòng WARNING/ERROR của job.

- [ ] **Step 8: Cập nhật `README.md`**

Thêm mục cấu hình queue:

```markdown
### Hàng đợi job

Mọi tác vụ nền (VoxCPM TTS, LightTTS, render video, upload YouTube) chạy qua một queue
chung, mỗi loại có trần song song riêng:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `QUEUE_CONCURRENCY` | `voxcpm_tts=1,video=2,youtube_upload=1` | trần song song theo loại |
| `QUEUE_DEFAULT_CONCURRENCY` | `10` | trần cho loại không liệt kê ở trên (hiện là `light_tts`) |
| `QUEUE_LOG_RETENTION_DAYS` | `7` | số ngày giữ log job trong `data/logs/jobs/` |
| `QUEUE_REAP_AFTER_SECONDS` | `120` | job `running` im lặng quá lâu bị trả về `pending` |

Đặt một loại về `0` để tắt hẳn nó: `QUEUE_CONCURRENCY="voxcpm_tts=0,video=2,youtube_upload=1"`.
Loại bị tắt vẫn nhận job vào hàng đợi, chỉ là không có worker nào chạy chúng cho tới khi
bật lại.

`ENABLE_WORKER=false` là lối tắt cho trường hợp hay gặp nhất — máy dev không có GPU. Nó
**chỉ** đặt `voxcpm_tts` về 0; video, upload YouTube và LightTTS vẫn chạy bình thường.

Theo dõi ở `/queue`. Log chi tiết của từng job nằm ở `data/logs/jobs/<job_id>.log`;
`data/app.log` chỉ nhận dòng WARNING trở lên.
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(queue): remove PatchWorker/UploadWorker loops, add integration tests"
```

---

## Ghi chú khi thực thi

**Thứ tự bắt buộc:** Task 1→6 là hạ tầng, phải xong trước. Task 7, 8, 9 độc lập với nhau,
làm song song được. Task 10 phụ thuộc 4 và 5. Task 11 phụ thuộc 7–10. Task 12 và 13 đều
phụ thuộc 11 và làm song song được với nhau. Task 14 cuối cùng — **đừng gỡ worker cũ
trước khi 12 và 13 xanh**, nếu không sẽ không còn gì chạy job trong lúc bạn debug.

**Ba chỗ dễ sai nhất:**

1. **Executor.** Nếu `test_concurrency_cap_is_never_exceeded` cho `peak == 1`, handler
   đang chạy tuần tự — kiểm `run_in_executor` có truyền `self._executor` không.
2. **Connection.** Handler nào lỡ dùng `request.app.state.conn` thay vì `ctx.conn` sẽ
   đưa cả queue về tuần tự và có thể hỏng dữ liệu (sqlite3 connection không an toàn khi
   dùng đồng thời). Test ở Task 14 Step 5 canh đúng chỗ này.
3. **Hợp đồng SSE.** `test_preview_stream_bridge.py` và `tests/test_light_tts.py` là chốt
   chặn. Nếu chúng fail sau Task 10, JavaScript ở `book_detail.html` sẽ hỏng ngoài đời
   thật mà không có test nào khác bắt được.


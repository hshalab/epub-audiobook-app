# Clear Job Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a queue-page control that deletes inactive queue jobs while preserving jobs that are running or cancelling.

**Architecture:** Keep queue SQL in `app/jobqueue/store.py`, expose it through a small locked FastAPI route, and call that route from the existing plain-JavaScript queue page. Use one SQL delete whose execution-time predicate protects active jobs.

**Tech Stack:** Python 3.11, SQLite, FastAPI, Jinja2, browser JavaScript, pytest

## Global Constraints

- Delete jobs in `pending`, `done`, `failed`, and `cancelled` states.
- Preserve jobs in `running` and `cancelling` states.
- Do not modify patches, book jobs, books, generated files, or job log files.
- Do not cancel active jobs or trigger queue backfill.
- Add no dependencies.

---

### Task 1: Inactive Queue Deletion

**Files:**
- Modify: `app/jobqueue/store.py:308-321`
- Test: `tests/test_jobqueue_store.py`

**Interfaces:**
- Consumes: `sqlite3.Connection`
- Produces: `clear_inactive(conn: sqlite3.Connection) -> int`, returning the number of deleted rows

- [ ] **Step 1: Write the failing store test**

Add this test to `tests/test_jobqueue_store.py`, using that file's existing connection fixture or helper in place of `conn` if its fixture has a different name:

```python
def test_clear_inactive_preserves_active_jobs(conn):
    ids = {}
    for status in ("pending", "done", "failed", "cancelled", "running", "cancelling"):
        job_id = store.enqueue(conn, "video")
        conn.execute("UPDATE job SET status=? WHERE id=?", (status, job_id))
        ids[status] = job_id
    conn.commit()

    assert store.clear_inactive(conn) == 4
    remaining = {job.status for job in store.list_jobs(conn)}
    assert remaining == {"running", "cancelling"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_jobqueue_store.py::test_clear_inactive_preserves_active_jobs -v`

Expected: FAIL because `store.clear_inactive` does not exist.

- [ ] **Step 3: Implement the store operation**

Add to `app/jobqueue/store.py` near the other list/count administration functions:

```python
def clear_inactive(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "DELETE FROM job WHERE status NOT IN ('running', 'cancelling')"
    )
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Run the store tests**

Run: `pytest tests/test_jobqueue_store.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the store change if commits were requested**

```bash
git add app/jobqueue/store.py tests/test_jobqueue_store.py
git commit -m "feat(queue): clear inactive jobs"
```

### Task 2: Clear Queue API And Page Control

**Files:**
- Modify: `app/routes/queue.py:210-218`
- Modify: `app/templates/queue.html:3-4,41-118`
- Test: `tests/test_queue_routes.py:59-85`

**Interfaces:**
- Consumes: `store.clear_inactive(conn) -> int`
- Produces: `POST /queue/clear` returning `{"cleared": int}`

- [ ] **Step 1: Write failing route and page tests**

Add to `tests/test_queue_routes.py`:

```python
def test_clear_queue_deletes_inactive_and_preserves_active(client):
    c, conn, _ = client
    for status in ("pending", "done", "failed", "cancelled", "running", "cancelling"):
        job_id = store.enqueue(conn, "video")
        conn.execute("UPDATE job SET status=? WHERE id=?", (status, job_id))
    conn.commit()

    response = c.post("/queue/clear")

    assert response.status_code == 200
    assert response.json() == {"cleared": 4}
    assert {job.status for job in store.list_jobs(conn)} == {"running", "cancelling"}


def test_queue_page_exposes_clear_control(client):
    response = client[0].get("/queue")
    assert response.status_code == 200
    assert 'id="clear-queue"' in response.text
    assert 'fetch("/queue/clear"' in response.text
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `pytest tests/test_queue_routes.py::test_clear_queue_deletes_inactive_and_preserves_active tests/test_queue_routes.py::test_queue_page_exposes_clear_control -v`

Expected: first test returns 404 and second test cannot find the clear control.

- [ ] **Step 3: Add the API route**

Add to `app/routes/queue.py` before the retry route:

```python
@router.post("/queue/clear")
def clear_queue(request: Request):
    with locked_conn(request) as conn:
        cleared = store.clear_inactive(conn)
    return {"cleared": cleared}
```

- [ ] **Step 4: Add the page button**

Add immediately below the `<h1>` in `app/templates/queue.html`:

```html
<button type="button" id="clear-queue" class="btn-danger btn-sm">Xóa hàng đợi</button>
```

- [ ] **Step 5: Add clear request handling**

Add after `refresh()` in `app/templates/queue.html`:

```javascript
document.getElementById("clear-queue").addEventListener("click", async () => {
    if (!confirm("Xóa toàn bộ job đang chờ và lịch sử? Job đang chạy sẽ được giữ lại.")) return;
    const response = await fetch("/queue/clear", {method: "POST"});
    if (!response.ok) {
        alert("Không thể xóa hàng đợi.");
        return;
    }
    await refresh();
});
```

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_jobqueue_store.py tests/test_queue_routes.py -v`

Expected: PASS.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`

Expected: PASS with no regressions.

- [ ] **Step 8: Commit the API and UI change if commits were requested**

```bash
git add app/routes/queue.py app/templates/queue.html tests/test_queue_routes.py
git commit -m "feat(queue): add clear queue control"
```

# Queued Patch Video Inline Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enqueue patch video rendering and show queue status, progress, errors, retry, and logs in each patch row.

**Architecture:** The patch route performs fast validation and inserts a deduplicated `patch_video` job. The existing handler becomes the single manual-render implementation and emits queue progress/logs. `book_detail.html` observes jobs through the existing queue REST/SSE APIs, restores them after reload, and falls back to polling.

**Tech Stack:** FastAPI, SQLite queue store, Python job handlers, Jinja2, browser JavaScript/EventSource, pytest.

## Global Constraints

- Keep the existing queue schema and `/queue` API compatible.
- Preserve atomic render validation, patch video registration, shared backgrounds, music, intro/outro, animation, codec, quality, bitrate, and optional YouTube behavior.
- Exact FFmpeg frame-percentage parsing and inline cancellation are out of scope.
- Use test-driven development for each behavior change.

---

### Task 1: Queue Endpoint Contract

**Files:**
- Modify: `app/routes/patches.py:392-534`
- Modify: `tests/test_patch_atomic_render.py`

**Interfaces:**
- Consumes: `store.enqueue`, `store.find_live_by_dedupe`, `_wants_json`, `locked_conn`.
- Produces: AJAX response `{status: "queued", job_id: int, deduplicated: bool}` with HTTP 202; payload `{patch_id, upload_youtube, privacy}`.

- [ ] **Step 1: Write failing route tests**

Add tests that seed a ready patch, spy on `video_gen.generate_segment`, POST the route, assert HTTP 202, inspect `store.get(...)`, and assert render was not called. Add a second POST assertion that the same live `job_id` is returned with `deduplicated == true` and only one live queue row exists.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/test_patch_atomic_render.py -k "queue or deduplic"`

Expected: existing route returns 200 and invokes render synchronously.

- [ ] **Step 3: Implement minimal enqueue behavior**

Import `store`; keep ownership/audio/book/background validation; remove synchronous render from the route; enqueue with:

```python
dedupe_key = f"patch_video:patch={patch_id}"
existing = store.find_live_by_dedupe(conn, dedupe_key)
job_id = existing.id if existing else store.enqueue(
    conn, "patch_video",
    payload={"patch_id": patch_id, "upload_youtube": upload_youtube, "privacy": privacy},
    book_id=book_id, dedupe_key=dedupe_key,
)
```

Return `JSONResponse(..., status_code=202)` for AJAX and redirect for non-AJAX. Log and return 500 only if insertion unexpectedly yields neither a job ID nor a live job.

- [ ] **Step 4: Run route tests and verify GREEN**

Run: `pytest -q tests/test_patch_atomic_render.py`

Expected: all tests pass with enqueue semantics.

### Task 2: Unified Patch Video Handler

**Files:**
- Modify: `app/jobqueue/handlers/patch_video.py`
- Modify: `tests/test_jobqueue_handler_patch_video.py`

**Interfaces:**
- Consumes: queue payload from Task 1; `get_book_video_config`, `resolve_patch_image`, `resolve_configured_patch_image`, `ensure_patch_overlay`, `publish_validated_video`, `upsert_patch_video`.
- Produces: result `{output_path: str, video_id: int, youtube: dict | None}` and phases `preparing`, `overlay`, `encoding`, `validating`, `registering`, optional `publishing`, `done`.

- [ ] **Step 1: Write failing handler tests**

Cover a ready patch without a `patch_pipeline` row; assert output under `books/{book_id}/patch_videos/{patch_id}.mp4`, video registration, and ordered progress phases. Add tests for shared background sequence, intro/outro forwarding, missing audio as `JobFatalError`, and optional publishing payload.

- [ ] **Step 2: Run handler tests and verify RED**

Run: `pytest -q tests/test_jobqueue_handler_patch_video.py`

Expected: manual job fails with `source_unavailable: book or pipeline missing`.

- [ ] **Step 3: Port synchronous route render behavior into handler**

Load current book/video settings at execution time. Resolve shared sequence versus single image/video background, render overlay when needed, resolve music and intro/outro, and retain atomic validation. Emit progress before each boundary and `ctx.log` messages containing patch ID, selected media paths, and completion output. Use an `on_progress` callback where the selected generator supports it and call `ctx.heartbeat()`.

- [ ] **Step 4: Preserve recovery and publishing behavior**

If a pipeline row exists, honor its media snapshot where required by integrity recovery and update its video fields on success. If `upload_youtube` is true, seed the generated video and invoke the existing publish stage with the requested privacy behavior. Do not require or create pipeline state for plain manual rendering.

- [ ] **Step 5: Run handler tests and verify GREEN**

Run: `pytest -q tests/test_jobqueue_handler_patch_video.py tests/test_jobqueue_handler_video.py tests/test_video_integrity_pipeline.py`

Expected: all pass.

### Task 3: Queue Response And Inline Row Observer

**Files:**
- Modify: `app/templates/book_detail.html:563-579,674-686,2181-2250,2482-2511`
- Modify: `tests/test_video_studio.py`

**Interfaces:**
- Consumes: enqueue response from Task 1; `/queue/jobs/{id}`, `/queue/jobs/{id}/log`, `/queue/jobs/{id}/stream`, `/queue/jobs?type=patch_video&book_id=...`.
- Produces: `monitorPatchVideoJob(jobId, cell)`, `restorePatchVideoJobs()`, inline status/progress/log/retry UI.

- [ ] **Step 1: Write failing template assertions**

Assert the rendered template contains a per-cell job container, `EventSource`, queue restoration URL filtered by book, retry endpoint, log toggle, and queued response handling. Assert batch completion text describes queued jobs rather than completed renders.

- [ ] **Step 2: Run template tests and verify RED**

Run: `pytest -q tests/test_video_studio.py -k "video or queue"`

Expected: observer and restoration markers are absent.

- [ ] **Step 3: Add row job UI rendering**

Add a `.pv-job-state` element and styles for phase, `<progress>`, error, and collapsible `<pre>`. Render text through DOM APIs or escaped values, never interpolate untrusted log/error HTML.

- [ ] **Step 4: Add SSE monitoring with polling fallback**

`monitorPatchVideoJob` stores `data-job-id`, opens EventSource, applies `progress` and log events, and closes on terminal status. On SSE error, close it and poll job detail every two seconds. Success sets `data-has-video=1` and rebuilds controls; failure shows error, log, and retry.

- [ ] **Step 5: Restore jobs after reload**

Fetch book-filtered patch-video jobs, map the newest row by numeric `payload.patch_id`, and attach live or newest failed jobs to matching cells. Do not replace controls for an older successful terminal job when the MP4 is already represented by server HTML.

- [ ] **Step 6: Change per-row and batch semantics**

`genPatchVideo` treats HTTP 202 as enqueue success and begins monitoring instead of marking video complete. Batch workers enqueue with bounded concurrency and summarize newly queued, deduplicated, and failed requests without waiting for render completion.

- [ ] **Step 7: Run template tests and verify GREEN**

Run: `pytest -q tests/test_video_studio.py`

Expected: all pass.

### Task 4: End-to-End Regression And Documentation Check

**Files:**
- Verify: `docs/superpowers/specs/2026-07-30-queued-patch-video-inline-observability-design.md`
- Verify: all modified production/test files.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: verified queued render flow with inline observability.

- [ ] **Step 1: Run focused queue/video suite**

Run: `pytest -q tests/test_patch_atomic_render.py tests/test_jobqueue_handler_patch_video.py tests/test_jobqueue_handler_video.py tests/test_video_integrity_pipeline.py tests/test_patch_video_upload.py tests/test_video_studio.py`

Expected: all pass.

- [ ] **Step 2: Run full test suite**

Run: `pytest -q`

Expected: all pass; pre-existing deprecation warnings may remain.

- [ ] **Step 3: Check diff hygiene and requirement coverage**

Run: `git diff --check` and inspect `git diff -- app/routes/patches.py app/jobqueue/handlers/patch_video.py app/templates/book_detail.html tests docs/superpowers`.

Expected: no whitespace errors; every design requirement maps to implementation or a test.

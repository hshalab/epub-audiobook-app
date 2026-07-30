# Video Integrity Validation and Automatic Re-rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block every corrupt or incomplete video before YouTube upload and automatically re-render application-owned outputs with their original configuration no more than two times.

**Architecture:** A queue-independent `video_integrity` service performs structural checks and full audio/video decode. The YouTube queue handler is the authoritative gate; source-specific render jobs publish through a validated temporary file and resume the same upload row. SQLite stores provenance, validation state, and retry generation so queue backfill can recover safely after a restart.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, FFmpeg/ffprobe, existing `app.jobqueue` runner and Jinja templates.

## Global Constraints

- Every upload attempt performs a fresh full-file validation immediately before `youtube.process_upload`; no permanent validation cache may bypass it.
- Full decode uses FFmpeg `-v error -xerror`, explicit `0:v:0` and `0:a:0` maps, and the null muxer.
- A/V drift from 1 second up to but excluding 5 seconds is a non-blocking warning; absolute drift of 5 seconds or more is `av_drift` and blocks upload.
- Decode timeout is `min(21600, max(300, ceil(duration_seconds * 2 + 120)))` seconds: minimum 5 minutes, twice media duration plus 2 minutes, maximum 6 hours.
- Stored stderr/error messages are limited to the final 2,000 characters.
- Automatic re-rendering uses the original configuration, never changes NVENC to `libx264`, and is limited to two retries after the initial invalid output.
- Retry count is persisted before enqueueing re-render work and is never reset by startup backfill.
- Environment failures and `source_unavailable` do not increment `integrity_retry_count`.
- All renderers publish by validating a temporary sibling file and then calling `Path.replace`; an existing final file survives render or validation failure.
- Existing unrelated worktree changes must not be reverted or included in task commits.

## File Structure

- Create `app/video_integrity.py`: immutable result types, ffprobe parsing, format policy, timeout calculation, and full decode.
- Create `app/video_publish.py`: temporary sibling path lifecycle, validation, atomic replacement, and cancellation cleanup.
- Create `app/video_recovery.py`: source inference, recoverability classification, persisted retry transition, source-specific enqueue, and post-render upload resumption.
- Modify `app/db.py`: upload validation/provenance columns and standalone render configuration persistence.
- Modify `app/youtube.py`: provenance-aware enqueue and validation state update helpers.
- Modify `app/jobqueue/handlers/youtube_upload.py`: authoritative validation gate and failure routing.
- Modify `app/jobqueue/handlers/video.py`: validated atomic whole-book publishing and recovery upload resumption.
- Create `app/jobqueue/handlers/patch_video.py`: queue-owned patch re-render using the pipeline snapshots.
- Create `app/jobqueue/handlers/standalone_video.py`: queue-owned standalone re-render from persisted inputs/configuration.
- Modify `app/jobqueue/backfill.py`: register/recover the two render handlers and interrupted validation/re-render states.
- Modify `app/video_repository.py`, `app/patch_publishing.py`, `app/routes/video.py`, `app/routes/youtube.py`, and `app/upload_worker.py`: persist provenance and route every upload through the queue gate.
- Modify `scripts/check_video_integrity.py`: reuse the shared validator.
- Modify `app/templates/youtube.html`, `app/templates/video.html`, and `app/templates/book_detail.html`: show validation/re-render status and terminal cause using existing visual patterns.
- Create `tests/test_video_integrity.py`, `tests/test_video_publish.py`, and `tests/test_video_recovery.py`; extend the existing queue, render, route, script, and UI tests listed in each task.

---

### Task 1: Build the Pure Video Integrity Service

**Files:**
- Create: `app/video_integrity.py`
- Create: `tests/test_video_integrity.py`

**Interfaces:**
- Consumes: `settings.get_ffprobe_path()`, `settings.get_ffmpeg_path()`, and a filesystem path.
- Produces: `ValidationResult`, `validate_video(path: str | Path) -> ValidationResult`, `decode_timeout(duration_seconds: float) -> int`, `RECOVERABLE_OUTPUT_CODES: frozenset[str]`.

- [ ] **Step 1: Write failing tests for result shape, timeout boundaries, and basic filesystem failures**

```python
from pathlib import Path

from app.video_integrity import decode_timeout, validate_video


def test_decode_timeout_has_duration_aware_bounds():
    assert decode_timeout(0) == 300
    assert decode_timeout(60) == 300
    assert decode_timeout(3600) == 7320
    assert decode_timeout(999999) == 21600


def test_missing_and_empty_files_fail_before_probe(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("app.video_integrity.subprocess.run", lambda *a, **k: calls.append(a))
    missing = validate_video(tmp_path / "missing.mp4")
    empty_path = tmp_path / "empty.mp4"
    empty_path.touch()
    empty = validate_video(empty_path)
    assert (missing.valid, missing.error_code) == (False, "file_missing")
    assert (empty.valid, empty.error_code) == (False, "file_empty")
    assert calls == []
```

- [ ] **Step 2: Run the focused tests and confirm the module is missing**

Run: `pytest tests/test_video_integrity.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app.video_integrity'`.

- [ ] **Step 3: Implement immutable result types, constants, timeout, and filesystem checks**

```python
from dataclasses import dataclass
from math import ceil

MAX_ERROR_CHARS = 2000
DRIFT_WARN_SECONDS = 1.0
DRIFT_FATAL_SECONDS = 5.0
RECOVERABLE_OUTPUT_CODES = frozenset({
    "probe_failed", "missing_video_stream", "missing_audio_stream",
    "invalid_duration", "unsupported_format", "av_drift", "decode_failed",
})


@dataclass(frozen=True)
class ValidationFacts:
    container: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    video_duration: float = 0.0
    audio_duration: float = 0.0
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error_code: str | None
    message: str
    warnings: tuple[str, ...]
    facts: ValidationFacts
    elapsed_seconds: float


def decode_timeout(duration_seconds: float) -> int:
    return min(21600, max(300, ceil(max(0.0, duration_seconds) * 2 + 120)))
```

Implement `validate_video` filesystem guards returning a zeroed `ValidationFacts` and bounded message.

- [ ] **Step 4: Add failing table-driven ffprobe parser tests**

```python
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(("streams", "fmt", "code"), [
    ([], {"format_name": "mov,mp4", "duration": "10"}, "missing_video_stream"),
    ([{"codec_type": "video", "codec_name": "h264", "duration": "10"}],
     {"format_name": "mov,mp4", "duration": "10"}, "missing_audio_stream"),
    ([{"codec_type": "video", "codec_name": "h264", "duration": "nan"},
      {"codec_type": "audio", "codec_name": "aac", "duration": "10"}],
     {"format_name": "mov,mp4", "duration": "10"}, "invalid_duration"),
    ([{"codec_type": "video", "codec_name": "vp9", "duration": "10"},
      {"codec_type": "audio", "codec_name": "aac", "duration": "10"}],
     {"format_name": "mov,mp4", "duration": "10"}, "unsupported_format"),
])
def test_probe_rejects_invalid_media(tmp_path, monkeypatch, streams, fmt, code):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    probe = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": streams, "format": fmt}), stderr="")
    monkeypatch.setattr("app.video_integrity.subprocess.run", lambda *a, **k: probe)
    result = validate_video(path)
    assert (result.valid, result.error_code) == (False, code)
```

Also add cases for invalid JSON/non-zero probe (`probe_failed`), malformed/zero/infinite duration (`invalid_duration`), accepted MP4/MOV/M4V with H.264 or HEVC video plus AAC/MP3 audio, and rejected container/codec combinations.

- [ ] **Step 5: Implement ffprobe invocation and structural policy**

Use this exact probe shape:

```python
probe_cmd = [
    settings.get_ffprobe_path(), "-v", "error", "-print_format", "json",
    "-show_streams", "-show_format", str(path),
]
```

Select the first `video` and `audio` stream. Parse duration from stream duration, falling back to format duration only when the stream value is absent. Accept containers containing one of `mp4`, `mov`, or `m4v`, video codecs `h264` or `hevc`, and audio codecs `aac` or `mp3`. Return the stable error codes from the spec and catch missing executables as `tool_unavailable`.

- [ ] **Step 6: Add failing drift, warning, decode command, timeout, and stderr-bound tests**

```python
def test_full_decode_maps_both_streams_and_uses_xerror(valid_probe, tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"media")
    commands = []
    def fake_run(cmd, **kwargs):
        commands.append((cmd, kwargs))
        return valid_probe if "ffprobe" in str(cmd[0]) else SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("app.video_integrity.subprocess.run", fake_run)
    result = validate_video(path)
    decode_cmd, decode_kwargs = commands[1]
    assert result.valid is True
    assert [decode_cmd[i + 1] for i, value in enumerate(decode_cmd) if value == "-map"] == ["0:v:0", "0:a:0"]
    assert "-xerror" in decode_cmd
    assert decode_cmd[-2:] == ["null", "-"]
    assert decode_kwargs["timeout"] == decode_timeout(10)
```

Add cases for 0.9-second drift (valid, no warning), 1.0-second drift (valid with warning), 5.0-second drift (`av_drift`), decode return code (`decode_failed`), `subprocess.TimeoutExpired` (`validation_timeout`), `PermissionError` (`tool_unavailable`), and a 3,000-character stderr reduced to 2,000 characters.

- [ ] **Step 7: Implement drift and complete decode**

Use this exact decode command after a successful probe:

```python
decode_cmd = [
    settings.get_ffmpeg_path(), "-v", "error", "-xerror", "-i", str(path),
    "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
]
```

Invoke with `capture_output=True`, `text=True`, and `timeout=decode_timeout(max(video_duration, audio_duration))`. Measure total validation time with `time.monotonic()` and bound every returned error message with `message[-MAX_ERROR_CHARS:]`.

- [ ] **Step 8: Run the integrity service tests**

Run: `pytest tests/test_video_integrity.py -v`

Expected: PASS for all filesystem, probe, policy, drift, decode, timeout, and stderr cases.

- [ ] **Step 9: Commit the service**

```bash
git add app/video_integrity.py tests/test_video_integrity.py
git commit -m "feat: add full video integrity validation"
```

### Task 2: Persist Validation State, Provenance, and Standalone Render Inputs

**Files:**
- Modify: `app/db.py:190-202,527-549`
- Modify: `app/youtube.py:375-402,429-462`
- Modify: `app/video_repository.py:15-43,168-183`
- Create: `tests/test_video_integrity_persistence.py`
- Modify: `tests/test_database_io.py`

**Interfaces:**
- Consumes: existing `db.init_schema`, `youtube.enqueue_upload`, `video_repository.insert_video`.
- Produces: `youtube.enqueue_upload(..., render_source_type: str = "external", render_source_id: int | None = None) -> int`, `youtube.mark_validation_started`, `youtube.mark_validation_valid`, `youtube.mark_validation_failed`, and standalone `render_config_json` storage.

- [ ] **Step 1: Write failing fresh-schema and migration tests**

```python
EXPECTED_UPLOAD_COLUMNS = {
    "validation_status", "validation_error_code", "validation_error_message",
    "validated_at", "integrity_retry_count", "render_source_type", "render_source_id",
}


def test_schema_adds_integrity_and_provenance_columns(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    db.init_schema(conn)
    upload_columns = {row["name"] for row in conn.execute("PRAGMA table_info(youtube_uploads)")}
    video_columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    assert EXPECTED_UPLOAD_COLUMNS <= upload_columns
    assert "render_config_json" in video_columns
```

Create a legacy database containing the old `youtube_uploads` and `videos` layouts, call `db.init_schema`, and assert defaults are `pending`, `0`, and `external` without changing existing `status` or `retry_count`.

- [ ] **Step 2: Run migration tests and verify missing columns**

Run: `pytest tests/test_video_integrity_persistence.py::test_schema_adds_integrity_and_provenance_columns -v`

Expected: FAIL because the columns do not exist.

- [ ] **Step 3: Add schema and additive migration definitions**

Add all seven spec columns to the `CREATE TABLE youtube_uploads` statement and to `upload_columns`. Add this column to `videos` and its migration map:

```sql
render_config_json TEXT
```

Do not add a second configuration snapshot to `youtube_uploads`.

- [ ] **Step 4: Write failing repository/enqueue/helper tests**

```python
def test_enqueue_upload_persists_explicit_provenance(conn):
    upload_id = youtube.enqueue_upload(
        conn, "v.mp4", "T", render_source_type="book", render_source_id=7,
    )
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert (row["render_source_type"], row["render_source_id"]) == ("book", 7)


def test_validation_state_helpers_clear_stale_errors(conn):
    upload_id = youtube.enqueue_upload(conn, "v.mp4", "T")
    youtube.mark_validation_failed(conn, upload_id, "decode_failed", "bad")
    youtube.mark_validation_started(conn, upload_id)
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert row["validation_status"] == "validating"
    assert row["validation_error_code"] is None
    assert row["validation_error_message"] is None
```

Also assert `insert_video(..., render_config={...})` serializes deterministic JSON and `update_video` allows `render_config_json`, `source_audio`, `background_path`, and `file_path` so re-render completion can preserve source and refresh output metadata.

- [ ] **Step 5: Implement provenance-aware enqueue and state helpers**

Extend `enqueue_upload` with keyword-only provenance arguments and validate `render_source_type` against `{"book", "patch", "standalone", "external"}`. Implement helpers with one `UPDATE` plus `commit()` each:

```python
def mark_validation_started(conn, upload_id): ...
def mark_validation_valid(conn, upload_id, validated_at): ...
def mark_validation_failed(conn, upload_id, code, message): ...
```

`mark_validation_valid` clears prior validation errors. `mark_validation_failed` sets `validation_status='failed'`, stores the bounded error fields, and sets `validated_at`.

- [ ] **Step 6: Extend database export/import coverage**

Update `tests/test_database_io.py` expectations only if that subsystem lists explicit columns. Round-trip one upload with retry/provenance state and one standalone video with `render_config_json`; assert values survive export/import.

- [ ] **Step 7: Run persistence tests and related regressions**

Run: `pytest tests/test_video_integrity_persistence.py tests/test_database_io.py tests/test_youtube_upload_lifecycle.py -v`

Expected: PASS.

- [ ] **Step 8: Commit persistence changes**

```bash
git add app/db.py app/youtube.py app/video_repository.py tests/test_video_integrity_persistence.py tests/test_database_io.py
git commit -m "feat: persist video validation provenance"
```

### Task 3: Publish Rendered Files Atomically After Validation

**Files:**
- Create: `app/video_publish.py`
- Create: `tests/test_video_publish.py`

**Interfaces:**
- Consumes: `video_integrity.validate_video`, a final path, and a render callable accepting the temporary path.
- Produces: `publish_validated_video(final_path: str | Path, render: Callable[[str], None], *, validator: Callable = validate_video) -> ValidationResult` and `VideoValidationError` carrying `.result`.

- [ ] **Step 1: Write failing success and preservation tests**

```python
def test_success_validates_temp_then_atomically_replaces_final(tmp_path):
    final = tmp_path / "video.mp4"
    final.write_bytes(b"old")
    seen = {}
    def render(temp):
        seen["temp"] = Path(temp)
        Path(temp).write_bytes(b"new")
    result = publish_validated_video(final, render, validator=lambda p: valid_result(p))
    assert result.valid
    assert final.read_bytes() == b"new"
    assert seen["temp"].parent == final.parent
    assert not seen["temp"].exists()


def test_failed_validation_preserves_existing_final_and_cleans_temp(tmp_path):
    final = tmp_path / "video.mp4"
    final.write_bytes(b"old")
    with pytest.raises(VideoValidationError):
        publish_validated_video(
            final, lambda temp: Path(temp).write_bytes(b"broken"),
            validator=lambda p: invalid_result("decode_failed"),
        )
    assert final.read_bytes() == b"old"
    assert list(tmp_path.glob("*.rendering-*.mp4")) == []
```

Add cases for renderer exception and `KeyboardInterrupt`/cancellation cleanup.

- [ ] **Step 2: Run tests and verify the module is missing**

Run: `pytest tests/test_video_publish.py -v`

Expected: FAIL during collection with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the publish boundary**

```python
class VideoValidationError(RuntimeError):
    def __init__(self, result: ValidationResult):
        super().__init__(f"{result.error_code}: {result.message}")
        self.result = result


def publish_validated_video(final_path, render, *, validator=validate_video):
    final = Path(final_path)
    temp = final.with_name(f"{final.stem}.rendering-{uuid.uuid4().hex}{final.suffix}")
    try:
        render(str(temp))
        result = validator(temp)
        if not result.valid:
            raise VideoValidationError(result)
        temp.replace(final)
        return result
    finally:
        temp.unlink(missing_ok=True)
```

Create the destination directory before rendering. Catch no `BaseException`; the `finally` block handles cancellation while preserving the original exception.

- [ ] **Step 4: Run atomic publishing tests**

Run: `pytest tests/test_video_publish.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the publishing boundary**

```bash
git add app/video_publish.py tests/test_video_publish.py
git commit -m "feat: publish only validated video outputs"
```

### Task 4: Add Recovery Decisions and Persisted Retry Transitions

**Files:**
- Create: `app/video_recovery.py`
- Create: `tests/test_video_recovery.py`
- Modify: `app/patch_publishing.py:114-138`

**Interfaces:**
- Consumes: upload rows, `ValidationResult`, `RECOVERABLE_OUTPUT_CODES`, existing `videos`, `patch_pipeline`, `book_job`, and `store.enqueue`.
- Produces: `infer_render_source(conn, upload: dict) -> tuple[str, int | None]`, `schedule_rerender(conn, upload_id: int, result: ValidationResult) -> RecoveryDecision`, `resume_upload_after_render(conn, upload_id: int) -> int | None`, and immutable `RecoveryDecision(action, retry_count, job_id, message)`.

- [ ] **Step 1: Write failing source inference tests**

```python
def test_source_inference_uses_relationships_not_filename(conn):
    # Explicit provenance wins. A linked videos.patch_id infers patch; a linked videos row
    # with reproducible standalone inputs infers standalone; an upload referenced by the
    # latest whole-book video job infers book; an unlinked row remains external.
    assert infer_render_source(conn, explicit_upload) == ("book", book_job_id)
    assert infer_render_source(conn, patch_upload) == ("patch", patch_id)
    assert infer_render_source(conn, standalone_upload) == ("standalone", video_id)
    assert infer_render_source(conn, unlinked_upload) == ("external", None)
```

Build rows using foreign keys/pipeline links and deliberately misleading filenames to prove inference never parses paths.

- [ ] **Step 2: Implement safe source inference**

Order the checks: explicit non-external provenance; `patch_pipeline.youtube_upload_id`; `youtube_uploads.video_id -> videos.patch_id`; reproducible standalone `videos` row (`source_audio`, `background_path`, `render_config_json` all present); whole-book upload linked explicitly to a `book_job`; otherwise external. Persist inferred provenance only when unambiguous.

- [ ] **Step 3: Write failing retry transition tests**

```python
@pytest.mark.parametrize(("starting", "expected"), [(0, 1), (1, 2)])
def test_recoverable_failure_persists_count_before_enqueue(conn, monkeypatch, starting, expected):
    upload_id = seeded_book_upload(conn, retry_count=starting)
    observed = []
    monkeypatch.setattr(store, "enqueue", lambda c, *a, **k: observed.append(
        c.execute("SELECT integrity_retry_count FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()[0]
    ) or 91)
    decision = schedule_rerender(conn, upload_id, invalid_result("decode_failed"))
    assert decision.action == "rerender"
    assert decision.retry_count == expected
    assert observed == [expected]
```

Add cases for the third failure becoming terminal, external source becoming terminal, `tool_unavailable`/`validation_timeout` not incrementing, missing source files becoming `source_unavailable`, patch pipeline/video UI state becoming `rerendering`, and metadata fields remaining unchanged.

- [ ] **Step 4: Implement transactional retry scheduling**

Inside `BEGIN IMMEDIATE`, reload the upload, infer source, validate source prerequisites, and either:

- set terminal validation/upload state and return `RecoveryDecision("failed", ...)`; or
- increment `integrity_retry_count`, set `validation_status='waiting_for_rerender'`, set `status='waiting_for_rerender'`, update the owning `videos.upload_status` or `patch_pipeline` state, commit, then call `store.enqueue`.

Use these job types and payloads:

```python
("video", {"book_job_id": source_id, "recovery_upload_id": upload_id})
("patch_video", {"patch_id": source_id, "recovery_upload_id": upload_id})
("standalone_video", {"video_id": source_id, "recovery_upload_id": upload_id})
```

Use dedupe keys ending in `:integrity_retry={retry_count}`. If enqueue fails, preserve `waiting_for_rerender`; backfill will restore it.

- [ ] **Step 5: Write and implement upload resumption tests**

```python
def test_resume_reuses_same_upload_and_enqueues_once(conn):
    upload_id = seeded_waiting_upload(conn, retry_count=1)
    job_id = resume_upload_after_render(conn, upload_id)
    row = conn.execute("SELECT status, validation_status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert dict(row) == {"status": "pending", "validation_status": "pending"}
    jobs = store.list_jobs(conn, job_type="youtube_upload")
    assert [j.payload["upload_id"] for j in jobs] == [upload_id]
    assert resume_upload_after_render(conn, upload_id) is None
```

The dedupe key is `youtube_upload:upload={upload_id}:integrity_retry={count}`. Preserve title, description, tags, privacy, metadata snapshot, thumbnail, and playlist columns.

- [ ] **Step 6: Make patch upload synchronization expose validation failures**

Update `sync_pipeline_from_upload` so `waiting_for_rerender` maps to `stage='video'`, `video_status='rerendering'`, and `upload_status='waiting_for_rerender'`; terminal validation failure maps to `stage='upload'`, `upload_status='failed'`, and the validation message in `last_error`.

- [ ] **Step 7: Run recovery tests**

Run: `pytest tests/test_video_recovery.py tests/test_patch_publishing.py -v`

Expected: PASS.

- [ ] **Step 8: Commit recovery orchestration**

```bash
git add app/video_recovery.py app/patch_publishing.py tests/test_video_recovery.py tests/test_patch_publishing.py
git commit -m "feat: schedule bounded video integrity recovery"
```

### Task 5: Put the Authoritative Gate Before Every YouTube Transfer

**Files:**
- Modify: `app/jobqueue/handlers/youtube_upload.py:25-92`
- Modify: `tests/test_jobqueue_handler_youtube.py`
- Modify: `app/upload_worker.py:86-134`
- Modify: `tests/test_upload_worker.py`
- Modify: `app/youtube.py:356-372`

**Interfaces:**
- Consumes: `validate_video`, YouTube validation state helpers, and `schedule_rerender`.
- Produces: queue upload behavior that either transfers a freshly validated file or exits with a terminal/`rerender_scheduled` result without touching YouTube.

- [ ] **Step 1: Write failing queue-handler gate-order tests**

```python
def test_validation_finishes_before_youtube_transfer(tmp_path, monkeypatch):
    conn, upload_id, ctx = seeded_upload_context(tmp_path)
    calls = []
    monkeypatch.setattr(handler, "validate_video", lambda path: calls.append("validate") or valid_result())
    monkeypatch.setattr(handler.youtube, "process_upload", lambda c, uid: calls.append("upload") or {"status": "done", "youtube_video_id": "yt"})
    monkeypatch.setattr(handler.youtube, "publish_completed_upload", lambda *a: {"status": "published"})
    monkeypatch.setattr(handler, "sync_pipeline_from_upload", lambda *a: None)
    handler.handle(ctx)
    assert calls == ["validate", "upload"]
```

Add assertions that `ctx.phase` is `validating` before decode, the upload row reaches `validation_status='valid'`, and `process_upload` sees that committed state.

- [ ] **Step 2: Write failing invalid-output and infrastructure tests**

For recoverable application output, assert `schedule_rerender` is called, YouTube is not called, and handler returns `{"rerender_scheduled": True, "retry_count": 1}` without raising a queue-retriable exception. For external invalid output, assert terminal `JobFatalError`. For `tool_unavailable` and `validation_timeout`, assert YouTube is not called and the raised error allows the queue's normal retry policy while integrity count remains unchanged.

- [ ] **Step 3: Implement validation at the top of `handle`**

Load the upload row by `upload_id`; derive `video_id` from the row rather than trusting payload duplication. Call `mark_validation_started`, `ctx.progress(0, 1, phase="validating")`, log validation start/end, run `validate_video(upload["video_path"])`, and route failure through `schedule_rerender`. Only call existing transfer code after `mark_validation_valid` commits.

Treat `RecoveryDecision.action == "rerender"` as successful orchestration and return without YouTube. Treat output/source terminal failures as `JobFatalError`. Raise `RuntimeError` for infrastructure errors so normal job retry can retry validation, never transfer.

- [ ] **Step 4: Preserve upload/publish regressions after successful validation**

Update existing handler tests to monkeypatch `validate_video` to a valid result in their fixture. Keep all transfer, quota, post-process, thumbnail, playlist, and pipeline assertions unchanged.

- [ ] **Step 5: Remove the legacy worker bypass**

The application has both `UploadWorker` and the unified job queue. Change `UploadWorker._process_upload` to enqueue a `youtube_upload` job through the configured queue or, if retained only for compatibility tests, call a shared `validate_then_process_upload` function used by the queue handler. It must not invoke `youtube.process_upload` directly without validation. Update tests to assert validation precedes transfer in both code paths.

- [ ] **Step 6: Make the compatibility `youtube.upload_video` fail closed**

Because this synchronous wrapper currently calls `process_upload` directly, call `validate_video` before `process_upload` and mark validation state. External invalid input returns a failed result without creating `MediaFileUpload`. This closes non-queue internal callers while routes continue to enqueue jobs.

- [ ] **Step 7: Run all upload lifecycle tests**

Run: `pytest tests/test_jobqueue_handler_youtube.py tests/test_upload_worker.py tests/test_youtube_upload_lifecycle.py tests/test_youtube_upload_progress.py -v`

Expected: PASS; new ordering tests prove no YouTube call occurs before a full successful validation.

- [ ] **Step 8: Commit the upload gate**

```bash
git add app/jobqueue/handlers/youtube_upload.py app/upload_worker.py app/youtube.py tests/test_jobqueue_handler_youtube.py tests/test_upload_worker.py tests/test_youtube_upload_lifecycle.py
git commit -m "feat: validate every video before youtube upload"
```

### Task 6: Make Whole-Book Rendering Atomic and Recovery-Aware

**Files:**
- Modify: `app/jobqueue/handlers/video.py:21-121`
- Modify: `tests/test_jobqueue_handler_video.py`
- Modify: `app/worker.py:350-473`
- Modify: `tests/test_video_job.py`

**Interfaces:**
- Consumes: `publish_validated_video`, optional `recovery_upload_id` payload, and `resume_upload_after_render`.
- Produces: whole-book output that is validated before final-path publication and resumes the same upload row after recovery.

- [ ] **Step 1: Write failing atomic render tests for the queue handler**

```python
def test_whole_book_renders_to_temp_and_replaces_only_after_validation(tmp_path, monkeypatch):
    final = expected_book_video_path(tmp_path)
    final.parent.mkdir(parents=True)
    final.write_bytes(b"old")
    seen = {}
    def fake_generate(patches, book, out_path, **kwargs):
        seen["out"] = Path(out_path)
        Path(out_path).write_bytes(b"new")
    monkeypatch.setattr(video_handler.video_gen, "generate_full_video", fake_generate)
    monkeypatch.setattr(video_handler, "validate_video", lambda p: valid_result(p))
    result = video_handler.handle(ctx)
    assert seen["out"] != final
    assert final.read_bytes() == b"new"
    assert result["output_path"] == str(final)
```

Add failure cases proving old final content survives and book/book_job are not marked done when validation fails.

- [ ] **Step 2: Wrap `_render` with `publish_validated_video`**

Keep existing configuration resolution unchanged. Define a closure that calls `video_gen.generate_full_video(..., temp_path, ...)`; publish to the existing `video_{book_job_id}.mp4` final path. Map validation progress to `ctx.progress(..., phase="validating")` and log error code/message.

- [ ] **Step 3: Write failing recovery-resumption tests**

Enqueue the video job with `payload={"book_job_id": id, "recovery_upload_id": upload_id}`. Assert no new `youtube_uploads` row is created, `resume_upload_after_render` receives the same ID, and normal `_maybe_enqueue_upload` is skipped. For a normal render, assert enqueue includes `render_source_type="book"` and `render_source_id=book_job_id`.

- [ ] **Step 4: Implement provenance and recovery branching**

After successful publish and book updates:

```python
recovery_upload_id = ctx.job.payload.get("recovery_upload_id")
if recovery_upload_id is not None:
    resume_upload_after_render(ctx.conn, recovery_upload_id)
else:
    _maybe_enqueue_upload(ctx, book_id, book_job_id, output_path)
```

Change `_maybe_enqueue_upload` to pass explicit book provenance.

- [ ] **Step 5: Close the legacy `app.worker` whole-book path**

Wrap `Worker._run_video_job` with the same `publish_validated_video` boundary and set explicit book provenance when it enqueues an upload. Do not leave a legacy rendering path that writes directly to the final file.

- [ ] **Step 6: Run whole-book tests**

Run: `pytest tests/test_jobqueue_handler_video.py tests/test_video_job.py tests/test_auto_build.py -v`

Expected: PASS.

- [ ] **Step 7: Commit whole-book integration**

```bash
git add app/jobqueue/handlers/video.py app/worker.py tests/test_jobqueue_handler_video.py tests/test_video_job.py tests/test_auto_build.py
git commit -m "feat: validate whole book renders atomically"
```

### Task 7: Add Queue-Owned Patch Re-rendering

**Files:**
- Create: `app/jobqueue/handlers/patch_video.py`
- Modify: `app/jobqueue/backfill.py:8-33,66-95`
- Modify: `app/patch_publishing.py:179-230`
- Modify: `app/routes/patches.py:430-510`
- Create: `tests/test_jobqueue_handler_patch_video.py`
- Modify: `tests/test_patch_publishing.py`
- Modify: `tests/test_patch_video_upload.py`

**Interfaces:**
- Consumes: patch ID, optional recovery upload ID, `patch_pipeline.config_snapshot`, `media_snapshot`, current patch/book data, `publish_validated_video`, `upsert_patch_video`, and `resume_upload_after_render`.
- Produces: `patch_video.handle(ctx) -> {"output_path": str}` and registered `patch_video` queue jobs.

- [ ] **Step 1: Write failing patch-handler source and configuration tests**

Seed a patch pipeline whose snapshots select a known overlay/background, resolution, fps, codec, quality, audio bitrate, music, intro, and outro. Assert the handler passes those exact original values into the existing `video_gen` functions and does not substitute current defaults during integrity recovery.

```python
def test_recovery_uses_patch_pipeline_snapshots(conn, ctx, monkeypatch):
    captured = {}
    monkeypatch.setattr(patch_video.video_gen, "generate_segment", lambda image, audio, out, **kw: captured.update(image=image, audio=audio, **kw) or Path(out).write_bytes(b"video"))
    monkeypatch.setattr(patch_video, "validate_video", lambda p: valid_result(p))
    patch_video.handle(ctx)
    assert captured["resolution"] == (1280, 720)
    assert captured["fps"] == 30
    assert captured["codec"] == "h264_nvenc"
```

- [ ] **Step 2: Implement the patch video queue handler**

Move the deterministic rendering portion of `run_patch_publish_stage` into a reusable function in the new handler module. Validate source audio and snapshot-referenced media before rendering; raise `JobFatalError("source_unavailable: ...")` with the missing path. Publish atomically, upsert the patch video row, update pipeline to `video_status='done'`, and either resume the recovery upload or continue normal patch publishing.

- [ ] **Step 3: Write failing retry/resume and failure tests**

Assert recovery keeps the same upload ID, valid output resets upload to pending, invalid output leaves it waiting/failed for the upload gate to count correctly, and missing audio/background becomes terminal without overwriting the old patch video.

- [ ] **Step 4: Register and enqueue `patch_video` consistently**

Register the handler in `build_queue`. Replace inline patch rendering in `run_patch_publish_stage` and the relevant patch route background task with `store.enqueue(..., "patch_video", payload={"patch_id": patch_id}, book_id=book_id, dedupe_key=f"patch_video:patch={patch_id}")`. Keep preview-only temporary rendering synchronous only if it never produces an uploadable final video; otherwise route it through the same publish boundary.

- [ ] **Step 5: Add patch backfill behavior**

Backfill `patch_pipeline.video_status IN ('pending', 'rerendering')` only when the pipeline is already in an active publishing flow. For `waiting_for_rerender` uploads with patch provenance, enqueue `patch_video` using `integrity_retry_count` in the dedupe key. Assert running backfill twice creates no duplicate.

- [ ] **Step 6: Run patch pipeline tests**

Run: `pytest tests/test_jobqueue_handler_patch_video.py tests/test_patch_publishing.py tests/test_patch_video_upload.py tests/test_patch_rebuild.py tests/test_patch_publishing_routes.py -v`

Expected: PASS.

- [ ] **Step 7: Commit patch integration**

```bash
git add app/jobqueue/handlers/patch_video.py app/jobqueue/backfill.py app/patch_publishing.py app/routes/patches.py tests/test_jobqueue_handler_patch_video.py tests/test_patch_publishing.py tests/test_patch_video_upload.py tests/test_patch_rebuild.py tests/test_patch_publishing_routes.py
git commit -m "feat: recover invalid patch videos"
```

### Task 8: Persist and Recover Standalone Video Renders

**Files:**
- Create: `app/jobqueue/handlers/standalone_video.py`
- Modify: `app/jobqueue/backfill.py:8-33,66-95`
- Modify: `app/routes/video.py:571-653`
- Modify: `app/video_repository.py:15-43`
- Create: `tests/test_jobqueue_handler_standalone_video.py`
- Modify: `tests/test_video_studio.py`
- Modify: `tests/test_video_batch_extras.py`
- Modify: `tests/test_video_gen_standalone.py`

**Interfaces:**
- Consumes: `videos.id`, persisted `source_audio`, `background_path`, and `render_config_json`, `publish_validated_video`, and `resume_upload_after_render`.
- Produces: `standalone_video.handle(ctx) -> {"output_path": str}`, reproducible standalone rows, and explicit standalone upload provenance.

- [ ] **Step 1: Write failing standalone persistence tests**

Submit/create a standalone render with resolution, fps, codec, quality, audio bitrate, music path/volume, intro, and outro. Assert `videos.source_audio` stores the durable copied audio path, not `original_name`, `background_path` is durable, and `render_config_json` contains every argument needed by `generate_standalone_video`.

- [ ] **Step 2: Persist durable source paths and exact config**

In `_run_single_video`, copy or retain source audio/background under application-owned storage before deleting request temporaries. Build `render_config_json` with this exact shape:

```json
{
  "resolution": "1920x1080",
  "fps": 30,
  "image_type": "none",
  "codec": "libx264",
  "quality": 23,
  "audio_bitrate": "192k",
  "music_path": null,
  "music_volume": 0.15,
  "intro_audio": null,
  "outro_audio": null
}
```

Use actual request values. Paths must refer to retained application files.

- [ ] **Step 3: Write failing standalone recovery handler tests**

Assert the handler loads the persisted row, validates every source path, calls `generate_standalone_video` with the exact saved config, publishes atomically, updates file size/status, and resumes the same upload ID. Missing source/config raises `source_unavailable` and preserves the old final.

- [ ] **Step 4: Implement and register `standalone_video`**

Use `publish_validated_video` around `generate_standalone_video`. Register the handler. Normal standalone completion enqueues upload with `render_source_type="standalone"`, `render_source_id=video_id`; recovery completion calls `resume_upload_after_render`.

- [ ] **Step 5: Route normal standalone rendering through the same boundary**

Replace `shutil.move(tmp_out, final_path)` with `publish_validated_video(final_path, render)` so the initial output is validated before registration and auto-upload. Keep progress result creation only after atomic publication.

- [ ] **Step 6: Add standalone backfill behavior**

For `waiting_for_rerender` uploads with standalone provenance, enqueue `standalone_video` with retry generation in the dedupe key. Do not scan or re-render ordinary local standalone videos at startup.

- [ ] **Step 7: Run standalone tests**

Run: `pytest tests/test_jobqueue_handler_standalone_video.py tests/test_video_studio.py tests/test_video_batch_extras.py tests/test_video_gen_standalone.py -v`

Expected: PASS.

- [ ] **Step 8: Commit standalone integration**

```bash
git add app/jobqueue/handlers/standalone_video.py app/jobqueue/backfill.py app/routes/video.py app/video_repository.py tests/test_jobqueue_handler_standalone_video.py tests/test_video_studio.py tests/test_video_batch_extras.py tests/test_video_gen_standalone.py
git commit -m "feat: recover invalid standalone videos"
```

### Task 9: Attach Provenance to Every Upload Entry Point

**Files:**
- Modify: `app/routes/youtube.py:24-50`
- Modify: `app/routes/video.py:628-640`
- Modify: `app/patch_publishing.py:141-171`
- Modify: `app/jobqueue/handlers/video.py:99-119`
- Modify: `app/upload_worker.py:42-62`
- Modify: `app/worker.py:375-399`
- Modify: `tests/test_patch_video_upload.py`
- Modify: `tests/test_book_detail_youtube_ui.py`
- Modify: `tests/test_youtube_upload_lifecycle.py`

**Interfaces:**
- Consumes: provenance-aware `youtube.enqueue_upload`.
- Produces: explicit provenance when safe and conservative `external` classification otherwise.

- [ ] **Step 1: Write failing provenance matrix tests**

```python
@pytest.mark.parametrize(("entry", "expected_type"), [
    ("book_auto", "book"),
    ("patch_pipeline", "patch"),
    ("standalone_auto", "standalone"),
    ("manual_path", "external"),
])
def test_upload_entry_points_record_provenance(entry, expected_type, seeded_app):
    upload_id = trigger_entry(entry, seeded_app)
    row = seeded_app.conn.execute("SELECT render_source_type FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert row[0] == expected_type
```

- [ ] **Step 2: Pass explicit source type/id at each creation point**

Patch publishing uses `(patch, patch_id)`. Whole-book queue and legacy worker use `(book, book_job_id)`. Standalone auto-upload uses `(standalone, video_id)`. The generic YouTube page and direct path API use `(external, None)` unless the request supplies a verified `video_id`; when supplied, resolve that row and classify by database links, never by filename.

- [ ] **Step 3: Ensure payload/video relationship consistency**

Stop relying on optional `video_id` in queue payload. The upload row is authoritative. Keep payload support temporarily only where existing tests require it, then add a regression proving a mismatched payload cannot update the wrong `videos` row.

- [ ] **Step 4: Run upload-entry regressions**

Run: `pytest tests/test_patch_video_upload.py tests/test_book_detail_youtube_ui.py tests/test_youtube_upload_lifecycle.py tests/test_jobqueue_handler_video.py -v`

Expected: PASS with every created row classified correctly.

- [ ] **Step 5: Commit entry-point provenance**

```bash
git add app/routes/youtube.py app/routes/video.py app/patch_publishing.py app/jobqueue/handlers/video.py app/upload_worker.py app/worker.py tests/test_patch_video_upload.py tests/test_book_detail_youtube_ui.py tests/test_youtube_upload_lifecycle.py tests/test_jobqueue_handler_video.py
git commit -m "feat: identify upload render sources"
```

### Task 10: Recover Interrupted Validation and Re-render Work at Startup

**Files:**
- Modify: `app/jobqueue/backfill.py:66-95`
- Modify: `tests/test_jobqueue_backfill.py`
- Modify: `app/jobqueue/store.py`
- Modify: `tests/test_jobqueue_store.py`

**Interfaces:**
- Consumes: persisted validation status, source provenance, retry count, and source-specific queue job contracts.
- Produces: idempotent startup recovery with no retry reset or duplicate active jobs.

- [ ] **Step 1: Write failing interrupted-state backfill tests**

```python
def test_backfill_recovers_interrupted_validation_without_resetting_count(conn):
    upload_id = seeded_upload(conn, status="pending", validation_status="validating", retry_count=1)
    backfill_pending_jobs(conn)
    row = conn.execute("SELECT validation_status, integrity_retry_count FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    assert tuple(row) == ("pending", 1)
    assert one_upload_job(conn, upload_id)
```

Add book, patch, and standalone `waiting_for_rerender` cases; terminal `failed` exclusion; running backfill twice; and an active matching job preventing duplication.

- [ ] **Step 2: Normalize interrupted validation transactionally**

At backfill start, change only `validation_status='validating' AND status='pending'` rows back to `validation_status='pending'`. Do not alter count or errors. Then enqueue upload validation using generation-aware dedupe keys.

- [ ] **Step 3: Restore source-specific re-render jobs**

Query `validation_status='waiting_for_rerender'`. Dispatch by source type using the exact payloads from Task 4 and dedupe keys containing current `integrity_retry_count`. Unknown/external source transitions to terminal `source_unavailable` rather than looping.

- [ ] **Step 4: Verify dedupe semantics allow later retry generations**

If `store.enqueue` treats completed jobs with the same dedupe key as reusable, retain generation in every recovery key. Add store tests proving generation 2 can enqueue after generation 1 completed while a duplicate generation 2 cannot.

- [ ] **Step 5: Run recovery/backfill tests**

Run: `pytest tests/test_jobqueue_backfill.py tests/test_jobqueue_store.py tests/test_video_recovery.py -v`

Expected: PASS and counts remain unchanged after repeated startup recovery.

- [ ] **Step 6: Commit startup recovery**

```bash
git add app/jobqueue/backfill.py app/jobqueue/store.py tests/test_jobqueue_backfill.py tests/test_jobqueue_store.py tests/test_video_recovery.py
git commit -m "feat: recover interrupted video validation jobs"
```

### Task 11: Reuse the Validator in the Integrity CLI

**Files:**
- Modify: `scripts/check_video_integrity.py`
- Create: `tests/test_check_video_integrity_script.py`

**Interfaces:**
- Consumes: `video_integrity.validate_video`.
- Produces: existing scan CLI with shared verdicts and exit code 1 for blocking failures.

- [ ] **Step 1: Write failing CLI delegation tests**

```python
def test_cli_inspect_delegates_to_shared_validator(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(script, "validate_video", lambda p: calls.append(Path(p)) or invalid_result("decode_failed"))
    row = script.inspect(video)
    assert calls == [video]
    assert row["verdict"] == "broken"
    assert row["reasons"] == ["decode_failed: broken"]
```

- [ ] **Step 2: Replace duplicate probe/drift logic**

Keep target discovery, YouTube ID annotation, grouped output, `--uploaded`, and exit-code behavior. Translate `ValidationResult.valid=False` to `broken`, warnings to `suspect`, and clean valid results to `ok`.

- [ ] **Step 3: Run CLI tests and help command**

Run: `pytest tests/test_check_video_integrity_script.py -v`

Expected: PASS.

Run: `python scripts/check_video_integrity.py --help`

Expected: exit 0 and display `--uploaded`.

- [ ] **Step 4: Commit CLI reuse**

```bash
git add scripts/check_video_integrity.py tests/test_check_video_integrity_script.py
git commit -m "refactor: share video integrity checks with cli"
```

### Task 12: Show Validation and Recovery State in the UI

**Files:**
- Modify: `app/templates/youtube.html`
- Modify: `app/templates/video.html`
- Modify: `app/templates/book_detail.html`
- Modify: `app/routes/books.py:350-390`
- Modify: `tests/test_book_detail_youtube_ui.py`
- Modify: `tests/test_video_studio.py`
- Modify: `tests/test_youtube_upload_lifecycle.py`

**Interfaces:**
- Consumes: upload validation fields, `videos.upload_status`, and patch pipeline state.
- Produces: visible `validating`, `rerendering 1/2`, `rerendering 2/2`, and terminal reason text.

- [ ] **Step 1: Write failing rendered-HTML tests**

```python
def test_youtube_page_shows_validation_and_retry_state(client, seeded_upload):
    set_upload(seeded_upload, validation_status="waiting_for_rerender", integrity_retry_count=1,
               validation_error_code="decode_failed", validation_error_message="corrupt frame")
    html = client.get("/youtube").text
    assert "rerendering 1/2" in html
    assert "decode_failed" in html
    assert "corrupt frame" in html
```

Add `validating`, retry `2/2`, external invalid guidance, source-unavailable missing-input guidance, standalone video list status, and patch book-detail state.

- [ ] **Step 2: Expose fields in route query/view models**

Include `validation_status`, `validation_error_code`, `validation_error_message`, `integrity_retry_count`, and source type wherever upload data is reduced before templates. Do not add new polling endpoints; reuse existing upload/queue polling payloads.

- [ ] **Step 3: Render concise state labels using existing design language**

Use exact user-facing labels:

```text
validating
rerendering 1/2
rerendering 2/2
validation failed: <code> - <message>
automatic re-render unavailable for external file
source unavailable: <message>
```

Escape database text through Jinja defaults; do not inject it with `|safe` or `innerHTML`.

- [ ] **Step 4: Run UI tests**

Run: `pytest tests/test_book_detail_youtube_ui.py tests/test_video_studio.py tests/test_youtube_upload_lifecycle.py tests/test_queue_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit UI state reporting**

```bash
git add app/templates/youtube.html app/templates/video.html app/templates/book_detail.html app/routes/books.py tests/test_book_detail_youtube_ui.py tests/test_video_studio.py tests/test_youtube_upload_lifecycle.py
git commit -m "feat: show video validation recovery status"
```

### Task 13: End-to-End Acceptance and Regression Verification

**Files:**
- Create: `tests/test_video_integrity_pipeline.py`
- Modify: `README.md:53-62,159-215`

**Interfaces:**
- Consumes: all completed validation, provenance, rendering, recovery, backfill, and UI interfaces.
- Produces: acceptance-level proof and operator documentation.

- [ ] **Step 1: Write an application-owned recovery integration test**

Use a temporary SQLite database and real queue handlers with FFmpeg and YouTube calls monkeypatched at process boundaries. Simulate initial `decode_failed`, retry `1/2`, another `decode_failed`, retry `2/2`, then a valid render and successful upload. Assert:

```python
assert upload_ids_created == [original_upload_id]
assert render_attempts == 2
assert youtube_calls == [original_upload_id]
assert final_upload["integrity_retry_count"] == 2
assert final_upload["validation_status"] == "valid"
assert final_upload["status"] == "done"
```

- [ ] **Step 2: Write terminal and restart integration tests**

Cover failure after retry `2/2` with zero YouTube calls; external invalid input with zero render jobs; process restart while `validating`; restart while each source type is `waiting_for_rerender`; and source removal resulting in `source_unavailable` without count increment.

- [ ] **Step 3: Run the new acceptance suite**

Run: `pytest tests/test_video_integrity_pipeline.py -v`

Expected: PASS.

- [ ] **Step 4: Document operator-visible behavior**

Add a README section explaining that uploads first fully decode the file, the additional validation time, the two automatic re-render attempts, external-file behavior, state labels, and the audit command:

```powershell
python scripts/check_video_integrity.py
python scripts/check_video_integrity.py --uploaded
```

- [ ] **Step 5: Run focused feature verification**

Run:

```powershell
pytest tests/test_video_integrity.py tests/test_video_publish.py tests/test_video_recovery.py tests/test_video_integrity_persistence.py tests/test_jobqueue_handler_youtube.py tests/test_jobqueue_handler_video.py tests/test_jobqueue_handler_patch_video.py tests/test_jobqueue_handler_standalone_video.py tests/test_jobqueue_backfill.py tests/test_video_integrity_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the complete test suite**

Run: `pytest -q`

Expected: all tests pass with no failures or errors.

- [ ] **Step 7: Run static and repository checks**

Run: `git diff --check`

Expected: no output and exit 0.

Run: `python -m compileall -q app scripts`

Expected: exit 0.

- [ ] **Step 8: Perform one real-media smoke test when FFmpeg is available**

Create a short local fixture without contacting YouTube:

```powershell
& "assets/bin/ffmpeg.exe" -y -f lavfi -i "color=c=black:s=320x240:d=2" -f lavfi -i "sine=frequency=1000:duration=2" -c:v libx264 -c:a aac "$env:TEMP\integrity-smoke.mp4"
python -c "from app.video_integrity import validate_video; r=validate_video(r'$env:TEMP\integrity-smoke.mp4'); print(r); raise SystemExit(0 if r.valid else 1)"
```

Expected: printed `ValidationResult(valid=True, ...)` and exit 0. If bundled FFmpeg is unavailable, record the skipped smoke test explicitly; automated subprocess tests remain mandatory.

- [ ] **Step 9: Commit acceptance tests and documentation**

```bash
git add tests/test_video_integrity_pipeline.py README.md
git commit -m "test: verify video integrity recovery pipeline"
```

## Completion Criteria

- Every queue, legacy worker, route, and compatibility upload path validates the current file fully before constructing or invoking a YouTube transfer.
- Full validation checks file presence/size, ffprobe readability, required streams, finite positive duration, supported output policy, fatal A/V drift, and complete audio/video decode.
- Whole-book, patch, and standalone initial renders and re-renders use temporary sibling files, validate them, and atomically replace the final path only on success.
- Application-owned invalid outputs schedule exactly retry `1/2` and `2/2` with original configuration; failure after the second re-render is terminal.
- External invalid files never schedule render work and provide an actionable terminal message.
- Infrastructure/source errors do not consume the integrity retry budget, and no failure path bypasses validation.
- The same upload row and all YouTube metadata/publishing relationships survive re-rendering.
- Startup recovery restores interrupted validation and source-specific re-render jobs idempotently without resetting retry counts.
- UI and job logs expose `validating`, retry generation, and bounded terminal errors.
- Focused tests, full `pytest -q`, `git diff --check`, and `compileall` pass; the real-media smoke test passes when FFmpeg is available.

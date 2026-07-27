# Automated Patch Video and YouTube Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recoverable per-patch automation flow from generated audio through overlay thumbnail, multi-source video with webcam PiP, YouTube upload, custom thumbnail, and playlist insertion.

**Architecture:** Extend the existing SQLite worker architecture with validated JSON settings and one persisted pipeline row per patch. Keep media composition in a focused FFmpeg module and YouTube post-processing in the existing upload worker, with each external stage persisted independently for idempotent retry.

**Tech Stack:** Python 3.10-3.12, FastAPI, SQLite, Pydantic v2, Jinja2, Pillow, FFmpeg/FFprobe, Google YouTube Data API v3, pytest.

## Global Constraints

- Produce one independently retryable video per patch.
- Reuse the existing patch overlay PNG as both generated thumbnail and YouTube thumbnail.
- Play ordered image/video backgrounds for 3-300 seconds each and loop the full list to narration duration.
- Render webcam media as independently looped PiP; discard all background and webcam audio.
- Accept only validated FFmpeg fields; never accept raw FFmpeg arguments.
- Resolve settings as built-in defaults, then system defaults, then per-book overrides; snapshot them when enqueueing.
- Keep existing manual standalone, patch, and full-book video flows operational.
- Do not add a broker, distributed queue, AI image generation, or YouTube live broadcasting.
- Do not modify or revert unrelated dirty-worktree changes.

## File Map

- Create `app/automation_config.py`: typed settings, merge, validation, template rendering.
- Create `app/automation_repository.py`: settings, media selection, pipeline, playlist-map persistence.
- Create `app/video_compositor.py`: probes and constructs the multi-source/PiP FFmpeg command.
- Create `app/automation_worker.py`: claims and executes thumbnail/video stages.
- Create `app/routes/automation.py`: settings, media selection, status, enqueue, and retry API/UI routes.
- Create `app/templates/automation_settings.html`: system automation settings UI.
- Modify `app/db.py`: schema and additive migrations.
- Modify `app/models.py`: book automation configuration field.
- Modify `app/main.py`: initialize and run automation worker; register routes.
- Modify `app/image_overlay.py`: expose an idempotent patch-thumbnail helper if the existing helper is not sufficient.
- Modify `app/video_gen.py`: delegate multi-source patch rendering without breaking single-source callers.
- Modify `app/video_repository.py`: associate a video with book/patch and upsert generated patch output.
- Modify `app/youtube.py`: expanded OAuth, single-row upload lifecycle, thumbnails, playlists.
- Modify `app/upload_worker.py`: upload and post-process the same queue row stage by stage.
- Modify `app/routes/youtube.py` and `app/templates/youtube.html`: detailed defaults, playlist sync, stage history, retry.
- Modify `app/routes/books.py`, `app/routes/patches.py`, and `app/templates/book_detail.html`: book overrides, media ordering, progress, enqueue/retry.
- Add focused tests under `tests/` for each boundary.

---

### Task 1: Validated Automation Configuration

**Files:**
- Create: `app/automation_config.py`
- Test: `tests/test_automation_config.py`

**Interfaces:**
- Produces: `AutomationConfig`, `merge_automation_config(system: dict, override: dict | None) -> AutomationConfig`, `render_metadata_template(template: str, values: dict[str, object]) -> str`.
- Consumes: Pydantic v2 already installed through `pydantic-settings`.

- [ ] **Step 1: Write failing tests for defaults, overrides, bounds, encoder rules, and templates**

```python
import pytest
from pydantic import ValidationError
from app.automation_config import merge_automation_config, render_metadata_template

def test_override_inherits_defaults():
    cfg = merge_automation_config(
        {"video": {"fps": 25, "resolution": "1280x720"}},
        {"video": {"fps": 30}},
    )
    assert cfg.video.resolution == "1280x720"
    assert cfg.video.fps == 30
    assert cfg.video.encoder == "libx264"

def test_rejects_raw_ffmpeg_and_invalid_slot_duration():
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"video": {"ffmpeg_args": "-f lavfi", "background_duration_seconds": 2}})

def test_rejects_wrong_encoder_preset():
    with pytest.raises(ValidationError):
        merge_automation_config({}, {"video": {"encoder": "h264_nvenc", "preset": "medium"}})

def test_metadata_template_allowlist():
    assert render_metadata_template("{book_title} - {patch_index}", {"book_title": "Book", "patch_index": 2}) == "Book - 2"
    with pytest.raises(ValueError, match="unknown template field"):
        render_metadata_template("{secret}", {})
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run: `pytest tests/test_automation_config.py -q`

Expected: FAIL during import with `ModuleNotFoundError: app.automation_config`.

- [ ] **Step 3: Implement strict nested Pydantic models and recursive merge**

Implement models with `ConfigDict(extra="forbid")`: `VideoConfig`, `WebcamConfig`, `YouTubeConfig`, and `AutomationConfig`. Use the exact allow-lists and defaults from the design; validate CRF 0-51, CQ 0-51, webcam width 10-50%, non-negative margin/border, and source duration 3-300. Define CPU presets `ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow` and NVENC presets `p1` through `p7`. Recursively merge dictionaries before `AutomationConfig.model_validate()`.

- [ ] **Step 4: Run the focused tests**

Run: `pytest tests/test_automation_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the configuration boundary**

```bash
git add app/automation_config.py tests/test_automation_config.py
git commit -m "feat: add validated automation configuration"
```

### Task 2: Automation and Media Persistence

**Files:**
- Modify: `app/db.py:7-245,262-349`
- Modify: `app/models.py:7-43`
- Create: `app/automation_repository.py`
- Test: `tests/test_automation_repository.py`

**Interfaces:**
- Consumes: `AutomationConfig` and `merge_automation_config()` from Task 1.
- Produces: `get_system_config`, `save_system_config`, `get_effective_config`, `save_book_override`, `upsert_media_asset`, `set_book_media`, `list_book_media`, `enqueue_patch_pipeline`, `claim_next_pipeline_stage`, `update_pipeline_stage`, `get_or_create_playlist_map`.

- [ ] **Step 1: Write schema and repository contract tests**

```python
import json
from app import automation_repository, db, repository

def test_settings_override_media_order_and_idempotent_enqueue(tmp_path):
    conn = db.connect(str(tmp_path / "app.db")); db.init_schema(conn)
    automation_repository.save_system_config(conn, {"video": {"fps": 25}})
    # Insert the smallest valid book/patch using repository helpers or direct SQL matching db.py.
    now = "2026-07-26T00:00:00+00:00"
    book_id = conn.execute("INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) VALUES ('B','b.epub','b.epub',1,'ready',?,?)", (now, now)).lastrowid
    patch_id = conn.execute("INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,status,created_at,updated_at) VALUES (?,0,0,0,'done',?,?)", (book_id, now, now)).lastrowid
    automation_repository.save_book_override(conn, book_id, {"video": {"fps": 30}})
    assert automation_repository.get_effective_config(conn, book_id).video.fps == 30
    a = automation_repository.upsert_media_asset(conn, "/tmp/a.jpg", "a.jpg", "image")
    b = automation_repository.upsert_media_asset(conn, "/tmp/b.mp4", "b.mp4", "video")
    automation_repository.set_book_media(conn, book_id, "background", [b["id"], a["id"]])
    assert [x["id"] for x in automation_repository.list_book_media(conn, book_id, "background")] == [b["id"], a["id"]]
    first = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    second = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    assert first["id"] == second["id"]
    assert json.loads(first["config_snapshot"])["video"]["fps"] == 30
```

- [ ] **Step 2: Run the test and verify missing tables/functions**

Run: `pytest tests/test_automation_repository.py -q`

Expected: FAIL because `automation_repository` does not exist.

- [ ] **Step 3: Add additive schema and migration columns**

Add `automation_settings`, `media_assets`, `book_media_selection`, `patch_pipeline`, and `youtube_playlist_map` exactly as specified. Add nullable `book.automation_config`; add nullable `videos.book_id`, `videos.patch_id` with a unique partial index for patch videos; add upload/post-processing status, error, playlist, snapshot, and retry columns to `youtube_uploads`. Every migration checks `PRAGMA table_info` before `ALTER TABLE`.

- [ ] **Step 4: Implement repository functions with short transactions**

Use canonical JSON (`sort_keys=True`) for snapshots. `enqueue_patch_pipeline()` must use `INSERT ... ON CONFLICT(patch_id) DO NOTHING`, then select the row. Claim only rows whose `next_retry_at` is null or due, and atomically set the claimed stage to processing.

- [ ] **Step 5: Run persistence and existing DB tests**

Run: `pytest tests/test_automation_repository.py tests/test_database_io.py tests/test_reset_all_jobs.py -q`

Expected: PASS.

- [ ] **Step 6: Commit persistence**

```bash
git add app/db.py app/models.py app/automation_repository.py tests/test_automation_repository.py
git commit -m "feat: persist automation pipeline settings"
```

### Task 3: Reusable Media Assets and Selection API

**Files:**
- Create: `app/routes/automation.py`
- Modify: `app/main.py:15-160`
- Modify: `app/routes/video.py:69-87,688-784`
- Test: `tests/test_automation_routes.py`

**Interfaces:**
- Consumes: media/settings repository functions from Task 2.
- Produces: `GET/PUT /automation/settings`, `GET /automation/media`, `PUT /books/{book_id}/automation/media/{role}`, and retained `/video/backgrounds` compatibility.

- [ ] **Step 1: Write route tests for validation, path safety, and ordering**

```python
def test_save_settings_rejects_unknown_ffmpeg_field(client):
    response = client.put("/automation/settings", json={"video": {"raw_args": "-y"}})
    assert response.status_code == 422

def test_book_media_rejects_wrong_role_and_preserves_order(client, seeded_book, seeded_media):
    assert client.put(f"/books/{seeded_book}/automation/media/nope", json={"asset_ids": []}).status_code == 404
    ids = [seeded_media[1], seeded_media[0]]
    response = client.put(f"/books/{seeded_book}/automation/media/background", json={"asset_ids": ids})
    assert response.status_code == 200
    assert [x["id"] for x in response.json()["assets"]] == ids
```

- [ ] **Step 2: Run and confirm 404/missing routes**

Run: `pytest tests/test_automation_routes.py -q`

Expected: FAIL because automation routes are absent.

- [ ] **Step 3: Implement routes and register the router**

Restrict roles to `background` and `webcam`. Reuse `_safe_background_path` behavior while importing existing files into `media_assets`; upload endpoints continue writing under `DATA_ROOT/backgrounds`. Return Pydantic validation details as HTTP 422. Never accept arbitrary paths in selection requests, only known asset IDs.

- [ ] **Step 4: Preserve old background endpoints as adapters**

Keep existing response keys for `/video/backgrounds`, `/video/backgrounds/preview`, and `/video/upload-background`; additionally upsert uploaded/listed files into `media_assets`.

- [ ] **Step 5: Run route and media regressions**

Run: `pytest tests/test_automation_routes.py tests/test_media_manage.py tests/test_video_background.py -q`

Expected: PASS.

- [ ] **Step 6: Commit media APIs**

```bash
git add app/routes/automation.py app/main.py app/routes/video.py tests/test_automation_routes.py
git commit -m "feat: add reusable automation media selections"
```

### Task 4: Multi-source FFmpeg Compositor and Webcam PiP

**Files:**
- Create: `app/video_compositor.py`
- Modify: `app/video_gen.py:76-260,441-489`
- Test: `tests/test_video_compositor.py`

**Interfaces:**
- Consumes: `VideoConfig`, `WebcamConfig`, media rows, `get_ffmpeg_path()`, and `get_ffprobe_path()`.
- Produces: `probe_media(path: str) -> dict`, `build_composite_command(audio_path, backgrounds, webcam, output_path, config, music_path=None) -> list[str]`, `render_composite(...) -> None`.

- [ ] **Step 1: Write command tests for image, video, mixed timeline, narration mapping, and PiP**

```python
def test_mixed_background_and_webcam_command_drops_source_audio(tmp_path, monkeypatch):
    cmd = build_composite_command(
        "voice.wav",
        [{"file_path": "a.jpg", "kind": "image"}, {"file_path": "b.mp4", "kind": "video"}],
        [{"file_path": "cam.mp4", "kind": "video"}],
        "out.mp4",
        merge_automation_config({}, {"video": {"background_duration_seconds": 12}, "webcam": {"enabled": True}}),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=0" in graph
    assert "overlay=" in graph
    assert "12" in graph
    assert "0:a" not in [cmd[i + 1] for i, value in enumerate(cmd[:-1]) if value == "-map"]
    assert "-shortest" in cmd
```

Also assert image inputs use `-loop 1`, video inputs use `-stream_loop -1`, all visual chains contain scale/pad or crop, webcam includes the configured position expression, and final maps select only composed video and narration/music.

- [ ] **Step 2: Run and verify missing compositor**

Run: `pytest tests/test_video_compositor.py -q`

Expected: FAIL on missing module.

- [ ] **Step 3: Implement command construction without running a shell**

Construct a list passed directly to `subprocess.run`; never use `shell=True`. Give narration a deterministic input index, normalize every visual stream to resolution/FPS/SAR/yuv420p, trim slots, concat one cycle, use `-stream_loop -1` inputs so the cycle can cover narration, overlay PiP, and terminate with `-shortest`. Build optional border with `pad`; implement rounded corners only when the installed FFmpeg filter support check succeeds, otherwise reject nonzero radius during capability validation.

- [ ] **Step 4: Add encoder capability validation**

Cache `ffmpeg -hide_banner -encoders` output. `validate_ffmpeg_capabilities(config)` raises a clear error when `h264_nvenc` is unavailable; it does not fall back to CPU.

- [ ] **Step 5: Add a compatibility delegation from video_gen**

Add optional `backgrounds`, `webcam_sources`, and `automation_config` keyword parameters to standalone/patch generation. When absent, retain the exact existing single-background command path; when present, call `render_composite()`.

- [ ] **Step 6: Run compositor and all existing video tests**

Run: `pytest tests/test_video_compositor.py tests/test_video_background.py tests/test_video_gen_standalone.py tests/test_video_job.py tests/test_video_studio.py -q`

Expected: PASS.

- [ ] **Step 7: Commit compositor**

```bash
git add app/video_compositor.py app/video_gen.py tests/test_video_compositor.py
git commit -m "feat: compose looping backgrounds and webcam pip"
```

### Task 5: Patch Thumbnail and Video Pipeline Stages

**Files:**
- Create: `app/automation_worker.py`
- Modify: `app/image_overlay.py`
- Modify: `app/video_repository.py:15-43,116-131`
- Modify: `app/main.py`
- Test: `tests/test_automation_worker.py`

**Interfaces:**
- Consumes: pipeline repository, `image_overlay.ensure_patch_overlay`, `video_compositor.render_composite`, `video_repository`.
- Produces: `AutomationWorker.start()`, `stop()`, `run_once()`, `enqueue_book(book_id)`, and a durable transition from `audio_ready` to `video_ready`.

- [ ] **Step 1: Write worker tests with fake thumbnail/render functions**

```python
async def test_pipeline_generates_thumbnail_before_video_and_recovers(tmp_path, seeded_pipeline, monkeypatch):
    calls = []
    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", lambda *a, **k: calls.append("thumbnail") or str(tmp_path / "thumb.png"))
    monkeypatch.setattr("app.video_compositor.render_composite", lambda *a, **k: calls.append("video") or Path(k["output_path"]).write_bytes(b"mp4"))
    worker = AutomationWorker(seeded_pipeline.conn, seeded_pipeline.lock, tmp_path)
    await worker.run_once(); await worker.run_once()
    row = automation_repository.get_patch_pipeline(seeded_pipeline.conn, seeded_pipeline.patch_id)
    assert calls == ["thumbnail", "video"]
    assert row["stage"] == "video_ready"
    assert row["video_id"] is not None
```

Add tests that missing audio waits without attempts, no media uses overlay fallback, render failure stores bounded error, and a restart skips an existing valid thumbnail/video.

- [ ] **Step 2: Run and confirm missing worker**

Run: `pytest tests/test_automation_worker.py -q`

Expected: FAIL on missing class.

- [ ] **Step 3: Implement thumbnail and render claims**

Run file/FFmpeg work through `asyncio.to_thread` outside `db_lock`. Keep DB transactions limited to claim and stage update. Name output `DATA_ROOT/books/{book_id}/patch_videos/{patch_id}.mp4`. Use an upserted `videos` row linked to book/patch.

- [ ] **Step 4: Start and stop worker in app lifespan**

Initialize only when `ENABLE_WORKER=true`, sharing the existing connection and lock. Shutdown waits/cancels using the existing lifespan policy without deleting outputs.

- [ ] **Step 5: Run worker, video repository, and lifecycle tests**

Run: `pytest tests/test_automation_worker.py tests/test_patch_video_upload.py tests/test_enable_worker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit patch rendering pipeline**

```bash
git add app/automation_worker.py app/image_overlay.py app/video_repository.py app/main.py tests/test_automation_worker.py
git commit -m "feat: automate patch thumbnails and videos"
```

### Task 6: Early Batch Thumbnail Enqueue and Book Controls

**Files:**
- Modify: `app/routes/books.py`
- Modify: `app/routes/patches.py`
- Modify: `app/routes/automation.py`
- Test: `tests/test_automation_enqueue.py`

**Interfaces:**
- Consumes: `AutomationWorker.enqueue_book`, effective settings, pipeline rows.
- Produces: `POST /books/{id}/automation/enqueue`, `POST /books/{id}/automation/retry/{patch_id}`, and automatic enqueue after patch creation/build.

- [ ] **Step 1: Write enqueue and retry tests**

```python
def test_enqueue_book_creates_one_pipeline_per_patch_and_is_idempotent(client, seeded_book_with_patches):
    first = client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    second = client.post(f"/books/{seeded_book_with_patches}/automation/enqueue")
    assert first.status_code == second.status_code == 200
    assert first.json()["pipeline_ids"] == second.json()["pipeline_ids"]

def test_retry_keeps_completed_youtube_upload(client, uploaded_pipeline):
    response = client.post(f"/books/{uploaded_pipeline.book_id}/automation/retry/{uploaded_pipeline.patch_id}")
    assert response.json()["stage"] == uploaded_pipeline.failed_stage
    assert response.json()["youtube_video_id"] == uploaded_pipeline.youtube_video_id
```

- [ ] **Step 2: Run and verify route failure**

Run: `pytest tests/test_automation_enqueue.py -q`

Expected: FAIL with route 404.

- [ ] **Step 3: Implement explicit and automatic enqueue**

When automation is enabled, enqueue pipeline rows immediately after patches are created so thumbnails can generate before audio. Do not enqueue the old automatic full-book `book_job`; retain manual full-book enqueue routes. Retry clears error/backoff only for the failed stage and downstream non-completed stages.

- [ ] **Step 4: Run enqueue and existing patch-build tests**

Run: `pytest tests/test_automation_enqueue.py tests/test_auto_build.py tests/test_patch_images.py -q`

Expected: PASS.

- [ ] **Step 5: Commit orchestration hooks**

```bash
git add app/routes/books.py app/routes/patches.py app/routes/automation.py tests/test_automation_enqueue.py
git commit -m "feat: enqueue per-patch automation early"
```

### Task 7: Fix Single-record YouTube Upload Lifecycle

**Files:**
- Modify: `app/youtube.py:214-346`
- Modify: `app/upload_worker.py:41-122`
- Test: `tests/test_youtube_upload_lifecycle.py`

**Interfaces:**
- Consumes: existing `youtube_uploads` migration columns.
- Produces: `process_upload(conn, upload_id) -> dict`, updating one row from pending to uploading/done/failed; worker never calls a function that inserts a second row.

- [ ] **Step 1: Write a regression test proving one queue row remains one row**

```python
def test_pending_upload_updates_same_row(db_conn, fake_youtube_service, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"video")
    upload_id = youtube.enqueue_upload(db_conn, str(video), "Title")
    result = youtube.process_upload(db_conn, upload_id)
    rows = db_conn.execute("SELECT * FROM youtube_uploads").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == upload_id
    assert rows[0]["youtube_video_id"] == result["youtube_video_id"]
```

- [ ] **Step 2: Run and demonstrate duplicate-row behavior/missing API**

Run: `pytest tests/test_youtube_upload_lifecycle.py -q`

Expected: FAIL because `process_upload` is absent.

- [ ] **Step 3: Refactor upload without changing manual behavior**

Extract `_execute_video_insert()` for the API call. `process_upload(upload_id)` loads and marks the existing row, uploads, then persists ID/status on that row. Keep `upload_video(...)` as a compatibility wrapper that enqueues once and calls `process_upload` once. Decode JSON tags before API calls.

- [ ] **Step 4: Point UploadWorker at the row-based API**

Replace `_do_upload(upload)` with `youtube.process_upload(self.conn, upload["id"])`. Preserve linked `videos.upload_status` updates.

- [ ] **Step 5: Run upload and route regressions**

Run: `pytest tests/test_youtube_upload_lifecycle.py tests/test_patch_video_upload.py tests/test_video_batch_extras.py -q`

Expected: PASS.

- [ ] **Step 6: Commit upload lifecycle fix**

```bash
git add app/youtube.py app/upload_worker.py tests/test_youtube_upload_lifecycle.py
git commit -m "fix: process queued youtube upload once"
```

### Task 8: YouTube OAuth, Thumbnail, and Playlist Post-processing

**Files:**
- Modify: `app/youtube.py:32-211,293-346`
- Modify: `app/upload_worker.py`
- Modify: `app/automation_repository.py`
- Test: `tests/test_youtube_postprocess.py`

**Interfaces:**
- Produces: `list_playlists`, `create_playlist`, `set_thumbnail`, `playlist_contains_video`, `add_video_to_playlist`, `resolve_book_playlist`, and resumable post-processing in `UploadWorker`.

- [ ] **Step 1: Write mocked API tests for scopes and idempotency**

```python
def test_postprocess_sets_thumbnail_and_reuses_playlist(db_conn, fake_service, uploaded_pipeline):
    first = youtube.postprocess_upload(db_conn, uploaded_pipeline.upload_id)
    second = youtube.postprocess_upload(db_conn, uploaded_pipeline.upload_id)
    assert first["status"] == second["status"] == "published"
    assert fake_service.thumbnails_set_calls == 1
    assert fake_service.playlists_insert_calls == 1
    assert fake_service.playlist_items_insert_calls == 1
```

Also test existing playlist mode, no-playlist mode, missing scopes -> `auth_required`, thumbnail failure followed by retry, and exact patch ordering.

- [ ] **Step 2: Run and verify APIs are absent**

Run: `pytest tests/test_youtube_postprocess.py -q`

Expected: FAIL on missing post-process functions.

- [ ] **Step 3: Expand OAuth scopes**

Use `youtube.upload`, `youtube`, and `youtube.force-ssl` only as required by the Data API operations. Persist granted scopes or validate credentials by a lightweight channel/playlist request. Return an actionable reconnect error for old tokens.

- [ ] **Step 4: Implement API wrappers and playlist mapping**

Limit playlist listing pagination, escape no user values because API bodies are structured dictionaries, use exact book/channel mapping, and check existing playlist items before insertion. Persist YouTube video ID before any post-processing call.

- [ ] **Step 5: Process post-upload stages independently**

Upload worker selects rows with upload done but thumbnail/playlist incomplete. Each successful stage commits immediately. Transient 429/5xx failures set bounded exponential delay; auth failures set `auth_required`; permanent 4xx stores the stage error for manual correction.

- [ ] **Step 6: Run YouTube tests**

Run: `pytest tests/test_youtube_postprocess.py tests/test_youtube_upload_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 7: Commit YouTube post-processing**

```bash
git add app/youtube.py app/upload_worker.py app/automation_repository.py tests/test_youtube_postprocess.py
git commit -m "feat: set youtube thumbnails and playlists"
```

### Task 9: System, YouTube, and Book Configuration UI

**Files:**
- Create: `app/templates/automation_settings.html`
- Modify: `app/routes/automation.py`
- Modify: `app/routes/youtube.py`
- Modify: `app/templates/youtube.html`
- Modify: `app/routes/books.py`
- Modify: `app/templates/book_detail.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_automation_ui.py`

**Interfaces:**
- Consumes: settings/media/playlist/status APIs from previous tasks.
- Produces: `/automation/settings-page`, detailed YouTube defaults and playlist sync UI, book overrides/media ordering/progress/retry UI.

- [ ] **Step 1: Write route/render smoke tests for required controls**

```python
def test_automation_settings_page_contains_safe_fields_only(client):
    html = client.get("/automation/settings-page").text
    for name in ("resolution", "fps", "encoder", "quality", "preset", "audio_bitrate", "background_duration_seconds", "webcam_position"):
        assert f'name="{name}"' in html
    assert "ffmpeg_args" not in html

def test_youtube_page_contains_playlist_and_postprocess_columns(client):
    html = client.get("/youtube").text
    assert "playlist" in html.lower()
    assert "thumbnail" in html.lower()
```

- [ ] **Step 2: Run and verify missing controls**

Run: `pytest tests/test_automation_ui.py -q`

Expected: FAIL assertions.

- [ ] **Step 3: Implement the system settings form**

Use existing card/form CSS. Include automation toggles, FFmpeg allow-list selects/numeric constraints, webcam fields, and YouTube defaults. Submit JSON to `PUT /automation/settings`, render field-specific 422 errors, and show FFmpeg/NVENC capability status.

- [ ] **Step 4: Extend YouTube UI**

Add reconnect/scope status, playlist refresh/search/select, detailed metadata defaults, and separate upload/thumbnail/playlist columns with retry buttons. Keep the existing manual upload form.

- [ ] **Step 5: Add compact book overrides and media ordering**

Add auto-flow, upload, preset, privacy/templates/playlist, ordered background/webcam selectors, and per-patch stage status/retry. Use native select, number, checkbox, and drag/reorder controls; preserve current patch actions and dirty edits in `book_detail.html`.

- [ ] **Step 6: Run UI and existing route tests**

Run: `pytest tests/test_automation_ui.py tests/test_routes_preview.py tests/test_video_studio.py -q`

Expected: PASS.

- [ ] **Step 7: Commit UI**

```bash
git add app/templates/automation_settings.html app/routes/automation.py app/routes/youtube.py app/templates/youtube.html app/routes/books.py app/templates/book_detail.html app/templates/base.html tests/test_automation_ui.py
git commit -m "feat: configure patch automation and youtube"
```

### Task 10: End-to-end Recovery and Regression Verification

**Files:**
- Create: `tests/test_automation_pipeline_integration.py`
- Modify: `README.md:5-178`

**Interfaces:**
- Consumes: the complete pipeline.
- Produces: executable proof of audio-ready to published recovery and operator documentation.

- [ ] **Step 1: Write a complete integration test with real short FFmpeg fixture and fake YouTube**

The test creates a 1-second WAV and two tiny generated color images in `tmp_path`, inserts a book and two audio-ready patches, selects backgrounds/webcam, runs worker stages, simulates restart with a new worker instance after video upload, and asserts both patches are `published`, have distinct videos/thumbnails, and share one playlist in patch order. Skip only when `get_ffmpeg_path()` cannot execute `-version`; do not skip for application failures.

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_automation_pipeline_integration.py -q`

Expected: PASS, or one explicit environment skip when FFmpeg is unavailable.

- [ ] **Step 3: Document setup and operation**

Update README with expanded OAuth reconnect requirement, automation settings page, media roles, safe FFmpeg fields, state/retry behavior, and commands to verify FFmpeg encoders. Correct the old claim that automatic output is only a full-book video.

- [ ] **Step 4: Run all targeted tests together**

Run: `pytest tests/test_automation_config.py tests/test_automation_repository.py tests/test_automation_routes.py tests/test_video_compositor.py tests/test_automation_worker.py tests/test_automation_enqueue.py tests/test_youtube_upload_lifecycle.py tests/test_youtube_postprocess.py tests/test_automation_ui.py tests/test_automation_pipeline_integration.py -q`

Expected: PASS, with only the documented FFmpeg environment skip allowed.

- [ ] **Step 5: Run the full regression suite**

Run: `pytest -q`

Expected: PASS. Investigate every failure; do not classify failures as unrelated without reproducing them against the pre-task revision or confirming they come from concurrent worktree changes.

- [ ] **Step 6: Run static and schema checks**

Run: `python -m compileall -q app tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: exit code 0.

- [ ] **Step 7: Manually verify the browser flow**

Run: `python -m uvicorn app.main:app --reload`

Verify desktop and mobile widths for settings, book overrides, media reorder, patch progress, YouTube playlist sync, and retry. Use a private test upload when credentials/quota are available; otherwise record that live Google API verification remains pending while mocked API tests pass.

- [ ] **Step 8: Commit integration tests and documentation**

```bash
git add tests/test_automation_pipeline_integration.py README.md
git commit -m "test: verify automated publishing pipeline"
```

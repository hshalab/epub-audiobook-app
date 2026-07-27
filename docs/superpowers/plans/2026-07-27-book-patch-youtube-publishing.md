# Book Patch YouTube Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish each patch as one YouTube episode using book defaults, durable patch overrides, the matching overlay thumbnail, and a resumable per-patch pipeline.

**Architecture:** Extend the existing `automation_config`, `patch_pipeline`, `youtube_uploads`, video registration, thumbnail, and playlist code. Put pure metadata resolution and validation in one focused module, persist only overrides on books/patches, snapshot resolved data when enqueueing, and run all manual or automatic requests through one idempotent pipeline service.

**Tech Stack:** Python 3, FastAPI, SQLite, Jinja2, vanilla JavaScript, pytest, FFmpeg, YouTube Data API.

## Global Constraints

- One patch is one batch and one episode; do not create another batch entity.
- Episode number is always `patch_index + 1`.
- Genre tags drive both the title suffix and YouTube tags.
- Retry must not re-upload a completed YouTube video.
- Media ownership uses immutable `patch_id`, never display order or filename sorting.
- Do not add dependencies or a new queue engine.
- Preserve unrelated dirty worktree changes in YouTube and Drive files.

---

### Task 1: Metadata Configuration and Resolution

**Files:**
- Create: `app/youtube_metadata.py`
- Modify: `app/db.py`
- Modify: `app/models.py`
- Modify: `app/repository.py`
- Test: `tests/test_youtube_metadata.py`

**Interfaces:**
- Produces: `DEFAULT_BOOK_YOUTUBE_CONFIG: dict`, `validate_book_youtube_config(config: dict) -> dict`, `resolve_patch_youtube_metadata(book, patch, override: dict | None) -> dict`, `get_book_youtube_config(conn, book_id) -> dict`, `save_book_youtube_config(conn, book_id, config) -> None`, `get_patch_youtube_override(conn, patch_id) -> dict`, and `save_patch_youtube_override(conn, patch_id, override) -> None`.
- Resolved metadata keys: `title`, `description`, `tags`, `privacy_status`, and `youtube` playlist configuration.

- [ ] **Step 1: Write failing metadata tests**

```python
def test_default_patch_title_and_tags():
    result = resolve_patch_youtube_metadata(book, patch, None)
    assert result["title"] == "Nha Tro - Tap 1 - Chuong 1-8: Mua | kinh di, huyen huyen"
    assert result["tags"] == ["kinh di", "huyen huyen"]

def test_optional_title_segments_are_omitted():
    patch.name = ""
    book.automation_config = json.dumps({"youtube": {"genre_tags": ""}})
    assert resolve_patch_youtube_metadata(book, patch, None)["title"] == "Nha Tro - Tap 1 - Chuong 1-8"

def test_patch_override_wins_and_empty_field_inherits():
    result = resolve_patch_youtube_metadata(book, patch, {"title": "Custom", "description": ""})
    assert result["title"] == "Custom"
    assert result["description"] == "book description"
```

- [ ] **Step 2: Run tests and verify the module/schema are missing**

Run: `pytest tests/test_youtube_metadata.py -q`
Expected: FAIL importing `app.youtube_metadata` or reading patch override data.

- [ ] **Step 3: Add durable patch override storage and typed fields**

Add `youtube_override TEXT` to the `patch` schema and migration, then add:

```python
@dataclass
class Patch:
    # existing fields
    youtube_override: str | None = None
```

Keep book settings in the existing `book.automation_config` JSON rather than adding duplicate columns.

- [ ] **Step 4: Implement minimal validation and rendering**

```python
ALLOWED_FIELDS = {"book_title", "episode_number", "chapter_start", "chapter_end", "patch_name", "genre_tags"}
DEFAULT_TITLE_TEMPLATE = "{book_title} - Tap {episode_number} - Chuong {chapter_start}-{chapter_end}: {patch_name} | {genre_tags}"

def split_tags(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
```

Use `string.Formatter().parse()` to reject unknown fields. Render optional title suffixes structurally so empty patch names and tags do not leave punctuation. Reject empty/over-100-character titles, descriptions over 5,000 characters, invalid privacy, and invalid playlist mode.

- [ ] **Step 5: Add repository JSON read/write functions**

Book saves merge only the `youtube` key into `automation_config`; patch saves normalize empty strings out of the override so empty means inheritance. Commit each write.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_youtube_metadata.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/youtube_metadata.py app/db.py app/models.py app/repository.py tests/test_youtube_metadata.py
git commit -m "feat: add patch YouTube metadata settings"
```

### Task 2: Idempotent Patch Publishing Pipeline

**Files:**
- Create: `app/patch_publishing.py`
- Modify: `app/db.py`
- Modify: `app/youtube.py`
- Modify: `app/upload_worker.py`
- Modify: `app/video_repository.py`
- Test: `tests/test_patch_publishing.py`
- Test: `tests/test_youtube_upload_lifecycle.py`

**Interfaces:**
- Consumes: metadata functions from Task 1 and existing `image_overlay.ensure_patch_overlay`, `video_gen.generate_segment`, `youtube.enqueue_upload`, and `youtube.publish_completed_upload`.
- Produces: `enqueue_patch_publish(conn, patch_id: int, *, force_new: bool = False) -> dict`, `run_patch_publish_stage(conn, patch_id: int) -> dict`, and `retry_patch_publish(conn, patch_id: int) -> dict`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_enqueue_snapshots_metadata_and_patch_thumbnail(conn, seeded_patch):
    row = enqueue_patch_publish(conn, seeded_patch.id)
    assert row["patch_id"] == seeded_patch.id
    assert json.loads(row["config_snapshot"])["title"].startswith("Book - Tap 1")

def test_retry_after_upload_does_not_enqueue_second_upload(conn, published_pipeline, monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(youtube, "enqueue_upload", enqueue)
    retry_patch_publish(conn, published_pipeline["patch_id"])
    enqueue.assert_not_called()

def test_force_new_clears_downstream_upload_only(conn, published_pipeline):
    row = enqueue_patch_publish(conn, published_pipeline["patch_id"], force_new=True)
    assert row["thumbnail_status"] == "done"
    assert row["video_status"] == "done"
    assert row["youtube_upload_id"] is None
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `pytest tests/test_patch_publishing.py tests/test_youtube_upload_lifecycle.py -q`
Expected: FAIL because `app.patch_publishing` does not exist.

- [ ] **Step 3: Complete persisted stage data**

Add independent `upload_status`, `thumbnail_status`, and `playlist_status` migrations where absent, retaining existing rows. Keep one unique `patch_pipeline` row per patch. `config_snapshot` stores resolved metadata and YouTube config; `media_snapshot` stores patch ID and resolved source paths.

- [ ] **Step 4: Implement idempotent stage transitions**

```python
STAGES = ("thumbnail", "video", "upload", "thumbnail_setting", "playlist", "published")

def retry_patch_publish(conn, patch_id):
    row = get_pipeline(conn, patch_id)
    return run_patch_publish_stage(conn, patch_id) if row else enqueue_patch_publish(conn, patch_id)
```

Each stage first verifies persisted output. Thumbnail uses `ensure_patch_overlay`; video uses the existing per-patch output path and registration; upload creates one `youtube_uploads` row; post-processing delegates to existing thumbnail/playlist code. Persist state and bounded errors before returning.

- [ ] **Step 5: Make upload snapshot authoritative**

Ensure `youtube.process_upload()` reads title, description, tags, and privacy from its existing row. Ensure `publish_completed_upload()` reads the pipeline thumbnail path and playlist config from `metadata_snapshot`; if YouTube video ID exists, post-processing never calls video upload again.

- [ ] **Step 6: Run focused pipeline tests**

Run: `pytest tests/test_patch_publishing.py tests/test_youtube_upload_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/patch_publishing.py app/db.py app/youtube.py app/upload_worker.py app/video_repository.py tests/test_patch_publishing.py tests/test_youtube_upload_lifecycle.py
git commit -m "feat: add resumable patch publishing pipeline"
```

### Task 3: Audio-ready Triggers and Publishing API

**Files:**
- Modify: `app/worker.py`
- Modify: `app/routes/patches.py`
- Modify: `app/routes/books.py`
- Test: `tests/test_patch_publishing_routes.py`
- Test: `tests/test_patch_video_upload.py`

**Interfaces:**
- Consumes: `enqueue_patch_publish`, `retry_patch_publish`, metadata repository functions.
- Produces routes: `POST /books/{book_id}/youtube-settings`, `GET /books/{book_id}/youtube-metadata-preview`, `GET|POST /books/{book_id}/patches/{patch_id}/youtube-metadata`, `POST /books/{book_id}/patches/{patch_id}/publish`, and `POST /books/{book_id}/patches/{patch_id}/publish/retry`.

- [ ] **Step 1: Write failing route and trigger tests**

```python
def test_manual_audio_upload_enqueues_when_enabled(client, enabled_book, wav_file):
    response = client.post(f"/books/{enabled_book.id}/patches/1/upload-audio", files={"audio": wav_file})
    assert response.status_code == 200
    assert pipeline_for_patch(1)["stage"] == "thumbnail"

def test_save_and_publish_persists_override_before_enqueue(client):
    response = client.post("/books/1/patches/1/publish", json={"title": "Custom"})
    assert response.json()["metadata"]["title"] == "Custom"

def test_disabled_book_does_not_auto_enqueue(client, disabled_book, wav_file):
    client.post(f"/books/{disabled_book.id}/patches/1/upload-audio", files={"audio": wav_file})
    assert pipeline_for_patch(1) is None
```

- [ ] **Step 2: Run route tests and verify 404/failure**

Run: `pytest tests/test_patch_publishing_routes.py tests/test_patch_video_upload.py -q`
Expected: FAIL because the settings/metadata/publish routes and trigger do not exist.

- [ ] **Step 3: Add one shared audio-ready hook**

```python
def on_patch_audio_ready(conn, patch_id):
    patch = repository.get_patch(conn, patch_id)
    config = repository.get_book_youtube_config(conn, patch.book_id)
    if config.get("auto_upload"):
        patch_publishing.enqueue_patch_publish(conn, patch_id)
```

Call this hook immediately after every `repository.mark_patch_done()` path: worker synthesis, manual upload, chunk merge, and imported audio. It must be idempotent.

- [ ] **Step 4: Add validated book settings and preview routes**

The settings route parses form/JSON input, validates via Task 1, verifies YouTube connection and playlist access before enabling automation, saves the config, and returns normalized JSON. Preview resolves one selected patch without writing state.

- [ ] **Step 5: Add patch metadata and publishing routes**

GET returns effective metadata plus explicit override fields. POST metadata saves overrides. Publish saves overrides first, then enqueues/resumes. Retry resumes the first incomplete stage. A `force_new=true` request is required to clear only upload/post-processing state.

- [ ] **Step 6: Replace direct enqueue paths**

Make existing patch video `upload_youtube` and `/youtube-upload` actions call the shared publishing service rather than `_enqueue_patch_youtube`, so manual and automatic behavior use one snapshot/retry path.

- [ ] **Step 7: Run focused route tests**

Run: `pytest tests/test_patch_publishing_routes.py tests/test_patch_video_upload.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/worker.py app/routes/patches.py app/routes/books.py tests/test_patch_publishing_routes.py tests/test_patch_video_upload.py
git commit -m "feat: connect patch audio to YouTube publishing"
```

### Task 4: Book Detail YouTube and Patch UI

**Files:**
- Modify: `app/routes/books.py`
- Modify: `app/templates/book_detail.html`
- Test: `tests/test_book_detail_youtube_ui.py`

**Interfaces:**
- Consumes: Task 3 JSON endpoints and pipeline status fields.
- Produces: book YouTube settings modal, unified patch publishing modal, and stage-aware patch table controls.

- [ ] **Step 1: Write failing rendered-page tests**

```python
def test_book_detail_has_youtube_settings_and_no_whole_book_audio(client, seeded_book):
    html = client.get(f"/books/{seeded_book.id}").text
    assert 'data-open-dialog="youtube-settings-modal"' in html
    assert 'id="patch-youtube-modal"' in html
    assert 'href="/books/' + str(seeded_book.id) + '/download/audio"' not in html

def test_patch_row_exposes_pipeline_stage(client, published_patch):
    html = client.get(f"/books/{published_patch.book_id}").text
    assert "Published" in html
```

- [ ] **Step 2: Run UI tests and verify failure**

Run: `pytest tests/test_book_detail_youtube_ui.py -q`
Expected: FAIL because the modals/stage UI are absent and the whole-book audio card remains.

- [ ] **Step 3: Supply normalized view data**

In `book_detail`, load book YouTube config, connected-channel state, available playlists, per-patch overrides, effective metadata, and pipeline rows once. Pass dictionaries keyed by patch ID to Jinja.

- [ ] **Step 4: Add book YouTube settings modal**

Add controls for auto-upload, privacy, comma-separated genre tags, title template, description template, playlist mode, existing playlist, auto-create playlist title/description, patch preview selector, connection status, and refresh. Submit asynchronously and render validation errors without reloading.

- [ ] **Step 5: Add unified patch publishing modal**

Show patch audio, thumbnail, video, effective metadata, explicit override inputs, per-field `Use book default`, stage/error, `Save`, `Save & Upload`, `Retry`, and an explicit `Upload again as new video` only after publication.

- [ ] **Step 6: Simplify the patch table and remove whole-book audio**

Use columns `Select | Episode/Patch | Chapters | Audio | Thumbnail | Video/YouTube | Status | Actions`. Remove the final book audio card. Keep exports and unrelated patch actions operational.

- [ ] **Step 7: Wire modal and polling behavior**

Use event delegation and existing dialog/toast patterns. Fetch metadata on modal open; save before publish; update only the affected row. Extend `/status` rendering for all pipeline stages without reloading during `ACTIVE_TASKS`.

- [ ] **Step 8: Run UI and existing template tests**

Run: `pytest tests/test_book_detail_youtube_ui.py tests/test_patch_preview_actions.py tests/test_patch_images.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/routes/books.py app/templates/book_detail.html tests/test_book_detail_youtube_ui.py
git commit -m "feat: add book and patch YouTube controls"
```

### Task 5: End-to-end Regression and Cleanup

**Files:**
- Modify only files required by failures found in this task.
- Test: `tests/test_patch_publishing.py`
- Test: `tests/test_patch_publishing_routes.py`
- Test: `tests/test_book_detail_youtube_ui.py`

**Interfaces:**
- Consumes and verifies all previous task interfaces.
- Produces a tested publishing flow with no dead direct-upload path.

- [ ] **Step 1: Add the cross-patch ownership regression**

```python
def test_batch_publish_keeps_thumbnail_bound_to_patch_id(conn, patch_a, patch_b):
    a = enqueue_patch_publish(conn, patch_a.id)
    b = enqueue_patch_publish(conn, patch_b.id)
    assert json.loads(a["media_snapshot"])["patch_id"] == patch_a.id
    assert json.loads(b["media_snapshot"])["patch_id"] == patch_b.id
    assert a["thumbnail_path"] != b["thumbnail_path"]
```

- [ ] **Step 2: Run the complete focused feature suite**

Run: `pytest tests/test_youtube_metadata.py tests/test_patch_publishing.py tests/test_patch_publishing_routes.py tests/test_book_detail_youtube_ui.py tests/test_youtube_upload_lifecycle.py tests/test_patch_video_upload.py tests/test_patch_images.py -q`
Expected: PASS.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: PASS with no new failures.

- [ ] **Step 4: Search for obsolete direct paths and dead UI**

Run: `rg "_enqueue_patch_youtube|runLightTTSPatch|final_audio_path|vc-auto-youtube|vc-privacy" app/templates/book_detail.html app/routes app`
Expected: no obsolete `_enqueue_patch_youtube`, dead controls, or whole-book audio UI references; legitimate backend full-book compatibility references may remain outside Book Detail.

- [ ] **Step 5: Run Python compilation check**

Run: `python -m compileall -q app`
Expected: exit code 0.

- [ ] **Step 6: Inspect the final diff without touching unrelated changes**

Run: `git status --short` and `git diff --check`.
Expected: only intended feature files plus pre-existing unrelated YouTube/Drive changes; no whitespace errors.

- [ ] **Step 7: Commit any test-driven cleanup**

```bash
git add tests/test_patch_publishing.py
git commit -m "test: cover patch YouTube publishing flow"
```

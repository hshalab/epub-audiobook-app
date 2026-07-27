# Patch Media Modal Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the per-patch `More` control to `Media`, remove its duplicate background controls, and preserve manual audio and MP4 uploads.

**Architecture:** Make a template-only UI cleanup in the existing Book Detail modal while leaving upload endpoints and payload contracts unchanged. Add rendered-HTML and route regression coverage before editing the template.

**Tech Stack:** FastAPI, Jinja2, vanilla JavaScript, pytest, Starlette TestClient

## Global Constraints

- Keep manual result-audio upload in the Media modal.
- Keep manual MP4 upload in the Media modal.
- Manage patch backgrounds only through the thumbnail/image modal.
- Do not change backend upload APIs or the database schema.

---

### Task 1: Media Modal UI And Upload Regressions

**Files:**
- Modify: `tests/test_book_detail_youtube_ui.py`
- Modify: `tests/test_patch_video_upload.py`
- Modify: `app/templates/book_detail.html`

**Interfaces:**
- Consumes: `POST /books/{book_id}/patches/{patch_id}/upload-audio` with multipart field `audio`; `POST /books/{book_id}/patches/{patch_id}/video` with multipart field `video`.
- Produces: a `Media` button and modal containing `.patch-audio-file`, `.patch-audio-upload-btn`, `#pm-video-file`, and `#pm-video-upload`, without `.patch-bg-select` or `.patch-bg-save-btn`.

- [ ] **Step 1: Add failing rendered-HTML assertions**

Assert that rendered Book Detail HTML contains `>Media</button>`, does not contain `>More</button>`, retains audio/MP4 upload controls, and omits `pm-bg-select`, `patch-bg-select`, and `patch-bg-save-btn`.

- [ ] **Step 2: Run the UI regression and verify failure**

Run: `python -m pytest tests/test_book_detail_youtube_ui.py -q --tb=short`

Expected: FAIL because the button still says `More` and the duplicate background controls still render.

- [ ] **Step 3: Preserve upload route coverage**

Extend `tests/test_patch_video_upload.py` so one test posts an audio file to `/books/1/patches/1/upload-audio` and verifies the patch becomes `done`, while the existing MP4 test verifies the canonical patch video file is stored.

- [ ] **Step 4: Remove duplicate controls and rename the button**

In `app/templates/book_detail.html`, change `More` to `Media`, delete the background form group from `#patch-media-modal`, delete `savePatchBackground`, remove background-related arguments/data attributes from `openPatchMediaModal`, and retain the thumbnail/image modal background functions.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_book_detail_youtube_ui.py tests/test_patch_video_upload.py -q --tb=short`

Expected: all tests pass.

- [ ] **Step 6: Run static and adjacent verification**

Run: `python -m compileall -q app tests`

Run: `git diff --check`

Expected: both commands exit 0; line-ending warnings are acceptable.

# Unified Background Media Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/photos` a unified image/video background library and expose its media through all background selectors.

**Architecture:** Extend the existing `data/backgrounds` file-backed library and routes instead of adding storage. Centralize media extension/type handling in `photos.py`, preserve `/photos`, and keep existing `_list_backgrounds()` consumers.

**Tech Stack:** FastAPI, Jinja2, SQLite JSON, pytest, Starlette TestClient

## Global Constraints

- Keep `/photos` and existing stored file paths compatible.
- Support `.jpg`, `.jpeg`, `.png`, `.webp`, `.mp4`, `.webm`, `.mov`.
- Rename/delete update book, patch, and `automation_config.video.backgrounds` references.
- Sequential selection follows filename order.

---

### Task 1: Unified Upload, List, Preview

**Files:** `app/routes/photos.py`, `app/templates/photos.html`, `tests/test_media_manage.py`

- [ ] Add failing tests for video upload, page listing, and video MIME preview.
- [ ] Expand accepted extensions/MIME map and page item metadata.
- [ ] Render image/video previews and Media Library copy.
- [ ] Run focused media route tests.

### Task 2: Rename/Delete Reference Integrity

**Files:** `app/routes/photos.py`, `tests/test_media_manage.py`

- [ ] Add failing tests with book scalar, patch scalar, and JSON-array references.
- [ ] Update all references atomically on rename/delete.
- [ ] Preserve unrelated automation configuration keys.
- [ ] Run reference-integrity tests.

### Task 3: Background Checkbox Selection

**Files:** `app/templates/book_detail.html`, `tests/test_book_detail_youtube_ui.py`, `tests/test_video_config.py`

- [ ] Add failing rendered-HTML test for image/video checkbox previews and selected values.
- [ ] Replace textarea with ordered media checkbox list sourced from `backgrounds`.
- [ ] Serialize checked values in DOM order to `/video-config`.
- [ ] Verify book/patch selectors still list both media types.

### Task 4: Verification

- [ ] Run media, Book Detail, video config, background selector, and renderer suites.
- [ ] Run `python -m compileall -q app tests`.
- [ ] Run `git diff --check`.

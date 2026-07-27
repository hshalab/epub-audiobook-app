# Shared Video Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move voice/music controls into shared video configuration and add validated FFmpeg, multi-background, intro/outro, enhancement, and concurrency support without breaking legacy books.

**Architecture:** Keep existing `book` columns and endpoints authoritative for voice, music, resolution, FPS, and animation. Add a validated `video` block under `automation_config`, resolve one effective snapshot at render start, and extend the video generator through explicit options while retaining legacy defaults.

**Tech Stack:** FastAPI, SQLite, Jinja2, vanilla JavaScript, FFmpeg, pytest

## Global Constraints

- Existing books retain stored resolution/FPS values.
- Existing voice/music persistence endpoints and columns remain in use.
- New configuration defaults: `1280x720`, `30`, `libx264`, `320k`, quality `23`, concurrency `3`.
- Output pixel format remains `yuv420p`; audio remains AAC.
- Patch-specific background replaces the shared background list.
- Intro/outro are applied per patch and music mixes only with main narration.

---

### Task 1: Configuration Model And Persistence

**Files:** `app/video_config.py` (new), `app/routes/books.py`, `app/templates/book_detail.html`, `tests/test_video_config.py` (new)

- [ ] Write failing tests for defaults, validation, legacy column fallback, path validation, codec/bitrate/quality/concurrency limits, and JSON round-trip.
- [ ] Add `DEFAULT_VIDEO_CONFIG`, `get_book_video_config`, `save_book_video_config`, and `resolve_effective_video_config` with deep-copy-safe defaults.
- [ ] Preserve existing voice/music/video-settings route behavior while accepting the new video block.
- [ ] Verify focused configuration tests pass.

### Task 2: Move Controls Into Video Modal

**Files:** `app/templates/book_detail.html`, `tests/test_book_detail_youtube_ui.py`, `tests/test_video_config_routes.py` (new)

- [ ] Add voice selector/transcript, music selector/volume, FFmpeg fields, background list/mode/duration, intro/outro voice selectors, enhancement toggles, and concurrency to `video-config-modal`.
- [ ] Remove voice/music forms and controls from Studio Setup, retaining only visual preview/background/overlay controls.
- [ ] Make the video modal save existing voice/music/video-settings forms plus the new JSON config with error-safe sequential requests.
- [ ] Add route/render tests proving fields are present once and old persistence endpoints receive unchanged payloads.

### Task 3: Renderer Options And FFmpeg Flags

**Files:** `app/video_gen.py`, `app/worker.py`, `app/routes/patches.py`, `tests/test_video_gen_options.py` (new)

- [ ] Add explicit renderer options for codec, bitrate, CRF/CQ, still animation, progress bar, background duration, and stable seed.
- [ ] Add tests asserting libx264 uses `-crf`/`-tune stillimage`, NVENC uses `-cq`, bitrate and `yuv420p` are forwarded, and invalid paths are rejected.
- [ ] Update full-book and patch rendering to pass effective configuration while preserving old calls.
- [ ] Verify existing video-generation suites plus new flag tests.

### Task 4: Multi-Background Segments

**Files:** `app/video_gen.py`, `app/image_overlay.py`, `tests/test_video_background_rotation.py` (new)

- [ ] Write tests for patch override priority, book list fallback, sequential rotation, stable random ordering, still-image duration, video-background looping, and missing-entry skipping.
- [ ] Implement background candidate resolution and deterministic ordering.
- [ ] Generate/concat timed background segments per patch, dropping background audio and applying optional crossfade/Ken Burns/progress bar.
- [ ] Verify one-background and legacy fallback behavior.

### Task 5: Intro/Outro And Music Scope

**Files:** `app/video_gen.py`, `app/routes/books.py`, `tests/test_video_intro_outro.py` (new)

- [ ] Add tests that every patch can produce intro/main/outro, intro/outro omit music, main includes music, and missing greeting files are skipped.
- [ ] Resolve intro/outro from the Voices library and pass them into patch rendering.
- [ ] Concatenate normalized segments with consistent video/audio formats.
- [ ] Verify standalone and full-book callers retain current behavior.

### Task 6: Batch Concurrency And UI Suggestions

**Files:** `app/templates/book_detail.html`, `app/templates/video_creator.html`, `app/static/video_creator.js`, `tests/test_video_batch_options.py` (new)

- [ ] Add tests for concurrency bounds and selected-patch scheduling.
- [ ] Apply configured concurrency only to multi-patch generation; single-patch generation remains synchronous in behavior.
- [ ] Add UI hints for crossfade, Ken Burns, progress bar, and background loop usage.
- [ ] Verify no duplicate voice/music controls remain in Studio Setup.

### Task 7: Full Verification

- [ ] Run focused configuration, route, renderer, background, intro/outro, and existing video suites.
- [ ] Run `python -m compileall -q app tests`.
- [ ] Run `git diff --check`.
- [ ] Run the full test suite and document any pre-existing collection/environment failures.

# Batch Notebook VoxCPM Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batch notebook exports chapter-aware, deterministic, pause-aware, timeline-capable, and import their completed WAV/sidecar pair back into the app.

**Architecture:** Export the existing shared chapter chunk plan into backward-compatible patch manifests. Refactor notebook Cell 8 to stream-merge chunk WAVs and atomically emit result WAV/timeline pairs, then add a result-first import helper that installs notebook outputs at canonical patch paths and falls back to chunk import.

**Tech Stack:** Python 3.10-3.12, Jupyter notebook JSON, NumPy, SoundFile, FastAPI, pytest

## Global Constraints

- Keep existing `chunks` and `expected_outputs` manifest fields as ordered filename lists.
- Add one-to-one `chunk_metadata` with exact fields `filename`, `chapter_index`, `chapter_title`, and `is_chapter_start`.
- Every notebook VoxCPM call uses `seed=42`, `cfg_value=2.0`, and `inference_timesteps=10` while preserving Ultimate Cloning.
- Merge with exactly 300 ms silence only between chunks using bounded-memory streaming and atomic result replacement.
- Old manifests without `chunk_metadata` still synthesize and merge, but produce no timeline.
- Timeline files use local version-1 schema and are persisted beside notebook result WAVs.
- Import prefers notebook result WAV/sidecar, installs canonical `<patch_id>.wav` and `<patch_id>.timeline.json`, and falls back to chunks.
- Sidecar absence/invalidity never blocks audio import; stale canonical sidecars must not survive a newly installed WAV.
- No database migration, dependency, whole-book timeline, single-patch notebook update, or inferred legacy chapter mapping.

---

### Task 1: Export Chapter Metadata

**Files:**
- Modify: `app/drive_export.py:90-143`
- Modify: `tests/test_drive_export.py`
- Modify: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: `repository.build_patch_chunk_plan(conn, patch) -> list[dict]`.
- Produces: patch manifest `chunk_metadata: list[dict]`, one entry per `chunks` item.

- [ ] Add failing export tests that seed two chapters, assert chunks never cross chapters, and assert exact metadata keys/markers.
- [ ] Run `pytest tests/test_drive_export.py -k "chunk_metadata or chapter" -v` and confirm failure because `chunk_metadata` is absent.
- [ ] Replace `_write_patch_files` combined-text splitting with `build_patch_chunk_plan`; write each plan item's text and metadata while preserving `chunks`/`expected_outputs` shapes.
- [ ] Add multi-chunk marker and excluded/empty chapter tests.
- [ ] Run `pytest tests/test_drive_export.py tests/test_chunk_manager.py -q` and expect all pass.

---

### Task 2: Refactor Batch Notebook Cell 8

**Files:**
- Modify: `app/assets/colab_kaggle_batch_tts_template.ipynb`
- Modify: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: Task 1 manifest `chunk_metadata` when present.
- Produces: atomic `result/*.wav` and optional matching `result/*.timeline.json`.

- [ ] Add failing template tests that parse notebook JSON and inspect Cell 8 for `seed=42`, `_CHUNK_PAUSE_MS = 300`, `sf.SoundFile`, format preflight, temporary result path, `os.replace`, timeline schema fields, `chunk_metadata` fallback, and timeline persistence.
- [ ] Run `pytest tests/test_notebook_templates.py -v` and confirm new assertions fail.
- [ ] Update Cell 8 generation call to pass seed 42 and retain reference/prompt arguments.
- [ ] Replace `np.concatenate(parts)` merge with header preflight plus block streaming to a temporary PCM16 WAV, inserting rounded pause frames only between chunks, then `os.replace` result.
- [ ] Validate `chunk_metadata` structurally; calculate chapter starts from inspected WAV frame counts; atomically write/persist timeline when valid, otherwise warn and omit it.
- [ ] Preserve legacy fallback and add missing-sidecar warning when skipping an existing result.
- [ ] Run `pytest tests/test_notebook_templates.py -q` and parse the notebook with `python -m json.tool app/assets/colab_kaggle_batch_tts_template.ipynb`.

---

### Task 3: Import Result WAV and Sidecar

**Files:**
- Modify: `app/routes/patches.py:600-662,792-873`
- Modify: `app/youtube_metadata.py`
- Modify: `tests/test_drive_desktop_sync.py`
- Modify: `tests/test_drive_import.py` if present; otherwise add focused cases to `tests/test_drive_desktop_sync.py`.

**Interfaces:**
- Produces private helpers in `app/routes/patches.py` for locating a batch result, validating/copying a sidecar, atomically installing a result, and fallback merging.
- Reuses timeline validation from `app/youtube_metadata.py` through a small public `load_timeline(audio_path) -> dict | None` helper rather than duplicate invariants.

- [ ] Add failing Drive Desktop import tests with a batch root, `batch_manifest.json`, result WAV, valid sidecar, and patch export folder; assert direct result is preferred and installed at canonical paths.
- [ ] Add tests for missing/invalid sidecar removing stale local sidecar while WAV import succeeds, and copy failure preserving old local pair.
- [ ] Add corrupt-result tests proving fallback to complete chunk files.
- [ ] Refactor timeline parsing in `youtube_metadata.py` into reusable validation without changing description behavior.
- [ ] Implement result lookup by matching `patch_id` in the batch manifest and atomic local install.
- [ ] Refactor chunk fallback to use shared chunk plan count, merge with `pause_ms=300`, and write a timeline only from authoritative manifest metadata; loose local chunk uploads use the same pause but infer no timeline.
- [ ] Run `pytest tests/test_drive_desktop_sync.py tests/test_youtube_metadata.py tests/test_chunk_files.py -q`.

---

### Task 4: Full Verification

**Files:**
- Reference: `docs/superpowers/specs/2026-07-28-batch-notebook-voxcpm-refactor-design.md`

- [ ] Run focused tests: `pytest tests/test_drive_export.py tests/test_notebook_templates.py tests/test_drive_desktop_sync.py tests/test_youtube_metadata.py tests/test_chunk_files.py -q`.
- [ ] Run full suite: `pytest tests -q`.
- [ ] Run `python -m compileall -q app tests`.
- [ ] Run `python -m json.tool app/assets/colab_kaggle_batch_tts_template.ipynb` and expect valid JSON output.
- [ ] Run `git diff --check` and inspect `git status --short` for intended files only.

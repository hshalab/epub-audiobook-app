# VoxCPM Chunk Pauses and Chapter Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 300 ms pauses between VoxCPM chunks, deterministic reference-driven synthesis with seed 42, and exact per-chapter timestamps that become valid YouTube Chapters in each patch video's description.

**Architecture:** Build one chapter-aware chunk plan shared by the worker and chunk manager, then calculate timestamps from actual generated frame counts while merging. Persist completed timing as an atomic `.timeline.json` sidecar and let YouTube metadata resolution append it only when every YouTube Chapters constraint is satisfied.

**Tech Stack:** Python 3.10-3.12, NumPy, SoundFile, SQLite, FastAPI, pytest, VoxCPM2

## Global Constraints

- Insert exactly 300 ms of silence only between adjacent TTS chunks; never add leading or trailing silence.
- Do not add the chunk pause while merging patch WAV files into final book audio.
- Pass `seed=42` to every VoxCPM generation while retaining `cfg_value=2.0` and `inference_timesteps=10`.
- Keep Ultimate Cloning arguments unchanged when both reference WAV and transcript exist; do not inject emotion or style text.
- Preserve chapter boundaries so no TTS chunk contains text from two chapters.
- Timeline timestamps are relative to patch audio and identify the first spoken frame after preceding silence.
- Store timing beside `<patch>.wav` as `<patch>.timeline.json`; add no database migration or dependency.
- Omit the entire YouTube timeline unless it has at least three chapters, starts at `00:00`, every segment is at least 10 seconds, every title is non-empty, and the final description is at most 5,000 characters.
- Missing or invalid timeline data must never block synthesis or publishing.

## File Map

- Modify `app/audio_merge.py`: optional inter-input silence for array and streamed WAV merge paths.
- Modify `app/tts_engine.py`: deterministic VoxCPM seed.
- Modify `app/repository.py`: shared chapter-aware chunk plan and timeline-sidecar cleanup.
- Modify `app/worker.py`: consume the shared plan, synthesize/merge with pauses, calculate exact chapter starts, and atomically write the sidecar.
- Modify `app/youtube_metadata.py`: validate sidecars, format timestamps, and append valid timeline blocks.
- Modify `tests/test_chunk_files.py`: pause merge, worker timeline, resume, and cleanup integration tests.
- Modify `tests/test_chunk_manager.py`: chapter-aware chunk-index consistency.
- Create `tests/test_tts_engine.py`: VoxCPM generation argument tests without loading the real model.
- Modify `tests/test_youtube_metadata.py`: sidecar validation and YouTube description behavior.

---

### Task 1: Add Optional Silence to Audio Merge

**Files:**
- Modify: `app/audio_merge.py:15-34`
- Test: `tests/test_chunk_files.py:41-81`

**Interfaces:**
- Produces: `concat_chunks_to_wav(chunks: list[np.ndarray], sample_rate: int, out_path: str, pause_ms: int = 0) -> None`
- Produces: `concat_wavs(input_paths: list[str], out_path: str, pause_ms: int = 0) -> None`
- Consumes: NumPy arrays shaped as mono `(frames,)` or multi-channel `(frames, channels)` and SoundFile-compatible WAV paths.

- [ ] **Step 1: Write failing array-merge tests**

Add tests that use a low sample rate so exact pause frames are obvious:

```python
def test_concat_chunks_inserts_pause_only_between_chunks(tmp_audio_dir):
    chunks = [np.ones(10, dtype=np.float32), np.full(20, 2.0, dtype=np.float32)]
    out_path = tmp_audio_dir + "/array_pause.wav"

    audio_merge.concat_chunks_to_wav(chunks, 1000, out_path, pause_ms=300)

    merged, sample_rate = sf.read(out_path, dtype="float32")
    assert sample_rate == 1000
    assert merged.shape == (330,)
    assert np.allclose(merged[:10], 1.0, atol=5e-4)
    assert np.count_nonzero(merged[10:310]) == 0
    assert np.allclose(merged[310:], 2.0, atol=5e-4)


def test_concat_chunks_default_has_no_pause(tmp_audio_dir):
    chunks = [np.ones(10, dtype=np.float32), np.ones(20, dtype=np.float32)]
    out_path = tmp_audio_dir + "/array_no_pause.wav"
    audio_merge.concat_chunks_to_wav(chunks, 1000, out_path)
    merged, _ = sf.read(out_path)
    assert merged.shape == (30,)
```

- [ ] **Step 2: Run the array tests and verify failure**

Run: `pytest tests/test_chunk_files.py::test_concat_chunks_inserts_pause_only_between_chunks tests/test_chunk_files.py::test_concat_chunks_default_has_no_pause -v`

Expected: the pause test fails with `TypeError` because `pause_ms` is not accepted; the default test passes.

- [ ] **Step 3: Implement minimal array silence insertion**

Update `concat_chunks_to_wav` without adding a separate abstraction:

```python
def concat_chunks_to_wav(
    chunks: list[np.ndarray], sample_rate: int, out_path: str, pause_ms: int = 0
) -> None:
    if chunks and pause_ms:
        shape = (round(sample_rate * pause_ms / 1000), *chunks[0].shape[1:])
        silence = np.zeros(shape, dtype=np.float32)
        parts = [part for i, chunk in enumerate(chunks) for part in ((silence, chunk) if i else (chunk,))]
        audio = np.concatenate(parts)
    else:
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    sf.write(out_path, audio, sample_rate)
```

- [ ] **Step 4: Write failing streamed-WAV tests**

Add one mono test and one stereo shape test:

```python
def test_concat_wavs_inserts_pause_between_files(tmp_audio_dir):
    paths = []
    for index, frames in enumerate((10, 20)):
        path = tmp_audio_dir + f"/pause_{index}.wav"
        sf.write(path, np.ones(frames, dtype=np.float32), 1000)
        paths.append(path)
    out_path = tmp_audio_dir + "/stream_pause.wav"

    audio_merge.concat_wavs(paths, out_path, pause_ms=300)

    merged, _ = sf.read(out_path, dtype="float32")
    assert merged.shape == (330,)
    assert np.count_nonzero(merged[10:310]) == 0


def test_concat_wavs_pause_preserves_stereo_channels(tmp_audio_dir):
    paths = []
    for index in range(2):
        path = tmp_audio_dir + f"/stereo_{index}.wav"
        sf.write(path, np.ones((10, 2), dtype=np.float32), 1000)
        paths.append(path)
    out_path = tmp_audio_dir + "/stereo_pause.wav"

    audio_merge.concat_wavs(paths, out_path, pause_ms=300)

    merged, _ = sf.read(out_path, always_2d=True)
    assert merged.shape == (320, 2)
    assert np.count_nonzero(merged[10:310]) == 0
```

- [ ] **Step 5: Run streamed-WAV tests and verify failure**

Run: `pytest tests/test_chunk_files.py::test_concat_wavs_inserts_pause_between_files tests/test_chunk_files.py::test_concat_wavs_pause_preserves_stereo_channels -v`

Expected: FAIL with `TypeError: concat_wavs() got an unexpected keyword argument 'pause_ms'`.

- [ ] **Step 6: Implement streamed silence writing**

Change the signature and write silence before every input except the first:

```python
def concat_wavs(input_paths: list[str], out_path: str, pause_ms: int = 0) -> None:
    if not input_paths:
        raise ValueError("no input paths to merge")
    with sf.SoundFile(input_paths[0]) as probe:
        sample_rate = probe.samplerate
        channels = probe.channels
    pause = np.zeros((round(sample_rate * pause_ms / 1000), channels), dtype=np.float32)
    with sf.SoundFile(out_path, mode="w", samplerate=sample_rate, channels=channels, subtype="PCM_16") as out_f:
        for index, path in enumerate(input_paths):
            if index and pause_ms:
                out_f.write(pause)
            with sf.SoundFile(path, mode="r") as in_f:
                if in_f.samplerate != sample_rate or in_f.channels != channels:
                    raise ValueError("input WAV formats do not match")
                while True:
                    block = in_f.read(frames=_BLOCK_FRAMES, dtype="float32")
                    if block.size == 0:
                        break
                    out_f.write(block)
```

- [ ] **Step 7: Run merge tests**

Run: `pytest tests/test_chunk_files.py -k "concat or merge_chunk_files" -v`

Expected: PASS, including existing no-pause merge tests.

- [ ] **Step 8: Commit Task 1**

```bash
git add app/audio_merge.py tests/test_chunk_files.py
git commit -m "feat: add optional pauses to audio merge"
```

---

### Task 2: Make VoxCPM Generation Deterministic

**Files:**
- Modify: `app/tts_engine.py:10-56`
- Create: `tests/test_tts_engine.py`

**Interfaces:**
- Produces: `VoxCPMEngine(..., seed: int = 42)` and forwards `seed` to `model.generate`.
- Preserves: `synthesize_chunk(text, reference_wav_path=None, prompt_text=None) -> np.ndarray`.

- [ ] **Step 1: Write tests with an injected fake loaded model**

Create `tests/test_tts_engine.py`:

```python
from unittest.mock import MagicMock

import numpy as np

from app.tts_engine import VoxCPMEngine


def _loaded_engine(**kwargs):
    engine = VoxCPMEngine(**kwargs)
    engine._model = MagicMock()
    engine._model.generate.return_value = np.zeros(4, dtype=np.float32)
    return engine


def test_synthesize_chunk_passes_default_seed():
    engine = _loaded_engine()
    engine.synthesize_chunk("hello")
    assert engine._model.generate.call_args.kwargs["seed"] == 42


def test_synthesize_chunk_keeps_ultimate_cloning_arguments():
    engine = _loaded_engine(seed=42)
    engine.synthesize_chunk("hello", reference_wav_path="voice.wav", prompt_text="reference words")
    kwargs = engine._model.generate.call_args.kwargs
    assert kwargs["seed"] == 42
    assert kwargs["reference_wav_path"] == "voice.wav"
    assert kwargs["prompt_wav_path"] == "voice.wav"
    assert kwargs["prompt_text"] == "reference words"
    assert kwargs["cfg_value"] == 2.0
    assert kwargs["inference_timesteps"] == 10
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_tts_engine.py -v`

Expected: one failure for missing `seed` and one `TypeError` because the constructor does not accept `seed`.

- [ ] **Step 3: Add the seed constructor argument and generation kwarg**

Modify `VoxCPMEngine`:

```python
def __init__(
    self,
    model_id: str = "openbmb/VoxCPM2",
    load_denoiser: bool = False,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    seed: int = 42,
):
    self.model_id = model_id
    self.load_denoiser = load_denoiser
    self.cfg_value = cfg_value
    self.inference_timesteps = inference_timesteps
    self.seed = seed
    self._model = None
```

Add `seed=self.seed` to the existing `generate` call without changing any other generation argument.

- [ ] **Step 4: Run engine tests**

Run: `pytest tests/test_tts_engine.py -v`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/tts_engine.py tests/test_tts_engine.py
git commit -m "feat: make VoxCPM chunk generation deterministic"
```

---

### Task 3: Build One Chapter-Aware Chunk Plan

**Files:**
- Modify: `app/repository.py:280-311`
- Modify: `app/worker.py:223-278`
- Modify: `tests/test_chunk_manager.py`
- Modify: `tests/test_chunk_files.py:110-207`

**Interfaces:**
- Produces: `repository.build_patch_chunk_plan(conn: sqlite3.Connection, patch: Patch) -> list[dict]`.
- Each returned dict has exact keys `text: str`, `chapter_index: int`, `chapter_title: str`, and `is_chapter_start: bool`.
- Consumes: existing chapter lookup, normalization options, replacement rules, and `split_into_tts_chunks`.
- Later tasks consume this exact flat plan in `worker._synthesize`.

- [ ] **Step 1: Write a failing repository plan test**

In `tests/test_chunk_manager.py`, seed two included chapters whose combined text could fit one chunk, then assert they remain separate:

```python
def test_chunk_plan_never_crosses_chapter_boundary(conn):
    book_id, patch = seed_patch_with_chapters(
        conn,
        [("First", "First. short text."), ("Second", "Second. short text.")],
    )

    plan = repository.build_patch_chunk_plan(conn, patch)

    assert [item["chapter_index"] for item in plan] == [0, 1]
    assert [item["chapter_title"] for item in plan] == ["First", "Second"]
    assert [item["is_chapter_start"] for item in plan] == [True, True]
```

Use the test file's existing DB fixture and seeding style; define `seed_patch_with_chapters` in that test file to insert a book, chapters, and one `Patch` row, then return `repository.get_patch(conn, patch_id)`.

- [ ] **Step 2: Add tests for multi-chunk, excluded, and empty chapters**

```python
def test_chunk_plan_marks_only_first_chunk_of_each_chapter(conn):
    _, patch = seed_patch_with_chapters(conn, [("Long", "One sentence. " * 80)])
    patch.max_chars = 80
    plan = repository.build_patch_chunk_plan(conn, patch)
    assert len(plan) > 1
    assert [item["is_chapter_start"] for item in plan] == [True] + [False] * (len(plan) - 1)


def test_chunk_plan_omits_excluded_and_empty_chapters(conn):
    book_id, patch = seed_patch_with_chapters(
        conn,
        [("Keep", "spoken"), ("Skip", "excluded"), ("Empty", "   ")],
    )
    repository.set_chapter_excluded(conn, book_id, 1, True)
    plan = repository.build_patch_chunk_plan(conn, patch)
    assert [(item["chapter_index"], item["chapter_title"]) for item in plan] == [(0, "Keep")]
```

- [ ] **Step 3: Run plan tests and verify failure**

Run: `pytest tests/test_chunk_manager.py -k "chunk_plan" -v`

Expected: FAIL with `AttributeError: module 'app.repository' has no attribute 'build_patch_chunk_plan'`.

- [ ] **Step 4: Implement chapter-aware planning in repository**

Move the current per-chapter text preparation out of `worker._synthesize` into `repository.build_patch_chunk_plan`. Keep it in `repository.py` because `get_patch_chunk_view` and the worker must share exactly one split. Use the existing functions and imports rather than duplicate normalization:

```python
def build_patch_chunk_plan(conn: sqlite3.Connection, patch: Patch) -> list[dict]:
    chapters = get_chapters_in_range(conn, patch.book_id, patch.chapter_start, patch.chapter_end)
    book = get_book(conn, patch.book_id)
    rules = list_replace_rules(conn, patch.book_id)
    options = NormalizationOptions(
        numbers=bool(book and book.normalize_numbers_enabled),
        junk=bool(book and book.normalize_junk_enabled),
        spellcheck=bool(book and book.normalize_spellcheck_enabled),
        dictionary=bool(book and book.normalize_dictionary_enabled),
        transliteration=bool(book and book.normalize_transliteration_enabled),
    )
    plan = []
    for chapter in chapters:
        if chapter.is_excluded:
            continue
        text = chapter.text
        if chapter.title and text.startswith(chapter.title) and chapter.title[-1] not in _TITLE_END_PUNCTUATION:
            suffix = text[len(chapter.title):].lstrip()
            if suffix:
                text = chapter.title + ".\n\n" + suffix
        text = normalize_chapter_titles(text)
        if book is not None:
            text = normalize_text(text, options)
        text = apply_replace_rules(text, rules)
        chunks = split_into_tts_chunks(text, max_chars=patch.max_chars or _TTS_MAX_CHARS)
        for index, chunk in enumerate(chunks):
            plan.append({
                "text": chunk,
                "chapter_index": chapter.chapter_index,
                "chapter_title": chapter.title.strip(),
                "is_chapter_start": index == 0,
            })
    return plan
```

Import `NormalizationOptions`, `normalize_chapter_titles`, and `normalize_text` into `repository.py`. Move `_TITLE_END_PUNCTUATION` to `repository.py` or define the same private constant there and delete the worker copy after Step 7. Use the actual existing repository replacement function signature when placing this code; it is `apply_replace_rules(text, rules)`.

- [ ] **Step 5: Make chunk manager consume the shared plan**

Replace `build_patch_text` plus `split_into_tts_chunks` in `get_patch_chunk_view`:

```python
plan = build_patch_chunk_plan(conn, patch)
chunks = [item["text"] for item in plan]
```

Keep the status calculation and returned response shape unchanged.

- [ ] **Step 6: Run repository and chunk-manager tests**

Run: `pytest tests/test_chunk_manager.py scripts/test_repo_and_chunker.py -v`

Expected: PASS.

- [ ] **Step 7: Make the worker consume the plan**

In `_synthesize`, retain the locked fetch of `book`, then obtain the plan under the same DB lock:

```python
with self.db_lock:
    book = repository.get_book(self.conn, patch.book_id)
    chunk_plan = repository.build_patch_chunk_plan(self.conn, patch)

ref_wav = book.voice_clip_path if book else None
ref_text = book.voice_transcript if book else None
```

Delete the worker's duplicated chapter assembly and normalization block. In chunk-file mode use:

```python
chunks = [item["text"] for item in chunk_plan]
```

In in-memory mode replace `engine.synthesize_patch(patch_text, ...)` with explicit `synthesize_chunk` calls over the same plan. This ensures both modes preserve chapter boundaries:

```python
wavs = [
    self.engine.synthesize_chunk(item["text"], reference_wav_path=ref_wav, prompt_text=ref_text)
    for item in chunk_plan
]
```

Do not add timeline writing yet; that belongs to Task 4.

- [ ] **Step 8: Add worker integration assertion for chapter-separated generation**

Change `FakeEngine` in `tests/test_chunk_files.py` to collect input texts, seed at least two chapters in a dedicated test, call `_synthesize`, and assert two adjacent chapter texts were sent as separate calls even when both fit below `max_chars`.

- [ ] **Step 9: Run worker integration tests**

Run: `pytest tests/test_chunk_files.py -k "synthesize" -v`

Expected: PASS in both chunk-file and in-memory modes.

- [ ] **Step 10: Commit Task 3**

```bash
git add app/repository.py app/worker.py tests/test_chunk_manager.py tests/test_chunk_files.py
git commit -m "refactor: preserve chapters in VoxCPM chunk planning"
```

---

### Task 4: Write Exact Patch Timeline Sidecars

**Files:**
- Modify: `app/worker.py:223-319`
- Modify: `app/repository.py:31-40,357-427` and all generated-audio cleanup call sites returned by `rg "Path\(.*audio_path.*unlink|paths_to_delete" app/repository.py`
- Modify: `tests/test_chunk_files.py`

**Interfaces:**
- Produces: `audio_merge` calls with `pause_ms=300` only for chunk-to-patch merge.
- Produces: `<audio_path stem>.timeline.json` schema version 1 with `sample_rate`, `total_frames`, and ordered `chapters`.
- Produces: `repository.delete_patch_audio_files(audio_path: str | None) -> None`, which deletes both WAV and matching timeline sidecar.
- Consumes: Task 3's `chunk_plan` entries and generated arrays or chunk WAV frame counts.

- [ ] **Step 1: Write a failing in-memory timeline test**

Use a fake engine with `sample_rate=1000` and deterministic arrays whose frame count depends on text. Seed three chapters, synthesize with `tts_write_chunk_files=False`, then assert:

```python
timeline_path = Path(audio_path).with_suffix(".timeline.json")
timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
assert timeline["version"] == 1
assert timeline["sample_rate"] == 1000
assert [entry["chapter_index"] for entry in timeline["chapters"]] == [0, 1, 2]
assert timeline["chapters"][0]["start_frame"] == 0
assert timeline["chapters"][1]["start_frame"] == first_chapter_frames + 300
assert timeline["chapters"][1]["start_seconds"] == timeline["chapters"][1]["start_frame"] / 1000
```

Also read the output WAV and assert its total frames equal generated frames plus `300` frames between every adjacent chunk.

- [ ] **Step 2: Run the in-memory timeline test and verify failure**

Run: `pytest tests/test_chunk_files.py::test_in_memory_synthesis_writes_exact_chapter_timeline -v`

Expected: FAIL because no `.timeline.json` exists and no pauses are passed to merge.

- [ ] **Step 3: Add one local timeline builder/writer in worker**

Keep the logic private and small in `worker.py`:

```python
_CHUNK_PAUSE_MS = 300


def _write_timeline(audio_path: str, chunk_plan: list[dict], frame_counts: list[int], sample_rate: int) -> None:
    pause_frames = round(sample_rate * _CHUNK_PAUSE_MS / 1000)
    position = 0
    chapters = []
    for index, (item, frames) in enumerate(zip(chunk_plan, frame_counts, strict=True)):
        if index:
            position += pause_frames
        if item["is_chapter_start"]:
            chapters.append({
                "chapter_index": item["chapter_index"],
                "title": item["chapter_title"],
                "start_frame": position,
                "start_seconds": position / sample_rate,
            })
        position += frames
    payload = {
        "version": 1,
        "sample_rate": sample_rate,
        "total_frames": position,
        "chapters": chapters,
    }
    path = Path(audio_path).with_suffix(".timeline.json")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
```

Import `json`. Python is at least 3.10, so use `zip(..., strict=True)` to catch plan/frame mismatches.

- [ ] **Step 4: Wire in-memory pause and sidecar generation**

After generating arrays:

```python
sample_rate = self.engine.sample_rate
audio_merge.concat_chunks_to_wav(wavs, sample_rate, audio_path, pause_ms=_CHUNK_PAUSE_MS)
_write_timeline(audio_path, chunk_plan, [len(wav) for wav in wavs], sample_rate)
```

Call `_write_timeline` only after the WAV write succeeds.

- [ ] **Step 5: Run the in-memory timeline test**

Run: `pytest tests/test_chunk_files.py::test_in_memory_synthesis_writes_exact_chapter_timeline -v`

Expected: PASS.

- [ ] **Step 6: Write failing chunk-file resume timeline test**

Pre-create chunk 0 with a known frame count, set `next_chunk_index=1`, then let the worker generate the remaining chunks. Assert that chapter starts are calculated using `sf.info(existing_path).frames`, not the fake engine's current output length:

```python
assert timeline["chapters"][1]["start_frame"] == existing_chunk_frames + 300
```

Also assert the final merged WAV contains 300 ms between every chunk and that timeline `total_frames == sf.info(audio_path).frames`.

- [ ] **Step 7: Run the resume test and verify failure**

Run: `pytest tests/test_chunk_files.py::test_resumed_chunk_synthesis_uses_existing_file_frames_for_timeline -v`

Expected: FAIL because chunk-file merge has no pause and no timeline sidecar.

- [ ] **Step 8: Wire chunk-file merge and exact frame inspection**

After all chunk paths exist:

```python
sample_rate = self.engine.sample_rate
frame_counts = [sf.info(path).frames for path in chunk_paths]
audio_merge.concat_wavs(chunk_paths, audio_path, pause_ms=_CHUNK_PAUSE_MS)
_write_timeline(audio_path, chunk_plan, frame_counts, sample_rate)
```

This naturally covers resumed chunks because all frame counts come from disk. Leave final book `_merge_final_audio` calling `concat_wavs(patch_wav_paths, final_path)` without `pause_ms`.

- [ ] **Step 9: Run all timeline and final-merge tests**

Run: `pytest tests/test_chunk_files.py scripts/test_merge_and_video.py -v`

Expected: PASS; if the script is not pytest-collectable, run `python scripts/test_merge_and_video.py` separately and expect exit code 0.

- [ ] **Step 10: Write failing sidecar cleanup tests**

Extend repository reset/delete tests in `tests/test_chunk_files.py` or their existing owning test file. Create both `patch.wav` and `patch.timeline.json`, invoke `reset_patch` and `delete_patch`, and assert both files are gone. Add one test for the bulk reset path if it bypasses these functions.

- [ ] **Step 11: Implement centralized audio/sidecar deletion**

Add to `repository.py`:

```python
def delete_patch_audio_files(audio_path: str | None) -> None:
    if not audio_path:
        return
    path = Path(audio_path)
    path.unlink(missing_ok=True)
    path.with_suffix(".timeline.json").unlink(missing_ok=True)
```

Replace every generated patch-audio `Path(...).unlink(missing_ok=True)` cleanup in `reset_patch`, `delete_patch`, rebuild/reset-all paths, and bulk cleanup with this helper. Do not replace EPUB, image, video, final-book audio, or user-upload deletion.

- [ ] **Step 12: Run cleanup and repository tests**

Run: `pytest tests/test_chunk_files.py tests/test_chunk_manager.py tests/test_auto_build.py -v`

Expected: PASS.

- [ ] **Step 13: Commit Task 4**

```bash
git add app/worker.py app/repository.py tests/test_chunk_files.py tests/test_chunk_manager.py tests/test_auto_build.py
git commit -m "feat: persist exact chapter timelines for patch audio"
```

Stage only test files actually changed; omit untouched paths from `git add`.

---

### Task 5: Append Valid Timeline to YouTube Patch Metadata

**Files:**
- Modify: `app/youtube_metadata.py:1-162`
- Modify: `tests/test_youtube_metadata.py`
- Modify: `tests/test_book_detail_youtube_ui.py:157-170`

**Interfaces:**
- Produces: `_youtube_timeline(audio_path: str | None) -> str | None`.
- Changes: `resolve_patch_youtube_metadata` reads `patch.audio_path`; callers already pass full `Patch` models.
- Consumes: Task 4's exact sidecar schema and the corresponding WAV metadata from `sf.info`.

- [ ] **Step 1: Update the metadata test patch fixture**

Add `audio_path=None` to `_patch()` so existing unit tests preserve ordinary description behavior:

```python
return SimpleNamespace(
    name="Mua",
    chapter_start=1,
    chapter_end=8,
    patch_index=3,
    audio_path=None,
)
```

- [ ] **Step 2: Write a valid timeline append test**

Create a 40-second WAV at 1,000 Hz and matching sidecar with starts at 0, 10, and 25 seconds:

```python
def _write_timeline(tmp_path, starts, titles=None, total_frames=40000):
    audio_path = tmp_path / "patch.wav"
    sf.write(audio_path, np.zeros(total_frames, dtype=np.float32), 1000)
    titles = titles or [f"Chapter {i + 1}" for i in range(len(starts))]
    payload = {
        "version": 1,
        "sample_rate": 1000,
        "total_frames": total_frames,
        "chapters": [
            {
                "chapter_index": i,
                "title": title,
                "start_frame": start,
                "start_seconds": start / 1000,
            }
            for i, (start, title) in enumerate(zip(starts, titles))
        ],
    }
    audio_path.with_suffix(".timeline.json").write_text(json.dumps(payload), encoding="utf-8")
    return str(audio_path)


def test_valid_timeline_is_appended_to_description(tmp_path):
    patch = _patch()
    patch.audio_path = _write_timeline(tmp_path, [0, 10000, 25000])
    result = resolve_patch_youtube_metadata(_book(), patch, None)
    assert result["description"] == (
        "book description\n\n"
        "00:00 Chapter 1\n"
        "00:10 Chapter 2\n"
        "00:25 Chapter 3"
    )
```

Import `numpy as np` and `soundfile as sf` in the test.

- [ ] **Step 3: Run the valid timeline test and verify failure**

Run: `pytest tests/test_youtube_metadata.py::test_valid_timeline_is_appended_to_description -v`

Expected: FAIL because the result still equals `book description`.

- [ ] **Step 4: Implement strict sidecar loading and formatting**

Add `Path`, `math`, and `soundfile as sf` imports, then add:

```python
def _format_timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _youtube_timeline(audio_path: str | None) -> str | None:
    if not audio_path:
        return None
    try:
        audio = sf.info(audio_path)
        payload = json.loads(Path(audio_path).with_suffix(".timeline.json").read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            return None
        if payload.get("sample_rate") != audio.samplerate or payload.get("total_frames") != audio.frames:
            return None
        chapters = payload.get("chapters")
        if not isinstance(chapters, list) or len(chapters) < 3:
            return None
        if any(not isinstance(entry, dict) for entry in chapters):
            return None
        starts = [entry.get("start_frame") for entry in chapters]
        raw_titles = [entry.get("title") for entry in chapters]
        if (
            any(not isinstance(frame, int) or frame < 0 for frame in starts)
            or any(not isinstance(title, str) for title in raw_titles)
            or starts[0] != 0
            or any(right - left < 10 * audio.samplerate for left, right in zip(starts, starts[1:]))
            or audio.frames - starts[-1] < 10 * audio.samplerate
        ):
            return None
        titles = [title.strip() for title in raw_titles]
        if any(not title for title in titles):
            return None
        return "\n".join(
            f"{_format_timestamp(frame // audio.samplerate)} {title}"
            for frame, title in zip(starts, titles)
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
```

Do not use `start_seconds` for validation or formatting; `start_frame` is authoritative.

- [ ] **Step 5: Append only when the 5,000-character result fits**

Immediately after resolving `description_template.format(**values)`:

```python
timeline = _youtube_timeline(getattr(patch, "audio_path", None))
if timeline:
    candidate = f"{description.rstrip()}\n\n{timeline}" if description.strip() else timeline
    if len(candidate) <= 5000:
        description = candidate
```

This is naturally idempotent because resolution starts from configured or overridden description every time; it never mutates stored configuration.

- [ ] **Step 6: Run the valid test**

Run: `pytest tests/test_youtube_metadata.py::test_valid_timeline_is_appended_to_description -v`

Expected: PASS.

- [ ] **Step 7: Add omission matrix tests**

Use `pytest.mark.parametrize` for fewer than three entries, adjacent starts under 10 seconds, final segment under 10 seconds, nonzero first start, and empty title. Add separate tests for malformed JSON, mismatched sample rate, mismatched total frames, missing sidecar, and an existing description whose appended result exceeds 5,000 characters. Every case must assert the result remains exactly the original description.

Also add an hour-format test using a sparse WAV or monkeypatch `sf.info` to avoid allocating a large array:

```python
assert _format_timestamp(3723) == "1:02:03"
```

Add a flooring test with `start_frame=10999` at 1,000 Hz and valid surrounding segments; assert the rendered timestamp is `00:10`, not `00:11`.

- [ ] **Step 8: Run metadata tests**

Run: `pytest tests/test_youtube_metadata.py -v`

Expected: PASS.

- [ ] **Step 9: Verify metadata API and snapshot integration**

Extend the seeded patch in `tests/test_book_detail_youtube_ui.py` with a real temporary WAV and valid sidecar, call `/books/1/patches/1/youtube-metadata`, and assert timeline lines appear once in `payload["description"]`. In `tests/test_patch_publishing.py`, extend the existing `enqueue_patch_publish` test with the same fixture shape and assert `json.loads(config_snapshot)["description"]` contains the block once.

- [ ] **Step 10: Run YouTube integration tests**

Run: `pytest tests/test_book_detail_youtube_ui.py tests/test_patch_publishing.py tests/test_youtube_metadata.py -v`

Expected: PASS.

- [ ] **Step 11: Commit Task 5**

```bash
git add app/youtube_metadata.py tests/test_youtube_metadata.py tests/test_book_detail_youtube_ui.py tests/test_patch_publishing.py
git commit -m "feat: add chapter timelines to YouTube metadata"
```

Stage the publishing test too if Step 9 changed a separate file.

---

### Task 6: Full Regression Verification and Spec Alignment

**Files:**
- Modify only if verification exposes a defect in files already listed above.
- Reference: `docs/superpowers/specs/2026-07-28-voxcpm-chunk-pauses-and-chapter-timeline-design.md`

**Interfaces:**
- Verifies all interfaces produced by Tasks 1-5 together.
- Produces no new runtime interface.

- [ ] **Step 1: Run focused feature tests together**

Run: `pytest tests/test_tts_engine.py tests/test_chunk_files.py tests/test_chunk_manager.py tests/test_youtube_metadata.py tests/test_book_detail_youtube_ui.py -v`

Expected: PASS with no skipped feature tests.

- [ ] **Step 2: Run the complete test suite**

Run: `pytest -q`

Expected: exit code 0 and all tests pass.

- [ ] **Step 3: Run static syntax compilation**

Run: `python -m compileall -q app tests`

Expected: exit code 0 and no output.

- [ ] **Step 4: Check formatting and unintended changes**

Run: `git diff --check`

Expected: no output.

Run: `git status --short`

Expected: only intended source, tests, spec, and plan files are listed; do not alter unrelated user changes.

- [ ] **Step 5: Manually inspect final book merge call**

Run: `rg -n "concat_wavs|pause_ms" app/worker.py app/audio_merge.py`

Expected: both chunk-to-patch branches pass `pause_ms=300`; `_merge_final_audio` does not pass `pause_ms`.

- [ ] **Step 6: Commit any verification-only fixes**

If Steps 1-5 required source or test corrections, inspect `git diff --name-only`, then stage only corrected files from this feature and commit:

```bash
git add app/audio_merge.py app/tts_engine.py app/repository.py app/worker.py app/youtube_metadata.py tests/test_tts_engine.py tests/test_chunk_files.py tests/test_chunk_manager.py tests/test_youtube_metadata.py tests/test_book_detail_youtube_ui.py tests/test_patch_publishing.py
git commit -m "fix: complete VoxCPM timeline integration"
```

If no correction was needed, do not create an empty commit.

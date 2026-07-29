# Export Chunks / Fast Notebook Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make restarting a Colab or Kaggle notebook runtime cheap by inlining chunk text into `manifest.json` and having the notebook decide what work remains from a Drive listing it already builds, instead of downloading the whole batch folder.

**Architecture:** The exported package stops emitting one `.txt` file per chunk and carries the text inside each `chunk_metadata` entry, so a patch's entire input is a single file. Kaggle Cell 4 splits its recursive walk into a listing phase (which becomes the authoritative remote inventory) and a tiny eager download of manifests plus the reference clip. Cell 8 decides "already merged" and "already synthesized" from that inventory plus one directory listing per patch, and pulls chunk WAVs only when a merge actually needs them.

**Tech Stack:** Python 3.11, FastAPI, sqlite3, pytest, `soundfile`, `numpy`, Jupyter notebook JSON (`app/assets/*.ipynb`), `google-api-python-client`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-29-export-chunks-fast-restart-design.md`. Read it before starting.
- Run pytest against the `tests/` directory only: bare `pytest` walks `build/` and `.venv/` and dies before running anything. Always `pytest tests/...`.
- `chunk_metadata` entries have exactly five fields: `filename`, `chapter_index`, `chapter_title`, `is_chapter_start`, `text`.
- `chunks[i]` stays the string `"chunk_000.txt"` — a logical identifier, not a file on disk. Do not rename it.
- `chunks` and `expected_outputs` keep their existing shape and ordering.
- The chunk pause stays `300` ms and generation stays `seed=42`, `cfg_value=2.0`, `inference_timesteps=10`.
- Backgrounds, music, and the reference clip keep being *exported*; only what the notebook *downloads* changes.
- `IS_KAGGLE` remains the single manual platform flag defined in the first code cell. No cell may auto-detect the platform.
- Never write notebook cell source through a Bash heredoc — `\n` and other escapes get mangled. Write the cell body to a file with the Write tool, then inject it with a Python one-liner.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `app/drive_export.py` | Builds export packages | Modify: `_write_patch_files` inlines text and writes no `.txt`; new `package_chunk_count()` |
| `app/routes/patches.py` | Export/import routes | Modify: line ~716 uses `package_chunk_count()` instead of counting `.txt` files |
| `app/assets/colab_kaggle_batch_tts_template.ipynb` | Batch notebook | Modify Cell 4 and Cell 8; delete Cell 9; update Cell 0 markdown |
| `app/assets/colab_kaggle_tts_template.ipynb` | Single-patch notebook | Modify Cell 8 to read text from the manifest |
| `tests/test_drive_export.py` | Export package tests | Modify + add |
| `tests/test_notebook_templates.py` | Notebook regression tests | Modify + add |

Both notebooks keep their `# BEGIN ... HELPERS` / `# END ... HELPERS` seam so pure logic is `exec`-testable without a GPU or a Drive connection. Cell 4 gains such a block for the first time; that is what makes the spec's "zero downloads for a merged batch" requirement testable at all.

### Editing notebook JSON

Notebook `source` is a list of lines. Use this procedure everywhere a cell body changes:

1. Write the complete new cell body to `<scratch>/cellN.py` with the Write tool.
2. Inject it:

```bash
python -c "
import json,io
p='app/assets/colab_kaggle_batch_tts_template.ipynb'
nb=json.load(io.open(p,encoding='utf-8'))
body=io.open('<scratch>/cellN.py',encoding='utf-8').read()
nb['cells'][N]['source']=body.splitlines(keepends=True)
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(nb,ensure_ascii=False,indent=1))
"
```

3. Verify it is still valid JSON and the cell count is what you expect:

```bash
python -c "import json;nb=json.load(open('app/assets/colab_kaggle_batch_tts_template.ipynb',encoding='utf-8'));print(len(nb['cells']),[c['cell_type'] for c in nb['cells']])"
```

---

## Task 1: Inline chunk text into the export manifest

**Files:**
- Modify: `app/drive_export.py:85-150` (`_write_patch_files`), module docstring at `:1-12`
- Test: `tests/test_drive_export.py`

**Interfaces:**
- Consumes: `repository.build_patch_chunk_plan(conn, patch)` → list of dicts with keys `text`, `chapter_index`, `chapter_title`, `is_chapter_start`.
- Produces: `drive_export.package_chunk_count(package_dir: Path) -> int`, used by Task 2. `manifest["chunk_metadata"][i]["text"]`, consumed by Tasks 3 and 6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drive_export.py`:

```python
def test_write_patch_files_inlines_text_and_writes_no_txt_files(conn, tmp_path):
    connection, book, patch = conn
    dest = tmp_path / "patch"
    manifest = drive_export._write_patch_files(connection, book, patch, dest, "reference.wav")

    assert list(dest.glob("chunk_*.txt")) == []

    plan = repository.build_patch_chunk_plan(connection, patch)
    metadata = manifest["chunk_metadata"]
    assert len(metadata) == len(plan)
    assert manifest["chunks"] == [f"chunk_{i:03d}.txt" for i in range(len(plan))]
    assert manifest["expected_outputs"] == [f"chunk_{i:03d}.wav" for i in range(len(plan))]

    for entry, item in zip(metadata, plan):
        assert set(entry) == {
            "filename", "chapter_index", "chapter_title", "is_chapter_start", "text",
        }
        assert entry["text"] == item["text"]
        assert entry["text"].strip()


def test_package_chunk_count_reads_the_manifest(conn, tmp_path):
    connection, book, patch = conn
    dest = tmp_path / "patch"
    manifest = drive_export._write_patch_files(connection, book, patch, dest, "reference.wav")
    assert drive_export.package_chunk_count(dest) == manifest["chunk_count"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_drive_export.py -v
```

Expected: `test_write_patch_files_inlines_text_and_writes_no_txt_files` FAILS (the glob finds `.txt` files, and `set(entry)` has 4 keys not 5); `test_package_chunk_count_reads_the_manifest` FAILS with `AttributeError: module 'app.drive_export' has no attribute 'package_chunk_count'`.

- [ ] **Step 3: Inline the text and drop the .txt writes**

In `app/drive_export.py`, in `_write_patch_files`, replace the plan loop:

```python
    chunk_filenames = []
    chunk_metadata = []
    for i, item in enumerate(plan):
        # chunk_NNN.txt is a logical id only - the text itself travels in the manifest,
        # so a whole patch is one file to download instead of one per chunk.
        filename = f"chunk_{i:03d}.txt"
        chunk_filenames.append(filename)
        chunk_metadata.append(
            {
                "filename": filename,
                "chapter_index": item["chapter_index"],
                "chapter_title": item["chapter_title"],
                "is_chapter_start": item["is_chapter_start"],
                "text": item["text"],
            }
        )
```

- [ ] **Step 4: Add the chunk-count helper**

Add to `app/drive_export.py`, after `_write_patch_files`:

```python
def package_chunk_count(package_dir: Path) -> int:
    """Chunk count of a built single-patch package, read from its manifest.

    Chunk texts no longer exist as separate files, so callers cannot count
    chunk_NNN.txt to learn how big a package is."""
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    return int(manifest["chunk_count"])
```

- [ ] **Step 5: Update the stale docstrings**

In the module docstring at the top of `app/drive_export.py`, change:

```
- single patch: chunk_NNN.txt + manifest.json + notebook at the package root
```

to:

```
- single patch: manifest.json (chunk text inlined in chunk_metadata) + notebook at
  the package root
```

In `_write_patch_files`, change the first docstring line from `"""Write chunk_NNN.txt files + manifest.json + background image for one patch into` to `"""Write manifest.json (with chunk text inlined) + background image for one patch into`.

In `build_export_package`, change `"""Write manifest.json + chunk_NNN.txt + background image + optional music + notebook` to `"""Write manifest.json + background image + optional music + notebook`.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
pytest tests/test_drive_export.py -v
```

Expected: PASS, including the pre-existing `test_write_patch_files_exports_chunk_metadata_at_chapter_boundaries` and `test_write_patch_files_rejects_empty_plan`.

- [ ] **Step 7: Commit**

```bash
git add app/drive_export.py tests/test_drive_export.py
git commit -m "refactor: inline chunk text into the export manifest"
```

---

## Task 2: Read chunk count from the manifest on the publish path

**Files:**
- Modify: `app/routes/patches.py:716`
- Test: `tests/test_drive_export.py`

**Interfaces:**
- Consumes: `drive_export.package_chunk_count(package_dir)` from Task 1.
- Produces: nothing new.

The Drive Desktop export route counts `chunk_*.txt` files in the built package to record `chunk_count`. After Task 1 there are none, so it would silently record `0`. The batch route just above it already takes `chunk_count` from the batch manifest and needs no change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_drive_export.py`:

```python
def test_publish_path_counts_chunks_from_manifest_not_txt_files(conn, tmp_path):
    """The Drive Desktop export route records chunk_count from the built package.

    Guards the regression where dropping chunk_NNN.txt made the old
    glob-based count silently record zero."""
    import inspect

    from app.routes import patches as patches_routes

    connection, book, patch = conn
    dest = tmp_path / "patch"
    manifest = drive_export._write_patch_files(connection, book, patch, dest, "reference.wav")
    assert manifest["chunk_count"] > 0

    source = inspect.getsource(patches_routes)
    assert 'f.suffix == ".txt"' not in source
    assert "drive_export.package_chunk_count(" in source
    assert drive_export.package_chunk_count(dest) == manifest["chunk_count"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_drive_export.py::test_publish_path_counts_chunks_from_manifest_not_txt_files -v
```

Expected: FAIL on `assert 'f.suffix == ".txt"' not in source`.

- [ ] **Step 3: Use the helper in the route**

In `app/routes/patches.py`, replace this line (inside the Drive Desktop single-patch export branch, right after `folder = drive_export.publish_package(...)`):

```python
            chunk_count = sum(1 for f in package_dir.iterdir() if f.name.startswith("chunk_") and f.suffix == ".txt")
```

with:

```python
            chunk_count = drive_export.package_chunk_count(package_dir)
```

A malformed package raises here and is already caught by the surrounding `except Exception` block, which logs and returns a 500 — the same handling every other failure on this path gets.

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_drive_export.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/patches.py tests/test_drive_export.py
git commit -m "refactor: read publish-path chunk count from the package manifest"
```

---

## Task 3: Batch Cell 8 — decide from the inventory, fetch lazily

**Files:**
- Modify: `app/assets/colab_kaggle_batch_tts_template.ipynb` cell index 8
- Test: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: `manifest["chunk_metadata"][i]["text"]` from Task 1. Optionally `_drive_file_ids` and `drive_fetch_many` from Task 4 — both read through `globals().get(...)`, so this task works standalone and on Colab.
- Produces: helper functions `validate_chunk_metadata`, `merge_wav_files`, `write_timeline_atomic` (existing, `validate_chunk_metadata` gains a `text` rule), plus new `chunk_text_for(manifest, offset, patch_dir)` and `available_wavs(dirs, remote_files, remote_dir)` inside the `# BEGIN CELL 8 HELPERS` block.

**Do this task before Task 4.** Cell 8 degrades gracefully when Cell 4's globals are absent, so it can be tested on its own; the reverse is not true.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notebook_templates.py`:

```python
def test_cell8_validator_requires_non_empty_text():
    helpers = _cell8_helpers()
    validate = helpers["validate_chunk_metadata"]
    chunks = ["chunk_000.txt", "chunk_001.txt"]

    def entry(offset, **overrides):
        item = {
            "filename": chunks[offset],
            "chapter_index": 0,
            "chapter_title": "One",
            "is_chapter_start": offset == 0,
            "text": "hello",
        }
        item.update(overrides)
        return item

    assert validate([entry(0), entry(1)], chunks) is not None
    # four-field legacy metadata is no longer valid
    legacy = entry(0)
    del legacy["text"]
    assert validate([legacy, entry(1)], chunks) is None
    assert validate([entry(0, text=""), entry(1)], chunks) is None
    assert validate([entry(0, text="   "), entry(1)], chunks) is None
    assert validate([entry(0, text=123), entry(1)], chunks) is None


def test_cell8_chunk_text_prefers_manifest_and_falls_back_to_txt(tmp_path):
    helpers = _cell8_helpers()
    chunk_text_for = helpers["chunk_text_for"]

    inlined = {
        "chunks": ["chunk_000.txt"],
        "chunk_metadata": [{"filename": "chunk_000.txt", "text": "from manifest"}],
    }
    assert chunk_text_for(inlined, 0, str(tmp_path)) == "from manifest"

    (tmp_path / "chunk_000.txt").write_text("from disk", encoding="utf-8")
    legacy = {"chunks": ["chunk_000.txt"], "chunk_metadata": [{"filename": "chunk_000.txt"}]}
    assert chunk_text_for(legacy, 0, str(tmp_path)) == "from disk"
    assert chunk_text_for({"chunks": ["chunk_000.txt"]}, 0, str(tmp_path)) == "from disk"


def test_cell8_available_wavs_merges_local_dirs_and_remote_inventory(tmp_path):
    helpers = _cell8_helpers()
    available_wavs = helpers["available_wavs"]

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "chunk_000.wav").write_bytes(b"")
    (out_dir / "notes.txt").write_bytes(b"")

    remote = {
        "patches/patch_000/output/chunk_001.wav": "id1",
        "patches/patch_000/output/nested/chunk_009.wav": "id9",
        "patches/patch_001/output/chunk_002.wav": "id2",
        "result/000 - a.wav": "idr",
    }
    names = available_wavs(
        [str(out_dir), str(tmp_path / "missing")], remote, "patches/patch_000/output"
    )
    assert names == {"chunk_000.wav", "chunk_001.wav"}


def test_batch_cell_8_uses_remote_inventory_and_lazy_fetch():
    src = _code_cells(TEMPLATES[1])[7]
    assert "_drive_file_ids" in src
    assert "drive_fetch_many" in src
    assert 'entry["result_wav"] in REMOTE' in src
    assert "available_wavs(" in src
    assert "chunk_text_for(" in src
    assert "find_wav" not in src
    assert "os.listdir" in src
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_notebook_templates.py -v -k "cell8_validator or chunk_text or available_wavs or remote_inventory"
```

Expected: all four FAIL — `KeyError: 'chunk_text_for'` / `'available_wavs'` from the helper namespace, the validator accepting four-field metadata, and the source assertions not finding the new tokens.

- [ ] **Step 3: Write the new Cell 8 body**

Write the complete cell body to `<scratch>/batch_cell8.py`. Start from the current cell (read it with the snippet in "Editing notebook JSON") and apply exactly these changes:

**3a.** In `validate_chunk_metadata`, change the required set and add the text rule:

```python
def validate_chunk_metadata(metadata, chunks):
    required = {"filename", "chapter_index", "chapter_title", "is_chapter_start", "text"}
```

and inside the per-item loop, immediately after the `chapter_index` / `chapter_title` / `is_chapter_start` type check, add:

```python
        if type(item["text"]) is not str or not item["text"].strip():
            return None
```

**3b.** Add these two helpers inside the `# BEGIN CELL 8 HELPERS` block, after `write_timeline_atomic`:

```python
def chunk_text_for(manifest, offset, patch_dir):
    """Chunk text from the manifest, falling back to chunk_NNN.txt on disk.

    The fallback exists only for the zip-dataset path, where an older package is
    attached whole as a Kaggle dataset. On the Drive path Cell 4 never downloads
    chunk_NNN.txt, so there is nothing to fall back to."""
    metadata = manifest.get("chunk_metadata")
    if isinstance(metadata, list) and offset < len(metadata):
        text = metadata[offset].get("text")
        if isinstance(text, str) and text.strip():
            return text
    with open(os.path.join(patch_dir, manifest["chunks"][offset]), "r", encoding="utf-8") as f:
        return f.read()

def available_wavs(dirs, remote_files, remote_dir):
    """Set of .wav basenames present in any of dirs locally, or on Drive directly
    under remote_dir. One listdir per directory replaces two stats per chunk - on
    Colab those stats are FUSE round trips."""
    names = set()
    for directory in dirs:
        if os.path.isdir(directory):
            names.update(n for n in os.listdir(directory) if n.endswith(".wav"))
    prefix = remote_dir.rstrip("/") + "/"
    for rel in remote_files:
        if rel.startswith(prefix) and rel.endswith(".wav"):
            tail = rel[len(prefix):]
            if "/" not in tail:
                names.add(tail)
    return names
```

**3c.** After the `persist = globals().get("drive_persist") or ...` line, add:

```python
# Remote inventory built by Cell 4 (Kaggle Drive mode). Empty on Colab and in the
# zip-dataset fallback, so both platforms take one code path below.
REMOTE = globals().get("_drive_file_ids") or {}

def _no_fetch(pairs):
    raise RuntimeError(
        "chunk WAVs exist only on Drive but this session has no Drive connection"
    )

drive_fetch_many = globals().get("drive_fetch_many") or _no_fetch
```

**3d.** Replace the merged-patch skip. Change:

```python
    if SKIP_EXISTING and os.path.exists(result_path):
```

to:

```python
    # Result WAVs are never downloaded, so on a restarted Kaggle session the local
    # file is absent even though the patch is finished. Without the REMOTE half every
    # finished patch would be re-merged, dragging its chunk WAVs back down.
    already_merged = os.path.exists(result_path) or entry["result_wav"] in REMOTE
    if SKIP_EXISTING and already_merged:
```

and inside that branch change `if manifest.get("chunk_metadata") and not os.path.exists(timeline_path):` to:

```python
        timeline_rel = os.path.splitext(entry["result_wav"])[0] + ".timeline.json"
        if manifest.get("chunk_metadata") and not (
            os.path.exists(timeline_path) or timeline_rel in REMOTE
        ):
```

**3e.** Delete the whole `def find_wav(wav_name):` function and replace the synthesis loop's skip test. Replace:

```python
    for chunk_filename in manifest["chunks"]:
        index = chunk_filename.split("_")[1].split(".")[0]  # chunk_000.txt -> 000
        wav_name = f"chunk_{index}.wav"
        if SKIP_EXISTING and find_wav(wav_name):
            print(f"skip {chunk_filename} (already synthesized)")
            continue

        with open(os.path.join(patch_dir, chunk_filename), "r", encoding="utf-8") as f:
            text = f.read()
```

with:

```python
    remote_out_dir = entry["folder"] + "/output"
    available = available_wavs(
        [out_dir, os.path.join(patch_dir, "output")], REMOTE, remote_out_dir
    )

    for offset, chunk_filename in enumerate(manifest["chunks"]):
        index = chunk_filename.split("_")[1].split(".")[0]  # chunk_000.txt -> 000
        wav_name = f"chunk_{index}.wav"
        if SKIP_EXISTING and wav_name in available:
            print(f"skip {chunk_filename} (already synthesized)")
            continue

        text = chunk_text_for(manifest, offset, patch_dir)
```

and immediately after the existing `persist(out_path, entry["folder"] + "/output")` line add:

```python
        available.add(wav_name)
```

**3f.** Replace the merge preflight. Change:

```python
    missing = [w for w in manifest["expected_outputs"] if find_wav(w) is None]
    if missing:
        print(f"patch incomplete - {len(missing)} chunk(s) missing "
              f"(first: {missing[0]}); re-run this cell to resume")
        summary.append((label, f"incomplete ({len(missing)} chunks missing)"))
        continue

    paths = [find_wav(wav_name) for wav_name in manifest["expected_outputs"]]
```

to:

```python
    missing = [w for w in manifest["expected_outputs"] if w not in available]
    if missing:
        print(f"patch incomplete - {len(missing)} chunk(s) missing "
              f"(first: {missing[0]}); re-run this cell to resume")
        summary.append((label, f"incomplete ({len(missing)} chunks missing)"))
        continue

    # Every expected chunk exists locally or on Drive. Pull down only the ones that
    # are not local yet - for a restarted session that is just this patch's earlier
    # chunks, not the whole batch.
    paths, pending = [], []
    for wav_name in manifest["expected_outputs"]:
        local = next(
            (c for c in (os.path.join(out_dir, wav_name),
                         os.path.join(patch_dir, "output", wav_name))
             if os.path.exists(c)),
            None,
        )
        if local:
            paths.append(local)
        else:
            dest = os.path.join(out_dir, wav_name)
            pending.append((remote_out_dir + "/" + wav_name, dest))
            paths.append(dest)

    if pending:
        print(f"fetching {len(pending)} chunk WAV(s) from Drive...")
        try:
            drive_fetch_many(pending)
        except Exception as exc:
            print(f"patch incomplete - chunk download failed: {exc}")
            summary.append((label, "incomplete (chunk download failed)"))
            continue
```

- [ ] **Step 4: Inject the cell and verify the notebook is valid**

```bash
python -c "
import json,io
p='app/assets/colab_kaggle_batch_tts_template.ipynb'
nb=json.load(io.open(p,encoding='utf-8'))
body=io.open('<scratch>/batch_cell8.py',encoding='utf-8').read()
nb['cells'][8]['source']=body.splitlines(keepends=True)
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(nb,ensure_ascii=False,indent=1))
print('cells:',len(nb['cells']))
"
python -c "import ast,json;nb=json.load(open('app/assets/colab_kaggle_batch_tts_template.ipynb',encoding='utf-8'));ast.parse(''.join(nb['cells'][8]['source']));print('cell 8 parses')"
```

Expected: `cells: 10` and `cell 8 parses`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/test_notebook_templates.py -v
```

Expected: PASS, including every pre-existing Cell 8 merge, timeline, atomicity and persist-failure test.

- [ ] **Step 6: Commit**

```bash
git add app/assets/colab_kaggle_batch_tts_template.ipynb tests/test_notebook_templates.py
git commit -m "perf: decide batch notebook work from the Drive inventory and fetch chunks lazily"
```

---

## Task 4: Batch Cell 4 — list first, download almost nothing

**Files:**
- Modify: `app/assets/colab_kaggle_batch_tts_template.ipynb` cell index 4
- Test: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: nothing from earlier tasks at runtime; Cell 8 (Task 3) reads the globals this task defines.
- Produces: globals `FOLDER_PATH`, `_drive_folder_ids`, `_drive_file_ids`, and functions `drive_fetch_many(pairs)`, `drive_fetch(rel, dest=None)`, `drive_persist(local_path, rel_dir)` (unchanged). Helper `plan_batch_downloads(batch_manifest, remote_files)` inside a new `# BEGIN CELL 4 HELPERS` block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notebook_templates.py`:

```python
def _cell4_helpers():
    src = _code_cells(TEMPLATES[1])[3]
    match = re.search(r"^# BEGIN CELL 4 HELPERS$(.*?)^# END CELL 4 HELPERS$", src, re.M | re.S)
    assert match, "Cell 4 helper block missing"
    namespace = {}
    exec(match.group(1), namespace)
    return namespace


def _fake_batch_inventory(patch_count=3, merged=0, chunks_per_patch=2):
    """Remote inventory for a batch where the first `merged` patches are finished."""
    manifest = {
        "reference_wav": "reference.wav",
        "patches": [
            {
                "patch_id": i,
                "patch_index": i,
                "folder": f"patches/patch_{i:03d}",
                "result_wav": f"result/{i:03d} - p.wav",
            }
            for i in range(patch_count)
        ],
    }
    remote = {"batch_manifest.json": "id", "reference.wav": "id", "music/bg.mp3": "id"}
    for i, entry in enumerate(manifest["patches"]):
        remote[f"{entry['folder']}/manifest.json"] = "id"
        remote[f"{entry['folder']}/background.jpg"] = "id"
        if i < merged:
            remote[entry["result_wav"]] = "id"
            for c in range(chunks_per_patch):
                remote[f"{entry['folder']}/output/chunk_{c:03d}.wav"] = "id"
    return manifest, remote


def test_cell4_plan_downloads_only_manifests_and_reference():
    plan_batch_downloads = _cell4_helpers()["plan_batch_downloads"]
    manifest, remote = _fake_batch_inventory(patch_count=3, merged=3)

    planned = plan_batch_downloads(manifest, remote)

    assert set(planned) == {
        "batch_manifest.json",
        "reference.wav",
        "patches/patch_000/manifest.json",
        "patches/patch_001/manifest.json",
        "patches/patch_002/manifest.json",
    }
    # A fully merged batch downloads no chunk WAV, no result, no background, no music.
    for rel in planned:
        assert "/output/" not in rel
        assert not rel.startswith("result/")
        assert not rel.startswith("music/")
        assert "background" not in rel


def test_cell4_plan_skips_paths_absent_from_the_inventory():
    plan_batch_downloads = _cell4_helpers()["plan_batch_downloads"]
    manifest, remote = _fake_batch_inventory(patch_count=2, merged=0)
    del remote["patches/patch_001/manifest.json"]
    del remote["reference.wav"]

    planned = plan_batch_downloads(manifest, remote)

    assert planned == ["batch_manifest.json", "patches/patch_000/manifest.json"]


def test_cell4_lists_before_downloading_and_is_thread_safe():
    src = _code_cells(TEMPLATES[1])[3]
    assert "plan_batch_downloads(" in src
    assert "ThreadPoolExecutor" in src
    assert "threading.local()" in src
    assert "def drive_fetch_many(" in src
    assert "def drive_persist(" in src
    # the old walk that downloaded while listing must be gone
    assert "def _sync_down(" not in src
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_notebook_templates.py -v -k cell4
```

Expected: FAIL with `AssertionError: Cell 4 helper block missing` on the first two, and the source assertions failing on the third.

- [ ] **Step 3: Write the new Cell 4 body**

Write the complete cell body to `<scratch>/batch_cell4.py`. Keep everything from the current cell up to and including the `_batch_folder_id` discovery and its `raise AssertionError(...)` — the credential setup, `_list_children`, `_download`, the export-root lookup, the `_seen_folders` diagnostics — all unchanged. Replace only from the `# Download the whole batch folder` comment onward with:

```python
    # Listing is separate from downloading. We already have to walk the folder to learn
    # every file id for drive_persist(), and that walk doubles as the remote inventory:
    # Cell 8 decides what is already merged or synthesized from it without transferring
    # a byte. Result WAVs, chunk WAVs, backgrounds and music are never downloaded here.
    FOLDER_PATH = "/kaggle/working/batch"
    _drive_folder_ids = {"": _batch_folder_id}
    _drive_file_ids = {}


    def _list_tree(folder_id, rel):
        for f in _list_children(folder_id):
            child_rel = f"{rel}/{f['name']}" if rel else f["name"]
            if f["mimeType"] == FOLDER_MIME:
                _drive_folder_ids[child_rel] = f["id"]
                _list_tree(f["id"], child_rel)
            else:
                _drive_file_ids[child_rel] = f["id"]


    _list_tree(_batch_folder_id, "")
    print(f"Indexed {len(_drive_file_ids)} files on Drive (nothing downloaded yet).")

    # googleapiclient's http object is NOT thread safe - sharing one service across a
    # pool produces intermittent, confusing failures rather than clean errors. Give each
    # worker thread its own service; build() uses static discovery, so it is cheap and
    # makes no network call.
    _thread_local = threading.local()


    def _service():
        service = getattr(_thread_local, "service", None)
        if service is None:
            service = build("drive", "v3", credentials=creds)
            _thread_local.service = service
        return service


    def _download_to(rel, dest):
        if rel not in _drive_file_ids:
            raise RuntimeError(f"not on Drive: {rel}")
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        request = _service().files().get_media(fileId=_drive_file_ids[rel])
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        os.replace(tmp, dest)
        return dest


    def drive_fetch_many(pairs):
        """Download [(batch_relative_path, local_destination)] in parallel.

        Raises on the first failure; callers treat that as a resumable condition
        rather than aborting the batch."""
        pairs = list(pairs)
        if not pairs:
            return []
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(lambda pair: _download_to(*pair), pairs))


    def drive_fetch(rel, dest=None):
        """Download one batch-relative path into the local batch folder."""
        return drive_fetch_many([(rel, dest or os.path.join(FOLDER_PATH, rel))])[0]


    # Bootstrap: the batch manifest names the patch folders, so it comes down first.
    drive_fetch("batch_manifest.json")
    with open(os.path.join(FOLDER_PATH, "batch_manifest.json"), encoding="utf-8") as fh:
        _bootstrap_manifest = json.load(fh)

    _wanted = plan_batch_downloads(_bootstrap_manifest, _drive_file_ids)
    _pending = [
        (rel, os.path.join(FOLDER_PATH, rel))
        for rel in _wanted
        if not os.path.exists(os.path.join(FOLDER_PATH, rel))
    ]
    drive_fetch_many(_pending)
    print(f"Batch ready at {FOLDER_PATH} ({len(_pending)} file(s) downloaded).")


    def drive_persist(local_path, rel_dir):
        """Upload a freshly written file into rel_dir inside the batch folder on Drive,
        creating the subfolder chain as needed and replacing an existing file in place.
        Cell 8 calls this after every chunk .wav and every merged result file, so a dead
        Kaggle session loses nothing."""
        parent, rel = _drive_folder_ids[""], ""
        for part in [p for p in rel_dir.split("/") if p]:
            rel = f"{rel}/{part}" if rel else part
            if rel not in _drive_folder_ids:
                folder = drive_service.files().create(
                    body={"name": part, "mimeType": FOLDER_MIME, "parents": [parent]},
                    fields="id",
                ).execute()
                _drive_folder_ids[rel] = folder["id"]
            parent = _drive_folder_ids[rel]
        name = os.path.basename(local_path)
        file_rel = f"{rel}/{name}" if rel else name
        media = MediaFileUpload(local_path)
        if file_rel in _drive_file_ids:
            drive_service.files().update(fileId=_drive_file_ids[file_rel], media_body=media).execute()
        else:
            created = drive_service.files().create(
                body={"name": name, "parents": [parent]}, media_body=media, fields="id",
            ).execute()
            _drive_file_ids[file_rel] = created["id"]
```

Add the helper block near the top of the cell, **outside** the `if not IS_KAGGLE: ... else:` branches so the tests can `exec` it without Kaggle imports. Put it immediately after the `import io` / `import json` / `import os` lines:

```python
import threading
from concurrent.futures import ThreadPoolExecutor

# BEGIN CELL 4 HELPERS
def plan_batch_downloads(batch_manifest, remote_files):
    """Batch-relative paths worth downloading before synthesis starts.

    Only the batch manifest, each patch manifest and the shared reference clip are
    read up front. Result WAVs are never read by this notebook at all - Cell 8 only
    tests whether they exist, which the inventory answers. Backgrounds and music are
    unused here. Chunk WAVs are fetched on demand at merge time."""
    wanted = ["batch_manifest.json"]
    reference = batch_manifest.get("reference_wav")
    if reference:
        wanted.append(reference)
    for entry in batch_manifest.get("patches", []):
        wanted.append(f"{entry['folder']}/manifest.json")
    return [rel for rel in wanted if rel in remote_files]
# END CELL 4 HELPERS
```

Also update the cell's header comment: the line `# This cell downloads the batch (including output/ wavs from earlier sessions) into` and the following line become:

```python
# This cell indexes the batch folder on Drive and downloads only the manifests and the
# shared voice reference into /kaggle/working/batch. Chunk .wav files are fetched on
# demand by Cell 8, and result .wav files are never downloaded. It also defines
# drive_persist(), which Cell 8 uses to upload every generated .wav and merged result
# file straight back to Drive.
```

- [ ] **Step 4: Inject the cell and verify**

```bash
python -c "
import json,io
p='app/assets/colab_kaggle_batch_tts_template.ipynb'
nb=json.load(io.open(p,encoding='utf-8'))
body=io.open('<scratch>/batch_cell4.py',encoding='utf-8').read()
nb['cells'][4]['source']=body.splitlines(keepends=True)
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(nb,ensure_ascii=False,indent=1))
print('cells:',len(nb['cells']))
"
```

Cell 4 contains a `!pip install` line, so it is not plain Python and `ast.parse` will fail on the whole cell — that is expected. Verify the helper block alone parses:

```bash
python -c "
import json,re,ast
nb=json.load(open('app/assets/colab_kaggle_batch_tts_template.ipynb',encoding='utf-8'))
src=''.join(nb['cells'][4]['source'])
m=re.search(r'^# BEGIN CELL 4 HELPERS\$(.*?)^# END CELL 4 HELPERS\$',src,re.M|re.S)
ast.parse(m.group(1)); print('helper block parses')
"
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/test_notebook_templates.py -v
```

Expected: PASS, including the pre-existing `test_is_kaggle_is_a_manual_global_set_in_cell_1`, `test_drive_mount_never_guarded_by_importerror` and `test_drive_mount_cells_guarded_by_is_kaggle`.

- [ ] **Step 6: Commit**

```bash
git add app/assets/colab_kaggle_batch_tts_template.ipynb tests/test_notebook_templates.py
git commit -m "perf: index the Drive batch folder instead of downloading it wholesale"
```

---

## Task 5: Remove batch Cell 9 and refresh the intro

**Files:**
- Modify: `app/assets/colab_kaggle_batch_tts_template.ipynb` — delete cell index 9, edit cell index 0 (markdown)
- Test: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the batch notebook drops to 9 cells; Cell 8 remains code-cell index 7, so `_code_cells(TEMPLATES[1])[7]` in existing tests keeps working.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notebook_templates.py`:

```python
def test_batch_notebook_has_no_result_zip_cell():
    nb = json.loads(TEMPLATES[1].read_text(encoding="utf-8"))
    assert len(nb["cells"]) == 9
    for cell in nb["cells"]:
        src = "".join(cell["source"])
        assert "make_archive" not in src
        assert "results.zip" not in src
        assert "Cell 9" not in src
    # Cell 8 must still be the eighth code cell for the other tests in this file
    assert "_CHUNK_PAUSE_MS = 300" in _code_cells(TEMPLATES[1])[7]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_notebook_templates.py::test_batch_notebook_has_no_result_zip_cell -v
```

Expected: FAIL on `assert len(nb["cells"]) == 9` (there are 10).

- [ ] **Step 3: Delete the cell**

```bash
python -c "
import json,io
p='app/assets/colab_kaggle_batch_tts_template.ipynb'
nb=json.load(io.open(p,encoding='utf-8'))
assert 'make_archive' in ''.join(nb['cells'][9]['source']), 'cell 9 is not the zip cell'
del nb['cells'][9]
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(nb,ensure_ascii=False,indent=1))
print('cells:',len(nb['cells']))
"
```

Expected: `cells: 9`.

- [ ] **Step 4: Update the intro markdown**

Read cell 0 and remove any sentence describing the results zip or "Cell 9". Where the intro explains how to get results off Kaggle, use:

```
In Kaggle Drive mode every merged result and its timeline sidecar is uploaded to
Drive as soon as it is written, so there is nothing to collect by hand. With the
zip-dataset fallback the results are under `/kaggle/working/result/` and the Kaggle
Output pane offers its own "Download All".
```

Apply it with the Write-then-inject procedure, targeting `nb['cells'][0]`.

- [ ] **Step 5: Run the full notebook test file**

```bash
pytest tests/test_notebook_templates.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/assets/colab_kaggle_batch_tts_template.ipynb tests/test_notebook_templates.py
git commit -m "refactor: drop the redundant batch result zip cell"
```

---

## Task 6: Single-patch notebook reads text from the manifest

**Files:**
- Modify: `app/assets/colab_kaggle_tts_template.ipynb` cell index 8
- Test: `tests/test_notebook_templates.py`

**Interfaces:**
- Consumes: `manifest["chunk_metadata"][i]["text"]` from Task 1.
- Produces: nothing.

`_write_patch_files` is shared by both package shapes, so this notebook breaks without the change. Its cell index 9 merges chunk WAVs by reading `manifest["chunks"]` for ordering only — it never opens a `.txt` and needs no change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_notebook_templates.py`:

```python
def test_single_patch_cell_8_reads_text_from_the_manifest():
    src = _code_cells(TEMPLATES[0])[7]
    assert 'chunk_metadata' in src
    assert 'open(os.path.join(FOLDER_PATH, chunk_filename)' not in src
    assert "enumerate(manifest[\"chunks\"])" in src
    # START_INDEX / END_INDEX windowing must survive
    assert "START_INDEX" in src
    assert "END_INDEX" in src
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_notebook_templates.py::test_single_patch_cell_8_reads_text_from_the_manifest -v
```

Expected: FAIL — the cell still opens the `.txt` file and iterates without `enumerate`.

- [ ] **Step 3: Write the new cell body**

Write the full cell to `<scratch>/single_cell8.py`, starting from the current cell 8 and changing only these three things.

Change the header comment's first two lines to:

```python
# Cell 8: synthesize the chunks and write chunk_NNN.wav into an 'output' subfolder
# (keeps generated audio separate from the exported manifest).
```

Change the loop header:

```python
for offset, chunk_filename in enumerate(manifest["chunks"]):
```

Replace the text read:

```python
    with open(os.path.join(FOLDER_PATH, chunk_filename), "r", encoding="utf-8") as f:
        text = f.read()
```

with:

```python
    # Chunk text travels inside the manifest; older packages still have the loose
    # chunk_NNN.txt files, so fall back to those when the field is absent.
    metadata = manifest.get("chunk_metadata") or []
    text = metadata[offset].get("text") if offset < len(metadata) else None
    if not (isinstance(text, str) and text.strip()):
        with open(os.path.join(FOLDER_PATH, chunk_filename), "r", encoding="utf-8") as f:
            text = f.read()
```

- [ ] **Step 4: Inject and verify**

```bash
python -c "
import json,io,ast
p='app/assets/colab_kaggle_tts_template.ipynb'
nb=json.load(io.open(p,encoding='utf-8'))
body=io.open('<scratch>/single_cell8.py',encoding='utf-8').read()
nb['cells'][8]['source']=body.splitlines(keepends=True)
io.open(p,'w',encoding='utf-8',newline='\n').write(json.dumps(nb,ensure_ascii=False,indent=1))
ast.parse(''.join(nb['cells'][8]['source'])); print('cells:',len(nb['cells']),'cell 8 parses')
"
```

Expected: `cells: 10 cell 8 parses`.

- [ ] **Step 5: Run the whole suite**

```bash
pytest tests/ -q
```

Expected: PASS. `test_heartbeat_keeps_long_create_claim_alive` is a known flake that fails only under full-suite timing — re-run it alone before blaming this change.

- [ ] **Step 6: Commit**

```bash
git add app/assets/colab_kaggle_tts_template.ipynb tests/test_notebook_templates.py
git commit -m "refactor: read single-patch chunk text from the export manifest"
```

---

## Self-Review Notes

**Spec coverage.** Export Format → Task 1 (and Task 6 for the second template). Publish Path Chunk Count → Task 2. Kaggle Cell 4: Inventory First → Task 4. Cell 8: merged-patch detection, chunk existence, chunk text, merge, validation → Task 3. Cell 9 Removal → Task 5. Colab → covered by Task 3's `REMOTE = {}` path, no Cell 3 change, as the spec requires. Error Handling → Task 3 step 3f (fetch failure leaves the patch resumable) and Task 4's `_download_to` (`.part` temp file plus `os.replace`, so an interrupted download never leaves a truncated WAV that later looks complete). Testing → every bullet maps to a named test above.

**Deliberate deviations from a literal reading of the spec.** The spec describes `drive_fetch(rel)`; the plan adds `drive_fetch_many(pairs)` as the primitive and defines `drive_fetch` in terms of it, because the merge step fetches a batch of chunks and the spec separately requires those to be parallel. The spec's `available` set is specified as built from two listdirs plus inventory keys; the plan extracts that as the testable helper `available_wavs`, and likewise extracts `plan_batch_downloads` from Cell 4's prose — without those seams the spec's "zero downloads for a merged batch" test cannot be written at all, since Cell 4 contains a `!pip install` line and cannot be `exec`ed.

**Thread safety.** Not in the spec, found while planning: `googleapiclient` service objects are not thread safe. Task 4 gives each pool thread its own service via `threading.local()`. Sharing one would fail intermittently rather than cleanly.

**Ordering.** Task 3 precedes Task 4 even though Cell 4 comes first in the notebook, because Cell 8 reads Cell 4's globals through `globals().get(...)` and is therefore testable alone; Cell 4's value cannot be demonstrated until Cell 8 consumes the inventory.

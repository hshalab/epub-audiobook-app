# Export Chunks / Fast Notebook Restart Design

## Goal

Make restarting a Colab or Kaggle notebook runtime cheap, at any point in a batch.

Today a Kaggle restart re-downloads the entire batch folder — including chunk WAVs and merged
result WAVs for patches that are already finished and will be skipped — and both platforms decide
"already synthesized" with two filesystem stats per chunk. For a 10-patch batch that is roughly
1.5 GB of wasted transfer and ~800 Drive round-trips in the worst case, and ~1000 FUSE stats on
Colab.

Two changes remove nearly all of it: the exported package SHALL carry chunk text inside
`manifest.json` instead of one file per chunk, and the notebook SHALL decide what work remains from
a remote file inventory it already builds, downloading chunk WAVs only when a merge actually needs
them.

## Scope

In scope: `app/drive_export.py`, `app/routes/patches.py` (publish path chunk counting),
`app/assets/colab_kaggle_batch_tts_template.ipynb`,
`app/assets/colab_kaggle_tts_template.ipynb`, and their tests.

Out of scope: MP4 rendering, YouTube publishing, the app-side import paths in
`app/routes/patches.py` that read result and chunk WAVs, and the local worker.

## Export Format

`drive_export._write_patch_files()` SHALL NOT write `chunk_NNN.txt` files.

Each `chunk_metadata` entry SHALL carry exactly five fields, ordered one-to-one with `chunks`:

```json
{
  "filename": "chunk_000.txt",
  "chapter_index": 10,
  "chapter_title": "Chapter title",
  "is_chapter_start": true,
  "text": "..."
}
```

`text` SHALL be the chunk text that was previously written to `chunk_NNN.txt`, taken unchanged from
`repository.build_patch_chunk_plan()`. It SHALL be a non-empty string.

`chunks` and `expected_outputs` SHALL keep their current shape. `chunks[i]` SHALL remain
`"chunk_000.txt"` and is a logical identifier, not a file on disk: the notebook validator
cross-checks `chunk_metadata[i]["filename"] == chunks[i]`, and both the notebook and the app derive
`chunk_NNN.wav` from the same ordinal. Renaming it would ripple into the validator, the index
parser and the app importer for no benefit.

All other manifest fields, the reference clip, per-patch background images, and music SHALL be
exported unchanged. The batch notebook does not read backgrounds or music, but the app and the
single-patch flow do; this design changes only what the notebook downloads, not what the app
exports.

The batch manifest SHALL NOT duplicate `chunk_metadata`.

Both notebook templates SHALL be updated to read chunk text from the manifest. Keeping the
single-patch template on `chunk_NNN.txt` would require `_write_patch_files` to emit two different
input shapes indefinitely, which costs more than updating the template's chunk-read loop.

## Publish Path Chunk Count

`app/routes/patches.py` records `chunk_count` for a manually published package by counting files
matching `chunk_*.txt` in the package directory. That count SHALL instead come from the package's
`manifest.json` `chunk_count` field. A missing or unreadable manifest SHALL be handled the same way
the route handles other malformed packages today, without silently recording zero.

## Kaggle Cell 4: Inventory First

The recursive walk that today lists and downloads in one pass SHALL split into a listing phase and
a selective download phase.

`_list_tree()` SHALL populate `_drive_folder_ids` and `_drive_file_ids` exactly as today and SHALL
download nothing. This listing is already required so `drive_persist()` can upload without
re-listing; it now doubles as the authoritative remote inventory.

The cell SHALL eagerly download only:

- `batch_manifest.json`
- each `patches/patch_NNN/manifest.json`
- the shared reference clip named by `batch_manifest["reference_wav"]`

For a 10-patch batch that is about twelve files.

The cell SHALL NOT download `result/*`, `output/*.wav`, `background.*`, or `music/*`. Cell 8 never
reads result WAVs, backgrounds, or music; chunk WAVs are fetched on demand by Cell 8.

The cell SHALL define `drive_fetch(rel)`, which downloads the file at batch-relative path `rel`
into `FOLDER_PATH` and returns the local path, creating parent directories as needed. It SHALL
raise if `rel` is not in `_drive_file_ids`.

Eager downloads and `drive_fetch` batches SHALL share one `ThreadPoolExecutor(max_workers=8)`.

`drive_persist()` SHALL keep its current behaviour and signature.

## Cell 8: Decide From The Inventory

Cell 8 SHALL read `REMOTE = globals().get("_drive_file_ids") or {}`. On Colab and in the
zip-dataset fallback this is an empty dict, so both platforms follow one code path.

### Merged-patch detection

A patch SHALL be treated as already merged when
`os.path.exists(result_path) or entry["result_wav"] in REMOTE`.

The remote half is load-bearing. Because result WAVs are no longer downloaded, a restarted Kaggle
session has no local result file; without this check every finished patch would be re-merged, which
would pull every chunk WAV back down and defeat the entire design.

The existing timeline-sidecar warning for skipped patches SHALL use the same local-or-remote test.

### Chunk existence

For each patch, Cell 8 SHALL build one `available` set of WAV filenames from:

- `os.listdir(out_dir)`, when the directory exists,
- `os.listdir(os.path.join(patch_dir, "output"))`, when the directory exists,
- the basenames of `REMOTE` keys whose batch-relative directory is
  `f"{entry['folder']}/output"`.

`REMOTE` keys are always batch-relative, while `out_dir` is rooted at `WORK_ROOT`, which differs
from `FOLDER_PATH` only in the zip-dataset fallback. In that fallback `REMOTE` is empty, so the two
never need to be reconciled.

`find_wav()`'s two `os.path.exists` calls per chunk SHALL be replaced by membership in that set.
This turns roughly 1000 FUSE stats per Colab run into about 20 directory listings.

### Chunk text

Chunk text SHALL come from `chunk_metadata[i]["text"]`. When that field is absent, Cell 8 SHALL
fall back to reading `chunk_NNN.txt` from the patch directory, so an older exported zip attached as
a Kaggle dataset still runs.

### Merge

Before merging, Cell 8 SHALL determine which entries of `expected_outputs` are absent locally and
SHALL fetch exactly those via `drive_fetch`, in parallel. It SHALL then merge with the existing
streamed, atomic `merge_wav_files` helper.

A fetch failure SHALL leave the patch incomplete and resumable, SHALL NOT replace an existing
result WAV or timeline sidecar, and SHALL record a summary entry naming the missing chunk.

### Validation

`validate_chunk_metadata` SHALL require the five-field set and SHALL reject an entry whose `text`
is not a `str` or is empty after stripping. All other invariants it enforces today — one-to-one
length with `chunks`, matching `filename`, non-decreasing `chapter_index`, first entry is a chapter
start, marker ordering — SHALL be unchanged.

Invalid metadata SHALL continue to disable timeline generation for that patch without preventing
synthesis or merge.

## Cell 9 Removal

Cell 9, which zipped `result/` for the Kaggle Output pane, SHALL be removed.

In Kaggle Drive mode every result WAV and sidecar is already uploaded to Drive by `drive_persist`
as soon as it is written. In the zip-dataset fallback results land in `/kaggle/working/result/`,
which the Kaggle Output pane exposes with its own "Download All" archive. Removing the cell also
stops a second full copy of every result counting against the 20 GB working-directory limit.

The notebook's intro markdown SHALL be updated so it no longer refers to Cell 9 or to cell numbers
that shift.

## Colab

Cell 3 SHALL be unchanged; mounting is already cheap and the export-folder scan reads only
`batch_manifest.json` per folder. Colab benefits from the Cell 8 changes through the `available`
set and the inlined chunk text.

## Expected Effect

For a batch of 10 patches at roughly 50 chunks each, 3 MB per chunk WAV and 150 MB per merged
result:

| Restart state | Now | After |
| --- | --- | --- |
| 5 patches merged, 5 not | ~1.5 GB, ~800 round-trips | ~90 MB, ~60 |
| Nothing synthesized yet | ~510 downloads | ~12 |
| Colab, any state | ~1000 FUSE stats | ~20 listdirs |

The ~90 MB in the first row is the existing chunk WAVs of the one partially finished patch, which a
merge genuinely needs.

## Error Handling

- A Drive listing failure SHALL fail Cell 4 with the current diagnostics, unchanged.
- A missing `batch_manifest.json`, patch `manifest.json`, or reference clip SHALL fail with the
  current messages.
- `drive_fetch` on a path absent from the inventory SHALL raise, and at merge time SHALL mark the
  patch incomplete rather than aborting the batch.
- Chunk synthesis errors SHALL continue to stop the run with the original exception.
- Merge preflight and streaming errors SHALL NOT replace an existing result WAV.
- Timeline write and persist failures SHALL NOT fail a completed result WAV.
- A patch whose result exists remotely but is corrupt SHALL be skipped, matching today's behaviour
  for a corrupt local result. Verifying remote results is not in scope.

## Testing

Tests SHALL verify:

- `_write_patch_files` writes no `chunk_NNN.txt` and inlines `text` one-to-one with `chunks`.
- Inlined `text` matches `build_patch_chunk_plan()` output exactly and preserves chapter boundaries,
  multi-chunk chapters marking only their first chunk, and omission of excluded and empty chapters.
- `chunks` and `expected_outputs` keep their current shape.
- The manual publish path records `chunk_count` from `manifest.json`.
- Both notebook templates remain valid JSON and keep their `IS_KAGGLE` guards and reference-clip
  safeguards.
- The batch notebook has no Cell 9 and its markdown does not reference it.
- Cell 8 helpers, extracted through the existing `BEGIN/END CELL 8 HELPERS` markers, reject
  metadata whose `text` is missing, non-string, or blank, and accept valid five-field metadata.
- Cell 8 sources contain the local-or-remote merged-patch test, the `available` set construction,
  and the lazy `drive_fetch` merge step.
- Against a fake Drive inventory: a fully merged batch triggers zero chunk and zero result
  downloads; a batch with one partially finished patch downloads only that patch's existing chunk
  WAVs; and no background, music, or result file is ever downloaded.
- The existing merge, timeline, atomicity, and persist-failure tests continue to pass unchanged.

## Out of Scope

- Removing background images or music from batch export packages.
- Verifying the integrity of result WAVs that exist only on Drive.
- Reducing the number of chunk WAV uploads during a run.
- A separate `progress.json` state file; the remote inventory is already authoritative and free,
  and a second source of truth can disagree with it.
- Changing the app-side import paths.

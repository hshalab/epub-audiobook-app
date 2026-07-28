# Batch Notebook VoxCPM Refactor Design

## Goal

Apply the local VoxCPM synthesis refactor to `colab_kaggle_batch_tts_template.ipynb` while keeping previously exported batches usable.

New exports SHALL preserve chapter boundaries, use deterministic VoxCPM generation, insert a 300 ms pause between chunks, and produce exact chapter timeline sidecars for merged patch WAV files. Old exports SHALL still synthesize with seed 42 and merge with 300 ms pauses, but SHALL omit chapter timelines because they lack authoritative chapter metadata.

## Export Manifest

`drive_export._write_patch_files()` SHALL use `repository.build_patch_chunk_plan()` as the source for exported chunk text. This keeps local worker synthesis, chunk manager display, and notebook export on one chapter-aware chunk plan.

The existing manifest fields SHALL remain compatible:

- `chunks` remains an ordered list of `chunk_NNN.txt` filenames.
- `expected_outputs` remains the corresponding ordered list of `chunk_NNN.wav` filenames.
- Existing patch, reference voice, background, and model fields remain unchanged.

New manifests SHALL add `chunk_metadata`, ordered one-to-one with `chunks`:

```json
{
  "filename": "chunk_000.txt",
  "chapter_index": 10,
  "chapter_title": "Chapter title",
  "is_chapter_start": true
}
```

Each object SHALL contain exactly those four fields. No TTS chunk may span chapters. Excluded, whitespace-only, and punctuation-only chapters SHALL not produce chunks or timeline markers.

The batch manifest SHALL not duplicate `chunk_metadata`; Cell 8 already reads each patch's `manifest.json`.

## VoxCPM Generation

Cell 8 SHALL pass `seed=42`, `cfg_value=2.0`, and `inference_timesteps=10` to every `model.generate()` call.

When a reference WAV and transcript exist, the notebook SHALL continue Ultimate Cloning by passing the same file as `reference_wav_path` and `prompt_wav_path` together with `prompt_text`. The reference WAV remains mandatory for exported batches. The notebook SHALL not inject style or emotion instructions into narration text.

## Chunk Merge

Cell 8 SHALL merge chunk WAVs into each patch result using bounded-memory streaming I/O rather than loading every chunk and calling `np.concatenate`.

Before opening or replacing the result WAV, the notebook SHALL inspect every expected chunk WAV and verify that all sample rates and channel counts match the first chunk. A missing, unreadable, or incompatible chunk SHALL leave the existing result untouched and mark the patch incomplete or failed with a clear message.

The merge SHALL:

- Insert `round(sample_rate * 300 / 1000)` zero frames between adjacent chunks.
- Add no silence before the first chunk or after the last chunk.
- Preserve mono or multichannel audio.
- Write the result as PCM 16-bit WAV.
- Use a temporary result file in the result directory and atomically replace the destination only after merge succeeds.

## Chapter Timeline

When `chunk_metadata` is present and valid, Cell 8 SHALL calculate chapter starts from actual chunk WAV frame counts. The running position starts at frame zero; before every chunk except the first it advances by the 300 ms pause; when `is_chapter_start` is true it records the current frame; then it advances by that chunk's frame count.

The timeline SHALL be written beside the merged result using the result basename and `.timeline.json` suffix. It SHALL use the same version-1 schema as local worker output:

```json
{
  "version": 1,
  "sample_rate": 48000,
  "total_frames": 12345678,
  "chapters": [
    {
      "chapter_index": 10,
      "title": "Chapter title",
      "start_frame": 0,
      "start_seconds": 0.0
    }
  ]
}
```

The timeline SHALL be written to a temporary file and atomically replace the destination only after the result WAV succeeds. A timeline write failure SHALL not fail or delete the valid result WAV and SHALL preserve any existing sidecar. In Kaggle Drive mode, the notebook SHALL persist a successfully written timeline file into `result/` alongside the WAV.

## Old Export Compatibility

If `chunk_metadata` is absent, the notebook SHALL:

- Continue synthesizing chunks with seed 42 and the shared voice reference.
- Merge chunks with 300 ms pauses using the new streamed merge.
- Print a warning that chapter metadata is unavailable.
- Omit timeline sidecar generation.

It SHALL not infer chapter boundaries from text or filenames.

If `SKIP_EXISTING=True` and a result WAV already exists, the notebook SHALL retain current skip behavior. When a new manifest has `chunk_metadata` but the existing result has no timeline sidecar, it SHALL print a warning explaining that the result must be deleted and the cell rerun to regenerate it with the new pause/timeline behavior. It SHALL not silently build a timeline for an existing WAV whose merge semantics are unknown.

## Importing Notebook Results

The app SHALL prefer importing the notebook's completed result WAV and timeline sidecar instead of downloading chunk WAVs and merging them again. This preserves the exact audio/timeline pair generated by the notebook and avoids applying merge behavior twice.

Each patch export record already identifies its patch folder. For batch exports, the importer SHALL locate the batch root from that patch folder and resolve the result path from `batch_manifest.json` by matching `patch_id`. For single-patch or legacy exports without a batch result entry, direct-result import is unavailable and the importer SHALL use the existing chunk fallback.

When the expected result WAV exists, the importer SHALL:

1. Inspect the source WAV with SoundFile.
2. Look for the matching `.timeline.json` beside it.
3. If the sidecar exists, validate the same version-1 invariants used by YouTube metadata: matching sample rate and total frames, ordered chapter entries, authoritative `start_frame`, and matching `start_seconds`.
4. Copy the result WAV to a temporary file beside the patch's local destination and atomically replace `<data_root>/books/<book_id>/patches/<patch_id>.wav` only after the copy succeeds.
5. If the sidecar is valid, copy it through a temporary file and atomically replace `<patch_id>.timeline.json`.
6. If the sidecar is absent or invalid, remove any stale local timeline sidecar and keep the imported WAV; timeline availability must not block audio import.
7. Mark the patch done and trigger `on_patch_audio_ready` only after the local WAV is installed.

The direct-result import SHALL not copy notebook result filenames directly into the app data directory. The app's canonical destination remains patch-ID based, so existing publishing and cleanup code can find `<patch_id>.timeline.json` beside `patch.audio_path`.

When no completed result WAV is available, the importer SHALL fall back to the existing contiguous chunk import. That fallback SHALL use the shared chapter-aware plan to determine the expected chunk count, merge all chunks with the same 300 ms pause, calculate timeline data from actual imported chunk frames when `chunk_metadata` is present, and write the canonical local sidecar. Legacy manifests without `chunk_metadata` SHALL still import and merge with the pause but SHALL omit timeline generation.

The Google Drive API path and Google Drive Desktop path SHALL follow the same result-first policy whenever they can access the result files. Local manual chunk upload remains a chunk-only flow; it SHALL merge with the 300 ms pause and MAY create a timeline only when authoritative exported metadata is supplied in the uploaded package. Uploading loose WAV chunks alone SHALL not infer chapter boundaries.

Direct-result installation errors SHALL leave the previous local WAV and sidecar pair unchanged. Once the new WAV is installed, a sidecar installation failure SHALL not fail audio import; it SHALL remove any stale local sidecar so metadata cannot describe the wrong WAV.

## Error Handling

- Invalid `chunk_metadata` length, filenames, types, chapter titles, or marker order SHALL disable timeline generation for that patch but SHALL not prevent synthesis or merge.
- Chunk synthesis errors SHALL continue to stop the current run with the original exception so the failing chunk is visible.
- Missing expected chunk WAVs SHALL keep the patch incomplete and resumable.
- Merge preflight or streaming errors SHALL not replace an existing result WAV.
- Timeline serialization, flush, or replace errors SHALL be logged and SHALL not fail the completed patch WAV.
- A corrupt or unreadable notebook result WAV SHALL trigger the chunk-import fallback when complete chunks are available; otherwise the import SHALL fail without changing the local patch audio.
- A missing or invalid result sidecar SHALL not block result WAV import and SHALL remove stale local timeline metadata.
- A result WAV copy failure SHALL not replace the existing local WAV or timeline sidecar.

## Testing

Tests SHALL verify:

- Patch export uses the shared chapter-aware plan and writes one-to-one `chunk_metadata`.
- Exported chunks do not cross chapter boundaries.
- Multi-chunk chapters mark only their first chunk.
- Excluded and empty chapters are absent.
- Existing `chunks` and `expected_outputs` fields remain unchanged in shape.
- The notebook remains valid JSON and preserves platform/reference safeguards.
- Cell 8 passes `seed=42` and retains Ultimate Cloning arguments.
- Cell 8 contains 300 ms pause calculation, streamed merge, format preflight, temporary WAV replacement, timeline frame calculation, atomic sidecar replacement, and Kaggle sidecar persistence.
- Cell 8 has an explicit fallback for manifests without `chunk_metadata`.
- Existing-result skip behavior warns when chapter metadata exists but the timeline sidecar does not.
- Drive Desktop import prefers the batch result WAV and installs a valid sidecar at the canonical patch-ID path.
- Result import without a sidecar succeeds and removes a stale local sidecar.
- Invalid result WAVs fall back to complete chunk files without changing local media first.
- Chunk fallback merges with 300 ms pauses and creates a timeline only from authoritative `chunk_metadata`.
- Atomic result-copy failure preserves the previous local WAV and sidecar.

## Out of Scope

- Updating the single-patch notebook template.
- Generating a whole-book timeline across patch results.
- Automatically uploading YouTube metadata from the notebook.
- Inferring chapter boundaries for old exports.
- Adding new notebook UI controls for seed or pause duration.

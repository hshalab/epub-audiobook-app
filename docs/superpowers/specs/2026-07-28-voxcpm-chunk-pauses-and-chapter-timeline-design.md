# VoxCPM Chunk Pauses and Chapter Timeline Design

## Goal

Improve VoxCPM audiobook patches in three related ways:

1. Insert a 300 ms pause between generated TTS chunks.
2. Make every VoxCPM generation reproducible and more consistent with the configured voice reference.
3. Record the exact start of each chapter in a patch and automatically append a valid YouTube Chapters timeline to that patch's video description.

The feature applies to patch videos. A typical patch contains about ten chapters and hundreds of TTS chunks. Timeline timestamps are relative to the start of the patch audio, not the whole book.

## Current Constraints

- The worker currently joins all chapter text in a patch before TTS chunking. A generated chunk can therefore cross a chapter boundary, which prevents an exact chapter timestamp.
- Both in-memory and file-based chunk merge paths concatenate audio without silence.
- `VoxCPMEngine` already sends the same reference clip and transcript to every generation call, using Ultimate Cloning when both are present.
- Patch YouTube metadata is resolved and frozen into `patch_pipeline.config_snapshot` before upload.
- Final book audio is assembled separately from completed patch WAV files.

## Chosen Approach

Preserve chapter boundaries before TTS chunking, add silence in the shared audio merge functions, and store each completed patch's chapter timing in a JSON sidecar next to its WAV file.

This avoids a database migration and keeps timing metadata coupled to the exact audio version that produced it. YouTube publishing reads the sidecar when resolving the patch metadata. Missing or invalid timing data never blocks publishing.

## Chapter-Aware Synthesis

The worker SHALL process included chapters in chapter order. For each chapter it SHALL:

1. Apply the same title punctuation handling currently performed during patch text assembly.
2. Run chapter-title normalization and the book's enabled text normalization options.
3. Apply the book's replacement rules.
4. Split the resulting chapter text into TTS chunks independently from every other chapter.

The worker SHALL then flatten those chapter chunk lists into one ordered patch chunk list. No TTS chunk may contain text from more than one chapter.

Empty normalized chapter text SHALL be skipped and SHALL not produce a timeline entry. Excluded chapters SHALL also be omitted. Chunk file indices and `patch.next_chunk_index` SHALL continue to use one flat zero-based sequence for the whole patch so the current resumable synthesis model remains intact.

The in-memory and chunk-file output modes SHALL use the same chapter-aware chunk plan so their audio and timeline semantics match.

## Chunk Pauses

`audio_merge.concat_chunks_to_wav` and `audio_merge.concat_wavs` SHALL accept an optional pause duration whose default preserves existing callers. The worker SHALL pass 300 ms when merging TTS chunks into a patch in either output mode.

The merge SHALL:

- Insert silence only between adjacent TTS chunks.
- Add no silence before the first chunk or after the final chunk.
- Derive the silence frame count from the source sample rate.
- Preserve the source channel count in file-based merging.

Final book assembly SHALL continue merging patch WAV files without this chunk pause. The feature must not silently add a second pause between patches.

## VoxCPM Stability

`VoxCPMEngine` SHALL have a generation seed with default value `42` and SHALL pass `seed=42` to every `VoxCPM.generate()` call.

When both a reference WAV and its transcript are configured, the engine SHALL continue passing the same file as both `reference_wav_path` and `prompt_wav_path`, together with `prompt_text`, for every chunk. This is VoxCPM2 Ultimate Cloning and is the selected mechanism for prioritizing reference-voice similarity over additional expression.

The change SHALL retain `cfg_value=2.0` and `inference_timesteps=10`. It SHALL not inject style or emotion instructions into narration text because that could alter spoken content and is not required to improve deterministic voice consistency.

## Timeline Calculation

A chapter timestamp SHALL identify the first audio frame spoken for that chapter, after any 300 ms pause preceding its first chunk.

For a flattened chunk plan, the worker SHALL calculate positions from actual generated frame counts, not text length or estimated speech duration. In-memory mode SHALL use the generated arrays' frame counts; chunk-file mode SHALL use each WAV file's frame count. The running patch frame position starts at zero. Before each chunk after the first, it advances by the exact inserted silence frame count; the chapter start is recorded when the worker reaches that chapter's first chunk; it then advances by that chunk's generated frame count.

Consequently:

- The first synthesized chapter starts at frame 0 and timestamp `00:00`.
- A later chapter starts after the silence between the preceding chunk and that chapter's first chunk.
- Pauses between chunks within one chapter contribute to all following chapter timestamps.
- Resume synthesis in chunk-file mode SHALL derive positions from the actual frame counts of all existing earlier chunk WAV files. It SHALL not assume a fixed generated chunk duration.

## Timeline Sidecar

After a patch WAV has merged successfully, the worker SHALL atomically write a sidecar at the same base path with suffix `.timeline.json`. For example, patch audio `patches/123.wav` has timeline `patches/123.timeline.json`.

The sidecar SHALL contain enough information to validate that timing matches the audio:

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

`start_frame` is authoritative. `start_seconds` is included for inspection and SHALL equal `start_frame / sample_rate`. Entries SHALL be ordered by `start_frame`, use the source chapter title, and contain only included chapters that produced audio.

The sidecar SHALL be written only after the final patch WAV succeeds. A failed synthesis or merge must not replace a previously valid timeline with partial data. Existing patch reset, regenerate, and delete cleanup paths SHALL remove the sidecar together with stale generated media.

## YouTube Description Integration

Patch YouTube metadata resolution SHALL attempt to load the sidecar associated with `patch.audio_path`. A valid timeline block SHALL be appended to the configured or overridden description before `patch_pipeline.config_snapshot` is created.

Each line SHALL use a YouTube-compatible timestamp and chapter title:

```text
00:00 Chapter title
12:34 Next chapter title
```

Hours SHALL be included when required, for example `1:02:03`. Timestamp formatting SHALL use whole seconds by flooring the exact frame position so a link never seeks after the true start of speech.

The timeline SHALL be appended only when all of these conditions hold:

- The sidecar parses successfully and matches the patch WAV sample rate and total frame count.
- There are at least three chapter entries.
- The first formatted timestamp is `00:00`.
- Every adjacent chapter start is at least 10 seconds apart.
- At least 10 seconds remain from the final chapter start to the end of the patch audio.
- Every title is non-empty after trimming.
- The complete description, including a separating blank line and timeline lines, is no longer than YouTube's 5,000-character limit.

If any condition fails, the system SHALL append no timeline at all. It SHALL not merge short chapters, estimate missing timestamps, truncate the timeline, or reject publishing. The original description remains unchanged.

Timeline insertion SHALL be idempotent within metadata resolution: a timeline generated by the application must not be appended twice when metadata is previewed, retried, or re-snapshotted.

## Error Handling

- Invalid, missing, stale, or unreadable sidecars SHALL be treated as unavailable timeline data.
- Timeline failures SHALL not fail audio synthesis after a valid patch WAV has been created.
- Timeline failures SHALL not fail YouTube publishing; publishing proceeds with the ordinary description.
- Audio format incompatibilities between chunk files SHALL retain the merge function's existing failure behavior rather than producing inaccurate timing.

## Testing

Tests SHALL cover:

- Exactly 300 ms of silence between two and multiple chunks.
- No leading or trailing silence introduced by the merge layer.
- Correct pause handling for mono and multi-channel file-based audio.
- No new pause during final patch-to-book merging.
- VoxCPM receives `seed=42` on every generation call.
- Ultimate Cloning arguments remain present for every referenced chunk.
- A chapter spanning many chunks receives one timestamp at its first spoken frame.
- A later chapter starts after the preceding inter-chunk silence.
- Excluded and empty chapters do not produce timeline entries.
- Resumed synthesis computes positions from existing chunk file frame counts.
- Sidecar output is complete, ordered, and atomically replaces stale timing only after successful merge.
- Reset, regeneration, and deletion remove stale sidecars.
- Valid sidecars append correctly formatted timeline lines to patch metadata.
- Timelines are omitted for fewer than three entries, any interval under 10 seconds, a final segment under 10 seconds, stale or malformed data, empty titles, or a description over 5,000 characters.
- Metadata preview, retry, and publish do not duplicate an application-generated timeline.

## Out of Scope

- A timeline for the final whole-book audio or video.
- Manual timeline editing in the UI.
- Automatic merging of chapters shorter than YouTube's minimum duration.
- Speech or speaker-similarity analysis after synthesis.
- New VoxCPM style-control text or changes to CFG and inference timestep defaults.

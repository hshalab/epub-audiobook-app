# Video Integrity Validation and Automatic Re-rendering

## Goal

Prevent incomplete or corrupt FFmpeg outputs from reaching YouTube. Every upload must pass a full-file validation first. A video rendered by the application is automatically rendered again with the same configuration up to two times when validation finds a recoverable output defect. An externally supplied file is blocked but is never re-rendered.

## Scope

The validation gate applies to every YouTube upload, including:

- whole-book videos;
- per-patch videos;
- videos made by the standalone video creator;
- old videos created before this feature;
- externally supplied videos.

Existing videos are validated on demand immediately before upload. The application does not scan every stored video at startup.

## Chosen Approach

Put the authoritative validation gate in the `youtube_upload` job and expose validation as a queue-independent service. Renderers also call the same service before publishing an output so defects are found early, but the upload gate always validates the current file again.

This fits the existing queue and guarantees that no upload route can bypass validation. A separate `video_validation` job would provide more scheduling detail but would add queue, backfill, pipeline, and UI complexity without providing a necessary capability.

## Architecture

### Integrity Service

Add `app/video_integrity.py`. It has no queue or database dependency. Its public operation accepts a video path and returns a structured result containing:

- whether the file is valid;
- a stable error code;
- a bounded human-readable message;
- relevant stream and duration facts;
- validation elapsed time.

Validation runs in this order:

1. Confirm that the path is a non-empty regular file.
2. Run `ffprobe` and parse the container and streams.
3. Require at least one usable video stream and one usable audio stream.
4. Require finite, positive stream durations.
5. Reject audio/video duration drift of 5 seconds or more. Drift from 1 up to 5 seconds is recorded as a warning but does not block upload.
6. Check that the container and selected stream codecs are supported by the application's YouTube output policy.
7. Decode the complete selected video and audio streams using FFmpeg with `-v error -xerror`, explicit stream maps, and null output.

The existing integrity script can consume this service instead of maintaining separate validation logic.

Validation results are not treated as a permanent cache. Every upload attempt performs a full validation against the current file.

### Upload Gate

The `youtube_upload` handler enters a `validating` phase and validates the file before setting the upload to `uploading` or calling any YouTube API. No video bytes are transmitted if validation fails.

On success, the handler records the result and continues through the existing upload and publishing flow. On failure, it records the validation error and determines whether the source is re-renderable.

### Render Integration

Every renderer writes to a temporary file in the destination directory, for example `video_123.rendering.mp4`. It validates that temporary file after FFmpeg exits. Only a valid file is atomically replaced into the final path. A failed or cancelled render removes its temporary output and leaves any prior valid final file untouched.

The renderer's validation provides early feedback. The upload handler still validates again to cover old files, external files, and damage after rendering.

## Source Identification

An upload records enough provenance to classify its input as one of:

- `book`, linked to the book/video render source;
- `patch`, linked to a patch and its pipeline;
- `standalone`, linked to a `videos` record with reproducible render inputs;
- `external`, with no reproducible application source.

Whole-book and patch renders continue to use their existing persisted configuration or pipeline snapshot. Standalone video creation uses the persisted `videos` render data. The upload row does not duplicate a second configuration snapshot.

If the required audio, backgrounds, or persisted configuration are no longer available, the source is classified as unavailable and automatic re-rendering stops.

## Automatic Re-render Flow

When validation reports a recoverable output defect for an application-rendered video:

1. Increment the persisted integrity retry count before enqueueing work.
2. Set validation to `waiting_for_rerender` and expose `rerendering` in the owning video or patch pipeline.
3. Enqueue the appropriate render operation with its original configuration and a dedupe key for that retry generation.
4. Render to a temporary file and fully validate it.
5. Atomically replace the final path only after validation succeeds.
6. Return the existing upload row to `pending` and enqueue its upload job again.

The same upload row is retained so title, description, privacy, tags, thumbnail, playlist, and pipeline links remain intact.

The maximum is two automatic re-render attempts after the initial invalid output. The first invalid validation schedules retry `1/2`; a second invalid output schedules retry `2/2`; failure of the second re-rendered output is terminal. The count is stored before enqueueing so a crash cannot create an unlimited loop.

Re-rendering always uses the original render configuration. It does not automatically switch NVENC to `libx264`.

External files fail validation immediately and are never re-rendered. The user must replace the source file and explicitly retry the upload.

## Error Classification

Stable validation codes include:

- `file_missing`;
- `file_empty`;
- `probe_failed`;
- `missing_video_stream`;
- `missing_audio_stream`;
- `invalid_duration`;
- `unsupported_format`;
- `av_drift`;
- `decode_failed`;
- `validation_timeout`;
- `tool_unavailable`;
- `source_unavailable`.

Output defects such as `probe_failed`, missing streams, invalid duration, fatal A/V drift, unsupported render output, and `decode_failed` are eligible for automatic re-rendering when a reproducible source exists.

Environment and source defects such as missing FFmpeg/ffprobe, permission errors, an unavailable source, or a validation timeout caused by a stuck local tool are terminal for that attempt and do not consume both re-render attempts. Queue retry policy may retry transient infrastructure failures, but it must not increment the integrity retry count or bypass validation.

FFmpeg stderr stored in the database and job log is truncated to a bounded tail. Logs include the phase, elapsed time, error code, and retry number without storing unbounded command output.

## Persistence

Add these fields to `youtube_uploads` using the project's existing additive migration mechanism:

- `validation_status TEXT NOT NULL DEFAULT 'pending'`;
- `validation_error_code TEXT`;
- `validation_error_message TEXT`;
- `validated_at TEXT`;
- `integrity_retry_count INTEGER NOT NULL DEFAULT 0`;
- `render_source_type TEXT NOT NULL DEFAULT 'external'`;
- `render_source_id INTEGER`.

Allowed validation statuses are:

- `pending`;
- `validating`;
- `valid`;
- `failed`;
- `waiting_for_rerender`.

`youtube_uploads.status` remains the upload lifecycle. Validation fields represent only file integrity. `videos.upload_status`, `patch_pipeline`, and queue phases mirror user-facing states such as `validating`, `rerendering`, and terminal failure.

New upload creation points explicitly set source provenance. Existing rows default to `external` unless their relationship can be inferred safely during migration or when the upload job starts. Inference must use existing foreign keys and pipeline links, not filename patterns. A row that cannot be identified safely remains external and is blocked rather than incorrectly re-rendered.

## Concurrency and Recovery

FFmpeg validation runs without holding a SQLite lock. Database state changes are short transactions around validation.

The final output is never written in place while an uploader may read it. Temporary output plus atomic replacement prevents partial files from appearing at the final path.

Queue dedupe keys include the upload identity and integrity retry generation. Backfill restores work based on persisted states:

- `pending` validation/upload rows enqueue upload validation;
- `waiting_for_rerender` rows enqueue or retain their source render;
- `validating` rows left by a terminated worker return to a recoverable pending state;
- terminal `failed` rows are not automatically restarted.

Backfill never resets `integrity_retry_count`.

Cancellation must terminate the FFmpeg child process, clean the temporary output, and leave a state that backfill or an explicit retry can recover.

## Timeout

Full decode receives a duration-aware timeout computed from the probed media duration, with a minimum allowance for process startup and slow disks and a finite upper bound. The implementation plan will define one shared formula and tests around its boundaries. A timeout fails closed: upload does not start.

## User Experience

Queue logs and relevant video or patch views show:

- `validating` while decoding the file;
- `rerendering 1/2` or `rerendering 2/2` after a recoverable failure;
- the stable validation error and concise message for a terminal failure.

An external invalid file explains that automatic re-rendering is unavailable. A source-unavailable application video explains which required input is missing.

## Testing

### Integrity Unit Tests

- Valid `ffprobe` result with supported audio and video.
- Missing, empty, unreadable, and truncated files.
- Missing audio or video stream.
- Non-finite, zero, and malformed durations.
- A/V drift below the warning threshold, in the warning range, and at or above the fatal threshold.
- Supported and unsupported container/codec combinations.
- Full decode command uses `-xerror`, maps video and audio explicitly, and emits null output.
- Non-zero decode exit, bounded stderr, and timeout classification.

### Handler and Retry Tests

- Validation always runs before `youtube.process_upload`.
- A validation failure never calls a YouTube API.
- Retry `1/2` and `2/2` enqueue exactly one appropriate render each.
- Failure after retry `2/2` is terminal and cannot be revived by normal queue retry.
- Upload metadata and publishing relationships survive re-rendering.
- Environment failures do not increment the integrity retry count.
- External files are blocked without a render job.

### Renderer and Recovery Tests

- The final path is atomically replaced only after successful validation.
- Render or validation failure cleans the temporary path and preserves an existing final file.
- Whole-book, patch, and standalone source reconstruction uses the original configuration.
- Missing source data produces `source_unavailable` without re-rendering.
- Backfill resumes interrupted validation and re-render states without resetting counts or creating duplicate jobs.
- Existing manual upload, auto-upload, thumbnail, playlist, and publishing behavior remains intact after successful validation.

## Acceptance Criteria

- No upload sends video data to YouTube before a complete successful decode validation.
- Every application-generated video type can be re-rendered automatically with its original configuration when its persisted source is available.
- Automatic re-rendering occurs no more than two times for one upload.
- External files are validated and blocked on failure but never re-rendered.
- Retry limits and workflow state survive process restarts.
- A final output path never exposes an incomplete render.
- Queue and video/pipeline status clearly report validation, re-render progress, retry count, and terminal cause.

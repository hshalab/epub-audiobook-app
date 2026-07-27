# Automated Patch Audio, Video, and YouTube Pipeline Design

Date: 2026-07-26

## Goal

Add configurable automation for this end-to-end flow:

```text
patch audio -> overlay thumbnail -> patch video -> YouTube upload
            -> set thumbnail -> add to playlist
```

Each patch produces one independently retryable video. System defaults apply to
all new work, while each book can override the settings needed for its own media,
rendering, and YouTube destination.

The existing manual full-book video flow remains available. Enabling the new
automation replaces the existing automatic full-book render with per-patch video
automation for that book.

## Existing Context

The application already has:

- Patch TTS and final audio generation with SQLite-backed recovery.
- Book-level video jobs and FFmpeg rendering from an image or one looping video.
- Batch overlay-image generation for patches.
- Image/video background and music libraries.
- YouTube OAuth, upload history, and an upload worker.
- Book video settings for resolution, FPS, animation, music, and overlays.

The new design reuses those paths. It does not introduce a second queue engine or
an external message broker.

## Decisions

- Output unit: one video per patch.
- Thumbnail source: the existing generated patch overlay image.
- Multiple backgrounds: ordered playback with a configured duration per source;
  loop the complete list until narration ends.
- Webcam: an independently looping picture-in-picture media list.
- Playlist: select an existing playlist or create one automatically per book.
- FFmpeg configuration: validated fields and presets only; no arbitrary arguments.
- Settings scope: system defaults with per-book overrides.

## Pipeline

### Stages

Each patch progresses independently through:

```text
audio_pending
audio_processing
audio_ready
thumbnail_generating
thumbnail_ready
video_rendering
video_ready
youtube_queued
youtube_uploading
youtube_uploaded
thumbnail_setting
playlist_adding
published
```

Failure and waiting states are attached to the failed stage rather than replacing
all progress. Examples are `waiting_for_audio`, `waiting_for_media`,
`auth_required`, and `retry_wait`.

### Scheduling

1. Patch audio generation continues through the existing TTS worker.
2. Overlay images are generated in batch as soon as patches and usable background
   media exist. Missing audio does not block thumbnail generation.
3. An `audio_ready` patch with a thumbnail can be claimed for rendering.
4. Rendering creates one MP4 and one `videos` record for the patch.
5. If auto-upload is enabled, the existing upload worker claims the associated
   `youtube_uploads` row.
6. After upload, the worker sets the overlay thumbnail and adds the video to the
   resolved playlist.
7. The patch reaches `published` only when all enabled YouTube post-processing
   stages complete.

Jobs are ordered by book and `patch_index`, but a failed patch does not prevent
later patches from progressing. Concurrency remains bounded by existing worker
patterns and is configurable only if measurements show the current sequential
render/upload behavior is insufficient.

### Idempotency and Recovery

- A stage checks its persisted output before doing work.
- Retry resumes at the first incomplete stage.
- Existing valid audio, thumbnail, MP4, YouTube video ID, and playlist mapping are
  reused.
- A post-upload thumbnail or playlist failure never uploads the MP4 again.
- A pipeline row stores a resolved settings snapshot at enqueue time. Later
  setting changes apply only to newly enqueued or explicitly restarted work.
- Resetting a stage requires an explicit action and clears only that stage and its
  downstream outputs.

## Settings

### Resolution Order

Effective settings are computed in this order:

1. Validated built-in defaults.
2. Saved system defaults.
3. Per-book overrides.

Missing per-book values inherit the system value. Values are validated by one
shared schema before saving and again before enqueueing. The complete resolved
configuration is saved on the pipeline row.

### Automation

- Enable audio-to-video automation.
- Enable YouTube auto-upload.
- Generate missing overlay thumbnails early.
- Continue later patches after one patch fails.

### Video and FFmpeg

- Resolution: `1280x720`, `1920x1080`, `2560x1440`, or `3840x2160`.
- FPS: `24`, `25`, `30`, `50`, or `60`.
- Encoder: `libx264` or `h264_nvenc`.
- Quality: CRF for `libx264`; CQ for `h264_nvenc`.
- Preset: an allow-listed value appropriate to the selected encoder.
- Audio bitrate: `128k`, `192k`, `256k`, or `320k`.
- Pixel format: `yuv420p` initially.
- Background source duration: 3 through 300 seconds.
- Optional music and volume, using the existing music library.

Default preset:

```text
1920x1080, 30 FPS, libx264, CRF 23, medium, AAC 192k, yuv420p
```

The backend inspects FFmpeg encoders before accepting NVENC. Unsupported hardware
encoding is reported as a validation error rather than silently switching codecs.
Raw FFmpeg command fragments are never accepted from the UI or API.

### Webcam PiP

- Enabled or disabled.
- Ordered webcam media list.
- Position: top-left, top-right, bottom-left, or bottom-right.
- Width as a validated percentage of output width.
- Margin in pixels.
- Border width and color.
- Corner radius where supported by the generated filter graph.

Defaults are bottom-right, 25% width, and 24px margin.

### YouTube

- Privacy: private, unlisted, or public.
- Category ID.
- Default tags.
- Title and description templates.
- Made for kids.
- Notify subscribers.
- Default audio/video language.
- License: YouTube or Creative Commons.
- Embeddable and public statistics flags.
- Playlist mode: none, existing, or auto-create per book.
- Existing playlist ID.
- Auto-created playlist title and description templates.
- Auto-created playlist privacy.

Supported template values are explicitly allow-listed:

```text
{book_title}
{patch_name}
{patch_index}
{chapter_start}
{chapter_end}
```

Unknown template fields fail validation before enqueueing.

## Media Model and Rendering

### Media Library

`media_assets` represents reusable image and video files. Each asset has:

- Path, original name, MIME/type, and role eligibility.
- Kind: image or video.
- Health: ready, invalid, or missing.
- Probed duration, dimensions, and FPS where applicable.

Assets can be selected for the `background` or `webcam` role. A separate ordered
selection table associates assets with a book and role; source files are not
copied for each book.

### Background Timeline

- Images hold for `background_duration_seconds`.
- Video assets are trimmed or looped to the same slot duration.
- Sources play in saved order.
- The entire ordered sequence repeats until narration ends.
- With no selected valid background, the patch overlay image is the fallback.
- Invalid sources are skipped if another valid source remains; otherwise the patch
  enters `waiting_for_media`.

### Webcam Timeline

The webcam list is normalized and looped independently until narration ends. Its
original audio is discarded. It is scaled, cropped, optionally rounded/bordered,
and overlaid on the selected corner after the background timeline is composed.

### FFmpeg Graph

One shared renderer builds commands for manual and automated rendering:

1. Probe selected media.
2. Build each background slot.
3. Normalize width, height, FPS, sample aspect ratio, and pixel format.
4. Concatenate slots and loop the sequence to narration duration.
5. Build and loop the webcam sequence if enabled.
6. Overlay webcam PiP.
7. Mix narration with optional background music.
8. Map only composed video and mixed narration/music audio.
9. Stop exactly at narration duration.

Background and webcam audio tracks are always dropped.

## YouTube Integration

### OAuth

The OAuth scope set must support:

- Video upload and metadata.
- Reading and creating playlists.
- Adding playlist items.
- Setting custom thumbnails.

Existing users reconnect once after deployment to grant the expanded scopes.
An insufficient token moves work to `auth_required` without losing render or
upload progress.

### Upload and Post-processing

One queued upload uses one `youtube_uploads` row from pending through completion.
The current path that creates a pending row and then creates another upload row is
removed.

After `videos.insert` succeeds:

1. Persist the YouTube video ID immediately.
2. Call `thumbnails.set` with the patch overlay PNG.
3. Resolve the playlist.
4. Call `playlistItems.insert` if the video is not already present.
5. Persist each step independently.

### Playlist Resolution

- Existing mode uses the selected playlist ID after verifying access.
- Auto-create mode first reads `youtube_playlist_map` for the book.
- If no mapping exists, search the channel for an exact previously created
  playlist marker before creating a playlist.
- Persist the mapping before adding items.
- Repeated retries reuse the same playlist and do not add duplicate items.

## Data Changes

### `automation_settings`

Single-row system defaults with schema version, JSON configuration, and timestamps.

### `book`

Add nullable `automation_config` JSON containing only per-book overrides.

### `patch_pipeline`

One row per patch:

- Current stage and stage statuses.
- Attempt count, last error, and `next_retry_at`.
- Thumbnail and video paths.
- `videos.id` and `youtube_uploads.id` references.
- Resolved settings snapshot and schema version.
- Created and updated timestamps.

The patch ID is unique so enqueue is idempotent.

### `media_assets` and `book_media_selection`

Store reusable media metadata and ordered book selections by role.

### `youtube_playlist_map`

Store book, channel, playlist ID, mode, and timestamps. Book and channel are unique
together.

### `youtube_uploads`

Add upload progress plus independent thumbnail and playlist statuses, errors,
playlist ID, metadata snapshot, and retry timing. Existing rows remain readable.

## UI

### System Settings

A settings area exposes:

- Automation defaults.
- FFmpeg/video preset and validation status.
- Background and webcam defaults.
- Detailed YouTube defaults.

### YouTube Page

Extend `/youtube` with:

- OAuth scope/connection status.
- Detailed upload defaults.
- Playlist synchronization and searchable selection.
- Upload history showing upload, thumbnail, and playlist stages separately.
- Retry controls for failed stages.

### Book Page

The book-level automation section exposes the common overrides:

- Enable automation and auto-upload.
- Ordered background and webcam selectors with reorder/remove controls.
- FFmpeg preset and source duration.
- YouTube privacy, title/description templates, and playlist mode/selection.
- Pipeline progress and stage-specific retry per patch.

Advanced global fields stay in settings to avoid overloading the book page.

## Error Handling

- Missing audio waits without consuming retries.
- Missing media falls back to the overlay; without either, it waits for media.
- Probe failures mark the asset invalid and preserve the error.
- FFmpeg failures store the exit code and bounded stderr tail. Diagnostic command
  data must not expose secrets.
- OAuth scope failures enter `auth_required`.
- Quota and transient API failures use bounded exponential backoff and
  `next_retry_at`.
- Permanent YouTube validation failures wait for user correction.
- User retry starts at the failed stage and never repeats a completed upload.

## Testing

### Unit

- Merge and validate system defaults and book overrides.
- Validate encoder-specific presets and quality fields.
- Reject raw/unknown FFmpeg and template fields.
- Build filter graphs for one image, one video, mixed ordered backgrounds, and
  looping webcam PiP.
- Verify stage transitions, retry scheduling, and idempotent enqueue.
- Mock YouTube upload, thumbnail, playlist selection/creation, and item insertion.
- Verify post-processing retry does not re-upload the video.

### Integration

- Run an `audio_ready` patch through thumbnail, short fixture render, fake YouTube
  upload, thumbnail, and playlist to `published`.
- Restart the worker between stages and verify recovery.
- Verify two patches enter one playlist in `patch_index` order.
- Verify invalid media fallback and `waiting_for_media` behavior.

### Regression and Manual

- Existing manual patch/full-book and standalone renders continue to work.
- Existing manual YouTube upload continues to work.
- Browser test system defaults, book overrides, media ordering, progress, reconnect,
  playlist synchronization, and stage-specific retry.

## Delivery Sequence

The implementation should be staged behind automation settings:

1. Settings schema, persistence, and media selections.
2. Multi-source FFmpeg renderer and webcam PiP.
3. Patch pipeline scheduling and early batch thumbnails.
4. Correct single-record YouTube queue processing.
5. Expanded OAuth, thumbnails, and playlists.
6. Settings, book progress, and retry UI.

Each stage keeps existing manual flows operational.

## Non-goals

- AI image generation; the existing overlay is the thumbnail.
- Arbitrary user-provided FFmpeg arguments.
- Live broadcasting through the YouTube Live API; webcam is a looped PiP effect in
  a normal uploaded video.
- Distributed workers or an external queue service.
- Automatically deleting source media or completed outputs.

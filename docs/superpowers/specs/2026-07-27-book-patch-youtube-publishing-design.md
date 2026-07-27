# Book Patch YouTube Publishing Design

Date: 2026-07-27

## Goal

Treat each patch as one independently publishable audiobook episode and add
book-level YouTube configuration with durable per-patch metadata overrides.
When a patch's final audio becomes ready, optional automation generates its
thumbnail and video, uploads the video, sets the matching thumbnail, and adds
the video to the book's playlist.

```text
patch = batch = episode

final patch audio -> thumbnail -> video -> YouTube upload
                                      -> set thumbnail -> add to playlist
```

The book detail page no longer presents a separate final audio output for the
whole book. Final audio belongs to each patch and remains available in that
patch's row.

## Existing Context

The application already has the main building blocks:

- Per-patch final audio, video generation, and overlay images.
- `patch_pipeline` stage persistence and independent retry state.
- YouTube upload records with metadata snapshots.
- Custom thumbnail upload and playlist post-processing.
- Existing-playlist and auto-create-per-book playlist modes.
- Book backgrounds, per-patch background overrides, and text overlays.

This design extends those paths. It does not add a separate batch model, a new
queue engine, or browser-managed upload orchestration.

## Decisions

- One patch is one batch and one YouTube episode.
- Episode number is always `patch_index + 1`.
- Final audio exists only at patch level in the book detail UI.
- A book may select an existing playlist or auto-create one playlist.
- Book genre tags are used both in the displayed title suffix and as YouTube
  tags.
- Book title and description formats are customizable templates.
- Per-patch metadata overrides are stored durably and reused by later uploads
  and retries.
- Manual `Save & Upload` generates missing thumbnail and video before upload.
- Auto-upload starts when final patch audio is ready, including manually
  uploaded audio.
- Changes to background or overlay after publication apply only to a later,
  explicitly requested run; they do not automatically alter YouTube.

## Approaches Considered

### Extend the persisted patch pipeline

This is the selected approach. It reuses existing stages and recovery data,
works after browser closure or process restart, and prevents completed upload
steps from being repeated.

### Orchestrate calls from the browser

This would initially require less worker integration, but closing the page or
losing the connection could interrupt the chain. Auto-upload and manual upload
would also duplicate orchestration logic.

### Introduce a separate batch model

This would only be useful if one batch could contain multiple patches. Since
the accepted model is patch equals batch, another entity would duplicate state
and complicate media ownership.

## Metadata Configuration

### Book-level YouTube configuration

The book detail page exposes:

- Enable or disable auto-upload when patch audio becomes ready.
- Privacy: private, unlisted, or public.
- Genre tags.
- Title template.
- Description template.
- Playlist mode: existing or auto-create.
- Existing playlist ID when existing mode is selected.
- Playlist title and description templates when auto-create mode is selected.
- A metadata preview rendered using a selected patch.
- YouTube connection state and an action to refresh available playlists.

### Supported template values

```text
{book_title}
{episode_number}
{chapter_start}
{chapter_end}
{patch_name}
{genre_tags}
```

Unknown fields fail validation before configuration is saved.

The default title template is conceptually:

```text
{book_title} - Tap {episode_number} - Chuong {chapter_start}-{chapter_end}: {patch_name} | {genre_tags}
```

The rendered Vietnamese UI output uses `Tập` and `Chương`. For example:

```text
Nhà Trọ Dị Giới - Tập 1 - Chương 1-8: Mưa | kinh dị, huyền huyễn và trinh thám
```

Optional segments are normalized after rendering:

- With no patch name, omit `: {patch_name}` rather than leaving an empty colon.
- With no genre tags, omit ` | {genre_tags}` rather than leaving an empty pipe.

Genre tags are entered once at book level, split into individual YouTube tags,
trimmed, deduplicated while retaining order, and also joined for the title.

### Per-patch overrides

A patch may durably override:

- Title.
- Description.
- Genre/YouTube tags.
- Privacy.
- Playlist destination.

Each unset field inherits the current effective book value. The UI offers an
explicit `Use book default` action for each overridden field. Resetting an
override returns that field to dynamic book-level rendering.

### Resolution and snapshots

Effective values are resolved in this order:

```text
built-in defaults
        -> system YouTube defaults
        -> book YouTube configuration
        -> durable patch overrides
        -> immutable upload snapshot
```

A newly enqueued upload saves a complete metadata and media snapshot. Changes
made to the book or patch after enqueueing do not mutate an active upload or its
retry behavior. Explicitly restarting the whole publishing pipeline creates a
new snapshot from current values.

## Playlist Resolution

### Existing playlist

The selected playlist must be accessible by the connected YouTube channel. The
playlist ID is saved in the effective metadata snapshot.

### Auto-create playlist

The application resolves the existing book-and-channel mapping before creating
a playlist. A playlist is created only when no valid mapping exists. The mapping
is persisted and reused by all patches and retries for that book and channel.

Adding a video is idempotent: the application checks whether the playlist
already contains the YouTube video before inserting it.

## Publishing Pipeline

### Trigger

When a patch obtains final audio from either TTS completion or manual audio
upload:

- If book auto-upload is enabled, enqueue or resume that patch's publishing
  pipeline.
- If auto-upload is disabled, retain the audio and do not enqueue YouTube work.
- Missing audio remains `waiting_for_audio` and does not consume a retry.

### Stages

```text
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

Each patch progresses independently. One failed patch does not block later
patches.

### Thumbnail ownership

- The thumbnail is the existing patch overlay image.
- It is generated from the book cover/background or a patch background override
  plus that patch's text overlay.
- Batch thumbnail generation stores and addresses output by `patch_id`.
- The pipeline and upload snapshot store the exact thumbnail path for that patch.
- Thumbnail association never depends on list position, patch number, or filename
  sorting.

This guarantees that a batch operation uploads each thumbnail with its matching
patch video even after patch deletion or reindexing.

### Idempotency and retry

- Only one active pipeline may exist for a patch.
- A stage checks persisted output before doing work.
- Existing valid final audio, thumbnail, video, and YouTube video ID are reused.
- A thumbnail-setting failure retries only thumbnail setting.
- A playlist failure retries only playlist addition.
- A completed YouTube upload is never repeated by ordinary retry.
- Repeated `Save & Upload` resumes the existing pipeline.
- Creating another YouTube video requires an explicit `Upload again as new
  video` action.

## Book Detail UI

### Book YouTube configuration

A `YouTube` item in book settings opens a focused configuration modal or section
containing:

- Auto-upload toggle.
- Privacy.
- Genre tags.
- Title and description templates.
- Existing/auto-create playlist controls.
- Connection state and playlist refresh.
- Patch selector for live metadata preview.
- Previewed title, description, tags, privacy, and playlist destination.

Configuration cannot enable auto-upload until YouTube is connected and the
playlist settings are valid.

### Patch table

The patch table is reduced to the main workflow columns:

```text
Select | Episode/Patch | Chapters | Audio | Thumbnail | Video/YouTube | Status | Actions
```

The Video/YouTube area shows the persisted stage, including:

- Waiting for audio.
- Generating thumbnail.
- Rendering video.
- Uploading.
- Setting thumbnail.
- Adding to playlist.
- Published.
- Failed with retry.

The separate whole-book final audio card is removed. Each patch row keeps its
own final audio player and related actions.

### Patch media and YouTube modal

A unified per-patch modal displays:

- Final audio preview.
- Thumbnail preview.
- Video preview when available.
- Resolved title, description, tags, privacy, and playlist.
- Controls to save or reset durable patch overrides.
- `Save`.
- `Save & Upload`.
- Stage-specific retry where applicable.
- Explicit `Upload again as new video` after successful publication.

`Save & Upload` persists the current overrides and then resumes the pipeline. It
automatically generates missing thumbnail and video before upload.

### Batch operations

Batch thumbnail generation processes selected patches while retaining strict
`patch_id` ownership. Batch publishing may enqueue selected patches, but each
patch retains its own state, snapshot, output paths, and retry behavior.

## Patch Reindexing

- `episode_number` is always calculated from the current `patch_index + 1`.
- Deleting and reindexing patches changes the generated title of patches without
  title overrides.
- A durable title override remains unchanged after reindexing.
- Resetting the title override regenerates it from the current index.
- Already published YouTube titles and playlist positions are not silently
  changed after local reindexing.
- Media and upload records continue to reference immutable `patch_id` values.

## Validation

- Rendered title is non-empty and within YouTube's title limit.
- Description is at most 5,000 characters.
- Genre tags are trimmed, empty values are removed, and duplicates are removed.
- Only allow-listed template fields are accepted.
- Existing playlist IDs must be accessible by the connected channel.
- Auto-create playlist configuration must produce a non-empty valid title.
- Privacy is one of private, unlisted, or public.
- Auto-upload requires a working YouTube connection and valid playlist mode.
- Final audio, thumbnail, and video files are verified before their stages are
  considered complete.

## Error Handling

- Missing audio waits without consuming retries.
- Missing book or patch background falls back to the existing default background
  behavior.
- Thumbnail generation failure blocks upload because each episode must publish
  with its matching thumbnail.
- OAuth expiration or insufficient scope enters `auth_required`; reconnecting
  allows retry without losing media or upload state.
- Quota and transient API failures preserve the current stage and use bounded
  retry behavior.
- Permanent validation errors wait for user correction.
- Errors identify the failed stage in the patch table and modal.
- `Retry failed` can operate on selected patches without resetting completed
  stages.

## Testing

### Metadata

- Render the default title with book title, episode number, chapter range, patch
  name, and genre tags.
- Omit optional punctuation when patch name or tags are empty.
- Split, trim, and deduplicate genre tags while retaining order.
- Reject unknown template fields and over-limit output.
- Verify patch overrides beat book values and reset restores inheritance.
- Verify upload retries retain the original immutable snapshot.

### Patch lifecycle

- Verify TTS completion and manual audio upload both trigger automation when
  enabled.
- Verify disabled automation does not enqueue YouTube work.
- Verify manual `Save & Upload` creates missing thumbnail and video.
- Verify a failure resumes at the first incomplete stage.
- Verify retry after upload does not upload the video again.
- Verify an explicit new-upload action creates a separate YouTube video.

### Thumbnail and playlist

- Verify patch A's thumbnail cannot be attached to patch B's video during batch
  generation or upload.
- Verify patch deletion and reindexing do not change `patch_id` media ownership.
- Verify auto-create makes one playlist per book and connected channel.
- Verify retries reuse the playlist mapping and do not add duplicate items.
- Verify existing playlist mode rejects an inaccessible playlist.

### UI regression

- Verify patch audio remains available and the whole-book final audio card is
  absent.
- Verify metadata preview matches the upload snapshot.
- Verify patch modal save, reset, upload, retry, and published states.
- Verify stage polling does not overwrite active local task progress.

## Scope

Included:

- Patch-level final audio presentation.
- Book YouTube metadata and playlist configuration.
- Durable per-patch overrides.
- Automatic and manual persisted publishing pipelines.
- Batch-safe thumbnail ownership.
- Stage-specific retry and duplicate prevention.

Not included:

- A separate batch entity.
- Grouping multiple patches into one episode.
- Automatic replacement of an already published YouTube video after media edits.
- Automatic mutation of published metadata after patch reindexing.
- Browser-only queue orchestration.

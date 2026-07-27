# Shared Video Configuration

## Goal

Make `Cấu hình video chung` the single UI entry point for book-wide video rendering options, while preserving the existing persistence mechanism for reference voice and music.

## Configuration Ownership

Move these controls from `Studio Setup` to `Cấu hình video chung`:

- Reference voice file and transcript.
- Background music selection and music volume.
- Audio mix preview controls remain available next to the moved music settings.

The existing endpoints and storage remain authoritative:

- `/books/{book_id}/voice-select` persists `book.voice_clip_path` and `book.voice_transcript`.
- `/books/{book_id}/music` persists `book.music_id` and `book.music_volume`.
- `/books/{book_id}/video-settings` persists existing resolution, FPS, and default image animation fields.

`Studio Setup` retains visual preview, book background selection, and overlay configuration. It no longer renders reference voice or music forms.

## New Video Configuration

New video-only options are stored in `book.automation_config` under `youtube`-independent `video` data:

```json
{
  "video": {
    "backgrounds": ["path-a.jpg", "path-b.mp4"],
    "background_mode": "sequential",
    "image_duration_seconds": 15,
    "intro_voice": "intro.wav",
    "outro_voice": "outro.wav",
    "codec": "libx264",
    "audio_bitrate": "320k",
    "quality": 23,
    "concurrency": 3,
    "crossfade_enabled": true,
    "crossfade_seconds": 1,
    "ken_burns_enabled": true,
    "progress_bar_enabled": true
  }
}
```

Existing `book.video_resolution`, `book.video_fps`, and `book.default_image_animation` remain persisted through their current columns and endpoints. The effective render snapshot combines those columns with `automation_config.video`.

Defaults for new configuration:

- New books/configurations: `1280x720`, `30`, `libx264`, `320k`, quality `23`, concurrency `3`.
- Existing books retain their stored resolution/FPS values; missing new fields receive the defaults above.
- `background_mode`: `sequential`.
- `image_duration_seconds`: `15`.
- `crossfade_enabled`, `ken_burns_enabled`, `progress_bar_enabled`: disabled by default unless explicitly enabled.

FFmpeg rules:

- `libx264` uses `-crf`; `h264_nvenc` uses `-cq`.
- Output pixel format is always `yuv420p`.
- Audio output is AAC with the selected bitrate.
- `-tune stillimage` applies only to libx264 still-image segments.
- Video backgrounds ignore still-image animation.

## Background Pipeline

- A patch-specific background override replaces the shared list.
- Otherwise, valid shared backgrounds are used; if none exist, the book background and then the default background are fallback candidates.
- `sequential` rotates through the list during each patch.
- `random` uses a stable seed derived from book and patch identity so retries produce the same order.
- Still images remain visible for `image_duration_seconds`; video backgrounds play through their available duration before advancing.
- The list loops until the narration segment ends.
- Background video audio is discarded.
- Missing shared background entries are skipped. If no valid background remains, rendering fails with a clear error.

## Intro And Outro

- Intro and outro are selected from the existing Voices library and stored as voice filenames/paths in `automation_config.video`.
- They are inserted into every independently rendered patch as `intro -> main -> outro`.
- Intro/outro use the first selected background and do not receive background music.
- Music is mixed only into the main narration segment.
- Missing intro/outro files are skipped with a warning; they do not fail the main video.

## Enhancements

- Optional crossfade between background segments, limited to `0-3` seconds, default `1` when enabled.
- Optional Ken Burns motion for still images, with stable alternating directions. It never applies to video backgrounds.
- Optional thin progress bar at the bottom of the main narration content. It is not shown for intro/outro.
- If there is only one background, crossfade is naturally skipped while Ken Burns can still apply.

## Snapshot And Compatibility

At render start, the effective configuration and resolved media paths are snapshotted for the job. Retries use that snapshot where the existing job/pipeline model supports it, preventing mid-render settings changes from changing output unexpectedly.

Legacy books continue using their current single-background, music, resolution, FPS, and animation behavior when no new video configuration is saved.

## Validation And Errors

- Background paths must be known library paths or valid existing book/patch paths.
- Voice paths must refer to files in the Voices library.
- Resolution, codec, bitrate, quality, concurrency, durations, and crossfade values are validated server-side.
- Invalid configuration returns a user-visible error and does not partially report success.
- Render logs include effective codec, quality, background count, music, intro/outro, and enhancement settings.

## Testing

- UI tests verify controls move from Studio Setup to video configuration and preserve existing form endpoints.
- Persistence tests verify voice/music remain stored in existing book columns and video options serialize under `automation_config.video`.
- Renderer tests cover legacy fallback, sequential/random stable backgrounds, still-image duration, video background audio removal, intro/outro, music-only-main behavior, codec quality flags, and enhancement toggles.
- Route tests cover validation errors and multi-patch concurrency behavior.

# Video Creator: Background Music, Text Overlay, Step Progress — Design

Date: 2026-07-13
Status: Approved

## Context

The Video Creator page (`/video`, [app/routes/video.py](../../../app/routes/video.py), [app/templates/video_creator.html](../../../app/templates/video_creator.html)) supports batch audio upload + per-row background image + shared ffmpeg config, processed sequentially and synchronously with no progress feedback in the UI (only server logs via `_make_progress_logger`).

Recent commits (8b61c46, 3034a1e) added to the system, but NOT to Video Creator:

- **Background music mixing** — `video_gen.generate_segment()` accepts `music_path` / `music_volume` (looped via `-stream_loop -1`, mixed with `amix`); a music library exists at `/music` with `GET /music/list` API and `repository.get_music()`.
- **Text overlay rendering** — `image_overlay.render_overlay(image, lines, cfg)` renders text onto an in-memory PIL image, decoupled from Book/Patch.

`generate_standalone_video()` (the entry point Video Creator uses) does not forward music params and there is no overlay hookup.

## Feature 1: Background music

**UI (Step 3 "Video Config", applies to all selected files):**
- Dropdown "Nhạc nền" populated from `GET /music/list` (`-- Không dùng --` default, entries show name + duration).
- Volume slider 0–100%, default 15%.

**Backend:**
- `generate_standalone_video()` gains `music_path: str | None = None`, `music_volume: float = 0.15`, forwarded to `generate_segment()`.
- `generate-batch` (and legacy `generate`) accept `music_id` + `music_volume` in config; route resolves `music_id → file path` via `repository.get_music()` using the request's DB connection. Missing/invalid id → no music (log a progress event, don't fail).

## Feature 2: Custom text overlay

**UI (Step 3, applies to all selected files):**
- Text input "Text overlay" (empty = no overlay).
- Position select (top/center/bottom), font size (number, default 52), text color (color picker, default #FFFFFF).

**Backend:**
- In the batch loop, when overlay text is non-empty: open the resolved background image with PIL, build a cfg dict from `image_overlay.get_default_overlay_config()` with the user's position/font_size/text_color, wrap lines with existing helpers, call `render_overlay()`, save PNG to `_TMP_DIR/<batch>_<idx>_overlay.png`, and use that as the image input. Overlay render failure → fall back to the original image and record a progress step (don't fail the video).

## Feature 3: Step progress for debugging

**Store:** module-level `_progress_store: dict[str, dict]` in `app/routes/video.py`, keyed by `"{batch_id}:{idx}"` (and `job_id` for single mode). Value: `{"status": "running|done|error", "steps": [{"t": iso_ts, "event": str, "detail": str}], "updated_at": epoch}`. The existing progress callback is extended to append into the store as well as logging. Route-level events added: `music.resolved`, `overlay.rendered`, `move.done`, etc.

**Endpoint:** `GET /video/progress/{batch_id}` → `{"jobs": {"0": {...}, "1": {...}}}` for all indices in the batch.

**Cleanup:** entries older than 1h purged inside `_cleanup_old_tmp_files()`.

**Frontend:**
- While the `generate-batch` POST is pending, poll `GET /video/progress/{batch_id}` every 1s; render the latest step into each row's Status cell (e.g. "FFmpeg đang chạy…"), stop polling when the POST resolves.
- Each result row (done or failed) gets a "Log" toggle showing the full step list with timestamps — the debug view.

## Non-goals

- No per-row music/overlay overrides (batch-wide only, matching Step 3's existing pattern).
- No redesign of the legacy single-file `/video/generate` form; it just stays compatible with the new `generate_standalone_video` signature (new params have defaults).
- No SSE/WebSocket; polling is sufficient for a local tool.

## Error handling

- Music file missing on disk → skip music, record step, continue.
- Overlay render exception → use original background, record step, continue.
- FFmpeg failure → existing behavior (result status "error"), but the progress log retains all prior steps for debugging.

## Testing

- Unit: `generate_standalone_video` forwards music params (assert via mocked `generate_segment`).
- Manual/browser: upload batch, select music + overlay text, generate, observe live step updates and final Log toggle; verify output video has music and overlay.

# Book Detail — Video Creation Refactor (design)

Date: 2026-07-25
Scope: `app/templates/book_detail.html`, `app/routes/patches.py`, `app/routes/books.py` only.
Leave `patch_builder.html` untouched.

## Goal

Bring per-patch **image (overlay) generation** and **video creation** into the book detail
page with good UX. After all patches render (TTS done), the user can:

- Generate an MP4 per patch (server FFmpeg) from the patch's overlay image + audio.
- Optionally auto-upload each video to YouTube (per-run checkbox + configurable default).
- Upload an externally-made MP4 (Colab/Kaggle) into a patch, then push it to YouTube.
- See a compact overlay-image thumbnail per row with preview + download.

Every per-row action (Run TTS, Gen image, Gen video, Upload MP4, Upload YouTube) runs as an
**independent async task** that updates only its own row and never blocks another.

## UX

Patches table columns:
`☑ | Patch (name + audio) | Chapters | 🖼 Image | Progress | Status | 🎬 Video | Actions`

- **🖼 Image**: ~48px overlay thumbnail → lightbox preview + Download. A tucked-away `⋯`
  reveals per-patch background upload + animation type (existing endpoints).
- **🎬 Video**: no video → "Tạo"; rendering → spinner %; ready → ▶ preview + Download +
  YouTube state; small "Upload MP4" for Colab/Kaggle files.
- **Status**: stacked TTS + Video badges.

Shared "Cấu hình video chung" modal (resolution, fps, default animation, default
YouTube auto-upload + privacy). Music / overlay / background stay in the Studio modal.

Bottom-nav (extends existing bar): `🎬 Tạo video (đã chọn)` + ☑ `Tự động upload YouTube`
(this run) + `🖼 Tạo ảnh overlay (đã chọn)`. Batch video runs sequentially with limited
concurrency (default 2), like Run Selected. Keeps Run Selected / Download zip / Drive /
Copy Description.

Independence: extend the existing `LIGHT_TTS_ACTIVE` set into a generic `ACTIVE_TASKS`
registry so the status poller skips any row with an in-flight local task.

## Backend additions (lean)

1. `POST /books/{id}/video-settings` — persist `video_resolution`, `video_fps`,
   `default_image_animation` via `repository.update_book_video_settings`. Returns JSON.
2. Enhance `POST /books/{id}/patches/{pid}/generate-video`:
   - Support JSON response (`?ajax=1` or `Accept: application/json`); keep the 303 redirect
     default for `patch_builder.html`.
   - Register/refresh the MP4 in the `videos` table (like `upload_patch_video`).
   - Accept `upload_youtube` + `privacy`; when set and `youtube.is_configured()`, enqueue via
     `upload_worker` with the new `video_id`.
3. `POST /books/{id}/patches/{pid}/youtube-upload` — ensure a `videos` row for the patch's
   existing MP4, enqueue YouTube upload. JSON. (Colab/Kaggle → YouTube path.)
4. `GET /books/{id}/patches/{pid}/overlay-image` — `ensure_patch_overlay` then serve the PNG
   (idempotent/cached). Powers thumbnail, preview, download, and batch "generate image".
5. `book_detail` route: pass `patch_video_ids` (existing MP4s) + `youtube_configured` +
   YouTube defaults so the initial render shows video/YouTube state.

Codec (NVENC) and CRF remain server defaults (`settings.use_nvenc`, video_gen default) — not
per-book, to avoid new DB columns.

## Non-goals

TTS/chunk pipeline, Studio internals, Drive/Kaggle export, and the notebook are untouched.
No new DB columns.

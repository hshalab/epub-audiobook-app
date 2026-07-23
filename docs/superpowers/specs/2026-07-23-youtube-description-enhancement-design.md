# YouTube Description Enhancement — Design Spec

## Overview

Three small, coordinated changes to enrich YouTube uploads:

1. **Auto-build description** with music license info + chapter list from patches
2. **Auto-generate tags** from book title + defaults
3. **"Copy Description" button** in book_detail.html and video_creator.html

## Description Format

```
{book title} - EPUB Audiobook

🎵 Background Music: {music name}
{music description}
License: {music license}

📚 Chapters:
Chương X-Y: Patch Name
Chương X-Y: Patch Name
...
```

YouTube description max = 5000 chars. The builder truncates cleanly if exceeded (cut at last complete chapter line before the limit).

## Tags

Auto-generated from book title:
- Split title by space, deduplicate
- Always include defaults: `audiobook, epub, text-to-speech, vietnamese`
- Passed to YouTube API upload call

## Data Flow

### Auto-upload (worker.py)

```
book_job done → build_description(conn, book) → youtube.upload_video(description=..., tags=...)
```

### Manual upload (youtube.py routes)

No change needed — the upload form already accepts `description` and `tags`. User can overwrite. No forced override.

### Copy button

Button calls `/api/books/{id}/youtube-description` → returns JSON `{description, tags}` → copies to clipboard via `navigator.clipboard.writeText`.

## Changes

### New: `repository.py`

```python
def build_youtube_description(conn, book_id) -> dict:
    """Return {description, tags} for YouTube upload."""

def build_youtube_description_from_patches(conn, book_id) -> dict:
    """Same but from patches for the Copy button."""
```

Single function since both auto and copy use the same data.

### New Route: `GET /books/{id}/youtube-description`

Returns `{description: str, tags: list[str]}` for the Copy button.

### Modified: `worker.py` (around line 414)

Replace hardcoded `description` and `tags` with call to `build_youtube_description()`.

### Modified: `book_detail.html`

Add "Copy Description" button in Patches card header (next to Download selected). On click → fetch route → clipboard → toast.

### Modified: `video_creator.html`

Add "Copy Description" button in Video Library tab (per-row or bulk). For standalone videos (no book context), returns filename-based description only.

## Files Changed

| File | Change |
|------|--------|
| `app/repository.py` | Add `build_youtube_description()` |
| `app/routes/books.py` | Add `GET /books/{id}/youtube-description` route |
| `app/worker.py` | Use new builder for auto-upload |
| `app/templates/book_detail.html` | Add "Copy Description" button + JS |
| `app/templates/video_creator.html` | Add "Copy Description" button + JS |
| `app/static/video_creator.js` | Wire up Copy button logic |

## Edge Cases

- **No music**: skip the 🎵 section entirely
- **No patches**: chapter list says "No patches yet"
- **Description too long (>5000 chars)**: truncate at last complete chapter line
- **Not a book (standalone video)**: Copy Description returns filename + empty music section

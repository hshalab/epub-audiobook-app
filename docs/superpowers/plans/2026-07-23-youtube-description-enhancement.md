# YouTube Description Enhancement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-build YouTube description with music license info + chapter list, auto-generate tags, and add a "Copy Description" button in both book detail and video creator pages.

**Architecture:** Add `build_youtube_description()` in repository.py as the single source of truth for description/tag formatting. A new route `/books/{id}/youtube-description` serves JSON for the Copy button. Worker auto-upload calls the same builder. Frontend adds JS to copy to clipboard.

**Tech Stack:** Python, FastAPI, SQLite, plain JS with Clipboard API

## Global Constraints

- YouTube description max 5000 chars — truncate at last complete chapter line if exceeded
- Tags generated from book title (split by space, deduplicate) + defaults: `audiobook, epub, text-to-speech, vietnamese`
- Music section omitted if book has no music_id
- Chapter list comes from patches table (chapter_start → chapter_end per patch)

---

### Task 1: Add `build_youtube_description()` to repository.py

**Files:**
- Modify: `app/repository.py:1769` (append after last line)

**Interfaces:**
- Produces: `build_youtube_description(conn, book_id) -> dict` with keys `description: str`, `tags: list[str]`

- [ ] **Step 1: Add the function at end of repository.py**

```python
def build_youtube_description(
    conn: sqlite3.Connection, book_id: int
) -> dict:
    """Return {description, tags} for YouTube upload.

    Description includes music license info (if book has music_id) and
    chapter list derived from patches.
    Tags are derived from book title + defaults.
    """
    book = get_book(conn, book_id)
    if book is None:
        return {"description": "", "tags": []}

    parts: list[str] = [f"{book.title} - EPUB Audiobook"]

    # Music section
    music = None
    if book.music_id is not None:
        music = get_music(conn, book.music_id)
    if music and (music.description or music.license):
        parts.append("")
        parts.append(f"🎵 Background Music: {music.name}")
        if music.description:
            parts.append(music.description)
        if music.license:
            parts.append(f"License: {music.license}")

    # Chapter list from patches
    patches = list_patches(conn, book_id)
    if patches:
        parts.append("")
        parts.append("📚 Chapters:")
        for p in patches:
            label = p.name or f"Patch {p.patch_index}"
            parts.append(f"Chương {p.chapter_start}-{p.chapter_end}: {label}")

    desc = "\n".join(parts)

    # Truncate to 5000 chars at last complete chapter line
    if len(desc) > 5000:
        cut = desc.rfind("\n", 0, 5000)
        desc = desc[:cut] if cut > 0 else desc[:5000]

    # Tags from title + defaults
    words = set(book.title.lower().split())
    defaults = {"audiobook", "epub", "text-to-speech", "vietnamese"}
    tags = list(words | defaults)

    return {"description": desc, "tags": tags}
```

- [ ] **Step 2: Commit**

```bash
git add app/repository.py
git commit -m "feat: add build_youtube_description() for enriched YouTube uploads"
```

---

### Task 2: Add `GET /books/{id}/youtube-description` route

**Files:**
- Modify: `app/routes/books.py` (add before the `_list_backgrounds` helper at line 910)

**Interfaces:**
- Produces: `GET /books/{id}/youtube-description` returning `{description: str, tags: list[str]}`
- Consumes: `build_youtube_description(conn, book_id)` from Task 1

- [ ] **Step 1: Add the route**

```python
@router.get("/books/{book_id}/youtube-description")
def get_youtube_description(request: Request, book_id: int):
    """Return the enriched YouTube description + tags for the Copy button."""
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        result = repository.build_youtube_description(conn, book_id)
    return JSONResponse(result)
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/books.py
git commit -m "feat: add GET /books/{id}/youtube-description route"
```

---

### Task 3: Update worker.py auto-upload to use enriched description

**Files:**
- Modify: `app/worker.py:408-430` (auto-upload block in `_process_book_job`)

**Interfaces:**
- Consumes: `build_youtube_description(conn, book_id)` from Task 1

- [ ] **Step 1: Replace hardcoded description/tags in worker.py**

Replace lines 408-430:

```python
                        if book:
                            yt_info = repository.build_youtube_description(self.conn, job.book_id)
                            tags = yt_info["tags"]
                            with self.db_lock:
                                upload_id = youtube.enqueue_upload(
                                    self.conn,
                                    video_path=output_path,
                                    title=book.title,
                                    description=yt_info["description"],
                                    tags=tags,
                                    privacy_status=settings.youtube_default_privacy,
                                )
                                result = youtube.upload_video(
                                    self.conn,
                                    video_path=output_path,
                                    title=book.title,
                                    description=yt_info["description"],
                                    tags=tags,
                                    privacy_status=settings.youtube_default_privacy,
                                )
```

- [ ] **Step 2: Commit**

```bash
git add app/worker.py
git commit -m "feat: auto-upload uses enriched YouTube description + tags"
```

---

### Task 4: Add "Copy Description" button in book_detail.html

**Files:**
- Modify: `app/templates/book_detail.html`

- [ ] **Step 1: Add Copy button in Patches card header** (after `batch-drive-btn` div, around line 439)

```html
            <button type="button" class="btn-outline btn-sm" id="copy-yt-desc"
                    title="Copy YouTube description (music license + chapter list) to clipboard">
                Copy Description
            </button>
```

- [ ] **Step 2: Add JS at end of the scripts block** (before `{% endblock %}` at line 520)

```javascript
// --- Copy YouTube Description ---
document.getElementById('copy-yt-desc')?.addEventListener('click', async function () {
    const btn = this;
    try {
        const res = await fetch(`/books/${BOOK_ID}/youtube-description`);
        if (!res.ok) throw new Error('Failed to fetch description');
        const data = await res.json();
        await navigator.clipboard.writeText(data.description);
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = orig, 2000);
    } catch (e) {
        showToast(e.message || 'Copy thất bại', 'error');
    }
});
```

Note: `BOOK_ID` is already defined in the existing script at line 598.

- [ ] **Step 3: Commit**

```bash
git add app/templates/book_detail.html
git commit -m "feat: add Copy Description button in book detail page"
```

---

### Task 5: Add "Copy Description" button in video_creator.html

**Files:**
- Modify: `app/templates/video_creator.html`
- Modify: `app/static/video_creator.js`

- [ ] **Step 1: Add Copy button in Video Library header** (after Delete Selected button, around line 151)

```html
            <button type="button" class="btn-outline btn-sm" id="btn-copy-video-desc" disabled>Copy Description</button>
```

- [ ] **Step 2: Add JS handler in video_creator.js** (add inside the video library IIFE, after `updateBulkButtons`)

```javascript
    // Copy Description for selected videos
    window.copyVideoDescription = async function() {
        if (selectedIds.size !== 1) {
            showToast('Select exactly one video to copy its description', 'error');
            return;
        }
        const id = [...selectedIds][0];
        try {
            const res = await fetch(`/video/api/videos/${id}`);
            if (!res.ok) throw new Error('Failed to fetch video');
            const video = await res.json();
            const desc = video.description || `${video.title || video.filename} - EPUB Audiobook`;
            await navigator.clipboard.writeText(desc);
            showToast('Description copied!', 'success');
        } catch (e) {
            showToast(e.message || 'Copy thất bại', 'error');
        }
    };
```

And wire the button:

```javascript
    document.getElementById('btn-copy-video-desc')?.addEventListener('click', window.copyVideoDescription);
```

And enable it when exactly one video is selected:

```javascript
    function updateBulkButtons() {
        document.getElementById('btn-bulk-upload').disabled = selectedIds.size === 0;
        document.getElementById('btn-bulk-delete').disabled = selectedIds.size === 0;
        document.getElementById('btn-copy-video-desc').disabled = selectedIds.size !== 1;
    }
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/video_creator.html app/static/video_creator.js
git commit -m "feat: add Copy Description button in video creator library"
```

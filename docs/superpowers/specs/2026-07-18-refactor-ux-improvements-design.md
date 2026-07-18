# UX Improvements Refactor — Design Spec

Date: 2026-07-18

## Overview

Five independent UX improvements to the EPUB Audiobook app:

1. Server-side pagination on all list pages
2. Multi-file upload for voice/music/photo libraries
3. Auto-save for all forms (debounced)
4. Voice description metadata
5. Voice card view (with table view toggle)

## 1. Pagination

### Scope

All list pages: `/books`, `/voices`, `/music`, `/photos`

### Backend

- Add `page` query param (default=1) to list routes
- Add `per_page` constant (default=20) to `app/config.py`
- Modify repository functions to accept `page` and `per_page`, return `(items, total_count, total_pages)`
- SQL: `LIMIT ? OFFSET ?` with `COUNT(*)` for total

### API Changes

```python
# repository.py
def list_books(conn, page=1, per_page=20) -> tuple[list[Book], int, int]:
    offset = (page - 1) * per_page
    rows = conn.execute("SELECT * FROM book ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset))
    count_row = conn.execute("SELECT COUNT(*) AS c FROM book").fetchone()
    total = count_row["c"]
    return [_book_from_row(r) for r in rows], total, math.ceil(total / per_page)
```

Similar for `list_music`, voices/photos (filesystem-based: slice the sorted list in Python).

### Frontend

- Pagination component: `{{ pagination_html(page, total_pages, base_url) }}`
- Jinja2 macro in `base.html` or partial template
- Shows: Previous | page numbers (with ellipsis for >7 pages) | Next
- Current page highlighted, disabled Previous/Next at boundaries

### Files to modify

- `app/repository.py` — `list_books`, `list_music`
- `app/routes/books.py` — `list_books` route
- `app/routes/voices.py` — `voices_page` route (filesystem slicing)
- `app/routes/music.py` — `music_page` route
- `app/routes/photos.py` — `photos_page` route
- `app/templates/book_list.html` — add pagination controls
- `app/templates/voices.html` — add pagination controls
- `app/templates/music.html` — add pagination controls
- `app/templates/photos.html` — add pagination controls
- `app/templates/base.html` — add pagination macro

## 2. Multi-file Upload

### Scope

Voices, Music, Photos pages — upload multiple files at once.

### Backend

Change upload endpoints to accept `list[UploadFile]`:

```python
@router.post("/voices/upload")
async def upload_voices(files: list[UploadFile] = File(...)):
    results = []
    for file in files:
        # validate, save, record
        results.append({"name": file.filename, "status": "ok"})
    return JSONResponse({"uploaded": results})
```

Music upload: keep size limit per file, process sequentially.

### Frontend

- Add `multiple` attribute to file inputs
- Dropzone: show count of selected files
- After upload: show brief results (X/Y uploaded successfully)
- For music: redirect back to page after upload (existing pattern)

### Files to modify

- `app/routes/voices.py` — `upload_voice` → `upload_voices`
- `app/routes/music.py` — `upload_music` (loop)
- `app/routes/photos.py` — `upload_photo` → `upload_photos`
- `app/templates/voices.html` — `multiple` input, result feedback
- `app/templates/music.html` — `multiple` input
- `app/templates/photos.html` — `multiple` input

## 3. Auto-save

### Scope

All forms in the app that modify settings (not upload forms or delete confirmations).

Target forms:
- Book normalization toggles (`/books/{id}/normalization`)
- Book overlay config (`/books/{id}/overlay-config`)
- Book music settings (`/books/{id}/music`)
- Chapter exclude toggles (individual POST)
- Replace rule create/edit (already POST-based)

### Approach

Create `app/static/autosave.js`:

```javascript
// Attach to forms with data-autosave attribute
// On input/change → debounce 800ms → fetch POST form action
// Show toast: "Đang lưu..." → "Đã lưu ✓"
// For toggle inputs (checkboxes): immediate save (no debounce)
```

Usage in templates:
```html
<form data-autosave action="/books/{{ book.id }}/normalization" method="post">
    <input type="checkbox" name="numbers" {{ 'checked' if book.normalize_numbers_enabled }}>
</form>
```

### Files to create/modify

- `app/static/autosave.js` — new file
- `app/templates/base.html` — include autosave.js
- `app/templates/book_detail.html` — add `data-autosave` to relevant forms
- `app/routes/books.py` — ensure POST endpoints return JSON (not redirect) for auto-save

### Design decision

Auto-save endpoints should return `{"status": "ok"}` JSON instead of redirect when called via fetch. Keep redirect for non-JS fallback. Detect via `X-Requested-With` header or query param `?autosave=1`.

## 4. Voice Description

### Backend

New SQLite table:

```sql
CREATE TABLE IF NOT EXISTS voice_meta (
    filename    TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

New endpoints:
- `POST /voices/{name}/description` — upsert description (JSON body: `{"description": "..."}`)
- `GET /voices` — include description in voice list

Repository additions in `app/repository.py`:
- `get_voice_meta(conn, filename) -> dict | None`
- `set_voice_meta(conn, filename, description) -> None`

On voice rename: `UPDATE voice_meta SET filename = ? WHERE filename = ?`
On voice delete: `DELETE FROM voice_meta WHERE filename = ?`

### Frontend

- Description textarea in voice cards (card view)
- Inline edit with auto-save
- Show description in table view as truncated text column

### Files to modify

- `app/db.py` — add `voice_meta` table to schema, migration
- `app/repository.py` — add `get_voice_meta`, `set_voice_meta`
- `app/routes/voices.py` — add description endpoint, update rename/delete
- `app/templates/voices.html` — description input in cards

## 5. Voice Card View

### Frontend

CSS grid layout for voice cards:

```css
.voice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: var(--space-md);
}
.voice-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--border-radius);
    padding: var(--space-md);
}
```

Toggle button: switches between card and table view. Preference saved to `localStorage.setItem('voices-view', 'card'|'table')`.

Card layout:
- Audio player (full width)
- File name (bold)
- Size info
- Description textarea (editable)
- Rename + Delete buttons (bottom)

### Files to modify

- `app/templates/voices.html` — add card grid, toggle button, both views
- `app/static/style.css` — add `.voice-grid`, `.voice-card` styles

## Implementation Order

1. **Pagination** (foundation, touches all list pages)
2. **Voice card view + description** (self-contained feature)
3. **Multi-file upload** (modifies upload endpoints)
4. **Auto-save** (touches forms across multiple pages)

Each feature is independently deployable.

# UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 5 UX improvements: pagination, multi-file upload, auto-save, voice description, voice card view.

**Architecture:** Server-side pagination via SQL LIMIT/OFFSET. Multi-file upload by accepting list[UploadFile]. Auto-save via vanilla JS debounce + fetch. Voice metadata in new SQLite table. Card view via CSS grid + JS toggle.

**Tech Stack:** FastAPI, SQLite, Jinja2, vanilla JavaScript (no new dependencies).

## Global Constraints

- Python >=3.10,<3.13
- No new pip dependencies
- Follow existing code style: dataclass models, repository pattern, Jinja2 templates
- All forms that auto-save must work without JS (progressive enhancement)
- Pagination: 20 items per page

---

### Task 1: Pagination — Repository Layer

**Files:**
- Modify: `app/repository.py`
- Modify: `app/config.py`

**Interfaces:**
- Produces: `list_books(conn, page, per_page) -> tuple[list[Book], int, int]`, `list_music(conn, page, per_page) -> tuple[list[Music], int, int]`

- [ ] **Step 1: Add `default_page_size` to config**

In `app/config.py`, add to the Settings class:
```python
default_page_size: int = 20
```

- [ ] **Step 2: Modify `list_books` for pagination**

Replace the existing `list_books` function in `app/repository.py`:

```python
def list_books(conn: sqlite3.Connection, page: int = 1, per_page: int = 20) -> tuple[list[Book], int, int]:
    offset = (page - 1) * per_page
    rows = conn.execute("SELECT * FROM book ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    count_row = conn.execute("SELECT COUNT(*) AS c FROM book").fetchone()
    total = count_row["c"]
    total_pages = max(1, math.ceil(total / per_page))
    return [_book_from_row(r) for r in rows], total, total_pages
```

- [ ] **Step 3: Add paginated `list_music` function**

Add to `app/repository.py`:

```python
def list_music_paginated(conn: sqlite3.Connection, page: int = 1, per_page: int = 20) -> tuple[list[Music], int, int]:
    offset = (page - 1) * per_page
    rows = conn.execute("SELECT * FROM music ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    count_row = conn.execute("SELECT COUNT(*) AS c FROM music").fetchone()
    total = count_row["c"]
    total_pages = max(1, math.ceil(total / per_page))
    return [_music_from_row(r) for r in rows], total, total_pages
```

- [ ] **Step 4: Commit**

```bash
git add app/repository.py app/config.py
git commit -m "feat(repo): add pagination to list_books and list_music"
```

---

### Task 2: Pagination — Pagination Macro

**Files:**
- Create: `app/templates/_pagination.html`

**Interfaces:**
- Produces: Jinja2 macro `render_pagination(page, total_pages, base_url)`

- [ ] **Step 1: Create pagination macro template**

Create `app/templates/_pagination.html`:

```html
{% macro render_pagination(page, total_pages, base_url) %}
{% if total_pages > 1 %}
<nav class="pagination" aria-label="Pagination">
    {% if page > 1 %}
    <a href="{{ base_url }}?page={{ page - 1 }}" class="pagination-link">&laquo; Prev</a>
    {% else %}
    <span class="pagination-link disabled">&laquo; Prev</span>
    {% endif %}

    {% set start = [1, page - 2] | max %}
    {% set end = [total_pages, page + 2] | min %}

    {% if start > 1 %}
    <a href="{{ base_url }}?page=1" class="pagination-link">1</a>
    {% if start > 2 %}<span class="pagination-ellipsis">&hellip;</span>{% endif %}
    {% endif %}

    {% for p in range(start, end + 1) %}
    {% if p == page %}
    <span class="pagination-link current">{{ p }}</span>
    {% else %}
    <a href="{{ base_url }}?page={{ p }}" class="pagination-link">{{ p }}</a>
    {% endif %}
    {% endfor %}

    {% if end < total_pages %}
    {% if end < total_pages - 1 %}<span class="pagination-ellipsis">&hellip;</span>{% endif %}
    <a href="{{ base_url }}?page={{ total_pages }}" class="pagination-link">{{ total_pages }}</a>
    {% endif %}

    {% if page < total_pages %}
    <a href="{{ base_url }}?page={{ page + 1 }}" class="pagination-link">Next &raquo;</a>
    {% else %}
    <span class="pagination-link disabled">Next &raquo;</span>
    {% endif %}
</nav>
{% endif %}
{% endmacro %}
```

- [ ] **Step 2: Add pagination CSS to style.css**

Append to `app/static/style.css`:

```css
.pagination {
    display: flex;
    gap: var(--space-xs, 0.25rem);
    align-items: center;
    justify-content: center;
    margin-top: var(--space-lg, 1.5rem);
    padding: var(--space-md, 1rem) 0;
}
.pagination-link {
    display: inline-block;
    padding: 0.4rem 0.75rem;
    border: 1px solid var(--border-color, #ddd);
    border-radius: var(--border-radius, 6px);
    color: var(--text-primary, #333);
    text-decoration: none;
    font-size: var(--font-size-sm, 0.875rem);
    transition: background 0.15s;
}
.pagination-link:hover:not(.disabled):not(.current) {
    background: var(--bg-hover, #f0f0f0);
}
.pagination-link.current {
    background: var(--color-primary, #4f46e5);
    color: #fff;
    border-color: var(--color-primary, #4f46e5);
}
.pagination-link.disabled {
    opacity: 0.4;
    cursor: not-allowed;
}
.pagination-ellipsis {
    padding: 0 0.25rem;
    color: var(--text-muted, #999);
}
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/_pagination.html app/static/style.css
git commit -m "feat(ui): add reusable pagination macro and styles"
```

---

### Task 3: Pagination — Book List Page

**Files:**
- Modify: `app/routes/books.py`
- Modify: `app/templates/book_list.html`

**Interfaces:**
- Consumes: `list_books(conn, page, per_page)` from Task 1
- Consumes: `render_pagination(page, total_pages, base_url)` from Task 2

- [ ] **Step 1: Update `list_books` route for pagination**

In `app/routes/books.py`, replace the `list_books` function:

```python
@router.get("/books", response_class=HTMLResponse)
def list_books(request: Request, page: int = Query(default=1, ge=1)):
    per_page = settings.default_page_size
    with locked_conn(request) as conn:
        books, total, total_pages = repository.list_books(conn, page=page, per_page=per_page)
        patch_counts = {
            b.id: {
                "total": len(repository.list_patches(conn, b.id)),
                "done": sum(1 for p in repository.list_patches(conn, b.id) if p.status == "done"),
                "pending": sum(1 for p in repository.list_patches(conn, b.id) if p.status == "pending"),
            }
            for b in books
        }
    return templates.TemplateResponse(
        request, "book_list.html", {
            "books": books,
            "patch_counts": patch_counts,
            "page": page,
            "total_pages": total_pages,
        }
    )
```

- [ ] **Step 2: Update book_list.html template**

In `app/templates/book_list.html`, add pagination import and controls. Add after the closing `</table>` tag, before the `{% endblock %}`:

```html
{% from "_pagination.html" import render_pagination %}
```

Add at the top of the file (after the extends block). Then after the `</div>` that closes `table-wrap`, add:

```html
{{ render_pagination(page, total_pages, "/books") }}
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/books.py app/templates/book_list.html
git commit -m "feat(books): add server-side pagination to book list"
```

---

### Task 4: Pagination — Music Page

**Files:**
- Modify: `app/routes/music.py`
- Modify: `app/templates/music.html`

- [ ] **Step 1: Update `music_page` route**

In `app/routes/music.py`, replace the `music_page` function:

```python
@router.get("/music", response_class=HTMLResponse)
def music_page(request: Request, page: int = Query(default=1, ge=1)):
    per_page = settings.default_page_size
    with locked_conn(request) as conn:
        music_list, total, total_pages = repository.list_music_paginated(conn, page=page, per_page=per_page)
    return templates.TemplateResponse(request, "music.html", {
        "request": request,
        "music_list": music_list,
        "settings": settings,
        "page": page,
        "total_pages": total_pages,
    })
```

Add `Query` to imports at top of file:
```python
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
```

- [ ] **Step 2: Update music.html template**

In `app/templates/music.html`, add at top:
```html
{% from "_pagination.html" import render_pagination %}
```

After the `{% endif %}` that closes the music list block, add:
```html
{{ render_pagination(page, total_pages, "/music") }}
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/music.py app/templates/music.html
git commit -m "feat(music): add pagination to music library"
```

---

### Task 5: Pagination — Voices & Photos Pages (filesystem-based)

**Files:**
- Modify: `app/routes/voices.py`
- Modify: `app/routes/photos.py`
- Modify: `app/templates/voices.html`
- Modify: `app/templates/photos.html`

- [ ] **Step 1: Update `voices_page` route with filesystem pagination**

In `app/routes/voices.py`, replace the `voices_page` function:

```python
@router.get("/voices", response_class=HTMLResponse)
def voices_page(request: Request, page: int = Query(default=1, ge=1)):
    per_page = 20
    all_voices = []
    for f in sorted(_voices_dir().iterdir()):
        if f.is_file() and f.suffix.lower() in ALLOWED_AUDIO_EXTENSIONS:
            all_voices.append({"name": f.name, "size_kb": max(1, f.stat().st_size // 1024)})
    total = len(all_voices)
    total_pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page
    voices = all_voices[offset:offset + per_page]
    return templates.TemplateResponse(request, "voices.html", {
        "request": request,
        "voices": voices,
        "page": page,
        "total_pages": total_pages,
    })
```

Add `import math` and `Query` to imports:
```python
import math
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
```

- [ ] **Step 2: Update `photos_page` route with filesystem pagination**

In `app/routes/photos.py`, replace the `photos_page` function:

```python
@router.get("/photos", response_class=HTMLResponse)
def photos_page(request: Request, page: int = Query(default=1, ge=1)):
    per_page = 20
    all_photos = []
    for f in sorted(_backgrounds_dir().iterdir()):
        if f.is_file() and f.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS:
            all_photos.append({"name": f.name, "size_kb": max(1, f.stat().st_size // 1024)})
    total = len(all_photos)
    total_pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page
    photos = all_photos[offset:offset + per_page]
    return templates.TemplateResponse(request, "photos.html", {
        "request": request,
        "photos": photos,
        "page": page,
        "total_pages": total_pages,
    })
```

Add `import math` and `Query` to imports.

- [ ] **Step 3: Add pagination to voices.html**

In `app/templates/voices.html`, add at top:
```html
{% from "_pagination.html" import render_pagination %}
```

After the `{% endif %}` that closes the voices list block (before `{% endblock %}`), add:
```html
{{ render_pagination(page, total_pages, "/voices") }}
```

- [ ] **Step 4: Add pagination to photos.html**

Same pattern for `app/templates/photos.html`.

- [ ] **Step 5: Commit**

```bash
git add app/routes/voices.py app/routes/photos.py app/templates/voices.html app/templates/photos.html
git commit -m "feat(voices,photos): add pagination to voice and photo libraries"
```

---

### Task 6: Voice Description — Database Layer

**Files:**
- Modify: `app/db.py`
- Modify: `app/repository.py`

**Interfaces:**
- Produces: `get_voice_meta(conn, filename) -> dict | None`, `set_voice_meta(conn, filename, description) -> None`

- [ ] **Step 1: Add `voice_meta` table to schema**

In `app/db.py`, append to `_SCHEMA` string:

```sql
CREATE TABLE IF NOT EXISTS voice_meta (
    filename    TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

- [ ] **Step 2: Add repository functions**

In `app/repository.py`, add:

```python
def get_voice_meta(conn: sqlite3.Connection, filename: str) -> dict | None:
    row = conn.execute("SELECT * FROM voice_meta WHERE filename = ?", (filename,)).fetchone()
    if row is None:
        return None
    return {"filename": row["filename"], "description": row["description"]}


def set_voice_meta(conn: sqlite3.Connection, filename: str, description: str) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO voice_meta (filename, description, created_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(filename) DO UPDATE SET description = excluded.description, updated_at = excluded.updated_at""",
        (filename, description, now, now),
    )
    conn.commit()


def rename_voice_meta(conn: sqlite3.Connection, old_filename: str, new_filename: str) -> None:
    conn.execute(
        "UPDATE voice_meta SET filename = ?, updated_at = ? WHERE filename = ?",
        (new_filename, _now(), old_filename),
    )
    conn.commit()


def delete_voice_meta(conn: sqlite3.Connection, filename: str) -> None:
    conn.execute("DELETE FROM voice_meta WHERE filename = ?", (filename,))
    conn.commit()
```

- [ ] **Step 3: Commit**

```bash
git add app/db.py app/repository.py
git commit -m "feat(db): add voice_meta table and repository functions"
```

---

### Task 7: Voice Description — API Endpoints

**Files:**
- Modify: `app/routes/voices.py`

**Interfaces:**
- Consumes: `get_voice_meta`, `set_voice_meta`, `rename_voice_meta`, `delete_voice_meta` from Task 6

- [ ] **Step 1: Add description endpoint**

In `app/routes/voices.py`, add:

```python
@router.post("/voices/{name}/description")
async def update_voice_description(name: str, request: Request):
    body = await request.json()
    description = body.get("description", "")
    p = _safe_voice_path(name)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy voice")
    with locked_conn(request) as conn:
        repository.set_voice_meta(conn, name, description)
    return {"status": "ok"}
```

Add `from app import repository` to imports.

- [ ] **Step 2: Update rename to also rename voice_meta**

In the `rename_voice` function, inside the `with locked_conn(request) as conn:` block, after `src.rename(dest)` and the book update, add:

```python
repository.rename_voice_meta(conn, old_name, dest.name)
```

- [ ] **Step 3: Update delete to also delete voice_meta**

In the `delete_voice` function, inside the `with locked_conn(request) as conn:` block, before `p.unlink`, add:

```python
repository.delete_voice_meta(conn, name)
```

- [ ] **Step 4: Include description in voice list**

Update `voices_page` to fetch metadata for each voice:

```python
with locked_conn(request) as conn:
    for v in all_voices:
        meta = repository.get_voice_meta(conn, v["name"])
        v["description"] = meta["description"] if meta else ""
```

- [ ] **Step 5: Commit**

```bash
git add app/routes/voices.py
git commit -m "feat(voices): add description API and integrate with rename/delete"
```

---

### Task 8: Voice Card View + Description UI

**Files:**
- Modify: `app/templates/voices.html`
- Modify: `app/static/style.css`

- [ ] **Step 1: Add card view CSS**

Append to `app/static/style.css`:

```css
.voice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: var(--space-md, 1rem);
    margin-top: var(--space-md, 1rem);
}
.voice-card {
    background: var(--bg-card, #fff);
    border: 1px solid var(--border-color, #ddd);
    border-radius: var(--border-radius, 6px);
    padding: var(--space-md, 1rem);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 0.5rem);
}
.voice-card audio {
    width: 100%;
    height: 36px;
}
.voice-card-name {
    font-weight: 600;
    font-size: var(--font-size-sm, 0.875rem);
    word-break: break-all;
}
.voice-card-meta {
    font-size: var(--font-size-xs, 0.75rem);
    color: var(--text-muted, #999);
}
.voice-card-desc {
    width: 100%;
    min-height: 2.5rem;
    padding: 0.4rem;
    border: 1px solid var(--border-color, #ddd);
    border-radius: var(--border-radius, 4px);
    font-size: var(--font-size-sm, 0.875rem);
    font-family: inherit;
    resize: vertical;
    background: var(--bg-input, #fff);
    color: var(--text-primary, #333);
}
.voice-card-actions {
    display: flex;
    gap: var(--space-sm, 0.5rem);
    margin-top: auto;
}
.view-toggle {
    display: inline-flex;
    border: 1px solid var(--border-color, #ddd);
    border-radius: var(--border-radius, 6px);
    overflow: hidden;
}
.view-toggle button {
    padding: 0.4rem 0.75rem;
    border: none;
    background: var(--bg-card, #fff);
    color: var(--text-primary, #333);
    cursor: pointer;
    font-size: var(--font-size-sm, 0.875rem);
}
.view-toggle button.active {
    background: var(--color-primary, #4f46e5);
    color: #fff;
}
```

- [ ] **Step 2: Rewrite voices.html with card + table views**

Replace `app/templates/voices.html` entirely:

```html
{% extends "base.html" %}
{% from "_pagination.html" import render_pagination %}
{% block title %}Voice Library{% endblock %}
{% block content %}
<h2>Voice Library</h2>
<p style="color:var(--text-muted);margin-bottom:var(--space-lg)">Quản lý clip giọng tham chiếu cho TTS.</p>

<div class="card">
    <div class="card-header">
        <h3 style="margin:0">Upload voice mới</h3>
    </div>
    <form method="post" action="/voices/upload" enctype="multipart/form-data" style="display:flex;gap:var(--space-md);align-items:flex-end;flex-wrap:wrap">
        <div class="form-group" style="margin:0;flex:1;min-width:200px">
            <label for="voice-file">File audio (.wav .mp3 .m4a .ogg) — có thể chọn nhiều file</label>
            <input type="file" name="files" id="voice-file" accept=".wav,.mp3,.m4a,.ogg" multiple required>
        </div>
        <button type="submit" class="btn-primary">Upload</button>
    </form>
</div>

<div class="card">
    <div class="card-header">
        <h3 style="margin:0">Thư viện voice</h3>
        <div style="display:flex;gap:var(--space-sm);align-items:center">
            <span style="font-size:var(--font-size-sm);color:var(--text-muted)">{{ voices|length }} voice</span>
            <div class="view-toggle">
                <button id="view-card" class="active" onclick="setView('card')">Card</button>
                <button id="view-table" onclick="setView('table')">Table</button>
            </div>
        </div>
    </div>

    {% if voices %}
    <div id="card-view" class="voice-grid">
        {% for v in voices %}
        <div class="voice-card">
            <audio controls preload="none">
                <source src="/voices/file/{{ v.name }}">
            </audio>
            <div class="voice-card-name">{{ v.name }}</div>
            <div class="voice-card-meta">{{ v.size_kb }} KB</div>
            <textarea class="voice-card-desc" placeholder="Mô tả voice..."
                data-voice="{{ v.name }}"
                oninput="debounceSaveDescription(this)">{{ v.description }}</textarea>
            <div class="voice-card-actions">
                <form method="post" action="/voices/rename" style="display:flex;gap:var(--space-sm);flex:1">
                    <input type="hidden" name="old_name" value="{{ v.name }}">
                    <input type="text" name="new_name" placeholder="Đổi tên" required style="flex:1;min-width:0">
                    <button type="submit" class="btn-sm">Rename</button>
                </form>
                <form method="post" action="/voices/delete" onsubmit="return confirm('Xóa voice này?')">
                    <input type="hidden" name="name" value="{{ v.name }}">
                    <button type="submit" class="btn-danger btn-sm">Xóa</button>
                </form>
            </div>
        </div>
        {% endfor %}
    </div>

    <div id="table-view" style="display:none">
        <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Tên file</th>
                    <th>Kích thước</th>
                    <th>Mô tả</th>
                    <th>Preview</th>
                    <th>Đổi tên</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
            {% for v in voices %}
                <tr>
                    <td style="word-break:break-all">{{ v.name }}</td>
                    <td>{{ v.size_kb }} KB</td>
                    <td>
                        <input type="text" value="{{ v.description }}" placeholder="Mô tả..."
                            data-voice="{{ v.name }}"
                            oninput="debounceSaveDescription(this)"
                            style="min-width:150px">
                    </td>
                    <td>
                        <audio controls preload="none" style="height:28px;vertical-align:middle">
                            <source src="/voices/file/{{ v.name }}">
                        </audio>
                    </td>
                    <td>
                        <form method="post" action="/voices/rename" style="display:flex;gap:var(--space-sm)">
                            <input type="hidden" name="old_name" value="{{ v.name }}">
                            <input type="text" name="new_name" placeholder="Tên mới" required style="min-width:120px">
                            <button type="submit" class="btn-sm">Đổi tên</button>
                        </form>
                    </td>
                    <td>
                        <form method="post" action="/voices/delete" onsubmit="return confirm('Xóa voice này?')">
                            <input type="hidden" name="name" value="{{ v.name }}">
                            <button type="submit" class="btn-danger btn-sm">Xóa</button>
                        </form>
                    </td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
        </div>
    </div>
    {% else %}
    <p style="margin:0;color:var(--text-muted)">Chưa có voice nào. Upload file audio ở trên.</p>
    {% endif %}
</div>

{{ render_pagination(page, total_pages, "/voices") }}

<script>
function setView(mode) {
    document.getElementById('card-view').style.display = mode === 'card' ? 'grid' : 'none';
    document.getElementById('table-view').style.display = mode === 'table' ? 'block' : 'none';
    document.getElementById('view-card').classList.toggle('active', mode === 'card');
    document.getElementById('view-table').classList.toggle('active', mode === 'table');
    localStorage.setItem('voices-view', mode);
}
(function() {
    const saved = localStorage.getItem('voices-view') || 'card';
    setView(saved);
})();

const _descTimers = {};
function debounceSaveDescription(el) {
    const name = el.dataset.voice;
    clearTimeout(_descTimers[name]);
    _descTimers[name] = setTimeout(() => {
        fetch('/voices/' + encodeURIComponent(name) + '/description', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({description: el.value}),
        });
    }, 800);
}
</script>
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add app/templates/voices.html app/static/style.css
git commit -m "feat(voices): add card view with description and view toggle"
```

---

### Task 9: Multi-file Upload — Voices

**Files:**
- Modify: `app/routes/voices.py`

- [ ] **Step 1: Update upload endpoint to accept multiple files**

Replace the `upload_voice` function in `app/routes/voices.py`:

```python
@router.post("/voices/upload")
async def upload_voices(files: list[UploadFile] = File(...)):
    dest_dir = _voices_dir()
    uploaded = []
    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            uploaded.append({"name": file.filename, "status": "error", "detail": f"Định dạng không hỗ trợ: {ext}"})
            continue
        base = Path(file.filename or f"voice{ext}").name
        dest = dest_dir / base
        if dest.exists():
            dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{base}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        uploaded.append({"name": dest.name, "status": "ok"})
    return RedirectResponse(url="/voices", status_code=303)
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/voices.py
git commit -m "feat(voices): support multi-file upload"
```

---

### Task 10: Multi-file Upload — Music

**Files:**
- Modify: `app/routes/music.py`

- [ ] **Step 1: Update upload endpoint to accept multiple files**

Replace the `upload_music` function:

```python
@router.post("/music/upload")
async def upload_music(request: Request, files: list[UploadFile] = File(...)):
    max_bytes = settings.music_max_size_mb * 1024 * 1024
    _MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    with locked_conn(request) as conn:
        for file in files:
            ext = Path(file.filename or "").suffix.lower()
            if ext not in _ALLOWED_EXTENSIONS:
                continue
            safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename or 'music').name}"
            dest = _MUSIC_DIR / safe_name
            size = 0
            with open(dest, "wb") as out:
                while chunk := await file.read(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        out.close()
                        dest.unlink(missing_ok=True)
                        break
                    out.write(chunk)
            if size > max_bytes:
                continue
            duration = _probe_duration(str(dest))
            display_name = Path(file.filename or safe_name).stem
            repository.create_music(conn, name=display_name, file_path=str(dest), duration_sec=duration)

    return RedirectResponse(url="/music", status_code=303)
```

- [ ] **Step 2: Update music.html template for multi-file**

In `app/templates/music.html`, change the file input:
```html
<input type="file" name="files" id="music-file" accept=".mp3,.wav,.ogg,.m4a" multiple required>
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/music.py app/templates/music.html
git commit -m "feat(music): support multi-file upload"
```

---

### Task 11: Multi-file Upload — Photos

**Files:**
- Modify: `app/routes/photos.py`
- Modify: `app/templates/photos.html`

- [ ] **Step 1: Update upload endpoint**

Replace `upload_photo`:

```python
@router.post("/photos/upload")
async def upload_photos(files: list[UploadFile] = File(...)):
    dest_dir = _backgrounds_dir()
    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            continue
        base = Path(file.filename or f"photo{ext}").name
        dest = dest_dir / base
        if dest.exists():
            dest = dest_dir / f"{uuid.uuid4().hex[:8]}_{base}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    return RedirectResponse(url="/photos", status_code=303)
```

- [ ] **Step 2: Update photos.html for multi-file input**

Change file input to accept multiple:
```html
<input type="file" name="files" id="photo-file" accept=".jpg,.jpeg,.png,.webp" multiple required>
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/photos.py app/templates/photos.html
git commit -m "feat(photos): support multi-file upload"
```

---

### Task 12: Auto-save — JavaScript Module

**Files:**
- Create: `app/static/autosave.js`

- [ ] **Step 1: Create autosave.js**

Create `app/static/autosave.js`:

```javascript
(function() {
    const DEBOUNCE_MS = 800;
    const timers = new Map();

    function showToast(msg, type) {
        let toast = document.getElementById('autosave-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'autosave-toast';
            toast.style.cssText = 'position:fixed;bottom:1rem;right:1rem;padding:0.5rem 1rem;border-radius:6px;font-size:0.875rem;z-index:9999;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.style.background = type === 'ok' ? '#10b981' : type === 'error' ? '#ef4444' : '#6b7280';
        toast.style.color = '#fff';
        toast.style.opacity = '1';
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2000);
    }

    function saveForm(form) {
        const action = form.action;
        const method = (form.method || 'POST').toUpperCase();
        let body;

        if (form.enctype === 'multipart/form-data') {
            body = new FormData(form);
        } else {
            body = new URLSearchParams(new FormData(form));
        }

        const headers = {};
        if (form.enctype !== 'multipart/form-data') {
            headers['Content-Type'] = 'application/x-www-form-urlencoded';
        }
        headers['X-Requested-With'] = 'autosave';

        showToast('Đang lưu...', 'saving');

        fetch(action, { method, body, headers })
            .then(r => {
                if (r.ok) showToast('Đã lưu ✓', 'ok');
                else showToast('Lỗi lưu', 'error');
            })
            .catch(() => showToast('Lỗi lưu', 'error'));
    }

    function debounceSave(form, key) {
        clearTimeout(timers.get(key));
        timers.set(key, setTimeout(() => saveForm(form), DEBOUNCE_MS));
    }

    function attachAutosave(form) {
        const key = form.action + '_' + Array.from(form.elements).map(e => e.name).join(',');

        form.addEventListener('input', function(e) {
            if (e.target.tagName === 'TEXTAREA') return;
            debounceSave(form, key);
        });

        form.addEventListener('change', function(e) {
            if (e.target.type === 'checkbox' || e.target.type === 'radio' || e.target.tagName === 'SELECT') {
                clearTimeout(timers.get(key));
                saveForm(form);
            }
        });

        form.querySelectorAll('textarea').forEach(ta => {
            ta.addEventListener('input', function() {
                debounceSave(form, key);
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('form[data-autosave]').forEach(attachAutosave);
    });
})();
```

- [ ] **Step 2: Include autosave.js in base.html**

In `app/templates/base.html`, before the closing `</body>` tag, add:

```html
<script src="/static/autosave.js"></script>
```

- [ ] **Step 3: Commit**

```bash
git add app/static/autosave.js app/templates/base.html
git commit -m "feat(ui): add auto-save module for forms"
```

---

### Task 13: Auto-save — Apply to Book Detail Forms

**Files:**
- Modify: `app/templates/book_detail.html`

**Interfaces:**
- Consumes: `autosave.js` from Task 12

- [ ] **Step 1: Add `data-autosave` to normalization form**

In `app/templates/book_detail.html`, find the normalization form and add the attribute:
```html
<form data-autosave method="post" action="/books/{{ book.id }}/normalization">
```

- [ ] **Step 2: Add `data-autosave` to music settings form**

Find the music form:
```html
<form data-autosave method="post" action="/books/{{ book.id }}/music">
```

- [ ] **Step 3: Add `data-autosave` to overlay config form**

Find the overlay config form:
```html
<form data-autosave method="post" action="/books/{{ book.id }}/overlay-config">
```

- [ ] **Step 4: Ensure redirect endpoints handle autosave**

In `app/routes/books.py`, for `update_normalization`, `update_book_music`, and `update_overlay_config`, add early JSON return when `X-Requested-With: autosave`:

```python
if request.headers.get("X-Requested-With") == "autosave":
    return {"status": "ok"}
```

Add this check after the DB write, before the `RedirectResponse`.

- [ ] **Step 5: Commit**

```bash
git add app/templates/book_detail.html app/routes/books.py
git commit -m "feat(books): enable auto-save for normalization, music, and overlay forms"
```

---

### Task 14: Final Integration Test

- [ ] **Step 1: Run existing tests**

```bash
pytest tests/ -v
```

- [ ] **Step 2: Manual smoke test**

Start the server and verify:
- `/books` shows paginated list
- `/voices` shows card view with toggle, description saves on type
- `/music` shows paginated list with multi-file upload
- `/photos` shows paginated list with multi-file upload
- Book detail forms auto-save (check network tab for fetch requests)

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes"
```

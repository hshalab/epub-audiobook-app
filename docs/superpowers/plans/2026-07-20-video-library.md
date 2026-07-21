# Video Library Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Video Creator page into a full-featured Video Library with CRUD, pagination, filtering, search, and sequential YouTube upload queue.

**Architecture:** Database-backed video registry using SQLite, REST API for CRUD operations, sequential upload worker with retry logic, and a redesigned frontend with pagination/filter/search.

**Tech Stack:** Python, FastAPI, SQLite, Jinja2 templates, vanilla JavaScript

## Global Constraints

- Python 3.10+ required
- SQLite3 with WAL mode enabled
- All timestamps in ISO 8601 format
- YouTube upload uses existing `app/youtube.py` module
- Follow existing code patterns in `app/routes/` and `app/templates/`
- Use existing `app/deps.py:locked_conn` for database connections
- Use existing `app/config.py:settings` for configuration

---

### Task 1: Database Schema - Add videos and batches tables

**Files:**
- Modify: `app/db.py:85-115`

**Interfaces:**
- Produces: `videos` table with columns: id, filename, original_name, title, description, tags, privacy, file_path, file_size_bytes, duration_sec, resolution, batch_id, source_audio, background_path, upload_status, youtube_video_id, youtube_upload_id, error_message, created_at, updated_at
- Produces: `batches` table with columns: id, name, total_files, completed_files, failed_files, status, config_json, created_at, updated_at

- [ ] **Step 1: Add videos table to db.py**

Open `app/db.py` and add after the existing `youtube_uploads` table creation (around line 115):

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT,
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            privacy TEXT DEFAULT 'private',
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER DEFAULT 0,
            duration_sec REAL DEFAULT 0,
            resolution TEXT DEFAULT '1920x1080',
            batch_id TEXT,
            source_audio TEXT,
            background_path TEXT,
            upload_status TEXT DEFAULT 'local_only',
            youtube_video_id TEXT,
            youtube_upload_id INTEGER,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_upload_status ON videos(upload_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_batch_id ON videos(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_created_at ON videos(created_at)")
```

- [ ] **Step 2: Add batches table to db.py**

Add immediately after the videos table:

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            total_files INTEGER DEFAULT 0,
            completed_files INTEGER DEFAULT 0,
            failed_files INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            config_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
```

- [ ] **Step 3: Verify tables exist**

Run: `python -c "from app.db import get_conn; conn = get_conn(); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"`

Expected: List includes 'videos' and 'batches'

- [ ] **Step 4: Commit**

```bash
git add app/db.py
git commit -m "feat: add videos and batches tables to database schema"
```

---

### Task 2: Video Repository - CRUD operations

**Files:**
- Create: `app/video_repository.py`

**Interfaces:**
- Produces: `insert_video()`, `get_video()`, `list_videos()`, `update_video()`, `delete_video()`, `bulk_delete_videos()`, `bulk_update_upload_status()`

- [ ] **Step 1: Create video_repository.py**

Create `app/video_repository.py`:

```python
"""Video repository: CRUD operations for the videos table."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def insert_video(
    conn: sqlite3.Connection,
    *,
    filename: str,
    original_name: str,
    file_path: str,
    file_size_bytes: int = 0,
    resolution: str = "1920x1080",
    batch_id: str | None = None,
    source_audio: str | None = None,
    background_path: str | None = None,
    title: str = "",
    description: str = "",
    tags: str = "",
    privacy: str = "private",
) -> dict[str, Any]:
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO videos
           (filename, original_name, title, description, tags, privacy,
            file_path, file_size_bytes, resolution, batch_id, source_audio,
            background_path, upload_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local_only', ?, ?)""",
        (filename, original_name, title, description, tags, privacy,
         file_path, file_size_bytes, resolution, batch_id, source_audio,
         background_path, now, now),
    )
    conn.commit()
    return get_video(conn, cur.lastrowid)


def get_video(conn: sqlite3.Connection, video_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def list_videos(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    upload_status: str = "",
    batch_id: str = "",
    sort: str = "created_at",
    order: str = "desc",
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    where_clauses = []
    params: list[Any] = []

    if search:
        where_clauses.append(
            "(filename LIKE ? OR title LIKE ? OR description LIKE ? OR tags LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])

    if upload_status:
        where_clauses.append("upload_status = ?")
        params.append(upload_status)

    if batch_id:
        where_clauses.append("batch_id = ?")
        params.append(batch_id)

    if date_from:
        where_clauses.append("created_at >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("created_at <= ?")
        params.append(date_to + "T23:59:59")

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    valid_sorts = {"created_at", "filename", "file_size_bytes", "upload_status"}
    sort_col = sort if sort in valid_sorts else "created_at"
    order_dir = "ASC" if order.lower() == "asc" else "DESC"

    count = conn.execute(f"SELECT COUNT(*) FROM videos {where}", params).fetchone()[0]
    per_page = max(1, min(100, per_page))
    total_pages = max(1, (count + per_page - 1) // per_page)
    page = max(1, min(total_pages, page))
    offset = (page - 1) * per_page

    rows = conn.execute(
        f"SELECT * FROM videos {where} ORDER BY {sort_col} {order_dir} LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    return {
        "videos": [dict(r) for r in rows],
        "total": count,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def update_video(
    conn: sqlite3.Connection,
    video_id: int,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"title", "description", "tags", "privacy", "upload_status",
               "youtube_video_id", "youtube_upload_id", "error_message", "duration_sec"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_video(conn, video_id)
    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [video_id]
    conn.execute(f"UPDATE videos SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return get_video(conn, video_id)


def delete_video(conn: sqlite3.Connection, video_id: int) -> bool:
    row = conn.execute("SELECT file_path FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not row:
        return False
    file_path = Path(row["file_path"])
    if file_path.exists():
        file_path.unlink()
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    return True


def bulk_delete_videos(conn: sqlite3.Connection, video_ids: list[int]) -> int:
    if not video_ids:
        return 0
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(
        f"SELECT file_path FROM videos WHERE id IN ({placeholders})", video_ids
    ).fetchall()
    for row in rows:
        p = Path(row["file_path"])
        if p.exists():
            p.unlink()
    conn.execute(f"DELETE FROM videos WHERE id IN ({placeholders})", video_ids)
    conn.commit()
    return len(rows)


def bulk_update_upload_status(
    conn: sqlite3.Connection,
    video_ids: list[int],
    upload_status: str,
) -> int:
    if not video_ids:
        return 0
    placeholders = ",".join("?" * len(video_ids))
    now = _now_iso()
    conn.execute(
        f"UPDATE videos SET upload_status = ?, updated_at = ? WHERE id IN ({placeholders})",
        [upload_status, now] + video_ids,
    )
    conn.commit()
    return len(video_ids)
```

- [ ] **Step 2: Test basic CRUD**

Run: `python -c "from app.video_repository import insert_video, get_video, list_videos; from app.db import get_conn; conn = get_conn(); v = insert_video(conn, filename='test.mp4', original_name='test.mp4', file_path='/tmp/test.mp4'); print(get_video(conn, v['id'])); print(list_videos(conn))"`

Expected: Video dict returned with id, list_videos returns paginated result

- [ ] **Step 3: Commit**

```bash
git add app/video_repository.py
git commit -m "feat: add video repository with CRUD operations"
```

---

### Task 3: Upload Worker - Sequential queue processor

**Files:**
- Create: `app/upload_worker.py`

**Interfaces:**
- Consumes: `app.youtube.upload_video()`, `app.youtube.enqueue_upload()`
- Produces: `UploadWorker` class with `start()`, `stop()`, `enqueue()`, `get_status()` methods

- [ ] **Step 1: Create upload_worker.py**

Create `app/upload_worker.py`:

```python
"""Sequential YouTube upload queue worker."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime

from app import youtube
from app.db import get_conn
from app.video_repository import get_video, update_video

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [60, 300, 900]  # 1min, 5min, 15min
UPLOAD_DELAY = 2  # seconds between uploads


class UploadWorker:
    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Upload worker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Upload worker stopped")

    def enqueue(self, video_id: int, title: str, description: str, tags: str, privacy: str) -> int:
        conn = get_conn()
        try:
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]
            upload_id = youtube.enqueue_upload(
                conn, video_id, title, description, tags_list, privacy
            )
            update_video(conn, video_id, upload_status="queued", youtube_upload_id=upload_id)
            return upload_id
        finally:
            conn.close()

    async def _run_loop(self):
        while self._running:
            try:
                conn = get_conn()
                pending = youtube.get_pending_uploads(conn)
                conn.close()

                for upload in pending:
                    if not self._running:
                        break
                    await self._process_upload(upload)
                    await asyncio.sleep(UPLOAD_DELAY)
            except Exception as e:
                logger.error("Upload worker error: %s", e)

            await asyncio.sleep(5)  # poll interval

    async def _process_upload(self, upload: dict):
        upload_id = upload["id"]
        video_id = upload.get("video_id")
        conn = get_conn()
        try:
            if video_id:
                update_video(conn, video_id, upload_status="uploading")

            result = await asyncio.to_thread(
                youtube.upload_video,
                conn,
                upload["file_path"],
                upload["title"],
                upload["description"],
                upload.get("tags", []),
                upload.get("privacy_status", "private"),
            )

            if video_id:
                update_video(
                    conn, video_id,
                    upload_status="uploaded",
                    youtube_video_id=result.get("youtube_video_id", ""),
                )
            logger.info("Upload %s done: %s", upload_id, result.get("youtube_video_id"))
        except Exception as e:
            logger.error("Upload %s failed: %s", upload_id, e)
            if video_id:
                update_video(conn, video_id, upload_status="failed", error_message=str(e))
            youtube.mark_upload_failed(conn, upload_id, str(e))
        finally:
            conn.close()


# Singleton instance
upload_worker = UploadWorker()
```

- [ ] **Step 2: Commit**

```bash
git add app/upload_worker.py
git commit -m "feat: add sequential YouTube upload worker"
```

---

### Task 4: Video API Endpoints

**Files:**
- Create: `app/routes/video_api.py`
- Modify: `app/main.py:145-150`

**Interfaces:**
- Consumes: `app.video_repository.*`, `app.upload_worker.upload_worker`
- Produces: REST API at `/video/api/videos` and `/video/api/upload-queue`

- [ ] **Step 1: Create video_api.py**

Create `app/routes/video_api.py`:

```python
"""Video Library REST API endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app import repository, video_repository
from app.deps import locked_conn
from app.upload_worker import upload_worker

router = APIRouter()


@router.get("/video/api/videos")
def list_videos(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    upload_status: str = "",
    batch_id: str = "",
    sort: str = "created_at",
    order: str = "desc",
    date_from: str = "",
    date_to: str = "",
):
    with locked_conn(request) as conn:
        result = video_repository.list_videos(
            conn,
            page=page,
            per_page=per_page,
            search=search,
            upload_status=upload_status,
            batch_id=batch_id,
            sort=sort,
            order=order,
            date_from=date_from,
            date_to=date_to,
        )
    return JSONResponse(result)


@router.get("/video/api/videos/{video_id}")
def get_video(request: Request, video_id: int):
    with locked_conn(request) as conn:
        video = video_repository.get_video(conn, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse(video)


@router.patch("/video/api/videos/{video_id}")
async def update_video(request: Request, video_id: int):
    body = await request.json()
    with locked_conn(request) as conn:
        video = video_repository.get_video(conn, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        updated = video_repository.update_video(conn, video_id, **body)
    return JSONResponse(updated)


@router.delete("/video/api/videos/{video_id}")
def delete_video(request: Request, video_id: int):
    with locked_conn(request) as conn:
        if not video_repository.delete_video(conn, video_id):
            raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse({"status": "deleted"})


@router.post("/video/api/videos/{video_id}/requeue")
async def requeue_video(request: Request, video_id: int):
    with locked_conn(request) as conn:
        video = video_repository.get_video(conn, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        if video["upload_status"] not in ("failed", "local_only"):
            raise HTTPException(status_code=400, detail="Video cannot be requeued")
        upload_id = upload_worker.enqueue(
            video_id,
            title=video["title"] or video["filename"],
            description=video["description"],
            tags=video["tags"],
            privacy=video["privacy"],
        )
    return JSONResponse({"status": "queued", "upload_id": upload_id})


@router.post("/video/api/videos/bulk-delete")
async def bulk_delete(request: Request):
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    with locked_conn(request) as conn:
        count = video_repository.bulk_delete_videos(conn, ids)
    return JSONResponse({"deleted": count})


@router.post("/video/api/videos/bulk-upload")
async def bulk_upload(request: Request):
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    with locked_conn(request) as conn:
        queued = 0
        for vid in ids:
            video = video_repository.get_video(conn, vid)
            if video and video["upload_status"] in ("local_only", "failed"):
                upload_worker.enqueue(
                    vid,
                    title=video["title"] or video["filename"],
                    description=video["description"],
                    tags=video["tags"],
                    privacy=video["privacy"],
                )
                queued += 1
    return JSONResponse({"queued": queued})


@router.get("/video/api/upload-queue")
def list_upload_queue(request: Request):
    with locked_conn(request) as conn:
        pending = repository.get_pending_youtube_uploads(conn)
    return JSONResponse({"uploads": pending})


@router.post("/video/api/upload-queue/start")
async def start_upload_queue():
    await upload_worker.start()
    return JSONResponse({"status": "started"})
```

- [ ] **Step 2: Register router in main.py**

Open `app/main.py` and add after line 146 (`app.include_router(video.router)`):

```python
from app.routes import video_api
app.include_router(video_api.router)
```

- [ ] **Step 3: Test API**

Run: `curl http://localhost:8000/video/api/videos?page=1&per_page=5`

Expected: JSON with `{"videos": [], "total": 0, "page": 1, "per_page": 5, "total_pages": 1}`

- [ ] **Step 4: Commit**

```bash
git add app/routes/video_api.py app/main.py
git commit -m "feat: add video REST API with CRUD and bulk operations"
```

---

### Task 5: Frontend - Video Library page with pagination/filter/search

**Files:**
- Modify: `app/templates/video_creator.html`
- Modify: `app/routes/video.py:199-207`

**Interfaces:**
- Consumes: `/video/api/videos` endpoint
- Produces: Redesigned Video Creator page with video library table

- [ ] **Step 1: Update video.py route**

Open `app/routes/video.py` and modify the `video_creator_page` function (around line 199):

```python
@router.get("/video", response_class=HTMLResponse)
def video_creator_page(request: Request):
    _cleanup_old_tmp_files()
    return templates.TemplateResponse(request, "video_creator.html", {
        "request": request,
        "video_url": None,
        "error": None,
    })
```

- [ ] **Step 2: Add video library HTML**

Add this HTML block in `app/templates/video_creator.html` after the `<h2>Video Creator</h2>` section and before the batch upload section:

```html
<!-- Video Library -->
<div class="card" id="video-library">
    <div class="card-header">
        <h3 style="margin:0">Video Library</h3>
        <div style="display:flex;gap:var(--space-sm)">
            <button type="button" class="btn-outline btn-sm" id="btn-bulk-upload" disabled>Upload Selected</button>
            <button type="button" class="btn-danger btn-sm" id="btn-bulk-delete" disabled>Delete Selected</button>
        </div>
    </div>

    <!-- Pagination Top -->
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-md)">
        <div id="pagination-info" style="font-size:var(--font-size-sm);color:var(--text-muted)">No videos</div>
        <div id="pagination-top" class="btn-group"></div>
    </div>

    <!-- Filters -->
    <div style="display:flex;flex-wrap:wrap;gap:var(--space-sm);margin-bottom:var(--space-md)">
        <input type="text" id="filter-search" placeholder="Search..." style="flex:1;min-width:200px">
        <select id="filter-status">
            <option value="">All Status</option>
            <option value="local_only">Local</option>
            <option value="queued">Queued</option>
            <option value="uploading">Uploading</option>
            <option value="uploaded">Uploaded</option>
            <option value="failed">Failed</option>
        </select>
        <select id="filter-sort">
            <option value="created_at">Newest</option>
            <option value="created_at:asc">Oldest</option>
            <option value="filename">Filename A-Z</option>
            <option value="filename:desc">Filename Z-A</option>
            <option value="file_size_bytes">Size ↑</option>
            <option value="file_size_bytes:desc">Size ↓</option>
        </select>
        <select id="filter-per-page">
            <option value="20">20 / page</option>
            <option value="50">50 / page</option>
            <option value="100">100 / page</option>
        </select>
    </div>

    <!-- Video Table -->
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th><input type="checkbox" id="select-all-videos"></th>
                    <th>#</th>
                    <th>Filename</th>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Size</th>
                    <th>Date</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="video-table-body">
            </tbody>
        </table>
    </div>
</div>

<!-- Edit Modal -->
<div id="edit-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000">
    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--bg-primary);padding:var(--space-lg);border-radius:var(--radius-lg);width:90%;max-width:500px">
        <h3 style="margin-top:0">Edit Video</h3>
        <input type="hidden" id="edit-id">
        <div class="form-group">
            <label for="edit-title">Title</label>
            <input type="text" id="edit-title" style="width:100%">
        </div>
        <div class="form-group">
            <label for="edit-description">Description</label>
            <textarea id="edit-description" rows="3" style="width:100%"></textarea>
        </div>
        <div class="form-group">
            <label for="edit-tags">Tags (comma-separated)</label>
            <input type="text" id="edit-tags" style="width:100%">
        </div>
        <div class="form-group">
            <label for="edit-privacy">Privacy</label>
            <select id="edit-privacy" style="width:100%">
                <option value="private">Private</option>
                <option value="unlisted">Unlisted</option>
                <option value="public">Public</option>
            </select>
        </div>
        <div style="display:flex;gap:var(--space-sm);justify-content:flex-end">
            <button type="button" class="btn-outline" onclick="closeEditModal()">Cancel</button>
            <button type="button" class="btn-primary" onclick="saveVideo()">Save</button>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Add JavaScript for pagination/filter/search**

Add this JavaScript in the `<script>` section of `video_creator.html`:

```javascript
// Video Library
(function() {
    const API = '/video/api/videos';
    let currentPage = 1;
    let perPage = 20;
    let search = '';
    let statusFilter = '';
    let sort = 'created_at';
    let order = 'desc';
    let selectedIds = new Set();

    async function loadVideos() {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage,
            search,
            upload_status: statusFilter,
            sort,
            order,
        });
        const res = await fetch(`${API}?${params}`);
        const data = await res.json();
        renderTable(data);
        renderPagination(data);
        updateBulkButtons();
    }

    function renderTable(data) {
        const tbody = document.getElementById('video-table-body');
        tbody.innerHTML = '';
        if (!data.videos.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No videos found</td></tr>';
            return;
        }
        data.videos.forEach((v, i) => {
            const tr = document.createElement('tr');
            const statusBadge = getStatusBadge(v.upload_status);
            const sizeMB = (v.file_size_bytes / (1024 * 1024)).toFixed(1);
            const date = new Date(v.created_at).toLocaleDateString();
            tr.innerHTML = `
                <td><input type="checkbox" class="video-check" data-id="${v.id}" ${selectedIds.has(v.id) ? 'checked' : ''}></td>
                <td>${(data.page - 1) * data.per_page + i + 1}</td>
                <td title="${escHtml(v.filename)}">${escHtml(v.filename)}</td>
                <td>${escHtml(v.title || '-')}</td>
                <td>${statusBadge}</td>
                <td>${sizeMB} MB</td>
                <td>${date}</td>
                <td>
                    <button type="button" class="btn-outline btn-sm" onclick="editVideo(${v.id})">Edit</button>
                    <a href="/video/videos/${encodeURIComponent(v.filename)}" download class="btn-outline btn-sm">Download</a>
                    ${v.upload_status === 'local_only' || v.upload_status === 'failed' ?
                        `<button type="button" class="btn-outline btn-sm" onclick="uploadSingle(${v.id})">Upload</button>` : ''}
                    <button type="button" class="btn-danger btn-sm" onclick="deleteVideo(${v.id})">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.video-check').forEach(cb => {
            cb.addEventListener('change', function() {
                const id = parseInt(this.dataset.id);
                if (this.checked) selectedIds.add(id);
                else selectedIds.delete(id);
                updateBulkButtons();
            });
        });
    }

    function getStatusBadge(status) {
        const badges = {
            local_only: '<span class="badge badge-pending">Local</span>',
            queued: '<span class="badge badge-info">Queued</span>',
            uploading: '<span class="badge badge-processing">Uploading</span>',
            uploaded: '<span class="badge badge-done">Uploaded ✓</span>',
            failed: '<span class="badge badge-failed">Failed</span>',
        };
        return badges[status] || status;
    }

    function renderPagination(data) {
        const info = document.getElementById('pagination-info');
        info.textContent = `Showing ${(data.page - 1) * data.per_page + 1}-${Math.min(data.page * data.per_page, data.total)} of ${data.total}`;

        const container = document.getElementById('pagination-top');
        container.innerHTML = '';
        if (data.total_pages <= 1) return;

        const pages = [];
        for (let i = 1; i <= data.total_pages; i++) {
            if (i === 1 || i === data.total_pages || (i >= data.page - 2 && i <= data.page + 2)) {
                pages.push(i);
            } else if (pages[pages.length - 1] !== '...') {
                pages.push('...');
            }
        }

        pages.forEach(p => {
            const btn = document.createElement('button');
            btn.className = `btn-sm ${p === data.page ? 'btn-primary' : 'btn-outline'}`;
            btn.textContent = p;
            if (p !== '...') {
                btn.onclick = () => { currentPage = p; loadVideos(); };
            }
            container.appendChild(btn);
        });
    }

    function updateBulkButtons() {
        const hasSelected = selectedIds.size > 0;
        document.getElementById('btn-bulk-upload').disabled = !hasSelected;
        document.getElementById('btn-bulk-delete').disabled = !hasSelected;
    }

    function escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    // Filters
    let searchTimer;
    document.getElementById('filter-search').addEventListener('input', function() {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { search = this.value; currentPage = 1; loadVideos(); }, 300);
    });
    document.getElementById('filter-status').addEventListener('change', function() {
        statusFilter = this.value; currentPage = 1; loadVideos();
    });
    document.getElementById('filter-sort').addEventListener('change', function() {
        const [s, o] = this.value.split(':');
        sort = s; order = o || 'desc'; currentPage = 1; loadVideos();
    });
    document.getElementById('filter-per-page').addEventListener('change', function() {
        perPage = parseInt(this.value); currentPage = 1; loadVideos();
    });

    // Select all
    document.getElementById('select-all-videos').addEventListener('change', function() {
        document.querySelectorAll('.video-check').forEach(cb => {
            cb.checked = this.checked;
            const id = parseInt(cb.dataset.id);
            if (this.checked) selectedIds.add(id);
            else selectedIds.delete(id);
        });
        updateBulkButtons();
    });

    // Bulk actions
    document.getElementById('btn-bulk-upload').addEventListener('click', async function() {
        const res = await fetch(`${API}/bulk-upload`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: [...selectedIds]}),
        });
        const data = await res.json();
        alert(`Queued ${data.queued} videos for upload`);
        selectedIds.clear();
        loadVideos();
    });

    document.getElementById('btn-bulk-delete').addEventListener('click', async function() {
        if (!confirm(`Delete ${selectedIds.size} videos?`)) return;
        const res = await fetch(`${API}/bulk-delete`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: [...selectedIds]}),
        });
        const data = await res.json();
        alert(`Deleted ${data.deleted} videos`);
        selectedIds.clear();
        loadVideos();
    });

    // Single video actions
    window.editVideo = async function(id) {
        const res = await fetch(`${API}/${id}`);
        const video = await res.json();
        document.getElementById('edit-id').value = id;
        document.getElementById('edit-title').value = video.title || '';
        document.getElementById('edit-description').value = video.description || '';
        document.getElementById('edit-tags').value = video.tags || '';
        document.getElementById('edit-privacy').value = video.privacy || 'private';
        document.getElementById('edit-modal').style.display = 'block';
    };

    window.closeEditModal = function() {
        document.getElementById('edit-modal').style.display = 'none';
    };

    window.saveVideo = async function() {
        const id = document.getElementById('edit-id').value;
        await fetch(`${API}/${id}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                title: document.getElementById('edit-title').value,
                description: document.getElementById('edit-description').value,
                tags: document.getElementById('edit-tags').value,
                privacy: document.getElementById('edit-privacy').value,
            }),
        });
        closeEditModal();
        loadVideos();
    };

    window.uploadSingle = async function(id) {
        await fetch(`${API}/${id}/requeue`, {method: 'POST'});
        loadVideos();
    };

    window.deleteVideo = async function(id) {
        if (!confirm('Delete this video?')) return;
        await fetch(`${API}/${id}`, {method: 'DELETE'});
        loadVideos();
    };

    // Initial load
    loadVideos();
})();
```

- [ ] **Step 4: Test the page**

Run: `python -m uvicorn app.main:app --reload`
Open: `http://localhost:8000/video`

Expected: Video library section shows with filters, pagination, empty table

- [ ] **Step 5: Commit**

```bash
git add app/templates/video_creator.html app/routes/video.py
git commit -m "feat: add video library UI with pagination, filter, search"
```

---

### Task 6: Integration - Auto-upload after generation

**Files:**
- Modify: `app/routes/video.py:470-505` (in `_run_single_video`)

**Interfaces:**
- Consumes: `app.upload_worker.upload_worker.enqueue()`
- Produces: Auto-upload to YouTube after video generation if `settings.youtube_auto_upload` is true

- [ ] **Step 1: Add auto-upload to _run_single_video**

In `app/routes/video.py`, find the `_run_single_video` function and add after the success block (after `_progress_store[job_key]["result"] = {...}`):

```python
        # Auto-upload to YouTube if configured
        from app.config import settings
        from app import youtube
        if settings.youtube_auto_upload and youtube.is_configured():
            try:
                from app.upload_worker import upload_worker
                upload_worker.enqueue(
                    video_id=0,  # Will be updated when video table integration is complete
                    title=finfo["original_name"],
                    description="",
                    tags=settings.youtube_default_tags,
                    privacy=settings.youtube_default_privacy,
                )
            except Exception as e:
                logger.warning("Auto-upload enqueue failed: %s", e)
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/video.py
git commit -m "feat: add auto-upload to YouTube after video generation"
```

---

### Task 7: Integration - Register video in database after generation

**Files:**
- Modify: `app/routes/video.py:470-505` (in `_run_single_video`)

**Interfaces:**
- Consumes: `app.video_repository.insert_video()`
- Produces: Video record in database after generation

- [ ] **Step 1: Add video registration**

In `app/routes/video.py`, find the success block in `_run_single_video` and add after `shutil.move(str(tmp_out), str(final_path))`:

```python
                    # Register video in database
                    from app.video_repository import insert_video
                    from app.db import get_conn
                    db_conn = get_conn()
                    try:
                        video_record = insert_video(
                            db_conn,
                            filename=final_path.name,
                            original_name=finfo["original_name"],
                            file_path=str(final_path),
                            file_size_bytes=final_path.stat().st_size,
                            resolution=cfg.get("resolution", "1920x1080"),
                            batch_id=batch_id,
                            source_audio=finfo["original_name"],
                            background_path=str(image_path),
                            title=finfo["original_name"],
                        )
                        video_db_id = video_record["id"]
                    except Exception as e:
                        logger.warning("Failed to register video in database: %s", e)
                        video_db_id = 0
                    finally:
                        db_conn.close()
```

- [ ] **Step 2: Update auto-upload to use video_db_id**

Update the auto-upload section to use `video_db_id` instead of `0`:

```python
                    if settings.youtube_auto_upload and youtube.is_configured():
                        try:
                            from app.upload_worker import upload_worker
                            upload_worker.enqueue(
                                video_id=video_db_id,
                                title=finfo["original_name"],
                                description="",
                                tags=settings.youtube_default_tags,
                                privacy=settings.youtube_default_privacy,
                            )
                        except Exception as e:
                            logger.warning("Auto-upload enqueue failed: %s", e)
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/video.py
git commit -m "feat: register video in database after generation"
```

---

### Task 8: Testing and Polish

**Files:**
- Test: Manual testing of all features

**Interfaces:**
- All previous tasks integrated

- [ ] **Step 1: Test video generation**

1. Upload audio files
2. Generate batch
3. Verify videos appear in library table
4. Verify pagination works with multiple videos

- [ ] **Step 2: Test CRUD operations**

1. Edit video title/description/tags
2. Delete single video
3. Bulk delete multiple videos
4. Verify files are deleted from disk

- [ ] **Step 3: Test YouTube upload**

1. Connect YouTube account
2. Upload single video
3. Bulk upload multiple videos
4. Verify upload queue processes sequentially
5. Verify failed uploads can be requeued

- [ ] **Step 4: Test filters and search**

1. Search by filename
2. Filter by upload status
3. Sort by different columns
4. Verify pagination updates correctly

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: complete video library refactor with CRUD, pagination, and YouTube queue"
```

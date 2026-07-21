# Video Library Refactor - Design Spec

## Overview

Refactor the Video Creator page into a full-featured Video Library with CRUD operations, pagination, filtering, search, and a sequential YouTube upload queue. The current implementation stores videos as flat files with no metadata persistence. The new design adds a database-backed video registry with proper state management.

## Goals

1. Full CRUD for videos (rename, delete, edit metadata, re-generate)
2. Paginated video list with filters and search
3. YouTube upload as default with auto-upload option
4. Sequential upload queue with retry logic
5. Batch history tracking
6. Manage all batch assets (audio, backgrounds, videos, upload records)

## Architecture

### Database Schema

**New `videos` table:**
```sql
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
);

CREATE INDEX idx_videos_upload_status ON videos(upload_status);
CREATE INDEX idx_videos_batch_id ON videos(batch_id);
CREATE INDEX idx_videos_created_at ON videos(created_at);
```

**New `batches` table:**
```sql
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
);
```

**Extend `youtube_uploads`:** Add `video_id INTEGER REFERENCES videos(id)` column.

### Upload Status Values

- `local_only` - video exists on disk, not queued for YouTube
- `queued` - waiting in upload queue
- `uploading` - currently uploading
- `uploaded` - successfully uploaded to YouTube
- `failed` - upload failed after retries

## API Design

### Video CRUD Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/video/api/videos` | List with pagination/filter/search |
| GET | `/video/api/videos/{id}` | Get single video |
| PATCH | `/video/api/videos/{id}` | Update title/description/tags/privacy |
| DELETE | `/video/api/videos/{id}` | Delete video + file |
| POST | `/video/api/videos/{id}/requeue` | Re-queue failed upload |
| POST | `/video/api/videos/bulk-delete` | Delete multiple videos |
| POST | `/video/api/videos/bulk-upload` | Queue multiple for YouTube |

### Query Parameters for GET /video/api/videos

| Parameter | Default | Description |
|-----------|---------|-------------|
| page | 1 | Page number |
| per_page | 20 | Items per page (max 100) |
| search | - | Search filename, title, description, tags |
| upload_status | - | Filter by status |
| batch_id | - | Filter by batch |
| sort | created_at | Sort field |
| order | desc | Sort direction |
| date_from | - | ISO date filter |
| date_to | - | ISO date filter |

### Response Format

```json
{
  "videos": [
    {
      "id": 1,
      "filename": "video1.mp4",
      "original_name": "chapter1.mp3",
      "title": "Chapter 1",
      "description": "",
      "tags": "audiobook,epub",
      "privacy": "private",
      "file_path": "/data/videos/video1.mp4",
      "file_size_bytes": 47185920,
      "duration_sec": 180.5,
      "resolution": "1920x1080",
      "batch_id": "abc123",
      "source_audio": "chapter1.mp3",
      "background_path": "/data/backgrounds/bg.jpg",
      "upload_status": "local_only",
      "youtube_video_id": null,
      "error_message": null,
      "created_at": "2026-07-20T10:30:00",
      "updated_at": "2026-07-20T10:30:00"
    }
  ],
  "total": 150,
  "page": 1,
  "per_page": 20,
  "total_pages": 8
}
```

### Upload Queue Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/video/api/upload-queue` | List queued uploads |
| POST | `/video/api/upload-queue` | Add video to queue |
| DELETE | `/video/api/upload-queue/{id}` | Remove from queue |
| POST | `/video/api/upload-queue/start` | Start sequential worker |

## UI Design

### Video Library Page Layout

```
┌─────────────────────────────────────────────────────────┐
│ Video Library                    [Upload] [Batch Upload] │
├─────────────────────────────────────────────────────────┤
│ [< 1 2 3 ... 8 >]    Showing 1-20 of 150               │
├─────────────────────────────────────────────────────────┤
│ Filters: [Search box] [Status ▼] [Date ▼] [Batch ▼]    │
│          [Sort ▼] [Per page ▼]                          │
├─────────────────────────────────────────────────────────┤
│ ☐ │ # │ Filename    │ Status    │ Size │ Date    │ ▼   │
├───┼───┼─────────────┼───────────┼──────┼─────────┼─────┤
│ ☐ │ 1 │ video1.mp4  │ uploaded  │ 45MB │ 2026-07 │ ⋮  │
│ ☐ │ 2 │ video2.mp4  │ local     │ 32MB │ 2026-07 │ ⋮  │
│ ☐ │ 3 │ video3.mp4  │ failed    │ 28MB │ 2026-07 │ ⋮  │
└─────────────────────────────────────────────────────────┘
```

### Row Actions (⋮ menu)

- Edit metadata (title, description, tags, privacy)
- Upload to YouTube
- Download .mp4
- Delete

### Bulk Actions

- Upload selected to YouTube
- Delete selected

### Status Badges

| Status | Badge |
|--------|-------|
| local_only | Gray "Local" |
| queued | Blue "Queued" |
| uploading | Yellow "Uploading" |
| uploaded | Green "Uploaded ✓" |
| failed | Red "Failed" |

### Edit Modal

Click "Edit" opens modal with:
- Title (text input)
- Description (textarea)
- Tags (comma-separated input)
- Privacy (select: private/unlisted/public)
- Save/Cancel buttons

### Upload Queue Section

```
┌─────────────────────────────────────────────────────────┐
│ Upload Queue                          [Start] [Clear]   │
├─────────────────────────────────────────────────────────┤
│ # │ Video        │ Status      │ YouTube ID   │ Actions │
├───┼──────────────┼─────────────┼──────────────┼─────────┤
│ 1 │ video1.mp4   │ Uploading...│ -            │ Cancel  │
│ 2 │ video2.mp4   │ Queued      │ -            │ Remove  │
│ 3 │ video3.mp4   │ Done        │ dQw4w9WgXcQ  │ -       │
└─────────────────────────────────────────────────────────┘
```

## YouTube Upload Queue

### Worker Behavior

- Sequential processing: one upload at a time
- Auto-retry: 3 attempts with exponential backoff (1min, 5min, 15min)
- Status tracking: queued → uploading → uploaded/failed
- Resume on restart: worker checks for pending uploads on startup
- Rate limiting: 2 second delay between uploads

### Auto-upload Flow

1. Video generated → check `settings.youtube_auto_upload`
2. If true + YouTube configured → insert into `youtube_uploads` with status `queued`
3. Queue worker picks up → uploads → updates `videos.upload_status` and `videos.youtube_video_id`

### Manual Queue Flow

1. User selects videos → clicks "Upload to YouTube"
2. Edit title/tags/privacy in modal (optional) → confirm
3. Insert into `youtube_uploads` → worker processes sequentially

## Implementation Plan

### Phase 1: Database Schema
1. Add `videos` table to `db.py`
2. Add `batches` table to `db.py`
3. Extend `youtube_uploads` with `video_id` column
4. Add migration logic for existing data

### Phase 2: Backend API
1. Create `app/routes/video_api.py` with CRUD endpoints
2. Create `app/video_repository.py` for database operations
3. Implement pagination/filter/search queries
4. Add bulk operations

### Phase 3: Upload Queue Worker
1. Create `app/upload_worker.py` for sequential processing
2. Implement retry logic with exponential backoff
3. Add auto-upload hook in video generation flow
4. Add queue start/stop controls

### Phase 4: Frontend UI
1. Redesign `video_creator.html` with new layout
2. Add pagination component
3. Add filter/search controls
4. Add bulk action toolbar
5. Add edit metadata modal
6. Add upload queue section

### Phase 5: Integration
1. Wire up auto-upload after video generation
2. Add batch history tracking
3. Test end-to-end flow
4. Add error handling and recovery

## Files to Modify

| File | Change |
|------|--------|
| `app/db.py` | Add `videos` and `batches` tables |
| `app/repository.py` | Add video CRUD operations |
| `app/youtube.py` | Extend with queue management |
| `app/routes/video.py` | Refactor to use new API |
| `app/templates/video_creator.html` | Complete UI redesign |
| `app/config.py` | Add upload queue settings |

## New Files

| File | Purpose |
|------|---------|
| `app/video_repository.py` | Video database operations |
| `app/upload_worker.py` | Sequential upload queue worker |
| `app/routes/video_api.py` | REST API endpoints |

## Success Criteria

1. Videos persist across server restarts
2. Pagination works with 1000+ videos
3. Filters and search return results in <100ms
4. Upload queue processes sequentially without duplicates
5. Auto-upload works after video generation
6. Failed uploads can be retried
7. Bulk operations work for 50+ selected videos

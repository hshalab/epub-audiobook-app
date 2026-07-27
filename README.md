# EPUB → Audiobook → Video

Upload `.epub` files, automatically split into chapters and patches, synthesize with VoxCPM2 TTS, merge into audio, optionally generate video with background images, and auto-upload to YouTube. All work is tracked in SQLite for crash recovery.

## Features

- **EPUB Parsing** — Extract chapters from EPUB files with smart chapter detection
- **Patch System** — Split books into manageable patches for processing
- **TTS Synthesis** — VoxCPM2-based text-to-speech with per-chunk WAV output
- **Audio Merge** — Combine patches into full audiobook files
- **Video Generation** — Create videos with custom backgrounds per patch/chapter
- **YouTube Upload** — Auto-upload generated videos to YouTube
- **Automated Patch Pipeline** — Per-patch automation: overlay thumbnail → multi-source video (looping backgrounds + webcam PiP) → YouTube upload (thumbnail + playlist), with idempotent retry per stage
- **Automation Settings** — Validated FFmpeg presets, webcam config, and YouTube playlist defaults with system-wide defaults and per-book JSON overrides
- **Modern UI** — Dark mode, drag-and-drop upload, image preview
- **Batch Processing** — Upload multiple books, generate videos in bulk
- **Background Worker** — Non-blocking queue processing with admin controls
- **Crash Recovery** — SQLite tracking survives restarts

## Setup

Requires Python ≥3.10, <3.13.

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .
```

### TTS Engine

```bash
./.venv/Scripts/python.exe -m pip install voxcpm
```

**VRAM:** VoxCPM2 needs ~8GB VRAM. Use CPU mode or smaller model for lower VRAM GPUs.

### Environment

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Key settings:
- `DATA_ROOT` — Storage path for uploads and generated files
- `ENABLE_WORKER` — Toggle background processing (`true`/`false`)
- `USE_NVENC` — Hardware-accelerated video encoding
- `YOUTUBE_*` — YouTube upload credentials and defaults

### ffmpeg/ffprobe

The app needs `ffmpeg` and `ffprobe` binaries for audio merging and video generation. Place `ffmpeg.exe` and `ffprobe.exe` in `assets/bin/` (they are tracked via Git LFS, so they may already be present after cloning).

If `assets/bin/` is empty (e.g. Git LFS not installed), download the binaries manually:

**Windows**
1. Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (grab the `ffmpeg-release-essentials.zip`) or [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases).
2. Extract the archive and copy `ffmpeg.exe` and `ffprobe.exe` from the `bin/` folder into `assets/bin/`.

**macOS**
```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**
```bash
sudo apt install ffmpeg
```

On macOS/Linux, if `ffmpeg` and `ffprobe` are on your `PATH` you don't need to copy them into `assets/bin/`. Verify the install:

```bash
ffmpeg -version
ffprobe -version
```

## Running

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Open http://localhost:8000

### Pages

- `/books` — Upload EPUBs, view library
- `/books/{id}` — Book details, chapter management, patch controls, automation enqueue/retry
- `/queue` — Real-time processing queue monitor
- `/video` — Standalone video creator (upload audio + background)
- `/youtube` — YouTube upload management with thumbnail and playlist status
- `/automation/settings-page` — System automation settings (FFmpeg presets, webcam, YouTube defaults)
- `/logs` — Application logs

## Architecture

```
app/
├── main.py                   # FastAPI app, routes, lifespan
├── config.py                 # Pydantic settings
├── models.py                 # SQLAlchemy models
├── db.py                     # Database setup
├── repository.py             # Data access layer
├── epub_parser.py            # EPUB extraction
├── chunker.py                # Text chunking & patch building
├── tts_engine.py             # VoxCPM2 TTS wrapper
├── audio_merge.py            # Patch/chunk merging
├── video_gen.py              # ffmpeg video generation (delegates to compositor for automation)
├── ffmpeg.py                 # ffmpeg/ffprobe utilities
├── youtube.py                # YouTube API client (upload, thumbnail, playlist, OAuth)
├── worker.py                 # Background queue processor
├── automation_config.py      # Validated Pydantic settings (VideoConfig, WebcamConfig, YouTubeConfig)
├── automation_repository.py  # Settings, media selection, pipeline, playlist-map persistence
├── automation_worker.py      # Pipeline worker — claims and runs thumbnail/video stages
├── video_compositor.py       # Multi-source FFmpeg compositor with looping backgrounds + webcam PiP
├── upload_worker.py          # YouTube upload queue worker (upload + post-process)
├── routes/           # API endpoints
│   ├── books.py      # Book CRUD & upload, automation enqueue hook
│   ├── patches.py    # Patch management
│   ├── queue.py      # Queue status & controls
│   ├── video.py      # Video generation, legacy background endpoints
│   ├── downloads.py  # File downloads
│   ├── youtube.py    # YouTube upload and OAuth
│   ├── automation.py # Automation settings, media selection, enqueue/retry
│   └── logs.py       # Log streaming
├── templates/        # Jinja2 HTML (automation_settings.html, youtube.html, book_detail.html)
└── static/           # CSS, JS, images
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/books` | Upload EPUB |
| `GET` | `/api/books` | List all books |
| `GET` | `/api/books/{id}` | Book details |
| `DELETE` | `/api/books/{id}` | Delete book |
| `POST` | `/api/books/{id}/chapters/{ch}/exclude` | Toggle chapter exclude |
| `POST` | `/api/books/{id}/patches/build` | Build custom patches |
| `POST` | `/api/patches/{id}/regenerate` | Regenerate patch |
| `POST` | `/api/patches/{id}/replace` | Text replacement rules |
| `GET` | `/api/queue` | Queue status |
| `POST` | `/api/queue/pause` | Pause worker |
| `POST` | `/api/queue/resume` | Resume worker |
| `POST` | `/api/video/generate` | Generate video from audio |
| `POST` | `/api/youtube/upload/{book_id}` | Upload to YouTube |
| `GET` | `/automation/settings` | Get system automation config |
| `PUT` | `/automation/settings` | Save system automation config (validated) |
| `GET` | `/automation/media` | List media assets |
| `PUT` | `/books/{id}/automation/media/{role}` | Set ordered media for background/webcam |
| `POST` | `/books/{id}/automation/enqueue` | Enqueue all patches for automation pipeline |
| `POST` | `/books/{id}/automation/retry/{patch_id}` | Retry failed pipeline stage |

## CLI Scripts

```bash
# Test EPUB parsing
python scripts/test_epub_parse.py <epub>

# Test patch/chunk generation
python scripts/test_repo_and_chunker.py <epub>

# Test TTS (stub unless --real)
python scripts/test_tts_single_patch.py <epub> --real

# Test audio merge + video
python scripts/test_merge_and_video.py

# Test full worker lifecycle
python scripts/test_worker.py
```

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `DATA_ROOT` | `./data` | Storage root |
| `DEFAULT_PATCH_SIZE` | `10` | Chapters per patch |
| `TTS_MAX_CHARS` | `400` | Max chars per TTS call |
| `USE_NVENC` | `false` | Hardware video encoding |
| `ENABLE_WORKER` | `true` | Background processing |
| `WORKER_POLL_INTERVAL` | `2.0` | Queue poll interval (sec) |
| `YOUTUBE_AUTO_UPLOAD` | `true` | Auto-upload to YouTube |
| `YOUTUBE_DEFAULT_PRIVACY` | `private` | Video privacy |
| `RESET_ALL_JOBS_ON_STARTUP` | `false` | Dev-only DB reset |

## YouTube OAuth

YouTube upload and post-processing require OAuth 2.0 credentials. The scopes requested are:
- `youtube.upload` — Upload videos
- `youtube` — Set thumbnails, manage playlists
- `youtube.force-ssl` — Required by YouTube Data API v3

If you reconnect after a scope change, the app will detect missing scopes and redirect you through re-authorization. Playlist mapping is persisted per book/channel to avoid duplicates on retry.

## Known Limitations

- `TTS_MAX_CHARS=400` is untested — adjust after real-model testing
- Progress tracked per-patch, not per-chunk
- Single chapter across multiple spine files appears as multiple chapters
- Video generation requires ffmpeg in PATH or `assets/bin/`
- NVENC preset `h264_nvenc` must be available at pipeline start; no automatic fallback to CPU

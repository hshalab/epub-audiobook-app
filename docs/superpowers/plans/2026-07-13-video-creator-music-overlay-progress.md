# Video Creator: Music + Overlay + Step Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add background music, custom text overlay, and per-step debug progress to the Video Creator batch page.

**Architecture:** Reuse `video_gen.generate_segment()`'s existing `music_path`/`music_volume` support and `image_overlay`'s renderer. Progress: an in-memory store in `app/routes/video.py` fed by the existing progress callback, exposed via `GET /video/progress/{batch_id}`, polled by the frontend.

**Tech Stack:** FastAPI, Jinja2 template with vanilla JS, Pillow, ffmpeg, pytest.

## Global Constraints

- Windows dev machine; run tests with `python -m pytest tests/ -v` from repo root `D:\Projects\epub-audiobook-app`.
- Music/overlay/progress must not break the legacy single-file `/video/generate` endpoint (new params all have defaults).
- Music resolution failure or overlay render failure must NOT fail the video — record a progress step and continue without that feature.
- UI copy is Vietnamese, matching existing page style.
- Commit messages follow the repo's gitmoji style (`:sparkles: feat:`, `:bug: fix:`).

---

### Task 1: Forward music params through `generate_standalone_video`

**Files:**
- Modify: `app/video_gen.py:398-425`
- Test: `tests/test_video_gen_standalone.py` (create)

**Interfaces:**
- Produces: `generate_standalone_video(audio_path, image_path, out_path, *, resolution="1920x1080", fps=30, codec="libx264", audio_bitrate="192k", image_type="none", crf=23, music_path: str | None = None, music_volume: float = 0.15, on_progress=None)` — Task 3 calls it with `music_path`/`music_volume`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_video_gen_standalone.py`:

```python
"""generate_standalone_video must forward music params to generate_segment."""
from unittest.mock import patch

from app import video_gen


def test_standalone_forwards_music_params():
    with patch.object(video_gen, "generate_segment") as seg:
        video_gen.generate_standalone_video(
            "a.mp3", "i.jpg", "o.mp4",
            music_path="m.mp3", music_volume=0.25,
        )
    kwargs = seg.call_args.kwargs
    assert kwargs["music_path"] == "m.mp3"
    assert kwargs["music_volume"] == 0.25


def test_standalone_defaults_no_music():
    with patch.object(video_gen, "generate_segment") as seg:
        video_gen.generate_standalone_video("a.mp3", "i.jpg", "o.mp4")
    kwargs = seg.call_args.kwargs
    assert kwargs["music_path"] is None
    assert kwargs["music_volume"] == 0.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_gen_standalone.py -v`
Expected: FAIL — `KeyError: 'music_path'` (generate_segment currently called without music kwargs) and/or `TypeError: generate_standalone_video() got an unexpected keyword argument 'music_path'`.

- [ ] **Step 3: Add the params and forward them**

In `app/video_gen.py`, change `generate_standalone_video` (currently lines 398–425) to:

```python
def generate_standalone_video(
    audio_path: str,
    image_path: str,
    out_path: str,
    *,
    resolution: str = "1920x1080",
    fps: int = 30,
    codec: str = "libx264",
    audio_bitrate: str = "192k",
    image_type: str = "none",
    crf: int = 23,
    music_path: str | None = None,
    music_volume: float = 0.15,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Generate a standalone video from a single audio + image (Video Creator page)."""
    w, h = resolution.split("x")
    res = (int(w), int(h))
    use_nvenc = codec == "h264_nvenc"
    generate_segment(
        image_path, audio_path, out_path,
        image_type=image_type,
        resolution=res,
        fps=fps,
        audio_bitrate=audio_bitrate,
        crf=crf,
        use_nvenc=use_nvenc,
        music_path=music_path,
        music_volume=music_volume,
        on_progress=on_progress,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_gen_standalone.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite to check nothing broke**

Run: `python -m pytest tests/ -v`
Expected: all pass (pre-existing failures, if any, unrelated to video_gen).

- [ ] **Step 6: Commit**

```bash
git add tests/test_video_gen_standalone.py app/video_gen.py
git commit -m ":sparkles: feat: forward music params through generate_standalone_video"
```

---

### Task 2: Progress store + `GET /video/progress/{batch_id}` endpoint

**Files:**
- Modify: `app/routes/video.py` (add store near line 35; extend `_make_progress_logger` at lines 26–33; extend `_cleanup_old_tmp_files` at lines 49–62; add endpoint after `generate_batch`)
- Test: `tests/test_video_progress_store.py` (create)

**Interfaces:**
- Produces (module-level in `app/routes/video.py`):
  - `_progress_store: dict[str, dict]` — key `f"{batch_id}:{idx}"`; value `{"status": str, "steps": list[dict], "updated_at": float}`; each step is `{"t": str (HH:MM:SS), "event": str, "detail": str}`.
  - `_record_step(job_key: str, event: str, fields: dict) -> None` — appends a step; sets `status` to `"error"` on events ending in `.failed`, `"done"` on `job.done`, else `"running"`.
  - `_make_progress_logger(prefix, job_key=None, **base_fields)` — same logging as today, plus store recording when `job_key` is given.
  - `GET /video/progress/{batch_id}` → `{"jobs": {"<idx>": {"status": ..., "steps": [...]}}}` — Task 4's frontend polls this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_video_progress_store.py`:

```python
"""In-memory progress store for the Video Creator batch page."""
import time

from app.routes import video as video_routes


def setup_function():
    video_routes._progress_store.clear()


def test_record_step_appends_and_tracks_status():
    video_routes._record_step("b1:0", "segment.start", {"path": "o.mp4"})
    video_routes._record_step("b1:0", "segment.ffmpeg_start", {})
    entry = video_routes._progress_store["b1:0"]
    assert entry["status"] == "running"
    assert len(entry["steps"]) == 2
    assert entry["steps"][0]["event"] == "segment.start"
    assert "path=o.mp4" in entry["steps"][0]["detail"]


def test_record_step_failed_sets_error_status():
    video_routes._record_step("b1:1", "segment.failed", {"returncode": 1})
    assert video_routes._progress_store["b1:1"]["status"] == "error"


def test_record_step_job_done_sets_done_status():
    video_routes._record_step("b1:2", "job.done", {})
    assert video_routes._progress_store["b1:2"]["status"] == "done"


def test_progress_logger_records_when_job_key_given():
    cb = video_routes._make_progress_logger("video_creator.batch", job_key="b2:0", batch_id="b2")
    cb("segment.start", {"path": "x.mp4"})
    assert "b2:0" in video_routes._progress_store


def test_cleanup_purges_old_entries():
    video_routes._record_step("old:0", "segment.start", {})
    video_routes._progress_store["old:0"]["updated_at"] = time.time() - 7200
    video_routes._record_step("new:0", "segment.start", {})
    video_routes._cleanup_progress_store(max_age_seconds=3600)
    assert "old:0" not in video_routes._progress_store
    assert "new:0" in video_routes._progress_store
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_progress_store.py -v`
Expected: FAIL — `AttributeError: module 'app.routes.video' has no attribute '_progress_store'`.

- [ ] **Step 3: Implement store + recorder + cleanup + endpoint**

In `app/routes/video.py`:

3a. Add after the `logger = logging.getLogger(__name__)` line:

```python
# In-memory step progress for debug UI, keyed by "{batch_id}:{idx}" or job_id.
# Best-effort: lost on restart, purged after 1h alongside tmp files.
_progress_store: dict[str, dict] = {}


def _record_step(job_key: str, event: str, fields: dict) -> None:
    entry = _progress_store.setdefault(
        job_key, {"status": "running", "steps": [], "updated_at": 0.0}
    )
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    entry["steps"].append({
        "t": datetime.now().strftime("%H:%M:%S"),
        "event": event,
        "detail": detail,
    })
    if event.endswith(".failed"):
        entry["status"] = "error"
    elif event == "job.done":
        entry["status"] = "done"
    entry["updated_at"] = time.time()


def _cleanup_progress_store(max_age_seconds: int = 3600) -> None:
    now = time.time()
    stale = [k for k, v in _progress_store.items()
             if (now - v.get("updated_at", 0)) > max_age_seconds]
    for k in stale:
        _progress_store.pop(k, None)
```

3b. Replace `_make_progress_logger` (lines 26–33) with:

```python
def _make_progress_logger(
    prefix: str, job_key: str | None = None, **base_fields
) -> video_gen.ProgressCallback:
    def _on_progress(event: str, fields: dict) -> None:
        parts = [f"event={prefix}.{event}"]
        merged = {**base_fields, **fields}
        for k, v in merged.items():
            parts.append(f"{k}={v}")
        logger.info(" ".join(parts))
        if job_key is not None:
            _record_step(job_key, event, fields)
    return _on_progress
```

3c. At the end of `_cleanup_old_tmp_files`, add one line (same indent level as the `for` loop, i.e. function body):

```python
    _cleanup_progress_store(max_age_seconds)
```

3d. Add the endpoint after `generate_batch` (before the "Background image management" section):

```python
@router.get("/video/progress/{batch_id}")
def get_batch_progress(batch_id: str):
    """Per-file step progress for a running/finished batch (debug UI)."""
    prefix = f"{batch_id}:"
    jobs = {
        key[len(prefix):]: {"status": v["status"], "steps": v["steps"]}
        for key, v in _progress_store.items()
        if key.startswith(prefix)
    }
    return JSONResponse({"jobs": jobs})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_progress_store.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_video_progress_store.py app/routes/video.py
git commit -m ":sparkles: feat: step progress store and endpoint for Video Creator batch"
```

---

### Task 3: Music + overlay in the batch generate route

**Files:**
- Modify: `app/routes/video.py` (`generate_batch` at lines 285–391; single `generate_video` progress key at line 204)
- Test: `tests/test_video_batch_extras.py` (create)

**Interfaces:**
- Consumes: `generate_standalone_video(..., music_path=, music_volume=)` from Task 1; `_record_step`, `_make_progress_logger(prefix, job_key=..., ...)` from Task 2; existing `repository.get_music(conn, music_id)`, `locked_conn(request)` from `app.deps`, `image_overlay.get_default_overlay_config()` / `render_overlay()` / `_wrap_lines()` / `_load_font()`.
- Produces: `_render_overlay_for_batch(bg_path: Path, text: str, overlay_opts: dict, out_path: Path) -> Path | None` — returns the rendered PNG path or `None` on failure. `generate_batch` accepts in the JSON body: `config.music_id: int|null`, `config.music_volume: int (0–100)`, `config.overlay: {"text": str, "position": str, "font_size": int, "text_color": str} | null` — Task 4's frontend sends these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_video_batch_extras.py`:

```python
"""Overlay rendering helper for the Video Creator batch route."""
from pathlib import Path

from PIL import Image

from app.routes import video as video_routes


def _make_bg(tmp_path: Path) -> Path:
    bg = tmp_path / "bg.png"
    Image.new("RGB", (640, 360), (10, 30, 60)).save(bg)
    return bg


def test_render_overlay_creates_png(tmp_path):
    bg = _make_bg(tmp_path)
    out = tmp_path / "out.png"
    result = video_routes._render_overlay_for_batch(
        bg, "Xin chào Việt Nam",
        {"position": "bottom", "font_size": 40, "text_color": "#FFDD00"},
        out,
    )
    assert result == out
    assert out.exists()
    img = Image.open(out)
    assert img.size == (640, 360)


def test_render_overlay_returns_none_on_bad_background(tmp_path):
    result = video_routes._render_overlay_for_batch(
        tmp_path / "missing.png", "text", {}, tmp_path / "out.png",
    )
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_batch_extras.py -v`
Expected: FAIL — `AttributeError: module 'app.routes.video' has no attribute '_render_overlay_for_batch'`.

- [ ] **Step 3: Implement the overlay helper**

In `app/routes/video.py`, add imports at the top (with the other `app` imports):

```python
from app import image_overlay, repository
from app.deps import locked_conn
```

Add the helper after `_resolve_background_image`:

```python
def _render_overlay_for_batch(
    bg_path: Path, text: str, overlay_opts: dict, out_path: Path,
) -> Path | None:
    """Render user text onto a copy of the background. None on failure (caller
    falls back to the plain background rather than failing the video)."""
    try:
        from PIL import Image, ImageDraw
        cfg = image_overlay.get_default_overlay_config()
        cfg["position"] = overlay_opts.get("position", "top")
        cfg["font_size"] = int(overlay_opts.get("font_size", 52))
        cfg["text_color"] = overlay_opts.get("text_color", "#FFFFFF")
        img = Image.open(str(bg_path)).convert("RGB")
        draw = ImageDraw.Draw(img)
        font = image_overlay._load_font(None, cfg["font_size"])
        lines = image_overlay._wrap_lines(draw, text, font, img.size[0] - 80)
        image_overlay.render_overlay(img, lines, cfg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), "PNG")
        return out_path
    except Exception as exc:
        logger.error("video_creator: overlay render failed: %s", exc)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_video_batch_extras.py -v`
Expected: 2 passed.

- [ ] **Step 5: Wire music + overlay + progress keys into `generate_batch`**

In `generate_batch`, after the `cfg = _validate_config(...)` block, add:

```python
    # Music: resolve library id -> file path (batch-wide, optional)
    music_path: str | None = None
    music_volume = max(0, min(100, int(raw_cfg.get("music_volume", 15)))) / 100.0
    raw_music_id = raw_cfg.get("music_id")
    if raw_music_id is not None and str(raw_music_id).strip().isdigit():
        with locked_conn(request) as conn:
            music = repository.get_music(conn, int(raw_music_id))
        if music and Path(music.file_path).exists():
            music_path = music.file_path
        else:
            logger.warning("video_creator: music id %s not found/missing on disk", raw_music_id)

    # Overlay: batch-wide optional text
    overlay_opts = raw_cfg.get("overlay") or {}
    overlay_text = (overlay_opts.get("text") or "").strip()
```

Then replace the per-file loop body between `image_path = _resolve_background_image(bg_path)` handling and the `try:` block. The full updated loop body (replacing lines 339–389):

```python
    for idx in selected:
        idx = int(idx)
        job_key = f"{batch_id}:{idx}"
        _progress_store.pop(job_key, None)  # fresh log on regenerate
        finfo = files_map.get(idx)
        if not finfo:
            _record_step(job_key, "job.failed", {"reason": "file not found in batch"})
            results.append({"index": idx, "status": "error", "message": "File not found in batch"})
            continue

        audio_path = Path(finfo["path"])
        if not audio_path.exists():
            _record_step(job_key, "job.failed", {"reason": "audio file missing"})
            results.append({"index": idx, "status": "error", "message": "Audio file missing"})
            continue

        bg_path = backgrounds.get(str(idx))
        image_path = _resolve_background_image(bg_path)
        if image_path is None:
            _record_step(job_key, "job.failed", {"reason": "no background image"})
            results.append({"index": idx, "status": "error", "message": "No background image available"})
            continue

        _record_step(job_key, "job.start", {"file": finfo["original_name"]})
        if music_path:
            _record_step(job_key, "music.resolved", {"path": Path(music_path).name,
                                                     "volume": music_volume})

        if overlay_text:
            overlay_png = _TMP_DIR / f"{batch_id}_{idx}_overlay.png"
            rendered = _render_overlay_for_batch(image_path, overlay_text, overlay_opts, overlay_png)
            if rendered is not None:
                image_path = rendered
                _record_step(job_key, "overlay.rendered", {"path": overlay_png.name})
            else:
                _record_step(job_key, "overlay.failed_fallback", {"detail": "using plain background"})

        stem = _sanitize_basename(finfo["original_name"])
        final_path = _unique_video_path(stem)
        tmp_out = _TMP_DIR / f"{batch_id}_{idx}_{uuid.uuid4().hex[:6]}.mp4"
        progress_cb = _make_progress_logger(
            "video_creator.batch", job_key=job_key,
            batch_id=batch_id, index=idx, mode="batch",
        )
        started = time.time()
        try:
            await asyncio.to_thread(
                video_gen.generate_standalone_video,
                str(audio_path), str(image_path), str(tmp_out),
                on_progress=progress_cb,
                music_path=music_path,
                music_volume=music_volume,
                **cfg,
            )
            shutil.move(str(tmp_out), str(final_path))
            _record_step(job_key, "job.done", {"video": final_path.name})
            completed_at = datetime.now()
            results.append({
                "index": idx,
                "status": "done",
                "name": final_path.name,
                "video_url": f"/video/videos/{final_path.name}",
                "size_mb": round(final_path.stat().st_size / (1024 * 1024), 1),
                "elapsed_seconds": round(time.time() - started, 1),
                "completed_at": completed_at.isoformat(timespec="seconds"),
            })
        except Exception as exc:
            tmp_out.unlink(missing_ok=True)
            _record_step(job_key, "job.failed", {"error": str(exc)[:300]})
            results.append({
                "index": idx,
                "status": "error",
                "message": str(exc),
                "elapsed_seconds": round(time.time() - started, 1),
            })
```

Also update the single-file endpoint's progress logger (line 204) so single jobs are visible in the store too:

```python
    progress_cb = _make_progress_logger(
        "video_creator.single", job_key=f"single:{job_id}",
        job_id=job_id, mode="single",
    )
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all pass, including Tasks 1–2 tests.

- [ ] **Step 7: Smoke-check the app imports**

Run: `python -c "from app.routes import video; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add tests/test_video_batch_extras.py app/routes/video.py
git commit -m ":sparkles: feat: background music and text overlay in Video Creator batch"
```

---

### Task 4: Frontend — music/overlay controls + live step progress + debug log

**Files:**
- Modify: `app/templates/video_creator.html`

**Interfaces:**
- Consumes: `GET /music/list` → `{"music": [{"id", "name", "duration_sec"}]}`; `GET /video/progress/{batch_id}` → `{"jobs": {"<idx>": {"status", "steps": [{"t","event","detail"}]}}}`; `generate-batch` body `config` now carries `music_id`, `music_volume`, `overlay`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add music + overlay controls to Step 3 config**

In `app/templates/video_creator.html`, after the second `.form-row` (the one ending with the CRF slider, line ~129), insert a third form row:

```html
        <div class="form-row">
            <div class="form-group">
                <label for="cfg-music">Nhạc nền</label>
                <select id="cfg-music">
                    <option value="">-- Không dùng --</option>
                </select>
            </div>
            <div class="form-group">
                <label for="cfg-music-volume">Volume nhạc: <span id="music-vol-val">15%</span></label>
                <input type="range" id="cfg-music-volume" min="0" max="100" value="15"
                       oninput="document.getElementById('music-vol-val').textContent=this.value+'%'">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group" style="flex:2">
                <label for="cfg-overlay-text">Text overlay (để trống = không có chữ)</label>
                <input type="text" id="cfg-overlay-text" placeholder="VD: Tên sách - Tập 1" maxlength="200">
            </div>
            <div class="form-group">
                <label for="cfg-overlay-position">Vị trí chữ</label>
                <select id="cfg-overlay-position">
                    <option value="top" selected>Trên</option>
                    <option value="center">Giữa</option>
                    <option value="bottom">Dưới</option>
                </select>
            </div>
            <div class="form-group">
                <label for="cfg-overlay-size">Cỡ chữ</label>
                <input type="number" id="cfg-overlay-size" min="12" max="200" value="52">
            </div>
            <div class="form-group">
                <label for="cfg-overlay-color">Màu chữ</label>
                <input type="color" id="cfg-overlay-color" value="#FFFFFF">
            </div>
        </div>
```

- [ ] **Step 2: Load the music list on page init**

In the page script IIFE: add `musicList: '/music/list',` and `progress: '/video/progress/',` to the `API` object, then add below `loadBackgrounds()`'s definition:

```javascript
    async function loadMusicList() {
        try {
            const res = await fetch(API.musicList);
            const data = await res.json();
            const sel = document.getElementById('cfg-music');
            (data.music || []).forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                let label = m.name;
                if (m.duration_sec) {
                    const mins = Math.floor(m.duration_sec / 60);
                    const secs = String(Math.round(m.duration_sec % 60)).padStart(2, '0');
                    label += ` (${mins}:${secs})`;
                }
                opt.textContent = label;
                sel.appendChild(opt);
            });
        } catch(e) { console.error('Failed to load music list', e); }
    }
```

And change the last line of the IIFE from `loadBackgrounds();` to:

```javascript
    loadBackgrounds();
    loadMusicList();
```

- [ ] **Step 3: Send music + overlay in the generate request**

In the `btn-generate` click handler, extend the `config` object:

```javascript
        const musicSel = document.getElementById('cfg-music');
        const overlayText = document.getElementById('cfg-overlay-text').value.trim();
        const config = {
            resolution: document.getElementById('cfg-resolution').value,
            fps: parseInt(document.getElementById('cfg-fps').value),
            codec: document.getElementById('cfg-codec').value,
            audio_bitrate: document.getElementById('cfg-audio-bitrate').value,
            image_type: document.getElementById('cfg-image-type').value,
            crf: parseInt(document.getElementById('cfg-crf').value),
            music_id: musicSel.value ? parseInt(musicSel.value) : null,
            music_volume: parseInt(document.getElementById('cfg-music-volume').value),
            overlay: overlayText ? {
                text: overlayText,
                position: document.getElementById('cfg-overlay-position').value,
                font_size: parseInt(document.getElementById('cfg-overlay-size').value) || 52,
                text_color: document.getElementById('cfg-overlay-color').value,
            } : null,
        };
```

- [ ] **Step 4: Poll progress while generating and update Status cells**

Add these helpers inside the IIFE (near `formatTimeShort`):

```javascript
    const EVENT_LABELS = {
        'job.start': 'Bắt đầu...',
        'music.resolved': 'Đã chọn nhạc nền',
        'overlay.rendered': 'Đã render text overlay',
        'overlay.failed_fallback': 'Overlay lỗi — dùng ảnh gốc',
        'segment.start': 'Chuẩn bị FFmpeg...',
        'segment.probe_duration': 'Đo thời lượng audio...',
        'segment.ffmpeg_start': 'FFmpeg đang chạy...',
        'segment.ffmpeg_done': 'FFmpeg xong, đang hoàn tất...',
        'segment.done': 'Encode xong',
        'segment.failed': 'FFmpeg lỗi',
        'job.done': 'Hoàn thành',
        'job.failed': 'Lỗi',
    };

    let progressTimer = null;

    function startProgressPolling() {
        stopProgressPolling();
        progressTimer = setInterval(async () => {
            if (!batchId) return;
            try {
                const res = await fetch(API.progress + batchId);
                const data = await res.json();
                Object.entries(data.jobs || {}).forEach(([idx, job]) => {
                    const statusEl = document.querySelector(`[data-status="${idx}"]`);
                    if (!statusEl || job.status !== 'running') return;
                    const last = job.steps[job.steps.length - 1];
                    if (last) {
                        statusEl.textContent = EVENT_LABELS[last.event] || last.event;
                        statusEl.className = 'status-pending';
                        statusEl.title = last.t + ' ' + last.event + ' ' + last.detail;
                    }
                });
            } catch(e) { /* polling is best-effort */ }
        }, 1000);
    }

    function stopProgressPolling() {
        if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
    }

    function stepLogHtml(steps) {
        return (steps || []).map(s =>
            `<div class="step-log-line"><span class="step-t">${escHtml(s.t)}</span> ` +
            `<span class="step-e">${escHtml(s.event)}</span> ` +
            `<span class="step-d">${escHtml(s.detail)}</span></div>`
        ).join('');
    }
```

In the `btn-generate` handler: call `startProgressPolling();` right after `resultsList.innerHTML = '<p>Processing...</p>';`, and add `stopProgressPolling();` as the first line of the `finally` block.

- [ ] **Step 5: Add the "Log" debug toggle to each result row**

In the results-rendering loop (`data.results.forEach(r => { ... })`), after `resultsList.appendChild(div);` — fetch the final progress once and append log toggles. Replace the closing of the try block:

```javascript
            resultsList.innerHTML = '';
            let finalJobs = {};
            try {
                const progRes = await fetch(API.progress + batchId);
                finalJobs = (await progRes.json()).jobs || {};
            } catch(e) { /* log unavailable */ }
            data.results.forEach(r => {
```

And inside the forEach, just before `resultsList.appendChild(div);`, add:

```javascript
                const job = finalJobs[String(r.index)];
                if (job && job.steps && job.steps.length) {
                    const details = document.createElement('details');
                    details.className = 'step-log';
                    details.innerHTML = '<summary>Log các bước (' + job.steps.length + ')</summary>'
                        + '<div class="step-log-body">' + stepLogHtml(job.steps) + '</div>';
                    div.appendChild(details);
                }
```

- [ ] **Step 6: Add CSS for the step log**

In the page's `<style>` block, append:

```css
.step-log { margin-top: 0.3rem; font-size: 0.8rem; }
.step-log summary { cursor: pointer; color: var(--text-muted); }
.step-log-body {
    margin-top: 0.3rem; padding: 0.4rem 0.6rem;
    background: var(--bg-secondary, rgba(128,128,128,0.08));
    border-radius: 6px; font-family: monospace;
    max-height: 220px; overflow-y: auto;
}
.step-log-line { white-space: nowrap; }
.step-t { color: var(--text-muted); margin-right: 0.4rem; }
.step-e { font-weight: 600; margin-right: 0.4rem; }
.step-d { color: var(--text-muted); }
```

- [ ] **Step 7: Commit**

```bash
git add app/templates/video_creator.html
git commit -m ":sparkles: feat: music/overlay controls and live step progress in Video Creator UI"
```

---

### Task 5: End-to-end verification in the browser

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything above, running app (`.claude/launch.json` dev server or `uvicorn app.main:app`).

- [ ] **Step 1: Start the dev server and open `/video`**

Use the preview browser. Verify: music dropdown populated from the library (upload one at `/music` first if empty), overlay fields present.

- [ ] **Step 2: Generate a batch with music + overlay**

Upload 1–2 short audio files, pick a music track, volume ~25%, overlay text "Test Overlay", position bottom. Click Generate. Verify:
- Status cells cycle through Vietnamese step labels (poll working).
- Result rows show Done with a "Log các bước (N)" toggle listing timestamped steps including `music.resolved` and `overlay.rendered`.

- [ ] **Step 3: Verify the output video**

Download/play the generated mp4: overlay text visible at the chosen position; background music audible under the main audio.

- [ ] **Step 4: Verify failure path**

Temporarily rename the music file on disk (or pick a since-deleted track) and generate: video still succeeds without music, log shows the fallback. Restore afterwards.

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -A
git commit -m ":bug: fix: adjustments from Video Creator e2e verification"
```

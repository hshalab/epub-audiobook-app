---
# LightTTS Generate + Preview Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LightTTS-based patch generation (no GPU needed) and real-time streaming preview to the epub-audiobook-app.

**Architecture:** Two independent features sharing `LightTTSEngine`. Feature 1 adds routes to synthesize patches officially using LightTTS chunk-by-chunk (mirroring the VoxCPM2 worker flow). Feature 2 replaces the blocking `previewPatch()` with an SSE stream that synthesizes chunks on-the-fly, streams chunk URLs to the browser, and plays them via Web Audio API as they arrive; after all chunks done, merges and saves to patch.audio_path.

**Tech Stack:** FastAPI StreamingResponse + SSE, LightTTSEngine (edge-tts/gtts/piper), Web Audio API, existing audio_merge + repository helpers.

## Global Constraints

- Python 3.11+, FastAPI, SQLite via existing `locked_conn` context manager
- All new routes go in `app/routes/text_studio.py` (existing router)
- Use `settings.data_root` for all file paths, never hardcode `data/`
- Match existing code style: `from __future__ import annotations`, type hints, `logger = logging.getLogger(__name__)`
- effects (mix) applied **after merge**, not per-chunk — `_mix_effects(merged_wav_bytes, full_text, conn)`
- No new dependencies; use existing: `soundfile`, `asyncio.to_thread`, `uuid`, `pathlib.Path`
- No DB schema changes

---

## File Map

| File | Change |
|------|--------|
| `app/routes/text_studio.py` | Add 4 routes + 2 helper functions |
| `app/templates/text_studio.html` | Replace `previewPatch()`, add Generate LightTTS button |
| `app/templates/book_detail.html` | Add per-patch LightTTS button + Run All button |
| `app/main.py` | Create `preview_tmp` dir at startup |

---

## Task 1: Backend helper `_light_synthesize_patch`

**Files:**
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Produces: `_light_synthesize_patch(patch_id, book_id, backend, voice, with_effects, conn, db_lock) -> str` — returns `audio_path`

- [ ] **Step 1: Add the helper function** after `_mix_effects` in `app/routes/text_studio.py`

```python
import threading
import soundfile as sf

def _light_synthesize_patch(
    patch_id: int,
    book_id: int,
    backend: str,
    voice: str | None,
    with_effects: bool,
    conn,
    db_lock: threading.Lock,
) -> str:
    """Synthesize a patch using LightTTS, chunk-by-chunk. Returns audio_path."""
    from app.chunker import split_into_tts_chunks
    from app import audio_merge, repository
    from app.config import settings

    with db_lock:
        patch = repository.get_patch(conn, patch_id)
        text = repository.get_effective_patch_text(conn, patch)

    chunks = split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars)

    engine = LightTTSEngine(backend=backend, voice=voice)

    book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
    book_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = book_dir / f"{patch_id}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        wav_bytes, _ = engine.synthesize_to_wav_bytes(chunk_text)
        chunk_path = chunk_dir / f"chunk_{i:03d}.wav"
        chunk_path.write_bytes(wav_bytes)
        chunk_paths.append(str(chunk_path))

    audio_path = str(book_dir / f"{patch_id}.wav")
    audio_merge.merge_chunk_files_to_patch(chunk_paths, audio_path)

    if with_effects:
        merged = Path(audio_path).read_bytes()
        with db_lock:
            mixed = _mix_effects(merged, text, conn)
        Path(audio_path).write_bytes(mixed)

    with db_lock:
        repository.mark_patch_done(conn, patch_id, audio_path)

    return audio_path
```

- [ ] **Step 2: Verify imports** — ensure `threading`, `soundfile as sf`, `Path` are imported at top of `app/routes/text_studio.py`. They mostly exist; add `threading` if missing.

- [ ] **Step 3: Manual smoke test** — in Python REPL or a scratch script, confirm `LightTTSEngine("edge-tts").synthesize_to_wav_bytes("test")` returns `(bytes, int)` without error.

---

## Task 2: Route `POST /books/{book_id}/patches/{patch_id}/light-tts-generate`

**Files:**
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Consumes: `_light_synthesize_patch(...)` from Task 1, `locked_conn`, `repository.get_patch`
- Produces: `POST /books/{book_id}/patches/{patch_id}/light-tts-generate` → JSON `{"status":"done","patch_id":N,"audio_path":"..."}`

- [ ] **Step 1: Add route** in `app/routes/text_studio.py` after existing preview routes

```python
@router.post("/books/{book_id}/patches/{patch_id}/light-tts-generate")
async def light_tts_generate(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    backend = body.get("backend") or settings.light_tts_backend
    voice = body.get("voice") or settings.light_tts_voice
    with_effects = bool(body.get("with_effects", False))

    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        if patch.status == "processing":
            raise HTTPException(status_code=409, detail="patch is currently processing")

    db_lock = request.app.state.db_lock
    conn = request.app.state.conn

    try:
        audio_path = await asyncio.to_thread(
            _light_synthesize_patch,
            patch_id, book_id, backend, voice, with_effects, conn, db_lock,
        )
    except Exception as exc:
        logger.exception("light_tts_generate failed for patch %s", patch_id)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"status": "done", "patch_id": patch_id, "audio_path": audio_path})
```

- [ ] **Step 2: Test the route manually** — start the app, open Text Studio on a book with at least one patch, open browser devtools console and run:

```javascript
fetch(`/books/1/patches/1/light-tts-generate`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({backend: 'edge-tts', voice: 'vi-VN-HoaiMyNeural'})
}).then(r => r.json()).then(console.log)
```

Expected: `{status: "done", patch_id: 1, audio_path: "..."}` and patch status updates to `done` in DB.

- [ ] **Step 3: Commit**

```bash
git add app/routes/text_studio.py
git commit -m "feat: add _light_synthesize_patch helper and light-tts-generate route"
```

---

## Task 3: Route `POST /books/{book_id}/light-tts-generate-all`

**Files:**
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Consumes: `_light_synthesize_patch(...)` from Task 1
- Produces: `POST /books/{book_id}/light-tts-generate-all` → JSON `{"results":[{"patch_id":N,"status":"done"|"error","detail":"..."}]}`

- [ ] **Step 1: Add route** in `app/routes/text_studio.py`

```python
@router.post("/books/{book_id}/light-tts-generate-all")
async def light_tts_generate_all(request: Request, book_id: int):
    body = await request.json()
    backend = body.get("backend") or settings.light_tts_backend
    voice = body.get("voice") or settings.light_tts_voice
    with_effects = bool(body.get("with_effects", False))
    patch_ids: list[int] | None = body.get("patch_ids")

    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="book not found")
        all_patches = repository.list_patches(conn, book_id)

    if patch_ids is not None:
        targets = [p for p in all_patches if p.id in set(patch_ids)]
    else:
        targets = [p for p in all_patches if p.status in ("pending", "failed")]

    db_lock = request.app.state.db_lock
    conn = request.app.state.conn

    results = []
    for patch in targets:
        if patch.status == "processing":
            results.append({"patch_id": patch.id, "status": "skipped", "detail": "currently processing"})
            continue
        try:
            await asyncio.to_thread(
                _light_synthesize_patch,
                patch.id, book_id, backend, voice, with_effects, conn, db_lock,
            )
            results.append({"patch_id": patch.id, "status": "done"})
        except Exception as exc:
            logger.exception("light_tts_generate_all failed for patch %s", patch.id)
            results.append({"patch_id": patch.id, "status": "error", "detail": str(exc)})

    return JSONResponse({"results": results})
```

- [ ] **Step 2: Test manually** — with two `pending` patches, call:

```javascript
fetch(`/books/1/light-tts-generate-all`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({backend: 'edge-tts'})
}).then(r => r.json()).then(console.log)
```

Expected: `{results: [{patch_id:1,status:"done"},{patch_id:2,status:"done"}]}`

- [ ] **Step 3: Commit**

```bash
git add app/routes/text_studio.py
git commit -m "feat: add light-tts-generate-all batch route"
```

---

## Task 4: Preview tmp dir at startup + serve route

**Files:**
- Modify: `app/main.py`
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Produces: `GET /preview-tmp/{filename}` → serves WAV file from `{data_root}/preview_tmp/`

- [ ] **Step 1: Create preview_tmp dir at startup** in `app/main.py` lifespan, after `db.init_schema(conn)`:

```python
from pathlib import Path as _Path
_Path(settings.data_root, "preview_tmp").mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Add serve route** in `app/routes/text_studio.py`

```python
import re as _re

_SAFE_PREVIEW_NAME = _re.compile(r'^[\w\-]+\.wav$')

@router.get("/preview-tmp/{filename}")
def serve_preview_tmp(filename: str):
    if not _SAFE_PREVIEW_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    p = Path(settings.data_root) / "preview_tmp" / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), media_type="audio/wav")
```

Note: `re` is already imported as `re` in `text_studio.py`. Use the existing import, don't add `_re` alias — just `import re` at top if not present.

- [ ] **Step 3: Verify** — restart app, check `data/preview_tmp/` directory exists.

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/routes/text_studio.py
git commit -m "feat: create preview_tmp dir at startup and add serve route"
```

---

## Task 5: SSE preview-stream route

**Files:**
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Produces: `GET /books/{book_id}/text-studio/patches/{patch_id}/preview-stream?backend=&voice=&with_effects=0` → `text/event-stream`
- SSE event format: `data: {"type":"chunk","index":0,"total":5,"url":"/preview-tmp/..."}\n\n`
- Final event: `data: {"type":"done","saved":true}\n\n`
- Error event: `data: {"type":"error","message":"..."}\n\n`

- [ ] **Step 1: Add route** in `app/routes/text_studio.py`

```python
import json
import uuid as _uuid
from starlette.responses import StreamingResponse

@router.get("/books/{book_id}/text-studio/patches/{patch_id}/preview-stream")
async def preview_stream(
    request: Request,
    book_id: int,
    patch_id: int,
    backend: str = "",
    voice: str = "",
    with_effects: int = 0,
):
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)

    _backend = backend or settings.light_tts_backend
    _voice = voice or settings.light_tts_voice
    _with_effects = bool(with_effects)
    db_lock = request.app.state.db_lock
    conn_ref = request.app.state.conn

    async def _generate():
        from app.chunker import split_into_tts_chunks
        from app import audio_merge, repository as repo
        tmp_dir = Path(settings.data_root) / "preview_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # cleanup old tmp files for this patch
        for old in tmp_dir.glob(f"{patch_id}_*.wav"):
            old.unlink(missing_ok=True)

        session_token = _uuid.uuid4().hex[:8]
        chunks = split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars)
        total = len(chunks)
        tmp_paths = []

        try:
            engine = LightTTSEngine(backend=_backend, voice=_voice or None)
        except RuntimeError as exc:
            yield f"data: {json.dumps({'type':'error','message':str(exc)})}\n\n"
            return

        for i, chunk_text in enumerate(chunks):
            try:
                wav_bytes, _ = await asyncio.to_thread(
                    engine.synthesize_to_wav_bytes, chunk_text, _voice or None
                )
            except Exception as exc:
                yield f"data: {json.dumps({'type':'error','message':f'chunk {i} failed: {exc}'})}\n\n"
                return

            tmp_name = f"{patch_id}_{session_token}_{i}.wav"
            tmp_path = tmp_dir / tmp_name
            tmp_path.write_bytes(wav_bytes)
            tmp_paths.append(str(tmp_path))

            event = {"type": "chunk", "index": i, "total": total, "url": f"/preview-tmp/{tmp_name}"}
            yield f"data: {json.dumps(event)}\n\n"

        # merge + effects + save
        try:
            book_dir = Path(settings.data_root) / "books" / str(book_id) / "patches"
            book_dir.mkdir(parents=True, exist_ok=True)
            audio_path = str(book_dir / f"{patch_id}.wav")
            await asyncio.to_thread(audio_merge.merge_chunk_files_to_patch, tmp_paths, audio_path)

            if _with_effects:
                merged_bytes = Path(audio_path).read_bytes()
                with db_lock:
                    mixed = _mix_effects(merged_bytes, text, conn_ref)
                Path(audio_path).write_bytes(mixed)

            with db_lock:
                repo.mark_patch_done(conn_ref, patch_id, audio_path)

            yield f"data: {json.dumps({'type':'done','saved':True})}\n\n"
        except Exception as exc:
            logger.exception("preview_stream merge/save failed for patch %s", patch_id)
            yield f"data: {json.dumps({'type':'error','message':str(exc)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")
```

- [ ] **Step 2: Test with curl** — start app, then:

```bash
curl -N "http://localhost:8000/books/1/text-studio/patches/1/preview-stream?backend=edge-tts&voice=vi-VN-HoaiMyNeural"
```

Expected: stream of `data: {"type":"chunk",...}` lines, ending with `data: {"type":"done","saved":true}`.

- [ ] **Step 3: Commit**

```bash
git add app/routes/text_studio.py
git commit -m "feat: add SSE preview-stream route for LightTTS streaming"
```

---

## Task 6: Text Studio UI — replace previewPatch + add Generate button

**Files:**
- Modify: `app/templates/text_studio.html`

**Interfaces:**
- Consumes: `GET /preview-stream` (Task 5), `POST /light-tts-generate` (Task 2)

- [ ] **Step 1: Add Generate LightTTS button** — in `app/templates/text_studio.html`, find the `ts-actions` div (line ~167-177). Add button after `btnPreviewPatch`:

```html
<button class="btn-outline btn-sm" id="btnGenerateLightTTS" onclick="generateLightTTS()">💾 Generate LightTTS</button>
```

- [ ] **Step 2: Replace `previewPatch()` function** — find `async function previewPatch()` (line ~711) and replace the entire function:

```javascript
async function previewPatch() {
    if (!currentPatchId) return;
    const btn = document.getElementById('btnPreviewPatch');
    btn.disabled = true;
    btn.textContent = 'Chunk 0/...';

    const backend = document.getElementById('ttsBackendSelect').value;
    const voice = document.getElementById('ttsVoiceSelect').value;
    const withFx = document.getElementById('fxToggle').checked ? 1 : 0;

    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let nextStartTime = ctx.currentTime + 0.05;

    const params = new URLSearchParams({
        backend: backend || '',
        voice: voice || '',
        with_effects: withFx,
    });
    const url = `/books/${bookId}/text-studio/patches/${currentPatchId}/preview-stream?${params}`;
    const es = new EventSource(url);

    es.onmessage = async (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }

        if (msg.type === 'chunk') {
            btn.textContent = `Chunk ${msg.index + 1}/${msg.total}...`;
            try {
                const resp = await fetch(msg.url);
                const buf = await resp.arrayBuffer();
                const audio = await ctx.decodeAudioData(buf);
                const src = ctx.createBufferSource();
                src.buffer = audio;
                src.connect(ctx.destination);
                const startAt = Math.max(nextStartTime, ctx.currentTime);
                src.start(startAt);
                nextStartTime = startAt + audio.duration;
            } catch (_) {}
        } else if (msg.type === 'done') {
            es.close();
            btn.disabled = false;
            btn.textContent = '🔊 Preview nhanh';
            document.getElementById('mediaPreview').style.display = '';
            const item = document.querySelector(`.ts-patch-item[data-patch-id="${currentPatchId}"]`);
            if (item) {
                const dot = item.querySelector('.dot');
                if (dot) { dot.className = 'dot done'; }
            }
        } else if (msg.type === 'error') {
            es.close();
            btn.disabled = false;
            btn.textContent = '🔊 Preview nhanh';
            alert('Lỗi preview: ' + msg.message);
        }
    };
    es.onerror = () => {
        es.close();
        btn.disabled = false;
        btn.textContent = '🔊 Preview nhanh';
    };
}
```

- [ ] **Step 3: Add `generateLightTTS()` function** — add after `previewPatch()`:

```javascript
async function generateLightTTS() {
    if (!currentPatchId) return;
    const btn = document.getElementById('btnGenerateLightTTS');
    btn.disabled = true;
    btn.textContent = 'Đang generate...';
    const backend = document.getElementById('ttsBackendSelect').value;
    const voice = document.getElementById('ttsVoiceSelect').value;
    const withFx = document.getElementById('fxToggle').checked;
    try {
        const res = await fetch(
            `/books/${bookId}/patches/${currentPatchId}/light-tts-generate`,
            { method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({backend: backend||undefined, voice: voice||undefined, with_effects: withFx}) }
        );
        const data = await res.json();
        if (!res.ok) { alert('Lỗi: ' + (data.detail || res.status)); return; }
        const item = document.querySelector(`.ts-patch-item[data-patch-id="${currentPatchId}"]`);
        if (item) {
            const dot = item.querySelector('.dot');
            if (dot) { dot.className = 'dot done'; }
        }
    } catch (e) {
        alert('Lỗi kết nối');
    } finally {
        btn.disabled = false;
        btn.textContent = '💾 Generate LightTTS';
    }
}
```

- [ ] **Step 4: Manual browser test** — open Text Studio, click "🔊 Preview nhanh", verify audio starts playing within seconds of first chunk. Click "💾 Generate LightTTS", verify patch dot turns green.

- [ ] **Step 5: Commit**

```bash
git add app/templates/text_studio.html
git commit -m "feat: streaming preview and generate LightTTS UI in Text Studio"
```

---

## Task 7: Book Detail UI — per-patch + Run All LightTTS

**Files:**
- Modify: `app/templates/book_detail.html`

**Interfaces:**
- Consumes: `POST /books/{book_id}/patches/{patch_id}/light-tts-generate` (Task 2), `POST /books/{book_id}/light-tts-generate-all` (Task 3)

- [ ] **Step 1: Find the patches table** in `app/templates/book_detail.html` — search for `patch-status-` to find the patches section (around the `#patches-card` section). Find the Actions column for each patch row.

- [ ] **Step 2: Add per-patch LightTTS button** — in the actions cell of each patch row in the patches table, add:

```html
<button type="button" class="btn-outline btn-sm"
    onclick="runLightTTSPatch({{ p.id }}, this)"
    {% if p.status == 'processing' %}disabled{% endif %}>
    ▶ LightTTS
</button>
```

- [ ] **Step 3: Add "Run All LightTTS" button** — find the `#patches-card` header (card-header div), add after existing buttons:

```html
<button type="button" class="btn-outline btn-sm" id="btnRunAllLightTTS" onclick="runAllLightTTS()">
    ▶ Run All LightTTS
</button>
```

- [ ] **Step 4: Add JS functions** — add near the end of the `<script>` block in `book_detail.html`, before the closing `</script>`:

```javascript
async function runLightTTSPatch(patchId, btn) {
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '...';
    try {
        const res = await fetch(`/books/${BOOK_ID}/patches/${patchId}/light-tts-generate`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (!res.ok) { alert('Lỗi: ' + (data.detail || res.status)); btn.disabled = false; btn.textContent = origText; return; }
        const statusEl = document.getElementById(`patch-status-${patchId}`);
        if (statusEl) statusEl.innerHTML = '<span class="badge badge-done">Done</span>';
    } catch (e) {
        alert('Lỗi kết nối');
        btn.disabled = false;
        btn.textContent = origText;
    }
}

async function runAllLightTTS() {
    const btn = document.getElementById('btnRunAllLightTTS');
    btn.disabled = true;
    const origText = btn.textContent;
    try {
        const res = await fetch(`/books/${BOOK_ID}/light-tts-generate-all`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (!res.ok) { alert('Lỗi: ' + (data.detail || res.status)); return; }
        let done = 0, errors = 0;
        for (const r of (data.results || [])) {
            if (r.status === 'done') {
                done++;
                const el = document.getElementById(`patch-status-${r.patch_id}`);
                if (el) el.innerHTML = '<span class="badge badge-done">Done</span>';
            } else if (r.status === 'error') {
                errors++;
            }
        }
        btn.textContent = `${done} done${errors ? ', ' + errors + ' lỗi' : ''}`;
    } catch (e) {
        alert('Lỗi kết nối');
    } finally {
        btn.disabled = false;
    }
}
```

- [ ] **Step 5: Manual test** — open book detail, click "▶ LightTTS" on one pending patch, verify badge turns Done. Click "▶ Run All LightTTS", verify all pending patches process sequentially.

- [ ] **Step 6: Commit**

```bash
git add app/templates/book_detail.html
git commit -m "feat: add LightTTS per-patch and Run All buttons to book detail"
```

---

## Self-Review Notes

- **Spec coverage check:** All 5 spec sections covered: LightTTS Generate (Tasks 1-3), Preview Streaming (Tasks 4-5), mix effects post-merge (Tasks 1+5), Text Studio UI (Task 6), Book Detail UI (Task 7), `preview_tmp` dir (Task 4).
- **effects**: Applied after merge in both `_light_synthesize_patch` (Task 1) and `preview-stream` (Task 5) — correct per spec.
- **text param in SSE**: Uses DB text (not URL param) — avoids URL length limits, consistent with `get_effective_patch_text`.
- **Concurrency guard**: Route checks `patch.status == "processing"` and returns 409 before starting `asyncio.to_thread`.
- **`generate-all` route prefix**: Route is `/books/{book_id}/light-tts-generate-all` — no `text-studio` prefix, registered on `text_studio.router` which has no prefix in `main.py`. Correct.

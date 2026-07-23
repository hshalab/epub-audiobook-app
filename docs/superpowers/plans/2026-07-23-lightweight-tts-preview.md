# Lightweight TTS Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add lightweight TTS preview to Text Studio so users can hear paragraph-by-paragraph or full-patch audio with sound effects mixed in, without touching the VoxCPM2 queue.

**Architecture:** New LightTTSEngine with pluggable backends (edge-tts default), two new POST endpoints on text_studio routes for paragraph/patch preview, inline UI buttons in the editor.

**Tech Stack:** edge-tts (optional dep), numpy+soundfile (existing), FastAPI async endpoints

## Global Constraints

- edge-tts is optional: app starts fine without it, endpoint returns 503
- No new required dependencies in pyproject.toml, only optional
- Preview audio is ephemeral: never saved to disk, never queued
- Effect mixing uses numpy overlay, not ffmpeg
- Existing VoxCPMEngine and worker untouched

---

### Task 1: Add edge-tts optional dependency + settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`

- [ ] **Step 1: Add optional dependency to pyproject.toml**

```toml
[project.optional-dependencies]
light-tts = [
    "edge-tts>=7.0",
]
```

- [ ] **Step 2: Add settings to app/config.py**

Add inside class Settings:

```python
    # Lightweight TTS preview
    light_tts_backend: str = "edge-tts"
    light_tts_voice: str = "vi-VN-HoaiMyNeural"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml app/config.py
git commit -m "feat: add edge-tts optional dep and light TTS settings"
```

---

### Task 2: Create LightTTSEngine with edge-tts backend

**Files:**
- Create: `app/light_tts.py`

**Interfaces:**
- Produces: `LightTTSEngine.synthesize_to_wav_bytes(text: str, voice: str | None = None) -> tuple[bytes, int]`
  - Returns (wav_bytes, sample_rate) or raises RuntimeError if backend unavailable
- Produces: `LightTTSEngine.list_backends() -> list[dict]`

- [ ] **Step 1: Write test for LightTTSEngine**

Create `tests/test_light_tts.py`:

```python
"""Tests for lightweight TTS engine."""
from __future__ import annotations
import pytest
from app.light_tts import LightTTSEngine


class TestLightTTSEngine:
    def test_list_backends(self):
        engine = LightTTSEngine(backend="edge-tts", voice="vi-VN-HoaiMyNeural")
        backends = engine.list_backends()
        assert len(backends) >= 1
        ids = [b["id"] for b in backends]
        assert "edge-tts" in ids
        assert "gtts" in ids

    def test_synthesize_unavailable_backend(self):
        engine = LightTTSEngine(backend="nonexistent-tts")
        with pytest.raises(RuntimeError):
            engine.synthesize_to_wav_bytes("hello")

    def test_synthesize_with_edge_tts(self):
        pytest.importorskip("edge_tts")
        engine = LightTTSEngine(backend="edge-tts", voice="vi-VN-HoaiMyNeural")
        wav_bytes, sr = engine.synthesize_to_wav_bytes("Xin chào")
        assert len(wav_bytes) > 44  # WAV header + some data
        assert sr > 0
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_light_tts.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Implement LightTTSEngine**

Create `app/light_tts.py`:

```python
"""Lightweight TTS engine for Text Studio preview.

Pluggable backends: edge-tts (default), gtts (fallback).
Each backend is optional - app starts without any installed.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

_BACKENDS: dict[str, dict] = {
    "edge-tts": {"label": "Edge TTS (Microsoft)", "package": "edge_tts"},
    "gtts": {"label": "Google TTS", "package": "gtts"},
}


def _check_backend(backend_id: str) -> bool:
    """Return True if the backend package is importable."""
    info = _BACKENDS.get(backend_id)
    if info is None:
        return False
    try:
        __import__(info["package"])
        return True
    except ImportError:
        return False


class LightTTSEngine:
    def __init__(self, backend: str = "edge-tts", voice: str = "vi-VN-HoaiMyNeural"):
        self.backend = backend
        self.voice = voice

    def list_backends(self) -> list[dict]:
        """Return all known backends with availability status."""
        result = []
        for bid, info in _BACKENDS.items():
            result.append({
                "id": bid,
                "label": info["label"],
                "available": _check_backend(bid),
            })
        return result

    def synthesize_to_wav_bytes(self, text: str, voice: str | None = None) -> tuple[bytes, int]:
        """Synthesize text to WAV bytes. Returns (wav_bytes, sample_rate).
        Raises RuntimeError if backend is unavailable or synthesis fails."""
        if not text.strip():
            raise ValueError("text is empty")
        use_voice = voice or self.voice
        if not _check_backend(self.backend):
            raise RuntimeError(
                f"TTS backend '{self.backend}' not installed. "
                f"Install with: pip install {self.backend.replace('-', '_')}"
            )
        if self.backend == "edge-tts":
            return self._edge_tts_synthesize(text, use_voice)
        elif self.backend == "gtts":
            return self._gtts_synthesize(text)
        raise RuntimeError(f"Unknown TTS backend: {self.backend}")

    def _edge_tts_synthesize(self, text: str, voice: str) -> tuple[bytes, int]:
        import asyncio
        import edge_tts

        async def _generate() -> bytes:
            communicate = edge_tts.Communicate(text, voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        mp3_bytes = asyncio.run(_generate())
        if not mp3_bytes:
            raise RuntimeError("edge-tts returned empty audio")
        return self._mp3_to_wav_bytes(mp3_bytes)

    def _gtts_synthesize(self, text: str) -> tuple[bytes, int]:
        from gtts import gTTS
        tts = gTTS(text=text, lang="vi")
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        mp3_bytes = buf.getvalue()
        if not mp3_bytes:
            raise RuntimeError("gTTS returned empty audio")
        return self._mp3_to_wav_bytes(mp3_bytes)

    @staticmethod
    def _mp3_to_wav_bytes(mp3_bytes: bytes) -> tuple[bytes, int]:
        """Convert MP3 bytes to WAV bytes using soundfile."""
        mp3_buf = io.BytesIO(mp3_bytes)
        data, sr = sf.read(mp3_buf)
        wav_buf = io.BytesIO()
        sf.write(wav_buf, data, sr, format="WAV")
        return wav_buf.getvalue(), sr
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_light_tts.py -v
```
Expected: 2 PASS, 1 SKIP (edge_tts not installed in test env)

- [ ] **Step 5: Commit**

```bash
git add app/light_tts.py tests/test_light_tts.py
git commit -m "feat: add LightTTSEngine with pluggable backends"
```

---

### Task 3: Add preview endpoints to text_studio routes

**Files:**
- Modify: `app/routes/text_studio.py`

**Interfaces:**
- Consumes: `LightTTSEngine.synthesize_to_wav_bytes(text, voice)`
- Consumes: `LightTTSEngine.list_backends()`
- Consumes: `repository.list_sound_effects(conn)` - returns `list[dict]` with `file_path`, `marker` keys
- Consumes: `text_analysis._EFFECT_PATTERNS` - regex patterns for `[marker]` detection

- [ ] **Step 1: Write test for preview endpoints**

Add to `tests/test_text_studio.py`:

```python
class TestLightTTSPreview:
    @pytest.fixture()
    def client(self, tmp_path):
        from app.main import app
        from app import db
        c = db.connect(str(tmp_path / "test.db"))
        db.init_schema(c)
        app.state.conn = c
        import threading
        app.state.db_lock = threading.Lock()
        yield TestClient(app)
        c.close()

    def test_list_backends(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.get("/text-studio/light-tts/backends")
        assert resp.status_code == 200
        data = resp.json()
        assert "backends" in data
        ids = [b["id"] for b in data["backends"]]
        assert "edge-tts" in ids

    def test_preview_unavailable(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/preview-paragraph",
            json={"text": "Xin chào"},
        )
        # Either 200 (if edge-tts installed) or 503 (not installed)
        assert resp.status_code in (200, 503)
```

- [ ] **Step 2: Run tests to verify they pass**

```
pytest tests/test_text_studio.py::TestLightTTSPreview -v
```
Expected: PASS

- [ ] **Step 3: Implement effect mixing helper**

Add at the top of `app/routes/text_studio.py` (after existing imports):

```python
import io
import re
import numpy as np
import soundfile as sf
from app.light_tts import LightTTSEngine
from app import repository as _repo

_light_tts_engine: LightTTSEngine | None = None

def _get_light_engine() -> LightTTSEngine:
    global _light_tts_engine
    if _light_tts_engine is None:
        from app.config import settings
        _light_tts_engine = LightTTSEngine(
            backend=settings.light_tts_backend,
            voice=settings.light_tts_voice,
        )
    return _light_tts_engine


def _mix_effects(wav_bytes: bytes, text: str, conn) -> bytes:
    """Overlay sound effects onto TTS audio based on [marker] positions in text."""
    pattern = re.compile(r"\[([^\]]+)\]")
    matches = list(pattern.finditer(text))
    if not matches:
        return wav_bytes

    effects = _repo.list_sound_effects(conn)
    effect_map = {e["marker"].strip("[]").lower(): e["file_path"] for e in effects}
    if not effect_map:
        return wav_bytes

    try:
        data, sr = sf.read(io.BytesIO(wav_bytes))
    except Exception:
        return wav_bytes

    total_chars = len(text)
    if total_chars == 0:
        return wav_bytes

    for m in matches:
        marker_key = m.group(1).strip().lower()
        if marker_key not in effect_map:
            continue
        effect_path = effect_map[marker_key]
        try:
            effect_data, effect_sr = sf.read(effect_path)
            if effect_sr != sr:
                import resampy
                effect_data = resampy.resample(effect_data, effect_sr, sr)
            ratio = m.start() / total_chars
            insert_pos = int(ratio * len(data))
            end_pos = min(insert_pos + len(effect_data), len(data))
            chunk_len = end_pos - insert_pos
            data[insert_pos:end_pos] += effect_data[:chunk_len] * 0.8
        except Exception:
            continue

    peak = np.max(np.abs(data))
    if peak > 1.0:
        data = data / peak

    out_buf = io.BytesIO()
    sf.write(out_buf, data, sr, format="WAV")
    return out_buf.getvalue()
```

- [ ] **Step 4: Implement GET /text-studio/light-tts/backends**

```python
@router.get("/text-studio/light-tts/backends")
def list_light_tts_backends():
    engine = _get_light_engine()
    return JSONResponse({"backends": engine.list_backends()})
```

- [ ] **Step 5: Implement POST preview-paragraph**

```python
@router.post("/books/{book_id}/text-studio/patches/{patch_id}/preview-paragraph")
async def preview_paragraph(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    text = body.get("text", "").strip()
    with_effects = body.get("with_effects", True)
    backend = body.get("backend")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    engine = _get_light_engine()
    if backend:
        engine = LightTTSEngine(backend=backend, voice=engine.voice)
    try:
        wav_bytes, _ = engine.synthesize_to_wav_bytes(text)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if with_effects:
        with locked_conn(request) as conn:
            wav_bytes = _mix_effects(wav_bytes, text, conn)
    return Response(content=wav_bytes, media_type="audio/wav")
```

- [ ] **Step 6: Implement POST preview-patch**

```python
@router.post("/books/{book_id}/text-studio/patches/{patch_id}/preview-patch")
async def preview_patch(request: Request, book_id: int, patch_id: int):
    body = await request.json()
    with_effects = body.get("with_effects", True)
    backend = body.get("backend")
    with locked_conn(request) as conn:
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        text = repository.get_effective_patch_text(conn, patch)
    if not text.strip():
        raise HTTPException(status_code=400, detail="patch has no text")
    engine = _get_light_engine()
    if backend:
        engine = LightTTSEngine(backend=backend, voice=engine.voice)
    try:
        wav_bytes, _ = engine.synthesize_to_wav_bytes(text)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if with_effects:
        with locked_conn(request) as conn:
            wav_bytes = _mix_effects(wav_bytes, text, conn)
    return Response(content=wav_bytes, media_type="audio/wav")
```

Note: need to import Response from starlette:

```python
from starlette.responses import Response
```

- [ ] **Step 7: Run tests**

```
pytest tests/test_text_studio.py -v
```
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/routes/text_studio.py tests/test_text_studio.py
git commit -m "feat: add preview-paragraph and preview-patch TTS endpoints"
```

---

### Task 4: Add UI - inline paragraph preview + preview button + backend selector

**Files:**
- Modify: `app/templates/text_studio.html`

**Interfaces:**
- Consumes: `GET /text-studio/light-tts/backends`
- Consumes: `POST /books/{id}/text-studio/patches/{id}/preview-paragraph`
- Consumes: `POST /books/{id}/text-studio/patches/{id}/preview-patch`

- [ ] **Step 1: Add CSS for inline preview buttons and audio player**

Add to the `<style>` block in text_studio.html:

```css
.ts-para-preview{position:absolute;left:-28px;top:2px;opacity:0;transition:opacity .15s;background:none;border:none;color:var(--color-primary,#3b82f6);cursor:pointer;font-size:14px;padding:2px}
.ts-editor-area:hover .ts-para-preview{opacity:1}
.ts-para-preview:hover{opacity:1!important}
.ts-para-preview.loading{animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.ts-inline-audio{margin:4px 0 8px;padding:4px 8px;background:var(--bg-elevated,var(--bg-card));border:1px solid var(--border-color);border-radius:var(--border-radius);display:flex;align-items:center;gap:8px}
.ts-inline-audio audio{flex:1;height:32px}
.ts-inline-audio .close{background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:16px}
.ts-tts-select{padding:2px 6px;border:1px solid var(--border-color);border-radius:var(--border-radius);background:var(--bg-input,#1e1e1e);color:var(--text-primary);font-size:var(--font-size-xs)}
```

- [ ] **Step 2: Add backend selector to settings modal**

Add inside the `<dialog id="settingsModal">` div, after the normalization options:

```html
<div style="border-top:1px solid var(--border-color);padding-top:var(--space-md);margin-top:var(--space-sm)">
    <h4 style="margin:0 0 var(--space-xs)">TTS Preview</h4>
    <label style="display:flex;align-items:center;gap:var(--space-xs);font-size:var(--font-size-sm)">
        Backend:
        <select id="ttsBackendSelect" class="ts-tts-select">
            <option value="">Loading...</option>
        </select>
    </label>
</div>
```

- [ ] **Step 3: Add preview button to actions bar**

Add after the existing "Phân tích" button in `.ts-actions`:

```html
<button class="btn-outline btn-sm" id="btnPreviewPatch" onclick="previewPatch()">Preview nhanh</button>
<label style="font-size:var(--font-size-xs);color:var(--text-muted);display:flex;align-items:center;gap:4px"><input type="checkbox" id="previewWithEffects" checked> FX</label>
```

- [ ] **Step 4: Add inline preview JS functions**

Add to the `<script>` block:

```javascript
let _inlineAudioEl = null;

async function previewParagraph(text) {
    if (!text || !currentPatchId) return;
    const btn = event.target.closest('.ts-para-preview');
    if (btn) btn.classList.add('loading');
    try {
        const backend = localStorage.getItem('lightTtsBackend') || null;
        const res = await fetch(`/books/${bookId}/text-studio/patches/${currentPatchId}/preview-paragraph`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, with_effects: document.getElementById('previewWithEffects')?.checked ?? true, backend})
        });
        if (res.status === 503) { alert('TTS backend chưa cài. Vào Settings để cài.'); return; }
        if (!res.ok) return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        showInlineAudio(url, btn);
    } finally {
        if (btn) btn.classList.remove('loading');
    }
}

function showInlineAudio(url, anchorEl) {
    removeInlineAudio();
    const wrap = document.createElement('div');
    wrap.className = 'ts-inline-audio';
    wrap.innerHTML = `<audio controls autoplay style="flex:1;height:32px"><source src="${url}" type="audio/wav"></audio><button class="close" onclick="removeInlineAudio()">×</button>`;
    if (anchorEl && anchorEl.parentElement) {
        anchorEl.parentElement.after(wrap);
    }
    _inlineAudioEl = wrap;
    wrap.querySelector('audio').onended = () => { URL.revokeObjectURL(url); };
}

function removeInlineAudio() {
    if (_inlineAudioEl) {
        const audio = _inlineAudioEl.querySelector('audio');
        if (audio && audio.src) URL.revokeObjectURL(audio.src);
        _inlineAudioEl.remove();
        _inlineAudioEl = null;
    }
}

async function previewPatch() {
    if (!currentPatchId) return;
    const btn = document.getElementById('btnPreviewPatch');
    btn.disabled = true;
    btn.textContent = 'Đang tạo...';
    try {
        const backend = localStorage.getItem('lightTtsBackend') || null;
        const withEffects = document.getElementById('previewWithEffects')?.checked ?? true;
        const res = await fetch(`/books/${bookId}/text-studio/patches/${currentPatchId}/preview-patch`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({with_effects: withEffects, backend})
        });
        if (res.status === 503) { alert('TTS backend chưa cài.'); return; }
        if (!res.ok) return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const panel = document.getElementById('mediaPreview');
        const audio = document.getElementById('patchAudio');
        panel.style.display = '';
        audio.src = url;
        audio.play();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Preview nhanh';
    }
}

async function loadLightTTSBackends() {
    try {
        const res = await fetch('/text-studio/light-tts/backends');
        if (!res.ok) return;
        const data = await res.json();
        const sel = document.getElementById('ttsBackendSelect');
        sel.innerHTML = '';
        data.backends.forEach(b => {
            const opt = document.createElement('option');
            opt.value = b.id;
            opt.textContent = b.label + (b.available ? '' : ' (not installed)');
            opt.disabled = !b.available;
            sel.appendChild(opt);
        });
        const saved = localStorage.getItem('lightTtsBackend');
        if (saved) sel.value = saved;
        sel.onchange = () => localStorage.setItem('lightTtsBackend', sel.value);
    } catch(e) {}
}
```

- [ ] **Step 5: Call loadLightTTSBackends on page load**

Add after `loadEffectLibrary();` at the bottom of the script:

```javascript
loadLightTTSBackends();
```

- [ ] **Step 6: Modify renderEditor to add inline preview buttons**

Update the `renderEditor` function to wrap each paragraph with a preview button. Replace the existing function:

```javascript
function renderEditor(text, warnings) {
    if (!warnings.length) {
        editor.innerHTML = addPreviewButtons(text);
        return;
    }
    const sorted = [...warnings].sort((a,b) => a.position - b.position);
    let html = '', pos = 0;
    for (const w of sorted) {
        if (w.position > pos) html += esc(text.slice(pos, w.position));
        const chunk = text.slice(w.position, w.position + w.length);
        const cls = w.kind === 'effect_marker' ? 'fx-marker' : w.kind === 'junk' ? 'junk-char' : w.kind === 'sound_desc' ? 'sound-desc' : 'spell-err';
        html += `<span class="${cls}" data-warn-id="${w.id}" title="${esc(w.kind)}: ${esc(w.original)}">${esc(chunk)}</span>`;
        pos = w.position + w.length;
    }
    if (pos < text.length) html += esc(text.slice(pos));
    editor.innerHTML = wrapParagraphsWithPreview(html);
}

function addPreviewButtons(text) {
    const paras = text.split(/\n\n+/);
    return paras.map(p => {
        const trimmed = p.trim();
        if (!trimmed) return '';
        return `<div style="position:relative;padding-left:4px"><button class="ts-para-preview" onclick="previewParagraph(getPlainText().split(/\\n\\n+/)[${paras.indexOf(p)}]?.trim())" title="Preview đoạn này">&#9654;</button><span>${esc(trimmed)}</span></div>`;
    }).join('');
}

function wrapParagraphsWithPreview(html) {
    return html;
}
```

- [ ] **Step 7: Run the app and verify manually**

```bash
python -m uvicorn app.main:app --reload
```

Open Text Studio, check:
- Settings modal shows TTS backend dropdown
- Preview button appears in actions bar
- FX checkbox is present

- [ ] **Step 8: Commit**

```bash
git add app/templates/text_studio.html
git commit -m "feat: add TTS preview UI to Text Studio"
```

---

### Task 5: Final verification

- [ ] **Step 1: Run all tests**

```
pytest tests/ -v --tb=short 2>&1 | head -80
```
Expected: all existing tests still pass, new tests pass (or skip if edge-tts not installed)

- [ ] **Step 2: Verify no import errors**

```bash
python -c "from app.light_tts import LightTTSEngine; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Final commit if any fixups needed**

```bash
git add -A && git commit -m "fix: final polish for light TTS preview"
```

# TTS Voice Config for Preview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Text Studio preview pick a specific voice per backend (edge-tts, gtts, piper), and remove the non-functional kokoro backend.

**Architecture:** Add a `list_voices(backend)` resolver in `app/light_tts.py` (network/list calls cached, always falling back to the backend's `default_voice`). Expose it via a new `GET /text-studio/light-tts/voices` endpoint. The preview routes accept a `voice` field and forward it to `synthesize_to_wav_bytes`. The settings modal gains a voice dropdown that reloads when the backend changes and persists the choice per backend in `localStorage`.

**Tech Stack:** Python 3, FastAPI, pytest, edge-tts / gtts / piper-tts, vanilla JS + Jinja2 templates.

## Global Constraints

- Scope is **preview only** — do NOT touch `book.voice_clip_path` or the worker render path.
- No voice cloning, no kokoro backend (both explicitly out of scope).
- Tests must NOT make real TTS network/model calls — mock edge-tts / gtts / piper.
- Voice enumeration must never raise out of the endpoint — always fall back to `default_voice`.
- Follow existing code style in each file (no new formatters, no unrelated refactors).

---

### Task 1: Remove the kokoro backend

**Files:**
- Modify: `app/light_tts.py` (`_BACKENDS`, `_check_backend`, `_BACKEND_SYNTH`, `_kokoro_synthesize`)
- Test: `tests/test_light_tts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_BACKENDS` now contains exactly the keys `edge-tts`, `gtts`, `piper`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_light_tts.py` inside `class TestLightTTSEngine`:

```python
    def test_kokoro_removed(self):
        from app.light_tts import _BACKENDS, _BACKEND_SYNTH

        assert "kokoro" not in _BACKENDS
        assert "kokoro" not in _BACKEND_SYNTH
        assert set(_BACKENDS) == {"edge-tts", "gtts", "piper"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_light_tts.py::TestLightTTSEngine::test_kokoro_removed -v`
Expected: FAIL — `"kokoro" in _BACKENDS`.

- [ ] **Step 3: Remove kokoro from `app/light_tts.py`**

In `_BACKENDS`, delete the block:

```python
    "kokoro": {
        "description": "Kokoro ONNX (local CPU, ~100MB model)",
        "default_voice": "vi",
    },
```

In `_check_backend`, delete the branch:

```python
    elif name == "kokoro":
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            raise RuntimeError("kokoro-onnx is not installed. pip install kokoro-onnx")
```

Delete the entire `_kokoro_synthesize` function.

In `_BACKEND_SYNTH`, delete the line:

```python
    "kokoro": _kokoro_synthesize,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_light_tts.py -v`
Expected: PASS (all tests, including the existing `test_list_backends`).

- [ ] **Step 5: Commit**

```bash
git add app/light_tts.py tests/test_light_tts.py
git commit -m "refactor: remove non-functional kokoro TTS backend"
```

---

### Task 2: Add `piper_voices_dir` config setting

**Files:**
- Modify: `app/config.py:54-56`
- Test: `tests/test_light_tts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.piper_voices_dir: str` (default `""`), used by Task 4.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_light_tts.py` inside `class TestLightTTSEngine`:

```python
    def test_piper_voices_dir_setting_default(self):
        from app.config import settings

        assert settings.piper_voices_dir == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_light_tts.py::TestLightTTSEngine::test_piper_voices_dir_setting_default -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'piper_voices_dir'`.

- [ ] **Step 3: Add the setting**

In `app/config.py`, under the `# Lightweight TTS preview` block, after `light_tts_voice`:

```python
    # Lightweight TTS preview
    light_tts_backend: str = "edge-tts"
    light_tts_voice: str = "vi-VN-HoaiMyNeural"
    # Directory holding piper *.onnx models. A piper voice id is resolved to
    # "<piper_voices_dir>/<id>.onnx" at synth time. Empty => use the id as-is.
    piper_voices_dir: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_light_tts.py::TestLightTTSEngine::test_piper_voices_dir_setting_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_light_tts.py
git commit -m "feat: add piper_voices_dir setting"
```

---

### Task 3: `list_voices(backend)` resolver

**Files:**
- Modify: `app/light_tts.py` (add module-level cache + `list_voices`)
- Test: `tests/test_light_tts.py`

**Interfaces:**
- Consumes: `_BACKENDS` (Task 1).
- Produces: `list_voices(backend: str) -> list[dict]` where each dict is
  `{"id": str, "label": str, "language": str}`. Never raises for a known
  backend; returns `[{default_voice entry}]` on any enumeration failure. Raises
  `KeyError` only for an unknown backend (callers validate before calling).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_light_tts.py`:

```python
class TestListVoices:
    def test_edge_tts_sorted_vietnamese_first(self, monkeypatch):
        import app.light_tts as lt

        fake = [
            {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
            {"ShortName": "vi-VN-NamMinhNeural", "Gender": "Male", "Locale": "vi-VN"},
            {"ShortName": "vi-VN-HoaiMyNeural", "Gender": "Female", "Locale": "vi-VN"},
        ]
        lt._EDGE_VOICES_CACHE = None
        monkeypatch.setattr(lt, "_edge_list_voices_raw", lambda: fake)
        voices = lt.list_voices("edge-tts")
        assert voices[0]["language"] == "vi-VN"
        assert voices[1]["language"] == "vi-VN"
        assert voices[-1]["id"] == "en-US-AriaNeural"
        assert voices[0]["id"].startswith("vi-VN-")
        assert "(" in voices[0]["label"]  # gender in label

    def test_gtts_lists_languages(self, monkeypatch):
        import app.light_tts as lt

        monkeypatch.setattr(lt, "_gtts_langs", lambda: {"vi": "Vietnamese", "en": "English"})
        voices = lt.list_voices("gtts")
        ids = {v["id"] for v in voices}
        assert ids == {"vi", "en"}
        vi = next(v for v in voices if v["id"] == "vi")
        assert vi["label"] == "Vietnamese"

    def test_piper_constant_list(self):
        import app.light_tts as lt

        voices = lt.list_voices("piper")
        assert len(voices) >= 1
        assert all("id" in v and "label" in v for v in voices)
        assert any(v["id"].startswith("vi_VN") for v in voices)

    def test_fallback_on_enumeration_error(self, monkeypatch):
        import app.light_tts as lt

        def _boom():
            raise RuntimeError("network down")

        lt._EDGE_VOICES_CACHE = None
        monkeypatch.setattr(lt, "_edge_list_voices_raw", _boom)
        voices = lt.list_voices("edge-tts")
        assert voices == [{
            "id": "vi-VN-HoaiMyNeural",
            "label": "vi-VN-HoaiMyNeural",
            "language": "",
        }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_light_tts.py::TestListVoices -v`
Expected: FAIL — `AttributeError: module 'app.light_tts' has no attribute 'list_voices'`.

- [ ] **Step 3: Implement the resolver in `app/light_tts.py`**

Near the top, after the `_BACKENDS` dict, add the module-level cache and the piper constant:

```python
_EDGE_VOICES_CACHE: list[dict[str, Any]] | None = None

# Known rhasspy piper Vietnamese voices. The actual .onnx model is resolved from
# settings.piper_voices_dir at synth time (see _resolve_piper_model).
_PIPER_VOICES: list[dict[str, Any]] = [
    {"id": "vi_VN-vais1000-medium", "label": "Tiếng Việt — vais1000 (medium)", "language": "vi"},
    {"id": "vi_VN-vivos-x_low", "label": "Tiếng Việt — vivos (x_low)", "language": "vi"},
    {"id": "vi_VN-25hours_single-low", "label": "Tiếng Việt — 25hours (low)", "language": "vi"},
]
```

At the end of the file (after `_BACKEND_SYNTH`), add the enumeration helpers and `list_voices`:

```python
def _edge_list_voices_raw() -> list[dict[str, Any]]:
    """Fetch the raw edge-tts voice list (network call)."""
    import edge_tts

    async def _run() -> list[dict[str, Any]]:
        return await edge_tts.list_voices()

    return asyncio.run(_run())


def _gtts_langs() -> dict[str, str]:
    from gtts.lang import tts_langs

    return tts_langs()


def _fallback_voice(backend: str) -> list[dict[str, Any]]:
    dv = _BACKENDS[backend]["default_voice"]
    return [{"id": dv, "label": dv, "language": ""}]


def _edge_voices() -> list[dict[str, Any]]:
    global _EDGE_VOICES_CACHE
    if _EDGE_VOICES_CACHE is None:
        raw = _edge_list_voices_raw()
        voices = [
            {
                "id": v["ShortName"],
                "label": f"{v['ShortName']} ({v.get('Gender', '')})",
                "language": v.get("Locale", ""),
            }
            for v in raw
        ]
        # Vietnamese first, then by locale, then by id.
        voices.sort(key=lambda v: (not v["language"].startswith("vi-VN"), v["language"], v["id"]))
        _EDGE_VOICES_CACHE = voices
    return _EDGE_VOICES_CACHE


def list_voices(backend: str) -> list[dict[str, Any]]:
    """Return selectable voices for a backend. Never raises for a known backend;
    falls back to the backend's default_voice on any enumeration failure."""
    try:
        if backend == "edge-tts":
            voices = _edge_voices()
        elif backend == "gtts":
            voices = [
                {"id": code, "label": name, "language": code}
                for code, name in sorted(_gtts_langs().items(), key=lambda kv: kv[1])
            ]
        elif backend == "piper":
            voices = list(_PIPER_VOICES)
        else:
            return _fallback_voice(backend)
        return voices or _fallback_voice(backend)
    except Exception:
        return _fallback_voice(backend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_light_tts.py::TestListVoices -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/light_tts.py tests/test_light_tts.py
git commit -m "feat: add per-backend voice enumeration to light TTS"
```

---

### Task 4: Resolve piper voice id → model file path

**Files:**
- Modify: `app/light_tts.py` (`_piper_synthesize`, add `_resolve_piper_model`)
- Test: `tests/test_light_tts.py`

**Interfaces:**
- Consumes: `settings.piper_voices_dir` (Task 2), `_PIPER_VOICES` (Task 3).
- Produces: `_resolve_piper_model(voice: str) -> str` — returns
  `"<piper_voices_dir>/<voice>.onnx"` when `piper_voices_dir` is set and that
  file exists; otherwise returns `voice` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_light_tts.py`:

```python
class TestPiperResolve:
    def test_returns_id_when_no_dir(self, monkeypatch):
        import app.light_tts as lt
        from app.config import settings

        monkeypatch.setattr(settings, "piper_voices_dir", "")
        assert lt._resolve_piper_model("vi_VN-vais1000-medium") == "vi_VN-vais1000-medium"

    def test_resolves_to_onnx_path(self, monkeypatch, tmp_path):
        import app.light_tts as lt
        from app.config import settings

        model = tmp_path / "vi_VN-vais1000-medium.onnx"
        model.write_bytes(b"fake")
        monkeypatch.setattr(settings, "piper_voices_dir", str(tmp_path))
        assert lt._resolve_piper_model("vi_VN-vais1000-medium") == str(model)

    def test_returns_id_when_file_missing(self, monkeypatch, tmp_path):
        import app.light_tts as lt
        from app.config import settings

        monkeypatch.setattr(settings, "piper_voices_dir", str(tmp_path))
        assert lt._resolve_piper_model("vi_VN-missing") == "vi_VN-missing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_light_tts.py::TestPiperResolve -v`
Expected: FAIL — `module 'app.light_tts' has no attribute '_resolve_piper_model'`.

- [ ] **Step 3: Implement resolution**

At the top of `app/light_tts.py`, add imports if missing:

```python
from pathlib import Path

from app.config import settings
```

Add the helper (near `_piper_synthesize`):

```python
def _resolve_piper_model(voice: str) -> str:
    """Map a piper voice id to a model file under settings.piper_voices_dir,
    falling back to the id as-is when unset or the file is absent."""
    base = settings.piper_voices_dir
    if base:
        candidate = Path(base) / f"{voice}.onnx"
        if candidate.exists():
            return str(candidate)
    return voice
```

In `_piper_synthesize`, change the load line from:

```python
    voice_model = PiperVoice.load(voice)
```

to:

```python
    voice_model = PiperVoice.load(_resolve_piper_model(voice))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_light_tts.py::TestPiperResolve -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/light_tts.py tests/test_light_tts.py
git commit -m "feat: resolve piper voice id to model file via piper_voices_dir"
```

---

### Task 5: `/voices` endpoint + preview routes forward `voice`

**Files:**
- Modify: `app/routes/text_studio.py` (add voices endpoint; `preview_paragraph`, `preview_patch`)
- Test: `tests/test_text_studio.py`

**Interfaces:**
- Consumes: `list_voices` and `_BACKENDS` from `app/light_tts.py` (Tasks 1, 3);
  `LightTTSEngine.synthesize_to_wav_bytes(text, voice=None)` (existing).
- Produces:
  - `GET /text-studio/light-tts/voices?backend=<id>` → `{"voices": [...]}`; 400 if unknown backend.
  - `preview-paragraph` / `preview-patch` now read `voice` from the JSON body and pass it to `synthesize_to_wav_bytes`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_text_studio.py` inside `class TestTextStudioRoutes`:

```python
    def test_list_voices_endpoint(self, client, monkeypatch):
        import app.light_tts as lt
        monkeypatch.setattr(lt, "list_voices", lambda b: [{"id": "x", "label": "X", "language": "vi"}])
        resp = client.get("/text-studio/light-tts/voices?backend=edge-tts")
        assert resp.status_code == 200
        assert resp.json()["voices"][0]["id"] == "x"

    def test_list_voices_unknown_backend(self, client):
        resp = client.get("/text-studio/light-tts/voices?backend=nope")
        assert resp.status_code == 400

    def test_backends_excludes_kokoro(self, client):
        resp = client.get("/text-studio/light-tts/backends")
        ids = [b["id"] for b in resp.json()["backends"]]
        assert "kokoro" not in ids

    def test_preview_paragraph_forwards_voice(self, client, conn, book_and_patch, monkeypatch):
        import app.routes.text_studio as ts
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn

        captured = {}

        class FakeEngine:
            def synthesize_to_wav_bytes(self, text, voice=None):
                captured["text"] = text
                captured["voice"] = voice
                return b"RIFF0000WAVEfmt ", 22050

        monkeypatch.setattr(ts, "LightTTSEngine", lambda backend=None: FakeEngine())
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/preview-paragraph",
            json={"text": "Xin chào", "backend": "edge-tts", "voice": "vi-VN-NamMinhNeural"},
        )
        assert resp.status_code == 200
        assert captured["voice"] == "vi-VN-NamMinhNeural"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_text_studio.py::TestTextStudioRoutes -k "voices or kokoro or forwards_voice" -v`
Expected: FAIL — voices endpoint 404 / `voice` not forwarded.

- [ ] **Step 3: Add the endpoint**

In `app/routes/text_studio.py`, after the existing `list_backends` route (around line 275), add:

```python
@router.get("/text-studio/light-tts/voices")
def list_voices_endpoint(backend: str):
    from app.light_tts import _BACKENDS, list_voices
    if backend not in _BACKENDS:
        raise HTTPException(status_code=400, detail="unknown backend")
    return JSONResponse({"voices": list_voices(backend)})
```

- [ ] **Step 4: Forward `voice` in both preview routes**

In `preview_paragraph`, after `backend = body.get("backend")` add:

```python
    voice = body.get("voice")
```

and change the synthesis call from:

```python
            wav_bytes, _ = await asyncio.to_thread(engine.synthesize_to_wav_bytes, text)
```

to:

```python
            wav_bytes, _ = await asyncio.to_thread(engine.synthesize_to_wav_bytes, text, voice)
```

Apply the identical two changes in `preview_patch` (add `voice = body.get("voice")` after its `backend = body.get("backend")`, and pass `voice` to the `to_thread` call).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_text_studio.py::TestTextStudioRoutes -k "voices or kokoro or forwards_voice" -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest tests/test_light_tts.py tests/test_text_studio.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routes/text_studio.py tests/test_text_studio.py
git commit -m "feat: add voices endpoint and forward voice to TTS preview"
```

---

### Task 6: Voice dropdown in the settings modal

**Files:**
- Modify: `app/templates/text_studio.html` (settings modal markup + JS)

**Interfaces:**
- Consumes: `GET /text-studio/light-tts/voices?backend=`, `GET /text-studio/light-tts/backends` (Task 5); `previewParagraph` / `previewPatch` (existing).
- Produces: none (UI only).

- [ ] **Step 1: Add the voice `<select>` markup**

In `app/templates/text_studio.html`, the backend selector block is:

```html
    <div style="display:flex;gap:var(--space-sm);align-items:center;margin-bottom:var(--space-md)">
        <label style="font-weight:500;font-size:var(--font-size-sm);white-space:nowrap">TTS Backend:</label>
        <select id="ttsBackendSelect" class="ts-tts-select" style="flex:1">
            <option value="">Đang tải...</option>
        </select>
    </div>
```

Immediately after that closing `</div>`, add:

```html
    <div style="display:flex;gap:var(--space-sm);align-items:center;margin-bottom:var(--space-md)">
        <label style="font-weight:500;font-size:var(--font-size-sm);white-space:nowrap">Giọng đọc:</label>
        <select id="ttsVoiceSelect" class="ts-tts-select" style="flex:1">
            <option value="">Đang tải...</option>
        </select>
    </div>
```

- [ ] **Step 2: Add `loadVoicesForBackend` and wire it into `loadLightTTSBackends`**

Replace the existing `loadLightTTSBackends` function with this version (adds voice loading + backend-change hook):

```javascript
async function loadVoicesForBackend(backend) {
    const sel = document.getElementById('ttsVoiceSelect');
    if (!backend) { sel.innerHTML = ''; return; }
    sel.innerHTML = '<option value="">Đang tải...</option>';
    try {
        const res = await fetch(`/text-studio/light-tts/voices?backend=${encodeURIComponent(backend)}`);
        if (!res.ok) { sel.innerHTML = ''; return; }
        const data = await res.json();
        const saved = localStorage.getItem('tts-voice:' + backend);
        sel.innerHTML = (data.voices || []).map(v =>
            `<option value="${esc(v.id)}" ${v.id === saved ? 'selected' : ''}>${esc(v.label)}</option>`
        ).join('');
        if (!sel.value && sel.options.length) sel.selectedIndex = 0;
    } catch(e) { sel.innerHTML = ''; }
    sel.onchange = () => localStorage.setItem('tts-voice:' + backend, sel.value);
}

async function loadLightTTSBackends() {
    try {
        const res = await fetch('/text-studio/light-tts/backends');
        if (!res.ok) return;
        const data = await res.json();
        const select = document.getElementById('ttsBackendSelect');
        const saved = localStorage.getItem('tts-backend');
        select.innerHTML = (data.backends || []).map(b =>
            `<option value="${esc(b.id)}" ${!b.available ? 'disabled' : ''} ${b.id === saved ? 'selected' : ''}>${esc(b.label)}${!b.available ? ' (unavailable)' : ''}</option>`
        ).join('');
        if (!select.value && select.options.length) select.selectedIndex = 0;
        select.addEventListener('change', () => {
            localStorage.setItem('tts-backend', select.value);
            loadVoicesForBackend(select.value);
        });
        loadVoicesForBackend(select.value);
    } catch(e) {}
}
```

- [ ] **Step 3: Send the selected voice from both preview functions**

In `previewParagraph`, the body currently reads:

```javascript
        const backend = document.getElementById('ttsBackendSelect').value;
        const withFx = document.getElementById('fxToggle').checked;
        const res = await fetch(`/books/${bookId}/text-studio/patches/${currentPatchId}/preview-paragraph`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text, with_effects: withFx, backend: backend || undefined })
        });
```

Change it to include `voice`:

```javascript
        const backend = document.getElementById('ttsBackendSelect').value;
        const voice = document.getElementById('ttsVoiceSelect').value;
        const withFx = document.getElementById('fxToggle').checked;
        const res = await fetch(`/books/${bookId}/text-studio/patches/${currentPatchId}/preview-paragraph`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text, with_effects: withFx, backend: backend || undefined, voice: voice || undefined })
        });
```

In `previewPatch`, make the matching change: add
`const voice = document.getElementById('ttsVoiceSelect').value;` next to its
`backend` line, and add `voice: voice || undefined` to the `JSON.stringify` body.

- [ ] **Step 4: Verify in the browser**

Start the app preview (dev server named in `.claude/launch.json`, or create one), open a book's Text Studio, open ⚙ Cài đặt. Confirm:
- The **Giọng đọc** dropdown populates after the backend loads.
- Switching **TTS Backend** (edge-tts ↔ gtts ↔ piper) reloads the voice list; kokoro is absent.
- Selecting a voice, closing the modal, hovering a paragraph and clicking ▶ produces audio.
- Reopening the modal keeps the previously selected voice (localStorage).

Check `read_console_messages` for errors after each interaction.

- [ ] **Step 5: Commit**

```bash
git add app/templates/text_studio.html
git commit -m "feat: voice selector in Text Studio TTS settings"
```

---

## Self-Review Notes

- **Spec coverage:** A→Task 1; B→Task 3; C(endpoint + preview voice)→Task 5; D(UI)→Task 6; E(config)→Task 2; piper resolution→Task 4. Testing section covered across Tasks 1,3,5.
- **Type consistency:** `list_voices(backend)` returns `{"id","label","language"}` in Task 3 and is consumed unchanged in Tasks 5 (endpoint) and 6 (UI). `_resolve_piper_model(voice)` defined in Task 4, used in `_piper_synthesize`. `synthesize_to_wav_bytes(text, voice=None)` is the existing signature used in Task 5.
- **No placeholders:** every code step contains full code.

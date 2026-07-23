# TTS Voice Config + Clone Voice Selection for Preview

Date: 2026-07-23
Status: Approved (design)

## Goal

Text Studio's preview currently lets the user pick only a *backend*
(edge-tts / gtts / kokoro / piper), each hard-coded to a single voice, and has
no way to preview with a cloned voice. This change:

1. Exposes a full **voice list per backend** so the user picks a specific voice.
2. Adds a **`clone` backend** (enabled only when VoxCPM is importable) whose
   "voices" are the reference clips in the voices library, so preview can use a
   cloned voice.

Scope is **preview only**. It does NOT change `book.voice_clip_path` or the
worker's real render path.

## Current State

- `app/light_tts.py` — `LightTTSEngine`, "no GPU" preview engine. `_BACKENDS`
  holds 4 backends, each with one `default_voice`. `synthesize_to_wav_bytes`
  already accepts a `voice` argument but the UI never sends one.
- `app/tts_engine.py` — `VoxCPMEngine`, the heavy GPU engine used by the worker.
  Supports cloning via `reference_wav_path` (a clip in `data/voices/`). Meant to
  run sequentially (worker only).
- `app/routes/voices.py` — voices library over `data/voices/`
  (`ALLOWED_AUDIO_EXTENSIONS = .wav .mp3 .m4a .ogg`).
- `app/routes/text_studio.py` — `GET /text-studio/light-tts/backends`,
  `POST .../preview-paragraph`, `POST .../preview-patch`. Preview routes accept
  `{text, with_effects, backend}` today.
- `app/config.py` — `light_tts_backend`, `light_tts_voice` settings.
- `app/templates/text_studio.html` — settings modal has a single
  `#ttsBackendSelect`; `loadLightTTSBackends()` populates it and persists the
  choice to `localStorage['tts-backend']`. `previewParagraph()` /
  `previewPatch()` send `{backend}`.

## Design

### A. Per-backend voice enumeration (`app/light_tts.py`)

Add a resolver `list_voices(backend) -> list[dict]` returning
`[{"id": str, "label": str, "language": str}]`. Implemented per backend:

| Backend  | Source                                                            | Notes |
|----------|-------------------------------------------------------------------|-------|
| edge-tts | `edge_tts.list_voices()` (async, run + cache module-level)        | Return all; sort `vi-VN-*` first, then by locale. `label` = ShortName + gender. |
| gtts     | `gtts.lang.tts_langs()`                                           | `id` = lang code, `label` = language name. |
| kokoro   | **Constant** list of known kokoro-onnx voice ids                 | Model may be absent; still list. Backend `available` reflects import only. |
| piper    | **Constant** list of known Vietnamese piper voice ids            | Actual model file resolved via `settings.piper_voices_dir` at synth time. |

Fallback: if enumeration raises or returns empty, return a single entry for the
backend's existing `default_voice`. Enumeration must never 500 the endpoint.

edge-tts voice list is cached in a module-level variable after first successful
fetch (it is a network call).

### B. `clone` backend

- Add `clone` to the backends list surfaced by the API. `available = True`
  **only** when `import voxcpm` succeeds; otherwise `available = False`
  (dropdown shows it disabled).
- `clone`'s voices = reference clips in the voices library. Reuse the voices dir
  logic (`Path(settings.data_root)/"voices"`, filtered by
  `ALLOWED_AUDIO_EXTENSIONS`). Each voice: `id` = filename, `label` = filename
  (or stored description if present), `language` = "clone".
- Architecture: `light_tts.py` keeps its "no GPU" contract. Clone synthesis is
  handled in the **route layer** using `VoxCPMEngine` from `tts_engine.py`, not
  embedded in `light_tts.py`.
- GPU safety: a module-level `asyncio.Lock` in the route guards VoxCPM calls so
  two clone previews never run concurrently. VoxCPMEngine is a lazily-created
  module-level singleton reused across preview calls (same pattern as the light
  engine singleton). Known limitation, documented not solved: if the worker is
  actively rendering, a clone preview contends for the GPU — acceptable for an
  infrequent, user-initiated preview.

### C. API

New endpoint:

```
GET /text-studio/light-tts/voices?backend=<id>
→ 200 { "voices": [ { "id": "...", "label": "...", "language": "..." }, ... ] }
→ 400 if backend unknown
```

Changes:

- `GET /text-studio/light-tts/backends` — add the `clone` entry with its
  `available` flag.
- `POST .../preview-paragraph` and `POST .../preview-patch` — accept an optional
  `voice` field in the JSON body.
  - Light backends: pass `voice` to `engine.synthesize_to_wav_bytes(text, voice)`.
  - `backend == "clone"`: resolve `voice` to a clip path under the voices dir
    (reject path traversal), then synthesize via VoxCPM under the lock.

Error handling:

- Unknown backend/voice → 400.
- Clone selected but VoxCPM missing → 503 "VoxCPM chưa cài".
- Clone selected but `voice` empty/not found → 400 "Chưa chọn giọng clone" /
  404 clip not found.

### D. UI (`app/templates/text_studio.html`)

- Settings modal: keep `#ttsBackendSelect`; add `#ttsVoiceSelect` below it.
- `loadLightTTSBackends()`: after populating backends, load voices for the
  current backend.
- New `loadVoicesForBackend(backend)`: `GET .../voices?backend=`, fill
  `#ttsVoiceSelect`, restore saved voice from `localStorage`.
- On backend `change`: reload the voice dropdown and persist backend.
- Persist voice per backend: `localStorage['tts-voice:<backend>']`.
- When backend = `clone`: the voice dropdown lists library clips; show a
  "Quản lý giọng →" link to `/voices`.
- `previewParagraph()` / `previewPatch()`: include `voice` (the selected voice
  id) in the request body.

### E. Config (`app/config.py`)

- Add `piper_voices_dir: str = ""` — directory holding piper `*.onnx` models,
  used to resolve a piper voice id to a model file at synth time. Empty → use
  the id as-is (current behavior).

## Testing

Extend `tests/test_light_tts.py` and `tests/test_text_studio.py`:

- `list_voices` per backend with edge-tts / gtts mocked; assert Vietnamese-first
  ordering for edge-tts.
- Enumeration failure/empty → falls back to `default_voice`.
- `GET .../voices?backend=` returns list; unknown backend → 400.
- `backends` includes `clone` with correct `available` flag (voxcpm import
  mocked present/absent).
- preview routes forward `voice` to the engine (assert via mock).
- clone preview: VoxCPM absent → 503; clip missing → 404/400; happy path with
  `VoxCPMEngine` mocked (no real model load).

VoxCPM is always mocked in tests — never load the real model.

## Out of Scope (YAGNI)

- Saving the chosen clone clip to `book.voice_clip_path` (was the rejected
  option). Preview only. A future "Save this voice for book" button could add it.
- Coordinating GPU between preview and the worker beyond a single preview-side
  lock.

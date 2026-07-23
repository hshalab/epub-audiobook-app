# TTS Voice Config for Preview

Date: 2026-07-23
Status: Approved (design)

## Goal

Text Studio's preview currently lets the user pick only a *backend*
(edge-tts / gtts / kokoro / piper), each hard-coded to a single voice. This
change exposes a full **voice list per backend** so the user picks a specific
voice, for **edge-tts, gtts, and piper**.

Scope is **preview only**. It does NOT change `book.voice_clip_path` or the
worker's real render path.

Explicitly **out**: voice cloning in preview, and the `kokoro` backend (removed
from the backend list — it has no real Vietnamese voice support).

## Current State

- `app/light_tts.py` — `LightTTSEngine`, "no GPU" preview engine. `_BACKENDS`
  holds 4 backends (edge-tts, gtts, kokoro, piper), each with one
  `default_voice`. `synthesize_to_wav_bytes` already accepts a `voice` argument
  but the UI never sends one.
- `app/routes/text_studio.py` — `GET /text-studio/light-tts/backends`,
  `POST .../preview-paragraph`, `POST .../preview-patch`. Preview routes accept
  `{text, with_effects, backend}` today.
- `app/config.py` — `light_tts_backend`, `light_tts_voice` settings.
- `app/templates/text_studio.html` — settings modal has a single
  `#ttsBackendSelect`; `loadLightTTSBackends()` populates it and persists the
  choice to `localStorage['tts-backend']`. `previewParagraph()` /
  `previewPatch()` send `{backend}`.

## Design

### A. Remove kokoro (`app/light_tts.py`)

Remove the `kokoro` entry from `_BACKENDS`, its `_BACKEND_SYNTH` entry, and its
branch in `_check_backend`. After this the backends are edge-tts, gtts, piper.
Adjust any test that references kokoro.

### B. Per-backend voice enumeration (`app/light_tts.py`)

Add a resolver `list_voices(backend) -> list[dict]` returning
`[{"id": str, "label": str, "language": str}]`. Implemented per backend:

| Backend  | Source                                                     | Notes |
|----------|------------------------------------------------------------|-------|
| edge-tts | `edge_tts.list_voices()` (async, run + cache module-level) | Return all; sort `vi-VN-*` first, then by locale. `label` = ShortName + gender. |
| gtts     | `gtts.lang.tts_langs()`                                     | `id` = lang code, `label` = language name. |
| piper    | **Constant** list of known Vietnamese piper voice ids      | Actual model file resolved via `settings.piper_voices_dir` at synth time. |

Fallback: if enumeration raises or returns empty, return a single entry for the
backend's existing `default_voice`. Enumeration must never 500 the endpoint.

edge-tts voice list is cached in a module-level variable after first successful
fetch (it is a network call).

### C. API (`app/routes/text_studio.py`)

New endpoint:

```
GET /text-studio/light-tts/voices?backend=<id>
→ 200 { "voices": [ { "id": "...", "label": "...", "language": "..." }, ... ] }
→ 400 if backend unknown
```

Changes:

- `POST .../preview-paragraph` and `POST .../preview-patch` — accept an optional
  `voice` field in the JSON body and pass it to
  `engine.synthesize_to_wav_bytes(text, voice)`.

Error handling:

- Unknown backend → 400.
- Invalid/empty `voice` → backend falls back to its `default_voice` (no error).

### D. UI (`app/templates/text_studio.html`)

- Settings modal: keep `#ttsBackendSelect`; add `#ttsVoiceSelect` below it.
- `loadLightTTSBackends()`: after populating backends, load voices for the
  current backend.
- New `loadVoicesForBackend(backend)`: `GET .../voices?backend=`, fill
  `#ttsVoiceSelect`, restore saved voice from `localStorage`.
- On backend `change`: reload the voice dropdown and persist backend.
- Persist voice per backend: `localStorage['tts-voice:<backend>']`.
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
- `backends` no longer includes kokoro.
- preview routes forward `voice` to the engine (assert via mock).

No real TTS network/model calls in tests — edge-tts / gtts / piper are mocked.

## Out of Scope (YAGNI)

- Voice cloning in preview (VoxCPM). Deferred; would be a separate feature,
  possibly a "Save this voice for book" button writing `book.voice_clip_path`.
- The `kokoro` backend (removed).

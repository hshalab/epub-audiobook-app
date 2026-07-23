# Lightweight TTS + Preview Design

Date: 2026-07-23  
Status: Draft

## Problem

VoxCPM2 là model TTS chất lượng cao nhưng nặng và chậm (GPU-bound, vài phút/patch). Người dùng cần nghe thử text ngay trong Text Studio — để kiểm tra cách đọc, vị trí marker hiệu ứng, trước khi commit vào queue VoxCPM2.

## Goals

1. Preview tức thì từng đoạn văn trong editor, kèm sound effects đã mix
2. Preview toàn bộ patch bằng TTS nhẹ (không ảnh hưởng queue VoxCPM2)
3. Hỗ trợ nhiều backend TTS nhẹ: edge-tts, kokoro, piper, gTTS
4. Không thay thế VoxCPM2 — preview chỉ để nghe thử, không lưu làm audio chính thức

## Non-goals

- Thay thế VoxCPM2 cho synthesis cuối cùng
- Lưu kết quả preview thành patch audio chính thức
- Streaming audio real-time (trả về blob là đủ)

## Architecture

### Backend: LightTTSEngine

File mới: `app/light_tts.py`

```
LightTTSEngine (abstract)
  ├── EdgeTTSBackend   — edge-tts package, gọi Microsoft API, cần internet
  ├── KokoroBackend    — kokoro-onnx, CPU local ~100MB model
  ├── PiperBackend     — piper-tts, CPU local, có model tiếng Việt
  └── GttsTTSBackend   — gTTS, Google TTS API, cần internet, fallback
```

- `settings.light_tts_backend` chọn backend (default: `"edge-tts"`)
- `settings.light_tts_voice` giọng đọc (vd: `"vi-VN-HoaiMyNeural"` cho edge-tts)
- Nếu backend package chưa cài → endpoint trả 503 với message rõ ràng
- Lazy import: không crash khi start nếu không có package TTS nhẹ

### Server-side Effect Mixing

Sau khi TTS generate WAV:
1. Parse `[marker]` trong text theo position (reuse `text_analysis.py` logic)
2. Tính timestamp tương đối dựa trên character offset / tổng duration
3. Overlay effect WAV lên đúng timestamp bằng numpy (đã có `soundfile`, `numpy`)
4. Trả về WAV bytes

Dùng numpy/soundfile (đã có trong pyproject.toml) thay vì thêm ffmpeg dependency.

### Endpoints mới

Thêm vào `app/routes/text_studio.py`:

```
GET /text-studio/light-tts/backends
  response: { backends: [{ id: str, label: str, available: bool }] }
  — trả danh sách tất cả backends đã biết + available=true nếu package đã cài

POST /books/{book_id}/text-studio/patches/{patch_id}/preview-paragraph
  body: { text: str, with_effects: bool = true, backend: str | null }
  response: audio/wav (blob, không lưu file)
  timeout: 30s (TTS nhẹ nên dưới 5s thực tế)

POST /books/{book_id}/text-studio/patches/{patch_id}/preview-patch
  body: { with_effects: bool = true, backend: str | null }
  response: audio/wav (blob, không lưu file)
  timeout: 120s
```

Cả hai endpoint chạy trực tiếp trên event loop qua `asyncio.to_thread` — không đi qua queue worker, không ảnh hưởng VoxCPM2.

### UI trong Text Studio

**Inline paragraph preview:**
- Thêm nút ▶ nhỏ bên trái mỗi đoạn văn, hiện khi hover
- Click → POST `preview-paragraph` với text đoạn đó
- Inline `<audio>` mini player xuất hiện ngay dưới đoạn, tự biến mất sau khi phát xong hoặc click chỗ khác
- Loading state: nút ▶ spin trong lúc chờ

**Preview toàn patch:**
- Thêm nút "▶ Preview nhanh" vào `ts-actions` bar (cạnh nút Lưu / Phân tích)
- Checkbox "Kèm hiệu ứng" kèm theo
- Kết quả phát trong panel "Preview âm thanh" đã có (`mediaPreview`)

**Chọn backend:**
- Dropdown nhỏ trong Settings modal (dialog `settingsModal`)
- Gọi `GET /text-studio/light-tts/backends` để lấy danh sách backends khả dụng
- Lưu vào localStorage (per-browser preference, không cần lưu DB)

## Settings

Thêm vào `app/config.py`:

```python
light_tts_backend: str = "edge-tts"
light_tts_voice: str = "vi-VN-HoaiMyNeural"
```

## Dependencies

Không thêm vào `[project.dependencies]`. Thêm vào `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
light-tts = [
    "edge-tts>=7.0",
]
light-tts-kokoro = [
    "kokoro-onnx>=0.4",
]
light-tts-piper = [
    "piper-tts>=1.2",
]
```

Mỗi backend là optional — app vẫn start nếu không có cái nào cài.

## Effect Mixing Algorithm

```
1. TTS generate audio cho toàn bộ text (không có markers) → wav_array, sample_rate
2. Với mỗi [marker] tìm được:
   a. Tính char_offset / total_chars → ratio → timestamp_samples
   b. Load effect WAV từ thư viện (cached in-memory)
   c. Overlay: out[t:t+len(effect)] += effect * volume (clamp sau)
3. Normalize và trả về bytes
```

Timestamp tính theo ratio đơn giản — không cần forced alignment. Chấp nhận được cho preview.

## File Changes

- `app/light_tts.py` — LightTTSEngine + backends (mới)
- `app/config.py` — thêm 2 settings
- `app/routes/text_studio.py` — thêm 3 endpoints
- `app/templates/text_studio.html` — inline ▶ buttons + Preview nhanh button + backend dropdown
- `pyproject.toml` — thêm optional-dependencies

## Out of Scope (add if needed later)

- Streaming audio (chunked response)
- Lưu preview làm draft audio cho patch
- Per-paragraph voice override
- Forced alignment chính xác (cần whisper hoặc MFA)

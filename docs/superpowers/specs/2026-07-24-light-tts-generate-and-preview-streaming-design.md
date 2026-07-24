# Design: LightTTS Generate + Preview Streaming

**Date:** 2026-07-24  
**Status:** Approved

---

## Overview

Hai tính năng bổ sung cho LightTTS trong epub-audiobook-app:

1. **LightTTS Generate** — synthesize patch chính thức bằng LightTTS (không cần GPU/VoxCPM2), chunk-by-chunk như worker hiện tại, lưu vào `patch.audio_path`.
2. **Preview Streaming** — "Preview nhanh" mới: chunk text → synthesize dần → stream từng chunk qua SSE → browser phát ngay từ chunk đầu bằng Web Audio API, đồng thời lưu kết quả vào patch.

---

## Phần 1: LightTTS Generate

### Backend

**Route mới** trong `app/routes/text_studio.py`:

```
POST /books/{book_id}/patches/{patch_id}/light-tts-generate
Content-Type: application/json
Body: { "backend": "edge-tts", "voice": "vi-VN-HoaiMyNeural", "with_effects": false }
```

- Validate patch tồn tại, `patch.book_id == book_id`, status không phải `processing`
- Chạy `asyncio.to_thread(_light_synthesize_patch, ...)` — không block event loop
- `_light_synthesize_patch` mirror `worker._synthesize` nhưng dùng `LightTTSEngine`:
  - Lấy text qua `repository.get_effective_patch_text(conn, patch)`
  - `split_into_tts_chunks(text, max_chars=patch.max_chars or settings.tts_max_chars)`
  - Synthesize từng chunk → lưu `{data_root}/books/{book_id}/patches/{patch_id}_chunks/chunk_{i:03d}.wav`
  - Merge bằng `audio_merge.merge_chunk_files_to_patch`
  - `repository.mark_patch_done(conn, patch_id, audio_path)`
- Trả JSON: `{"status": "done", "patch_id": ..., "audio_path": ...}`
- Lỗi trả `{"status": "error", "detail": "..."}` với HTTP 500

**Route batch** trong `app/routes/text_studio.py`:

```
POST /books/{book_id}/light-tts-generate-all
Content-Type: application/json
Body: { "backend": "edge-tts", "voice": "...", "patch_ids": [1, 2, 3] }
```

- `patch_ids` optional — nếu bỏ qua, chọn tất cả patches có status `pending` hoặc `failed`
- Generate lần lượt (sequential, không parallel — tránh race condition trên engine)
- Trả `{"results": [{"patch_id": 1, "status": "done"}, {"patch_id": 2, "status": "error", "detail": "..."}]}`

### UI — Text Studio

Trong `ts-actions` bar, thêm nút cạnh "🔊 Preview nhanh":

```
[💾 Generate LightTTS]
```

- Click → `POST light-tts-generate` với backend/voice hiện tại
- Trong khi chạy: hiện progress text "Chunk 2/5..." (polling hoặc từ SSE progress events)
- Xong: cập nhật dot status của patch trong danh sách bên trái thành `done`

### UI — Book Detail

Trong bảng patches (section `#patches-card`):

- Mỗi patch row thêm nút nhỏ "▶ LightTTS" trong cột Actions
- Header section thêm nút "▶ Run All LightTTS" — chỉ generate các patches `pending/failed`
- Nút "Run All" disable trong khi đang chạy, hiện counter "3/7 patches done"

---

## Phần 2: Preview Streaming

### Backend

**SSE stream endpoint** trong `app/routes/text_studio.py`:

```
GET /books/{book_id}/text-studio/patches/{patch_id}/preview-stream
Query: backend=edge-tts&voice=vi-VN-HoaiMyNeural&with_effects=0&text=<url-encoded>
```

- `text` optional query param — nếu có dùng text đó (text đang edit chưa lưu), nếu không dùng DB text
- `StreamingResponse(generator(), media_type="text/event-stream")`
- Generator async:
  1. Cleanup tmp files cũ của patch này: xóa `data/preview_tmp/{patch_id}_*.wav`
  2. `split_into_tts_chunks(text, max_chars=...)`
  3. Per chunk `i`:
     - Synth bằng `LightTTSEngine.synthesize_to_wav_bytes(chunk_text, voice)`
     - Lưu vào `data/preview_tmp/{patch_id}_{token}_{i}.wav` (token = uuid hex 8 chars, cố định cho session này)
     - Yield SSE event: `data: {"type":"chunk","index":0,"total":5,"url":"/preview-tmp/{patch_id}_{token}_{i}"}\n\n`
  4. Sau tất cả chunks xong:
     - Merge bằng `merge_chunk_files_to_patch` → `audio_path`
     - `repository.mark_patch_done(conn, patch_id, audio_path)`
     - Yield: `data: {"type":"done","saved":true}\n\n`
  5. Nếu exception ở bất kỳ bước nào:
     - Yield: `data: {"type":"error","message":"TTS failed: ..."}\n\n`

**Serve tmp files** trong `app/routes/text_studio.py`:

```
GET /preview-tmp/{filename}
```

- `filename` chỉ được chứa `[a-zA-Z0-9_-]` + `.wav` — refuse path traversal
- Serve từ `data/preview_tmp/`
- File tạm được cleanup khi stream mới bắt đầu (không cần TTL phức tạp)

**Thư mục tmp**: `{data_root}/preview_tmp/` — tạo on-demand, không persist qua restart.

### JS Frontend (`text_studio.html`)

Thay `previewPatch()` bằng `previewPatchStream()`:

```javascript
async function previewPatchStream() {
    if (!currentPatchId) return;
    const btn = document.getElementById('btnPreviewPatch');
    btn.disabled = true;
    btn.textContent = 'Đang tạo chunk 0/...';

    const backend = document.getElementById('ttsBackendSelect').value;
    const voice = document.getElementById('ttsVoiceSelect').value;
    const withFx = document.getElementById('fxToggle').checked ? '1' : '0';
    const text = encodeURIComponent(getPlainText());

    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let nextStartTime = ctx.currentTime + 0.1;  // small buffer

    const url = `/books/${bookId}/text-studio/patches/${currentPatchId}/preview-stream`
        + `?backend=${encodeURIComponent(backend)}&voice=${encodeURIComponent(voice)}`
        + `&with_effects=${withFx}&text=${text}`;

    const es = new EventSource(url);
    es.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
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
            } catch (err) { /* chunk decode lỗi, bỏ qua tiếp tục */ }
        } else if (msg.type === 'done') {
            es.close();
            btn.disabled = false;
            btn.textContent = '🔊 Preview nhanh';
            document.getElementById('mediaPreview').style.display = '';
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

---

## Phần 3: Data & File Layout

```
data/
  preview_tmp/
    {patch_id}_{token}_{i}.wav   ← tmp chunks, cleanup on next stream
  books/{book_id}/patches/
    {patch_id}.wav                ← final merged (existing)
    {patch_id}_chunks/
      chunk_{i:03d}.wav           ← per-chunk files (existing pattern)
```

---

## Phần 4: Các điểm cần chú ý

- **Concurrency**: `_light_synthesize_patch` chạy trong `asyncio.to_thread`. Nếu user click Generate 2 lần, route cần guard (check status `processing` trước khi bắt đầu, set processing tạm thời). Tuy nhiên không dùng `repository.claim_next_pending_patch` vì đó là worker-owned — thay vào đó kiểm tra `patch.status != 'processing'` và fail fast nếu đang chạy.
- **LightTTS engine singleton**: `_get_light_engine()` dùng global singleton — safe vì LightTTS chạy trong thread, mỗi call độc lập. Batch generate vẫn sequential.
- **text param trong SSE**: text có thể dài, URL query có giới hạn. Nếu vượt quá ~4KB, cần fallback sang POST body. Thiết kế đơn giản: nếu text quá dài, dùng DB text (bỏ qua param `text`).
- **with_effects — chunked context**: `_mix_effects` dùng `pos / text_len` để định vị effect marker trong audio theo tỉ lệ. Khi chunk hóa, mỗi chunk chỉ là một đoạn text nhỏ nên ratio bị sai hoàn toàn. Giải pháp: **apply effects sau khi merge**, không phải per-chunk. Tức là: synthesize tất cả chunks → merge thành `full_wav_bytes` → `_mix_effects(full_wav_bytes, full_text, conn)` → ghi kết quả ra `audio_path`. Với preview streaming, các chunk tmp phục vụ playback không có effects (user nghe raw); sau khi merge xong server apply effects rồi mới `mark_patch_done`. Nếu `with_effects=False` thì bỏ qua bước mix.
- **AudioContext resume**: Mobile browser yêu cầu user gesture để resume AudioContext. Nút "Preview nhanh" đã là gesture, đủ điều kiện.
- **EventSource và text encoding**: text dài cần encode đúng trong URL. Alternative đơn giản: send `text` qua POST request trước, server cache trong memory với token, SSE dùng token. Nhưng với thiết kế hiện tại, dùng DB text là đủ cho use case thông thường.

---

## Phần 5: Files cần thay đổi

| File | Thay đổi |
|------|----------|
| `app/routes/text_studio.py` | Thêm 4 routes: `light-tts-generate`, `light-tts-generate-all`, `preview-stream`, `preview-tmp/{filename}` |
| `app/templates/text_studio.html` | Thêm nút Generate LightTTS, thay `previewPatch()` bằng `previewPatchStream()` |
| `app/templates/book_detail.html` | Thêm nút per-patch và "Run All LightTTS" |
| `app/main.py` | Đảm bảo `preview_tmp` dir được tạo khi startup |

Không cần thay đổi `light_tts.py`, `worker.py`, `repository.py`, hay DB schema.

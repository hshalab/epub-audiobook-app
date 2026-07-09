## Context

Ứng dụng hiện tại là một FastAPI app chuyển EPUB → TTS audio → MP4 video → YouTube. Pipeline Colab/Kaggle chỉ đảm nhận bước TTS (text → WAV) vì cần GPU. Video được render server-side bằng FFmpeg (image + audio, không có text overlay, không có nhạc nền).

Thay đổi này mở rộng pipeline Colab/Kaggle để sau khi TTS xong, notebook tự render MP4 per-patch (ảnh tĩnh đã có text + audio + nhạc nền) và upload thẳng lên YouTube — không cần import audio về server rồi render lại.

**Flow mới:**
1. User upload **1 background image** cho toàn book
2. Server dùng **Pillow** tạo sẵn ảnh per-patch: vẽ text `"Tên Sách - Tên Patch"` lên background, lưu vào disk. Ảnh này được tạo khi patch được tạo (hoặc khi background/tên thay đổi) và tái sử dụng nhiều lần.
3. Export package bundle ảnh đã render sẵn cho từng patch vào `patches/patch_NNN/background.jpg`
4. Notebook: ảnh tĩnh + TTS audio + nhạc nền (amix) → MP4. Không cần font, không cần drawtext trong FFmpeg.

Server-side video gen cũng cập nhật để dùng ảnh đã có text (không thêm drawtext vào FFmpeg), đảm bảo nhất quán giữa hai đường render.

**Ràng buộc hiện tại:**
- SQLite single-connection với `threading.Lock` — mọi DB write phải qua `db_lock`
- FFmpeg là dependency duy nhất cho video, không có thư viện Python wrapper
- Colab/Kaggle: môi trường ephemeral, cần resume-safe cho mọi bước
- YouTube credentials lưu trong SQLite, cần cơ chế export an toàn sang notebook
- Package export (zip/Drive) phải tự chứa đủ — notebook không được fetch resource từ ngoài khi render

## Goals / Non-Goals

**Goals:**
- Server dùng Pillow tạo ảnh per-patch (background + text `"Tên Sách - Tên Patch"`) và lưu vào disk
- Ảnh per-patch tự động re-render khi background hoặc tên patch thay đổi
- Export package bundle ảnh đã render sẵn per-patch vào `patches/patch_NNN/background.jpg`
- Notebook render MP4: ảnh tĩnh + TTS audio + nhạc nền (amix loop) — không cần font/drawtext
- Nhạc nền tự động loop, volume < TTS audio (mặc định 15%)
- Mỗi bước render resume-safe: nếu MP4 đã tồn tại thì skip
- Notebook upload từng MP4 lên YouTube dùng `YOUTUBE_CREDS` Kaggle/Colab Secret
- Server-side video gen dùng ảnh đã có text (không thêm drawtext vào FFmpeg)
- Music Library: upload/list/delete/preview nhạc, gán per-book
- Nút "Copy YouTube credentials" trên trang `/youtube`
- Thêm `pillow` vào `pyproject.toml`

**Non-Goals:**
- Ghép nhiều patch thành 1 video dài trên Colab/Kaggle (mỗi patch = 1 MP4 = 1 YouTube video)
- Dynamic subtitle/caption theo từng câu TTS
- Audio ducking phức tạp (sidechain compress) — chỉ fixed volume ratio
- Hỗ trợ platform ngoài YouTube (TikTok, Facebook, v.v.)
- Music library shared giữa nhiều book — nhạc được gán per-book
- Ken Burns effect trong notebook (chỉ ảnh tĩnh)

## Decisions

### D1: Server dùng Pillow tạo ảnh per-patch, không dùng FFmpeg drawtext

**Quyết định:** Server dùng `Pillow` (thêm vào `pyproject.toml`) để vẽ text `"Tên Sách - Tên Patch"` lên background image, lưu ra file PNG tại `data/books/{book_id}/patch_overlays/{patch_id}.png`. File ảnh này được tái sử dụng mọi lần render (không tạo lại trừ khi background hoặc tên thay đổi). Notebook và server-side video gen đều dùng ảnh đã có text sẵn — FFmpeg chỉ ghép ảnh + audio, không cần `drawtext`.

**Lý do:** Pillow kiểm soát font/layout/wrapping tốt hơn FFmpeg drawtext cho bài toán static image. Ảnh được tạo 1 lần server-side → notebook hoàn toàn không cần font file, không cần xử lý drawtext, đơn giản hóa đáng kể logic notebook. Font chỉ cần có trên server.

**Thay thế đã xem xét:** FFmpeg drawtext trên server để xuất ảnh PNG — phức tạp hơn Pillow, không có lợi thế gì. FFmpeg drawtext trong notebook — yêu cầu bundle font vào package (~15MB), notebook phức tạp hơn, bị loại.

---

### D2: Ảnh per-patch được tạo tự động khi patch được tạo hoặc background thay đổi

**Quyết định:** Tạo module `app/image_overlay.py` với hàm `render_patch_overlay(book, patch, font_path) -> Path`. Ảnh được tạo (hoặc re-render) tại các thời điểm:
- Khi batch được build (`POST /books/{id}/patches/build`)
- Khi background image của book thay đổi
- Khi tên patch thay đổi

Hàm idempotent: nếu file đã tồn tại và không cần re-render thì trả về path cũ.

**Lý do:** Tạo sẵn giúp export package nhanh (không cần render lúc export). Tái sử dụng được cho cả server-side video job và export.

**Thay thế đã xem xét:** Render lúc export — làm chậm export, render lặp lại không cần thiết.

---

### D2b: Bundle ảnh đã render sẵn vào export package

**Quyết định:** Khi build export package, copy `data/books/{book_id}/patch_overlays/{patch_id}.png` vào `patches/patch_NNN/background.png`. Nếu ảnh overlay chưa tồn tại (ví dụ chưa render), fallback về raw background image.

**Lý do:** Notebook self-contained, không cần font, không cần drawtext — chỉ cần ghép ảnh tĩnh + audio.

---

### D3: Nhạc nền dùng FFmpeg `amix` với `stream_loop -1`

**Quyết định:** Mix nhạc nền vào video bằng:
```
-stream_loop -1 -i music.mp3
-filter_complex "[1:a]volume=0.15[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=0"
```
`duration=first` đảm bảo output dài bằng TTS audio (không dài hơn). `stream_loop -1` loop nhạc vô hạn nếu ngắn hơn video.

**Lý do:** FFmpeg native, không cần library Python bổ sung, hoạt động trên cả server và Colab/Kaggle.

**Thay thế đã xem xét:** `pydub` để mix audio trước khi đưa vào FFmpeg — thêm dependency, phức tạp hơn không cần thiết.

---

### D4: YouTube credentials export dùng format giống GDRIVE_CREDS

**Quyết định:** Endpoint `GET /youtube/kaggle-credentials` trả về JSON:
```json
{"client_id": "...", "client_secret": "...", "refresh_token": "..."}
```
User paste vào Kaggle Secrets / Colab Secrets với key `YOUTUBE_CREDS`. Notebook đọc secret này và tạo `google.oauth2.credentials.Credentials` với auto-refresh.

**Lý do:** Đồng nhất với flow GDRIVE_CREDS đã có — user đã quen, code notebook có thể tái sử dụng pattern. Không cần OAuth flow riêng trong notebook.

**Thay thế đã xem xét:** OAuth device flow trong notebook — phức tạp, cần user copy code, không phù hợp với automated resume.

---

### D5: Video render trong notebook là cell riêng biệt sau TTS cells

**Quyết định:** Thêm 3 cell mới (Cell 10, 11, 12) sau Cell 9 hiện tại:
- **Cell 10:** Kiểm tra FFmpeg có sẵn (Colab/Kaggle đều có), không cần font
- **Cell 11:** Render MP4 per-patch — loop qua `batch_manifest.patches`, skip nếu MP4 đã tồn tại, gọi FFmpeg: ảnh tĩnh (`patches/patch_NNN/background.png`) + WAV + optional amix nhạc → MP4
- **Cell 12:** YouTube upload — đọc `YOUTUBE_CREDS` secret, upload từng MP4 dùng resumable upload

**Lý do:** Cell riêng cho phép user chạy chỉ video render (nếu TTS đã xong) hoặc chỉ YouTube upload mà không cần rerun toàn bộ notebook. Đồng nhất với pattern hiện tại (mỗi cell = 1 bước độc lập).

**Thay thế đã xem xét:** Gộp vào Cell 8 (TTS cell) — làm cell quá dài, khó debug, không thể re-run riêng phần video.

---

### D6: Music Library lưu file trong `data/music/`, metadata trong bảng `music`

**Quyết định:** Bảng `music` mới trong SQLite với `(id, name, file_path, duration_sec, created_at)`. File nhạc lưu trong `data/music/`. Book có cột `music_id` (FK nullable) và `music_volume` (REAL, default 0.15).

**Lý do:** Nhất quán với cách quản lý background images (`data/backgrounds/`). Duration lưu sẵn để hiển thị UI mà không cần probe lại mỗi lần.

**Thay thế đã xem xét:** Không có bảng riêng, chỉ scan thư mục — mất khả năng gán per-book và lưu metadata.

---

### D7: `generate_segment()` nhận thêm optional params, không breaking

**Quyết định:** Thêm keyword-only params với default `None` vào `generate_segment()`:
- `music_path: str | None = None`
- `music_volume: float = 0.15`

Text overlay không còn là param của `generate_segment()` — caller truyền ảnh đã có text sẵn (`image_path`). Khi `music_path` là `None`, behavior giữ nguyên như cũ.

**Lý do:** Backward-compatible. Text overlay được xử lý trước ở tầng Pillow, FFmpeg chỉ biết về ảnh tĩnh. Đơn giản hơn so với truyền text/font vào FFmpeg filter chain.

## Risks / Trade-offs

**[R1] Pillow text rendering cần font hỗ trợ tiếng Việt trên server**
→ Mitigation: `settings.default_font_path` trỏ tới font file do user cài. Nếu không cấu hình, Pillow dùng default bitmap font (không có tiếng Việt đẹp) — hiển thị cảnh báo trong UI patch builder.

**[R2] Ảnh overlay stale nếu background hoặc tên patch thay đổi sau khi đã render**
→ Mitigation: Re-render ảnh overlay tại mọi điểm thay đổi (build patches, đổi background, đổi tên patch). Hash tên patch + background path để detect stale.

**[R3] Colab session timeout giữa chừng khi render nhiều patch**
→ Mitigation: Resume logic: check file MP4 đã tồn tại trước khi render. Với Kaggle Drive mode, MP4 được upload lên Drive ngay sau khi render xong mỗi patch.

**[R4] YouTube upload quota (10,000 units/day default)**
→ Mitigation: Mỗi video upload ~1600 units. ~6 video/ngày trên quota mặc định. Hiển thị cảnh báo trong Cell 12 nếu upload fail do quota. Không mitigation tự động — user cần request quota tăng từ Google.

**[R5] `amix` có thể normalize audio không mong muốn ở một số version FFmpeg**
→ Mitigation: Thêm `normalize=0` vào amix filter: `amix=inputs=2:duration=first:normalize=0`.

**[R6] Music file lớn làm chậm upload lên Drive và inflate package zip**
→ Mitigation: Giới hạn upload size 20MB, kiểm tra trong route `POST /music/upload`. Hiển thị size trong UI.

## Migration Plan

1. Chạy `db.py` migration mới (thêm bảng `music`, thêm columns vào `book` và `patch`) — migration additive, không ảnh hưởng dữ liệu cũ.
2. Deploy app mới — các book cũ sẽ có `music_id = NULL`, `music_volume = 0.15` (default), video gen vẫn chạy bình thường không có nhạc/text overlay.
3. Notebook mới có Cell 10–12 là optional — user có thể chạy chỉ Cell 1–9 (TTS) như cũ, hoặc tiếp tục chạy Cell 10–12 để render video.
4. Rollback: revert code, không cần rollback DB (columns mới nullable hoặc có default).

## Open Questions

Không còn câu hỏi mở — tất cả đã được xác nhận trong planning session.

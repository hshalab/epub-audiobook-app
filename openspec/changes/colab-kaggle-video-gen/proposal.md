## Why

Hiện tại Colab/Kaggle chỉ được dùng để tổng hợp TTS (text → audio). Sau khi audio được import về server, video mới được render server-side — nhưng server không có text overlay hay nhạc nền. Tính năng này mở rộng pipeline Colab/Kaggle để render video MP4 hoàn chỉnh (background + text overlay + nhạc nền) ngay trên Colab/Kaggle và tự động upload lên YouTube, giảm phụ thuộc vào tài nguyên server.

## What Changes

- **Thêm Music Library**: User upload nhạc nền vào app, quản lý qua trang `/music`. Nhạc được gán per-book, tự động đưa vào export package khi export batch.
- **Text overlay trên video**: Hiển thị `"Tên Sách - Tên Patch"` cố định ở đầu video trong suốt thời lượng, dùng FFmpeg `drawtext` filter với font bundle vào package.
- **Nhạc nền trong video**: Mix nhạc nền (volume thấp, tự động loop) với TTS audio khi render video — cả server-side lẫn trong notebook.
- **Video rendering trong notebook**: Sau khi TTS hoàn tất, notebook tự động render MP4 cho từng patch (resume-safe: skip nếu MP4 đã tồn tại).
- **YouTube upload từ notebook**: Notebook upload trực tiếp từng MP4 lên YouTube dùng credentials lấy từ Kaggle/Colab Secret (`YOUTUBE_CREDS`), format giống `GDRIVE_CREDS` hiện có.
- **Nút "Copy YouTube credentials"**: Trang `/youtube` thêm nút export credentials cho Kaggle/Colab, tương tự nút "Copy Kaggle credentials" trên trang `/drive`.
- **Font bundle trong export package**: Font file (hỗ trợ tiếng Việt, thư pháp) được đóng gói vào package export thay vì download trong notebook.

## Capabilities

### New Capabilities

- `music-library`: Quản lý thư viện nhạc nền — upload, list, delete, preview. Gán nhạc per-book. File nhạc được copy vào export package.
- `video-text-overlay`: Render text `"Tên Sách - Tên Patch"` cố định lên video dùng FFmpeg drawtext, cả server-side và trong notebook.
- `video-background-music`: Mix nhạc nền volume thấp (mặc định 15%) với TTS audio, loop tự động nếu nhạc ngắn hơn video, cả server-side và trong notebook.
- `notebook-video-render`: Các cell mới trong batch notebook: cài FFmpeg, render video per-patch sau TTS, resume-safe.
- `notebook-youtube-upload`: Cell mới trong batch notebook upload từng MP4 lên YouTube dùng `YOUTUBE_CREDS` secret.
- `youtube-credentials-export`: Endpoint và UI cho phép user copy YouTube OAuth credentials để dùng làm Kaggle/Colab Secret.

### Modified Capabilities

- `book-video-job`: Thêm hỗ trợ text overlay và nhạc nền vào video job server-side (cùng pipeline, thêm tham số mới).

## Impact

**Files thay đổi:**
- `app/db.py` — thêm bảng `music`, thêm cột `music_id` và `music_volume` vào `book`, thêm `text_overlay_enabled` vào `patch`
- `app/models.py` — thêm dataclass `Music`
- `app/config.py` — thêm `default_font_path`, `music_max_size_mb`
- `app/video_gen.py` — thêm `text_overlay`, `font_path`, `music_path`, `music_volume` vào `generate_segment()`
- `app/repository.py` — thêm Music CRUD
- `app/drive_export.py` — copy background images, nhạc, font vào package; thêm `video_config` vào `batch_manifest.json`
- `app/routes/youtube.py` — thêm endpoint `GET /youtube/kaggle-credentials`
- `app/assets/colab_kaggle_batch_tts_template.ipynb` — thêm Cell 10–12 (FFmpeg, video render, YouTube upload)

**Files mới:**
- `app/routes/music.py` — Music Library routes
- `app/templates/music.html` — trang quản lý nhạc

**Dependencies không đổi** — FFmpeg đã có sẵn trên Colab/Kaggle (Ubuntu). `google-api-python-client` đã được cài trong Cell 4 của notebook hiện tại.

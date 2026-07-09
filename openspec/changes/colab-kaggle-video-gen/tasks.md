## 1. DB Schema và Models

- [x] 1.1 Thêm bảng `music` vào `app/db.py`: `(id, name, file_path, duration_sec, created_at)`
- [x] 1.2 Thêm cột `music_id INTEGER REFERENCES music(id)` và `music_volume REAL NOT NULL DEFAULT 0.15` vào bảng `book` trong `app/db.py`
- [x] 1.3 Thêm migration cho 2 cột mới trong `_migrate()` của `app/db.py`
- [x] 1.4 Thêm dataclass `Music` vào `app/models.py`: `(id, name, file_path, duration_sec, created_at)`
- [x] 1.5 Cập nhật dataclass `Book` trong `app/models.py`: thêm `music_id: int | None` và `music_volume: float`

## 2. Config

- [x] 2.1 Thêm `default_font_path: str` (default rỗng) và `music_max_size_mb: int` (default 20) vào `app/config.py`
- [x] 2.2 Thêm `pillow>=10.0.0` vào `pyproject.toml`
- [x] 2.3 Cập nhật `.env.example` với hướng dẫn `DEFAULT_FONT_PATH` và `MUSIC_MAX_SIZE_MB`

## 3. Image Overlay — Pillow

- [x] 3.1 Tạo module `app/image_overlay.py` với hàm `render_patch_overlay(book: Book, patch: Patch, font_path: str | None, out_path: str) -> None`: dùng Pillow mở background image, vẽ text `"Tên Sách - Tên Patch"` ở top-center (x căn giữa, y=50px), màu trắng, shadow/border đen, lưu ra PNG
- [x] 3.2 Thêm hàm `get_patch_overlay_path(book_id: int, patch_id: int) -> Path` trả về đường dẫn chuẩn `data/books/{book_id}/patch_overlays/{patch_id}.png`
- [x] 3.3 Thêm hàm `needs_rerender(book: Book, patch: Patch, out_path: Path) -> bool`: trả về `True` nếu file chưa tồn tại hoặc mtime của background image mới hơn file overlay
- [x] 3.4 Thêm hàm `ensure_patch_overlay(book: Book, patch: Patch, font_path: str | None) -> str | None`: gọi `needs_rerender`, nếu cần thì render, trả về path hoặc `None` nếu không có background
- [x] 3.5 Xử lý trường hợp text dài: wrap text nếu quá chiều rộng ảnh (dùng `ImageDraw.textlength` để tính)
- [x] 3.6 Fallback font: nếu `font_path` rỗng hoặc file không tồn tại, dùng `ImageFont.load_default()` và log warning

## 4. Repository — Music CRUD

- [x] 4.1 Thêm `create_music()` vào `app/repository.py`: insert bản ghi `music`, trả về `Music`
- [x] 4.2 Thêm `list_music()` vào `app/repository.py`: trả về `list[Music]` sắp xếp theo `created_at DESC`
- [x] 4.3 Thêm `get_music()` vào `app/repository.py`: lấy theo id
- [x] 4.4 Thêm `delete_music()` vào `app/repository.py`: xóa bản ghi và set `book.music_id = NULL` cho các book liên quan
- [x] 4.5 Thêm `set_book_music()` vào `app/repository.py`: cập nhật `book.music_id` và `book.music_volume`
- [x] 4.6 Cập nhật hàm `row_to_book()` trong `app/repository.py` để map `music_id`, `music_volume`

## 5. Video Generation — Server-side

- [x] 5.1 Thêm params `music_path: str | None = None`, `music_volume: float = 0.15` vào `generate_segment()` trong `app/video_gen.py`
- [x] 5.2 Thêm validation đầu hàm: nếu `music_path` được truyền nhưng file không tồn tại thì raise `FileNotFoundError`
- [x] 5.3 Xây dựng FFmpeg `amix` filter khi `music_path` không None: `-stream_loop -1 -i music`, `filter_complex "[1:a]volume=<vol>[music];[0:a][music]amix=inputs=2:duration=first:normalize=0"` — áp dụng cho cả nhánh static lẫn Ken Burns
- [x] 5.4 Cập nhật `generate_full_video()` trong `app/video_gen.py`: gọi `image_overlay.ensure_patch_overlay()` per-patch để lấy ảnh đã có text, truyền `music_path`/`music_volume` từ book xuống `generate_segment()`
- [x] 5.5 Cập nhật `_run_video_job()` trong `app/worker.py`: lấy `music` record từ `book.music_id`, truyền `music_path` và `music_volume` vào `generate_full_video()`

## 6. Music Library Routes và UI

- [x] 6.1 Tạo `app/routes/music.py` với các endpoints: `GET /music` (HTML page), `POST /music/upload`, `POST /music/{id}/delete`, `GET /music/{id}/file`
- [x] 6.2 `POST /music/upload`: validate size ≤ `settings.music_max_size_mb` MB, validate extension (`.mp3`, `.wav`, `.ogg`, `.m4a`), lưu vào `data/music/`, probe duration bằng `ffprobe`, ghi DB
- [x] 6.3 `POST /music/{id}/delete`: xóa file vật lý, gọi `repository.delete_music()`, redirect về `/music`
- [x] 6.4 `GET /music/{id}/file`: serve file nhạc với đúng MIME type, chỉ cho phép path trong `data/music/`
- [x] 6.5 Tạo `app/templates/music.html`: trang list nhạc với upload form, preview (`<audio>` tag), nút xóa — dùng cùng style dark-mode với các trang khác
- [x] 6.6 Mount router music vào `app/main.py`
- [x] 6.7 Thêm link "Music" vào navigation trong `app/templates/base.html`

## 7. UI — Gán nhạc cho Book

- [x] 7.1 Thêm section "Nhạc nền" vào trang book detail (`app/templates/book_detail.html`): dropdown chọn nhạc (list từ `GET /music` API), slider `music_volume` (0–100%, default 15%)
- [x] 7.2 Thêm route `POST /books/{book_id}/music` trong `app/routes/books.py`: cập nhật `book.music_id` và `book.music_volume`, trigger re-render overlay nếu cần
- [x] 7.3 Hiển thị tên nhạc đang được gán (hoặc "Không có nhạc") trên trang book detail

## 8. YouTube Credentials Export

- [x] 8.1 Thêm endpoint `GET /youtube/kaggle-credentials` vào `app/routes/youtube.py`: đọc credentials mới nhất từ DB, trả về JSON `{client_id, client_secret, refresh_token}`; HTTP 400 nếu chưa connect
- [x] 8.2 Thêm nút "Copy YouTube credentials for Kaggle/Colab" vào `app/templates/youtube.html`: fetch endpoint, copy JSON vào clipboard, hiện feedback "Copied!" (cùng pattern JS với nút trên trang `/drive`)

## 9. Drive Export — Mở rộng Package

- [x] 9.1 Cập nhật `_write_patch_files()` trong `app/drive_export.py`: nhận thêm `overlay_image_path: str | None`, copy file ảnh (overlay đã có text) vào `dest_dir/background.png`; nếu không có overlay, fallback copy raw background
- [x] 9.2 Cập nhật `build_batch_export_package()`: gọi `image_overlay.ensure_patch_overlay()` per-patch, truyền path kết quả vào `_write_patch_files()`
- [x] 9.3 Thêm logic copy music file từ `book.music_id` vào `music/<filename>` trong package root
- [x] 9.4 Thêm trường `video_config` vào `batch_manifest.json`: `{resolution, fps, music_file, music_volume, youtube_privacy}`
- [x] 9.5 Cập nhật `build_export_package()` (single-patch) tương tự: overlay image, music, `video_config` vào `manifest.json`

## 10. Notebook — Cell 10, 11, 12

- [x] 10.1 Thêm **Cell 10** vào `app/assets/colab_kaggle_batch_tts_template.ipynb`: kiểm tra FFmpeg có sẵn (`ffmpeg -version`), in kết quả — không cần font
- [x] 10.2 Thêm **Cell 11** vào notebook: loop qua `batch_manifest.patches`, với mỗi patch build FFmpeg command: `-loop 1 -i background.png -i result/NNN.wav` + optional `-stream_loop -1 -i music/<file>` + `amix` filter → MP4 tại `result/NNN - <name>.mp4`; skip nếu MP4 đã tồn tại; `drive_persist` nếu Kaggle Drive mode
- [x] 10.3 Thêm **Cell 12** vào notebook: đọc `YOUTUBE_CREDS` từ Kaggle/Colab secret, tạo `google.oauth2.credentials.Credentials` với auto-refresh, upload từng MP4 trong `result/` dùng YouTube Data API v3 resumable upload (10MB chunks); ghi `result/NNN.youtube_id` sau upload thành công; skip nếu file `.youtube_id` đã tồn tại; in hướng dẫn lấy credentials nếu secret không có
- [x] 10.4 Cập nhật markdown intro của notebook (Cell "batch-intro") để mô tả Cell 10–12

## 11. Notebook — Single-patch Template

- [x] 11.1 Thêm Cell 10, 11, 12 tương tự vào `app/assets/colab_kaggle_tts_template.ipynb` (single-patch variant), điều chỉnh để đọc từ `manifest.json` thay vì `batch_manifest.json`

## 12. Kiểm tra và Tích hợp

- [x] 12.1 Kiểm tra Pillow render: upload background, build patches, verify file `data/books/{id}/patch_overlays/{patch_id}.png` được tạo với text đúng
- [x] 12.2 Kiểm tra re-render: thay background image → overlay cũ bị thay thế
- [x] 12.3 Kiểm tra export package zip: chứa đúng `patches/patch_NNN/background.png`, `music/<file>`, `video_config` trong manifest
- [x] 12.4 Kiểm tra server-side video gen với nhạc: `generate_segment()` với `music_path` → MP4 có nhạc nền volume đúng
- [x] 12.5 Kiểm tra backward-compat: book không có nhạc → video gen chạy bình thường như cũ
- [x] 12.6 Kiểm tra endpoint `GET /youtube/kaggle-credentials` trả về đúng format JSON
- [x] 12.7 Chạy test suite hiện có: `pytest tests/` — 133 passed, 0 regression (1 pre-existing fail không liên quan)

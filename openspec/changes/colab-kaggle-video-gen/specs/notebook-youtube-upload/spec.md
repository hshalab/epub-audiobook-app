## ADDED Requirements

### Requirement: Cell 12 — YouTube upload từ notebook
Notebook SHALL có Cell 12 upload từng MP4 trong `result/` lên YouTube dùng credentials từ `YOUTUBE_CREDS` Kaggle/Colab Secret. Upload SHALL dùng resumable upload (chunked 10MB) như server-side. Mỗi video SHALL được upload với title = `"Tên Sách - Tên Patch"`, privacy từ `video_config.youtube_privacy` (mặc định `"private"`).

#### Scenario: Upload thành công
- **WHEN** Cell 12 chạy với `YOUTUBE_CREDS` secret hợp lệ và MP4 tồn tại
- **THEN** mỗi MP4 được upload lên YouTube, Cell in ra YouTube video ID cho mỗi video

#### Scenario: Skip video đã upload
- **WHEN** Cell 12 được re-run và một patch đã được ghi nhận là uploaded (track bằng file `result/NNN - <name>.youtube_id`)
- **THEN** patch đó bị skip, không upload lại

#### Scenario: YOUTUBE_CREDS không có
- **WHEN** secret `YOUTUBE_CREDS` không tồn tại trên Kaggle/Colab
- **THEN** Cell 12 in hướng dẫn lấy credentials từ app (`/youtube` → "Copy YouTube credentials"), không raise exception fatal

#### Scenario: Upload thất bại quota
- **WHEN** YouTube API trả về lỗi quota exceeded
- **THEN** Cell 12 in cảnh báo quota, dừng upload (không retry), các video đã upload trước đó được giữ nguyên

#### Scenario: MP4 chưa render
- **WHEN** một patch chưa có MP4 (Cell 11 chưa chạy hoặc bị skip)
- **THEN** patch đó bị skip với thông báo "MP4 chưa render", không lỗi fatal

## ADDED Requirements

### Requirement: Text overlay cố định trên video
Hệ thống SHALL render text `"Tên Sách - Tên Patch"` cố định ở vị trí top-center (x căn giữa, y=50px từ trên) trong suốt thời lượng video, dùng FFmpeg `drawtext` filter. Text SHALL có màu trắng, border đen (borderw=2) để dễ đọc trên mọi background.

#### Scenario: Render video với text overlay
- **WHEN** `generate_segment()` được gọi với `text_overlay="Tên Sách - Tên Patch"` và `font_path` hợp lệ
- **THEN** video output có text hiển thị ở top-center trong suốt thời lượng, màu trắng, viền đen

#### Scenario: Render video không có text overlay
- **WHEN** `generate_segment()` được gọi với `text_overlay=None`
- **THEN** video output không có text, behavior giống hệt hiện tại (backward-compatible)

#### Scenario: Font path không tồn tại
- **WHEN** `font_path` được truyền nhưng file không tồn tại trên disk
- **THEN** `generate_segment()` raise exception rõ ràng trước khi gọi FFmpeg

### Requirement: Text overlay trong notebook
Notebook SHALL render text overlay `"Tên Sách - Tên Patch"` từ `batch_manifest.json` (`book_title` + `patch_name`) dùng font file bundle trong package tại `fonts/`.

#### Scenario: Notebook render text overlay từ manifest
- **WHEN** Cell 11 render video cho một patch
- **THEN** FFmpeg command bao gồm `drawtext=fontfile=<fonts_dir>/font.ttf:text=<label>:x=(w-tw)/2:y=50`

#### Scenario: Font file không tìm thấy trong package
- **WHEN** thư mục `fonts/` không có trong package hoặc không có file `.ttf`/`.otf`
- **THEN** Cell 11 in cảnh báo và render video không có text overlay (không dừng batch)

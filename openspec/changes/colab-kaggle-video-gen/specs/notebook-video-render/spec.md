## ADDED Requirements

### Requirement: Cell 10 — Kiểm tra FFmpeg và xác định font
Notebook SHALL có Cell 10 kiểm tra FFmpeg có sẵn trong môi trường và xác định đường dẫn font từ thư mục `fonts/` trong package. Cell SHALL không install FFmpeg (đã có sẵn trên Colab/Kaggle Ubuntu).

#### Scenario: FFmpeg có sẵn
- **WHEN** Cell 10 chạy trên Colab hoặc Kaggle
- **THEN** lệnh `ffmpeg -version` thành công, `FONT_PATH` được set tới file `.ttf` hoặc `.otf` đầu tiên trong `<FOLDER_PATH>/fonts/`

#### Scenario: Font không có trong package
- **WHEN** thư mục `fonts/` không tồn tại hoặc rỗng
- **THEN** `FONT_PATH` được set về `None`, Cell 11 sẽ render không có text overlay

### Requirement: Cell 11 — Render MP4 per-patch (resume-safe)
Notebook SHALL có Cell 11 render một MP4 cho mỗi patch đã có merged result WAV. Với mỗi patch:
1. Kiểm tra MP4 output đã tồn tại → skip nếu có (`SKIP_EXISTING = True`)
2. Lấy `background.jpg` từ `patches/patch_NNN/`
3. Lấy merged WAV từ `result/NNN - <name>.wav`
4. Gọi FFmpeg: image + audio + optional drawtext + optional amix music → MP4
5. Lưu MP4 vào `result/NNN - <name>.mp4`
6. Nếu Kaggle Drive mode: upload MP4 lên Drive ngay (dùng `drive_persist`)

#### Scenario: Render thành công một patch
- **WHEN** Cell 11 chạy với patch có đủ WAV và background.jpg
- **THEN** FFmpeg tạo MP4 tại `result/NNN - <name>.mp4`, file tồn tại sau khi cell hoàn tất

#### Scenario: Skip patch đã có MP4
- **WHEN** `result/NNN - <name>.mp4` đã tồn tại và `SKIP_EXISTING = True`
- **THEN** patch đó được bỏ qua, không gọi FFmpeg lại

#### Scenario: Patch chưa có WAV (TTS chưa xong)
- **WHEN** merged WAV chưa tồn tại cho một patch
- **THEN** patch đó được skip với thông báo "TTS chưa hoàn tất", các patch khác vẫn render bình thường

#### Scenario: Background image không có trong package
- **WHEN** `patches/patch_NNN/background.jpg` không tồn tại
- **THEN** patch đó được skip với cảnh báo, không dừng batch

#### Scenario: Resume sau khi session bị kill
- **WHEN** notebook được restart và Cell 11 được re-run
- **THEN** các patch đã có MP4 bị skip, chỉ các patch chưa có MP4 được render

### Requirement: Export package chứa background images và font
`build_batch_export_package()` SHALL copy background image của từng patch (cascade: `patch.image_path → book.background_image_path → default`) vào `patches/patch_NNN/background.jpg`. SHALL copy font file từ `settings.default_font_path` vào `fonts/<filename>` trong package.

#### Scenario: Package chứa background và font
- **WHEN** export batch được tạo với book có background image và font được cấu hình
- **THEN** package có `patches/patch_NNN/background.jpg` cho mỗi patch và `fonts/font.ttf` (hoặc tên thực của font)

#### Scenario: Không có font được cấu hình
- **WHEN** `settings.default_font_path` rỗng hoặc file không tồn tại
- **THEN** package không có thư mục `fonts/`, notebook render không có text overlay

### Requirement: Export package chứa video_config trong batch_manifest
`batch_manifest.json` SHALL có thêm trường `video_config` với đầy đủ thông tin để Cell 11 render video.

#### Scenario: batch_manifest có video_config
- **WHEN** export package được tạo
- **THEN** `batch_manifest.json` có trường `video_config: {resolution, fps, font_path, music_file, music_volume, text_color, text_border}`

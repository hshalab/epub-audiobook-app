## ADDED Requirements

### Requirement: Nhạc nền trong video server-side
Hệ thống SHALL mix nhạc nền vào video khi `music_path` được truyền vào `generate_segment()`. Nhạc SHALL tự động loop nếu ngắn hơn video (`stream_loop -1`). Volume nhạc SHALL được kiểm soát bởi `music_volume` (float, default `0.15`). Output duration SHALL bằng TTS audio (`duration=first`). Filter SHALL dùng `normalize=0` để tránh auto-normalization.

#### Scenario: Render video có nhạc nền
- **WHEN** `generate_segment()` được gọi với `music_path` hợp lệ và `music_volume=0.15`
- **THEN** video output có nhạc nền ở volume 15%, nhạc loop nếu ngắn hơn video, TTS audio rõ ràng hơn nhạc

#### Scenario: Render video không có nhạc
- **WHEN** `generate_segment()` được gọi với `music_path=None`
- **THEN** video output chỉ có TTS audio, không có nhạc, behavior giống hiện tại (backward-compatible)

#### Scenario: Nhạc dài hơn video
- **WHEN** file nhạc dài hơn TTS audio
- **THEN** nhạc bị cắt tại điểm kết thúc TTS audio (`duration=first`)

#### Scenario: File nhạc không tồn tại
- **WHEN** `music_path` được truyền nhưng file không tồn tại
- **THEN** `generate_segment()` raise exception trước khi gọi FFmpeg

### Requirement: Nhạc nền trong notebook
Notebook SHALL mix nhạc nền từ `music/` trong package vào video khi `video_config.music_file` có giá trị. Cùng logic loop và volume như server-side.

#### Scenario: Notebook render video có nhạc
- **WHEN** `batch_manifest.video_config.music_file` trỏ tới file nhạc tồn tại trong package
- **THEN** FFmpeg command bao gồm `-stream_loop -1 -i <music_file>` và `amix` filter với volume từ `video_config.music_volume`

#### Scenario: Không có nhạc trong package
- **WHEN** `batch_manifest.video_config.music_file` là `null` hoặc file không tồn tại
- **THEN** Cell 11 render video chỉ với TTS audio, không lỗi

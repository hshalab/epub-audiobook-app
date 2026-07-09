## ADDED Requirements

### Requirement: Upload nhạc nền
Hệ thống SHALL cho phép user upload file nhạc nền (`.mp3`, `.wav`, `.ogg`, `.m4a`) vào thư viện. File phải nhỏ hơn hoặc bằng `music_max_size_mb` (mặc định 20MB). Hệ thống SHALL lưu file vào `data/music/` và ghi metadata vào bảng `music`.

#### Scenario: Upload thành công
- **WHEN** user upload file nhạc hợp lệ (≤20MB, đúng định dạng) qua `POST /music/upload`
- **THEN** file được lưu vào `data/music/`, bản ghi `music` được tạo với `name`, `file_path`, `duration_sec`, `created_at`; response trả về `{id, name, duration_sec}`

#### Scenario: Upload file quá lớn
- **WHEN** user upload file nhạc > 20MB
- **THEN** hệ thống trả về HTTP 400 với thông báo lỗi kích thước

#### Scenario: Upload định dạng không hỗ trợ
- **WHEN** user upload file có extension không phải `.mp3`, `.wav`, `.ogg`, `.m4a`
- **THEN** hệ thống trả về HTTP 400 với thông báo định dạng không hỗ trợ

### Requirement: Liệt kê thư viện nhạc
Hệ thống SHALL trả về danh sách tất cả nhạc trong thư viện qua `GET /music`, bao gồm `id`, `name`, `duration_sec`, `file_path`.

#### Scenario: Liệt kê thư viện
- **WHEN** user gọi `GET /music`
- **THEN** hệ thống trả về danh sách tất cả bản nhạc, sắp xếp theo `created_at` giảm dần

#### Scenario: Thư viện rỗng
- **WHEN** chưa có nhạc nào được upload
- **THEN** hệ thống trả về danh sách rỗng, không lỗi

### Requirement: Xóa nhạc
Hệ thống SHALL cho phép xóa nhạc khỏi thư viện qua `POST /music/{id}/delete`. Khi xóa, hệ thống SHALL xóa file vật lý và bản ghi DB. Nếu nhạc đang được gán cho book nào đó, cột `music_id` của book đó SHALL được set về `NULL`.

#### Scenario: Xóa nhạc thành công
- **WHEN** user xóa nhạc với id hợp lệ
- **THEN** file bị xóa khỏi disk, bản ghi DB bị xóa, các book có `music_id` trỏ vào nhạc này được set `music_id = NULL`

#### Scenario: Xóa nhạc không tồn tại
- **WHEN** user xóa nhạc với id không tồn tại
- **THEN** hệ thống trả về HTTP 404

### Requirement: Preview nhạc
Hệ thống SHALL cho phép stream file nhạc để preview qua `GET /music/{id}/file`.

#### Scenario: Preview nhạc
- **WHEN** user truy cập `GET /music/{id}/file`
- **THEN** hệ thống stream file nhạc với đúng MIME type

### Requirement: Gán nhạc cho book
Hệ thống SHALL cho phép gán một bản nhạc từ thư viện cho book (cột `music_id` trong bảng `book`). Cột `music_volume` (REAL, default `0.15`) lưu tỉ lệ âm lượng nhạc so với TTS audio.

#### Scenario: Gán nhạc
- **WHEN** user chọn nhạc cho book qua UI book detail
- **THEN** `book.music_id` được cập nhật, nhạc được dùng khi render video cho book đó

#### Scenario: Bỏ gán nhạc
- **WHEN** user chọn "Không có nhạc" cho book
- **THEN** `book.music_id` được set về `NULL`, video render không có nhạc nền

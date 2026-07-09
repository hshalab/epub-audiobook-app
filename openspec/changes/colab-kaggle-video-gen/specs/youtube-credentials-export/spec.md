## ADDED Requirements

### Requirement: Endpoint export YouTube credentials
Hệ thống SHALL cung cấp endpoint `GET /youtube/kaggle-credentials` trả về JSON `{client_id, client_secret, refresh_token}` từ `youtube_credentials` table. Endpoint chỉ hoạt động khi YouTube đã được kết nối (credentials tồn tại trong DB).

#### Scenario: Export credentials thành công
- **WHEN** user gọi `GET /youtube/kaggle-credentials` và YouTube đã được connect
- **THEN** response JSON có `{client_id, client_secret, refresh_token}` từ credentials mới nhất

#### Scenario: YouTube chưa được connect
- **WHEN** user gọi `GET /youtube/kaggle-credentials` nhưng chưa có credentials trong DB
- **THEN** hệ thống trả về HTTP 400 với thông báo yêu cầu connect YouTube trước

### Requirement: Nút Copy YouTube credentials trên UI
Trang `/youtube` SHALL có nút "Copy YouTube credentials for Kaggle/Colab" hiển thị khi YouTube đã connect. Khi click SHALL fetch `GET /youtube/kaggle-credentials` và copy JSON vào clipboard, tương tự nút "Copy Kaggle credentials" trên trang `/drive`.

#### Scenario: Copy credentials thành công
- **WHEN** user click nút "Copy YouTube credentials for Kaggle/Colab"
- **THEN** JSON credentials được copy vào clipboard, nút hiển thị feedback "Copied!"

#### Scenario: Nút ẩn khi chưa connect
- **WHEN** YouTube chưa được connect
- **THEN** nút không hiển thị hoặc bị disabled

## Why

Luồng upload hiện tại phụ thuộc vào Google Drive API, OAuth và quota, trong khi máy chạy ứng dụng đã có thể dùng Google Drive for desktop để đồng bộ file cục bộ ổn định hơn. Người dùng cần ánh xạ từng tài khoản Google Drive với một thư mục đồng bộ riêng và chọn đúng tài khoản đích khi export.

## What Changes

- Thay cơ chế upload package qua Google Drive API bằng việc ghi package trực tiếp vào thư mục Google Drive for desktop trên máy.
- Cho phép tạo, sửa và xóa nhiều cấu hình đích đồng bộ; mỗi cấu hình gồm tên hiển thị, tài khoản/email và đường dẫn thư mục cục bộ.
- Cho phép chọn cấu hình tài khoản đích khi export một patch hoặc nhiều patch; không tự động round-robin giữa tài khoản.
- Kiểm tra thư mục đích trước khi ghi và báo lỗi rõ ràng nếu đường dẫn không tồn tại, không phải thư mục hoặc không thể ghi.
- Lưu tài khoản đích và đường dẫn tương đối của package trong lịch sử export để ứng dụng có thể tìm kết quả đã được Google Drive for desktop đồng bộ về.
- Loại bỏ yêu cầu kết nối OAuth Google Drive khỏi luồng export/import này. Việc đăng nhập và đồng bộ cloud do Google Drive for desktop quản lý bên ngoài ứng dụng.

## Capabilities

### New Capabilities
- `drive-desktop-sync-targets`: Quản lý thư mục Google Drive for desktop theo từng tài khoản và dùng đích được chọn cho export/import package.

### Modified Capabilities

Không có.

## Impact

- Ảnh hưởng trang cấu hình Drive, màn hình export patch/batch, lịch sử export và luồng import kết quả.
- Cần thay đổi schema SQLite để lưu các đích đồng bộ và tham chiếu đích trên bản ghi export.
- Các module dự kiến bị ảnh hưởng: `app/db.py`, `app/models.py`, `app/repository.py`, `app/routes/drive.py`, `app/routes/patches.py`, `app/drive_export.py` và các template liên quan.
- Không thêm SDK hay dependency mới; sử dụng filesystem cục bộ và Google Drive for desktop đã cài trên máy.

## 1. Data model và repository

- [x] 1.1 Thêm bảng `drive_sync_target` và migration bổ sung `sync_target_id`, `local_folder_path` nullable vào `patch_export`
- [x] 1.2 Thêm model và repository CRUD cho đích đồng bộ, gồm truy vấn danh sách và kiểm tra bản ghi tồn tại
- [x] 1.3 Cập nhật repository lịch sử export để ghi/đọc snapshot đích desktop mà vẫn đọc được bản ghi Drive API legacy
- [x] 1.4 Thêm test migration trên database cũ và test repository cho xóa đích nhưng giữ lịch sử export

## 2. Quản lý đích Google Drive Desktop

- [x] 2.1 Thêm validation dùng chung cho tên, email và đường dẫn tuyệt đối tồn tại, là directory, có thể ghi
- [x] 2.2 Thay đổi route `/drive` và thêm các route create, update, delete cho `drive_sync_target`
- [x] 2.3 Thiết kế lại `app/templates/drive.html` để giải thích vai trò Google Drive Desktop và quản lý nhiều folder theo tài khoản
- [x] 2.4 Thêm test route cho CRUD thành công, đường dẫn không hợp lệ và xóa cấu hình đã có lịch sử

## 3. Export qua filesystem

- [x] 3.1 Thêm helper copy package vào folder tạm dưới đích, rename sang tên cuối và dọn folder tạm khi lỗi
- [x] 3.2 Cập nhật export single để yêu cầu `sync_target_id`, ghi vào đúng folder và lưu snapshot sau khi công bố package thành công
- [x] 3.3 Cập nhật batch export để yêu cầu một `sync_target_id`, bỏ round-robin và ghi toàn bộ batch vào cùng đích
- [x] 3.4 Cập nhật các form tại màn hình patch/book để hiển thị và gửi lựa chọn tài khoản đích
- [x] 3.5 Thêm test export single/batch cho đúng đích, thiếu đích, đích mất quyền ghi, trùng tên và lỗi copy không tạo lịch sử thành công

## 4. Import từ filesystem

- [x] 4.1 Thay luồng import Drive API bằng đọc từ `local_folder_path`, ưu tiên `output` và fallback về root package
- [x] 4.2 Giữ hành vi import contiguous prefix, cập nhật trạng thái và cho phép chạy lại khi Drive Desktop chưa sync đủ
- [x] 4.3 Trả thông báo hướng dẫn cho folder bị mất và bản ghi Drive API legacy không có đường dẫn cục bộ
- [x] 4.4 Thêm test import hoàn tất, import từng phần, chạy lại, folder mất và lịch sử legacy

## 5. Hoàn thiện và xác minh

- [x] 5.1 Loại bỏ phụ thuộc OAuth/Google Drive API khỏi điều kiện hiển thị và các endpoint export/import desktop; giữ code legacy chỉ khi còn consumer cụ thể
- [x] 5.2 Cập nhật nội dung UI và cấu hình mẫu liên quan để không yêu cầu OAuth cho Google Drive Desktop
- [x] 5.3 Chạy toàn bộ test suite và smoke test thủ công với hai folder đại diện hai tài khoản Google Drive Desktop

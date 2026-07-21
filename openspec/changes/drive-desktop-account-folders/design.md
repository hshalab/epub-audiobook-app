## Context

Ứng dụng hiện lưu OAuth credentials, chọn tài khoản Drive và dùng Google Drive API để tạo folder, upload package và tải file kết quả. Với Google Drive for desktop, mỗi tài khoản đã được mount thành một thư mục cục bộ; ứng dụng chỉ cần đọc/ghi filesystem, còn client desktop chịu trách nhiệm đồng bộ.

Đây là ứng dụng local, vì vậy đường dẫn được cấu hình là đường dẫn trên chính máy chạy server. Trình duyệt không upload một folder và ứng dụng không cố suy luận email từ cấu trúc mount của Google Drive.

## Goals / Non-Goals

**Goals:**

- Quản lý nhiều đích đồng bộ, mỗi đích ánh xạ một tài khoản/email tới một thư mục cục bộ.
- Bắt buộc chọn một đích khi export patch hoặc batch.
- Ghi package atomically ở mức folder để Google Drive for desktop không quan sát package dở dang.
- Import kết quả từ đúng folder đã lưu trong lịch sử export.
- Giữ lịch sử export có thể đọc được khi cấu hình đích bị đổi hoặc xóa.

**Non-Goals:**

- Cài đặt, đăng nhập, điều khiển hoặc theo dõi trạng thái sync của Google Drive for desktop.
- Duyệt folder cloud bằng Google Drive API.
- Tự động phân phối patch giữa nhiều tài khoản.
- Di chuyển hoặc xóa dữ liệu của các export cũ khi sửa/xóa cấu hình.
- Chuyển đổi tự động các bản ghi export Drive API cũ thành đường dẫn filesystem.

## Decisions

### Lưu đích đồng bộ độc lập với OAuth account

Thêm bảng `drive_sync_target` gồm `id`, `name`, `account_email`, `folder_path`, timestamps. `folder_path` là đường dẫn tuyệt đối đã chuẩn hóa trên máy server và không bắt buộc unique vì người dùng có thể đặt nhiều nhãn cho các thư mục con khác nhau.

Các bảng OAuth cũ không được tái sử dụng vì credentials API và mount desktop có vòng đời khác nhau. Chúng có thể được giữ lại trong migration để bảo toàn dữ liệu cũ, nhưng UI mới không yêu cầu OAuth cho luồng này.

### Lưu snapshot vị trí export

`patch_export` bổ sung `sync_target_id` nullable và `local_folder_path` nullable. Export mới ghi cả ID đích lẫn đường dẫn tuyệt đối của folder package tại thời điểm export. Import ưu tiên snapshot `local_folder_path`, không ghép lại từ cấu hình hiện tại; nhờ đó đổi đường dẫn cấu hình không âm thầm trỏ lịch sử cũ sang nơi khác.

Các cột Drive API cũ vẫn nullable/được giữ cho lịch sử legacy. Bản ghi legacy không có `local_folder_path` sẽ không được import qua desktop và UI phải hướng dẫn export lại hoặc dùng import file thủ công.

### Chọn đích rõ ràng cho mỗi thao tác

Form export single và batch gửi `sync_target_id`. Server xác thực đích tồn tại và folder gốc có thể ghi; không dùng mặc định ngầm và không round-robin. Batch được ghi trọn vẹn vào một đích được chọn để manifest và kết quả luôn nằm cùng vị trí.

### Copy vào folder tạm rồi rename

Package được build trong vùng tạm hiện tại, sau đó copy vào folder ẩn/tạm nằm bên trong folder đích và rename thành tên cuối khi hoàn tất. Tên cuối tiếp tục dùng quy ước timestamp hiện có; nếu đã tồn tại, thao tác thất bại thay vì ghi đè. Cùng filesystem cho phép rename an toàn hơn và giảm khả năng Drive Desktop sync package chưa hoàn chỉnh.

### Import trực tiếp từ filesystem

Import tìm `output/chunk_NNN.wav` bên dưới `local_folder_path`, fallback về root package cho format cũ tương thích. Logic contiguous-prefix và cập nhật trạng thái hiện tại được giữ nguyên; chỉ nguồn file đổi từ download API sang copy cục bộ.

### Quản lý đường dẫn tại trang Drive

Trang `/drive` đổi trọng tâm thành danh sách đích Google Drive Desktop và CRUD qua form server-rendered. Trường đường dẫn nhận đường dẫn tuyệt đối vì browser không thể chọn tùy ý một server-side folder một cách portable. Server trim, resolve và kiểm tra tồn tại, loại directory và quyền ghi khi tạo/cập nhật; không tự tạo mount root để tránh che giấu lỗi cấu hình.

## Risks / Trade-offs

- [Google Drive for desktop chưa sync xong khi import] → Chỉ import prefix file liên tục đã xuất hiện; người dùng có thể chạy import lại.
- [Folder bị tháo mount hoặc đổi ký tự ổ đĩa] → Kiểm tra lại đường dẫn ở mỗi export/import và trả lỗi có thể xử lý, không đánh dấu mất dữ liệu.
- [Drive Desktop bắt đầu sync folder tạm] → Dùng tên tạm riêng và rename sau khi copy; không thể bảo đảm atomic đối với hành vi của client ngoài ứng dụng.
- [Đường dẫn cấu hình cho phép ghi ngoài Drive] → Đây là ứng dụng local do operator tin cậy; UI cảnh báo và server chỉ ghi package con, không xóa nội dung ngoài package do thao tác hiện tại tạo.
- [Export API cũ không import được bằng filesystem] → Giữ lịch sử hiển thị và yêu cầu export lại hoặc import upload thủ công; tránh migration suy đoán không an toàn.

## Migration Plan

1. Tạo `drive_sync_target` và bổ sung các cột nullable cho `patch_export`.
2. Triển khai CRUD đích và UI chọn đích trước khi chuyển endpoint export/import.
3. Chuyển export/import mới sang filesystem; giữ dữ liệu OAuth và lịch sử cũ nhưng không dùng trong luồng desktop.
4. Kiểm thử với ít nhất hai folder mount đại diện hai tài khoản, gồm export single, batch, import chưa sync đủ và import hoàn tất.

Rollback code vẫn đọc được database vì các thay đổi chỉ thêm bảng/cột. Package đã ghi trong folder desktop không bị rollback hoặc xóa.

## Open Questions

- Có cần một đích mặc định để giảm thao tác chọn sau khi hành vi chọn rõ ràng đã được xác nhận thực tế hay không; phiên bản đầu không thêm mặc định.

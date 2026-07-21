## ADDED Requirements

### Requirement: Quản lý đích đồng bộ theo tài khoản
Hệ thống SHALL cho phép người dùng tạo, sửa, xem và xóa các đích đồng bộ Google Drive Desktop. Mỗi đích MUST có tên hiển thị, tài khoản/email và đường dẫn tuyệt đối tới một thư mục cục bộ.

#### Scenario: Tạo đích hợp lệ
- **WHEN** người dùng lưu tên, tài khoản và đường dẫn tới một thư mục tồn tại có thể ghi
- **THEN** hệ thống lưu đích và hiển thị đích đó trong danh sách lựa chọn export

#### Scenario: Đường dẫn không hợp lệ
- **WHEN** người dùng lưu đường dẫn không tồn tại, không phải thư mục hoặc không thể ghi
- **THEN** hệ thống từ chối thay đổi và hiển thị lý do cụ thể

#### Scenario: Xóa đích có lịch sử export
- **WHEN** người dùng xóa một đích đã được dùng để export
- **THEN** hệ thống xóa cấu hình nhưng MUST giữ nguyên lịch sử và đường dẫn snapshot của các export trước đó

### Requirement: Chọn tài khoản đích khi export
Hệ thống SHALL yêu cầu người dùng chọn chính xác một đích đồng bộ cho mỗi thao tác export patch hoặc batch và MUST ghi toàn bộ package vào folder của đích đó. Hệ thống MUST NOT tự động round-robin package giữa các đích.

#### Scenario: Export một patch
- **WHEN** người dùng chọn một patch và một đích đồng bộ hợp lệ rồi thực hiện export
- **THEN** hệ thống tạo một folder package bên dưới folder đích và lưu đích cùng đường dẫn package vào lịch sử export

#### Scenario: Export nhiều patch
- **WHEN** người dùng chọn nhiều patch và một đích đồng bộ hợp lệ rồi thực hiện batch export
- **THEN** hệ thống ghi toàn bộ batch vào một folder package duy nhất bên dưới đích đã chọn và tạo lịch sử tương ứng cho từng patch

#### Scenario: Không chọn đích
- **WHEN** yêu cầu export không chứa đích đồng bộ
- **THEN** hệ thống từ chối yêu cầu và không tạo package trong bất kỳ folder đồng bộ nào

#### Scenario: Đích không còn khả dụng
- **WHEN** folder của đích đã chọn không còn tồn tại hoặc không thể ghi tại thời điểm export
- **THEN** hệ thống báo lỗi có thể xử lý và không tạo bản ghi export thành công

### Requirement: Công bố package hoàn chỉnh
Hệ thống SHALL hoàn tất việc copy package trong một folder tạm bên dưới folder đồng bộ trước khi đổi tên thành folder package cuối cùng. Hệ thống MUST NOT ghi đè một folder package cuối đã tồn tại.

#### Scenario: Copy package thành công
- **WHEN** tất cả file package đã được copy thành công
- **THEN** hệ thống rename folder tạm thành tên package cuối và chỉ sau đó ghi lịch sử export thành công

#### Scenario: Copy package thất bại
- **WHEN** có lỗi xảy ra trước khi package được công bố
- **THEN** hệ thống không tạo lịch sử export thành công và dọn folder tạm do thao tác đó tạo nếu có thể

#### Scenario: Trùng tên package
- **WHEN** folder package cuối đã tồn tại
- **THEN** hệ thống từ chối export và MUST NOT sửa nội dung folder đã tồn tại

### Requirement: Import kết quả từ folder đã export
Hệ thống SHALL import audio từ đường dẫn package được snapshot trong bản ghi export gần nhất của patch, ưu tiên folder con `output` và fallback về root package. Hệ thống MUST không phụ thuộc Google Drive API hoặc OAuth cho import desktop.

#### Scenario: Import kết quả đã đồng bộ
- **WHEN** các file `chunk_NNN.wav` liên tục tồn tại trong folder output của package
- **THEN** hệ thống copy các file chưa có vào chunk directory cục bộ và cập nhật số chunk đã import

#### Scenario: Đồng bộ chưa hoàn tất
- **WHEN** chỉ một prefix liên tục của các file kết quả tồn tại
- **THEN** hệ thống import prefix đó, giữ trạng thái chưa hoàn tất và cho phép người dùng import lại sau

#### Scenario: Folder package không còn tồn tại
- **WHEN** đường dẫn package trong lịch sử không còn tồn tại
- **THEN** hệ thống báo lỗi có thể xử lý và không thay đổi các chunk đã import trước đó

#### Scenario: Lịch sử Drive API cũ
- **WHEN** bản ghi export gần nhất không có đường dẫn package cục bộ
- **THEN** hệ thống hướng dẫn người dùng export lại qua Google Drive Desktop hoặc dùng import file thủ công

### Requirement: Google Drive Desktop chịu trách nhiệm đồng bộ cloud
Hệ thống SHALL chỉ thực hiện thao tác filesystem đối với luồng export/import desktop và MUST không yêu cầu OAuth Google Drive, gọi Google Drive API hoặc tuyên bố package đã đồng bộ lên cloud.

#### Scenario: Export filesystem hoàn tất
- **WHEN** folder package đã được công bố thành công trong đích cục bộ
- **THEN** hệ thống báo export cục bộ thành công và cho biết việc đồng bộ cloud do Google Drive Desktop xử lý

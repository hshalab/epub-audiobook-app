# Quản lý tài khoản Google Drive (export patch)

App có tính năng export patch/batch lên Google Drive tại trang `/drive` (xem `app/routes/drive.py`,
`app/drive_export.py`, bảng `drive_sync_target`). Cơ chế: app copy package export vào một **folder cục bộ**
trên máy chạy server; folder đó phải được đồng bộ lên đúng tài khoản Google Drive bằng cách nào đó.
App không quan tâm folder được đồng bộ bằng cách nào — chỉ cần đúng đường dẫn.

Hiện có 10 tài khoản (`codex1`..`codex10@g.lsts.edu.vn`), chia làm 2 nhóm vì **Google Drive for Desktop
chỉ cho đăng nhập tối đa 4 tài khoản cùng lúc** (giới hạn cứng của chính ứng dụng, không cấu hình được).

## Nhóm 1 — codex1-4: Google Drive for Desktop (mount ổ đĩa)

| Sync target | Email | Ổ đĩa | Folder |
|---|---|---|---|
| codex1 | codex1@g.lsts.edu.vn | H: | `H:\My Drive\EPUB Audiobook Exports` |
| codex2 | codex2@g.lsts.edu.vn | G: | `G:\My Drive\EPUB Audiobook Exports` |
| codex3 | codex3@g.lsts.edu.vn | I: | `I:\My Drive\EPUB Audiobook Exports` |
| codex4 | codex4@g.lsts.edu.vn | J: | `J:\My Drive\EPUB Audiobook Exports` |

Các tài khoản này đăng nhập trực tiếp trong Google Drive for Desktop (icon khay hệ thống → Preferences →
Add another account). App export thẳng vào folder trên ổ đĩa đã mount — Drive for Desktop tự đồng bộ lên
cloud ngay, **không cần script gì thêm**.

Muốn đổi 1 trong 4 tài khoản này: sign out tài khoản cũ trong Drive for Desktop trước (vì đã đụng trần 4),
đăng nhập tài khoản mới, rồi sửa lại `account_email` + `folder_path` của sync target tương ứng ở trang `/drive`.

## Nhóm 2 — codex5-10: rclone (không mount ổ đĩa)

| Sync target | Email | Remote rclone | Folder cục bộ (staging) |
|---|---|---|---|
| codex5 | codex5@g.lsts.edu.vn | `codex5:` | `D:\RcloneDriveStaging\codex5` |
| codex6 | codex6@g.lsts.edu.vn | `codex6:` | `D:\RcloneDriveStaging\codex6` |
| codex7 | codex7@g.lsts.edu.vn | `codex7:` | `D:\RcloneDriveStaging\codex7` |
| codex8 | codex8@g.lsts.edu.vn | `codex8:` | `D:\RcloneDriveStaging\codex8` |
| codex9 | codex9@g.lsts.edu.vn | `codex9:` | `D:\RcloneDriveStaging\codex9` |
| codex10 | codex10@g.lsts.edu.vn | `codex10:` | `D:\RcloneDriveStaging\codex10` |

App export vào folder staging cục bộ (giống hệt cách nó ghi vào ổ Drive Desktop-mounted). Folder này **không**
tự động lên cloud — phải chạy script đẩy lên:

```powershell
# Đẩy cả 6 tài khoản
.\scripts\rclone_push_drives.ps1

# Chỉ đẩy 1 tài khoản
.\scripts\rclone_push_drives.ps1 codex7
```

Chạy script này sau khi export xong (thủ công), hoặc tự thêm Windows Task Scheduler nếu muốn tự động theo chu kỳ.

### ⚠️ Luật an toàn quan trọng nhất: KHÔNG BAO GIỜ dùng `rclone sync` để đẩy lên các remote này

Script `rclone_push_drives.ps1` dùng `rclone copy`, **không phải** `rclone sync`. Đây không phải lựa chọn tuỳ ý:

- `rclone sync <local> <remote>` làm cho remote **giống hệt** local — nghĩa là nó **xoá** mọi thứ trên remote
  không có trong folder local.
- Ngày 2026-07-21, chạy thử với folder staging codex5 đang trống đã **xoá mất 173 file export thật** đã có sẵn
  trên tài khoản đó từ trước (không phải do script này tạo ra). Phục hồi được nhờ Google Drive Trash
  (`rclone backend untrash "codex5:EPUB Audiobook Exports"`), nhưng lần sau có thể không may mắn vậy.
- Vì mỗi lần export, app tạo folder tên riêng có timestamp (xem `app/drive_export.py`), không bao giờ cần
  xoá gì ở phía remote — `copy` là đủ và an toàn tuyệt đối cho mục đích này.

Nếu cần viết thêm script/thao tác rclone khác nhắm vào các remote này, luôn dùng `copy`, không dùng `sync`,
trừ khi bạn chắc chắn 100% folder đích không chứa gì cần giữ.

## Thêm tài khoản mới (vượt quá 10)

1. `rclone config create <tenmoi> drive scope=drive config_is_local=true` — mở trình duyệt để đăng nhập.
2. Tạo folder staging: `mkdir D:\RcloneDriveStaging\<tenmoi>`.
3. Thêm sync target trong DB (hoặc qua UI `/drive`):
   ```python
   import sqlite3
   from datetime import datetime, timezone
   conn = sqlite3.connect("data/app.db")
   now = datetime.now(timezone.utc).isoformat()
   conn.execute(
       "INSERT INTO drive_sync_target (name, account_email, folder_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
       ("<tenmoi>", "<email>", r"D:\RcloneDriveStaging\<tenmoi>", now, now),
   )
   conn.commit()
   ```
4. Thêm remote đó vào `scripts/rclone_push_drives.ps1` (mảng `$targets`).

## Lưu ý khác

- rclone hiện dùng client_id dùng chung mặc định của chính rclone (thấy warning "This remote uses rclone's
  shared Google Drive client_id... will stop working during 2026" mỗi lần chạy). Trước khi nó ngừng hoạt động,
  cần tạo Client ID/Secret riêng trên Google Cloud Console và cập nhật lại từng remote — xem
  https://rclone.org/drive/#making-your-own-client-id.
- Kiểm tra danh sách remote: `rclone listremotes`. Kiểm tra dung lượng/quota: `rclone about <remote>:`.
- Nếu vô tình xoá nhầm gì trên remote: kiểm tra trash trước khi hoảng — `rclone lsf <remote>: --drive-trashed-only -R`,
  rồi `rclone backend untrash "<remote>:<path>"` để khôi phục hàng loạt.

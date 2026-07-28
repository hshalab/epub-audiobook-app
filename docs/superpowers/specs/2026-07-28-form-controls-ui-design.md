# Chuẩn hóa giao diện form control

Ngày: 2026-07-28
Phạm vi: `app/static/style.css` (lớp dùng chung), `app/templates/book_detail.html`, `app/templates/video_creator.html`

## Mục tiêu

Làm các form control trong app đồng bộ và gọn hơn: cùng chiều cao, cùng khoảng cách, canh hàng đúng.
Giữ nguyên ngôn ngữ thị giác hiện tại — đây là việc dọn dẹp, không phải thiết kế lại.

Không nằm trong phạm vi: đổi hành vi form, đổi bảng màu, đổi bố cục trang.

## Vấn đề hiện tại

1. `.form-grid` được dùng ở `book_detail.html` dòng 44 và 155 nhưng chỉ được định nghĩa trong
   khối `<style>` cục bộ của `video_creator.html`. Trên trang book detail class này vô nghĩa,
   nên form YouTube settings đổ thành một cột dồn cục.
2. `label` là `display:block` còn input là `width:100%`. Mẫu `<label>Genre tags <input></label>`
   (24 chỗ, 3 file) vì thế xuống dòng lộn xộn, không có khoảng cách giữa nhãn và ô nhập.
3. Chuỗi `style="display:flex;align-items:center;gap:var(--space-xs);font-weight:500"` lặp 6 lần
   trong `book_detail.html` chỉ để làm một checkbox nằm ngang với nhãn của nó.
4. `input[type=range]` không nằm trong danh sách selector được style ở `style.css` dòng 590-606,
   nên hiển thị theo mặc định của từng trình duyệt.
5. `.batch-patch-checkbox, #batch-select-all { transform: scale(2) }` phóng to phần nhìn nhưng
   hộp layout vẫn 16px, khiến checkbox đè lên ô kế bên và vùng bấm lệch phần nhìn.
6. Input cao khoảng 36px (`padding: 0.6rem 0.85rem` ở `font-size-sm`), `.btn-sm` cao khoảng 26px
   (`padding: 0.35rem 0.75rem` ở `font-size-xs`). Mọi hàng ngang trộn input và nút đều lệch đáy.
7. Trong modal Publish patch, nút `Use book default` không có class nút nào, và nằm bên trong
   `<label>` nên bấm nút cũng kích hoạt label.

## Thiết kế

### 1. Lớp form dùng chung

Thêm vào `:root`:

```css
--control-height: 2.25rem;
```

Thêm vào khối `/* --- Forms --- */` của `style.css`:

- `.form-grid` — `display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-md)`.
  Xóa bản định nghĩa trùng trong `<style>` của `video_creator.html`.
- `min-height: var(--control-height)` cho `input[type=text|number|email|password]`, `select`, `textarea`.
- `.form-inline` — `display: flex; gap: var(--space-sm); align-items: center; flex-wrap: wrap`,
  thay cho các chuỗi inline style cùng nội dung.
- Label bọc control dạng nhập liệu chuyển thành cột:

```css
label:has(> input:not([type=checkbox]):not([type=radio])),
label:has(> select),
label:has(> textarea) {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}
```

Dùng `:has()` để một quy tắc xử lý hết 24 chỗ thay vì thêm class thủ công vào từng label.
Yêu cầu Chrome 105+ / Safari 15.4+ / Firefox 121+ — chấp nhận được với app chạy nội bộ.

### 2. Checkbox và radio

```css
input[type=checkbox], input[type=radio] {
  width: 1rem; height: 1rem;
  margin: 0;
  flex: none;
  accent-color: var(--text-primary);
}

label:has(> input[type=checkbox]),
label:has(> input[type=radio]) {
  display: flex; align-items: center; gap: var(--space-xs);
}
```

`margin: 0` thay cho `margin-right: var(--space-xs)` hiện tại — khoảng cách do `gap` của label lo.
Việc này cũng sửa luôn `.chapter-checkbox-item`, nơi `gap: var(--space-sm)` và `margin-right`
đang cộng dồn thành khoảng cách đôi.

Sau thay đổi này, xóa được trong `book_detail.html`:
- 6 chuỗi inline style ở checkbox chuẩn hóa TTS và checkbox hiệu ứng LightTTS
- class `.vc-check` (trở thành thừa)

Checkbox trong bảng patch: bỏ `transform: scale(2)`, thay bằng kích thước thật.

```css
.batch-patch-checkbox, #batch-select-all { width: 18px; height: 18px; }
```

Ô `<td>` chứa checkbox nhận `padding` để giữ vùng bấm rộng.

### 3. Select và input số/range

```css
input[type=range] {
  width: 100%;
  height: var(--control-height);
  accent-color: var(--text-primary);
  background: transparent;
  padding: 0;
  border: 0;
}
```

Dùng `accent-color` thay vì tự vẽ track và thumb: ít code, tự đúng ở cả light và dark mode,
không đổi ngôn ngữ thị giác.

`select` **giữ nguyên mũi tên native**, chỉ nhận `min-height: var(--control-height)` như input.

Đã cân nhắc rồi loại bỏ phương án mũi tên tự vẽ:

- Bọc `.select-wrap` + pseudo-element: cần sửa 45 chỗ trên 6 template, và vẫn hụt các select do
  JS sinh động trong `video_creator.js` và bảng patch.
- `background-image` với SVG màu cứng: app có dark mode qua cả `prefers-color-scheme` lẫn
  `[data-theme]` (`style.css` dòng 93-110), nên phải nuôi ba biến thể màu.

Vấn đề thực tế cần giải là select lệch chiều cao so với input, và `min-height` đã giải quyết.
Mũi tên native tự bám theme mà không tốn chi phí bảo trì nào.

Gom các input số hẹp thành class dùng chung, thay cho ba khai báo riêng lẻ hiện có ở
`.auto-build-form input[type=number]`, `.range-row input`, `.replace-rule-form input[type=number]`:

```css
.input-narrow  { width: 6rem; max-width: none; text-align: center; }
.input-xnarrow { width: 5rem; max-width: none; text-align: center; }
```

### 4. Nút trong modal Publish patch

Trong `book_detail.html` dòng 68-69:

- Thêm `class="btn-outline btn-sm"` cho các nút `Use book default`.
- Đưa nút ra ngoài `<label>`. Cấu trúc mới: `.form-group` chứa `<label>` và một hàng
  `.field-with-action` (input `flex: 1`, nút bám phải).
- Thay nhãn tên biến thô bằng nhãn tiếng Việt qua một dict Jinja:
  `title` → Tiêu đề, `description` → Mô tả, `genre_tags` → Thẻ thể loại,
  `privacy_status` → Chế độ hiển thị.

## Rủi ro và cách xử lý

**Select nhỏ bị ép cao.** `.image-type-select` (`padding: 0.2rem 0.4rem`) và các select nhỏ trong
`.patch-search`, `.patch-inline-section` sẽ bị `min-height: var(--control-height)` làm cao lên.
Xử lý: đặt `min-height: 0` cho các select nhỏ đã biết này.

**Checkbox không bọc trong label.** Các checkbox "select all" ở đầu bảng trong `youtube.html`,
`drive.html`, `database_io.html`, `video_creator.html` không nằm trong `<label>` nên không được
`gap` bù lại phần `margin-right` bị bỏ. Cần kiểm tra từng chỗ; nếu sát chữ thì thêm khoảng cách
ở phần tử cha.

**Phạm vi lan tỏa.** Sửa `style.css` chạm tới 8 template có checkbox/range và 3 template có label
bọc input.

## Xác minh

Chạy dev server, chụp màn hình đối chiếu 4 trang: `book_detail` (gồm các modal Video config,
YouTube settings, Publish patch), `video_creator`, `patch_builder`, `upload`.
Kiểm tra riêng: chiều cao select so với input trong cùng hàng, khoảng cách checkbox, và
checkbox bảng patch không đè ô kế bên.

## Khác biệt phát sinh khi triển khai

1. `--control-height` đặt 2.375rem chứ không phải 2.25rem. Input vốn đã cao 38px do padding
   của chính nó; lấy đúng con số đó làm chuẩn thì mọi thứ khác canh theo mà không phải
   sửa padding input, tức không đổi diện mạo input.

2. Thêm `input:not([type])` vào danh sách selector gốc. Phát hiện khi đo: 8 input trên trang
   book detail không khai `type`, nên tuy hành xử như text lại không khớp `input[type="text"]`
   và render trần ở 21px. Nhân tiện thêm `search`, `url`, `tel`.

3. Thêm style cho `input[type="color"]` (3 chỗ trong modal overlay). Không có trong thiết kế
   ban đầu, nhưng nó nằm cùng lưới với input số và thấp hơn hẳn.

4. Hai rule `label:has(...)` bọc trong `:where()`. Bản đầu có specificity (0,1,2), đè cả
   `.chapter-checkbox-item` và bóp gap của nó từ 8px xuống 4px. `:where()` hạ về đúng
   specificity của `label`, để class component vẫn thắng.

5. Rule checkbox bảng patch phải viết `input[type="checkbox"].batch-patch-checkbox`.
   Dạng `.batch-patch-checkbox` (0,1,0) thua `input[type="checkbox"]` (0,1,1) trong `style.css`.

6. Nút trong `.form-inline` và `.field-with-action` cần cả `display:inline-flex` lẫn
   `min-height`. Các nút dạng `<a class="btn-outline">` không khớp rule gốc `.btn, button`
   nên không có flex centering; chỉ đặt `min-height` làm chữ dính lên mép trên.

7. Bump `style.css?v=` trong `base.html` lên `20260728`, nếu không trình duyệt vẫn dùng bản cache.

8. `.patch-search select` **không** được miễn trừ `min-height` như thiết kế ban đầu dự tính —
   nó nằm cạnh ô tìm kiếm nên phải cao bằng. Chỉ `.image-type-select` và
   `.patch-inline-section select` giữ nguyên kích thước nhỏ.

## Ghi nhận, không làm

`privacy_status` trong modal Publish patch là `<input>` text, trong khi ở modal YouTube settings
cùng trường đó là `<select>` ba lựa chọn. Sửa là đổi hành vi, không phải giao diện — để quyết riêng.

Nút dạng `<a class="btn-outline btn-sm">` trên toàn app không có viền: rule gốc
`.btn, button, [type=submit]` đặt `border: 1px solid transparent`, nhưng các anchor này không mang
class `.btn` nên `border-style` vẫn là `none`. Chúng hiện ra như chữ có padding, khác hẳn `<button>`
ngay cạnh. Sửa được bằng một dòng, nhưng đổi diện mạo nút ở mọi trang nên nằm ngoài phạm vi đã duyệt.

Trang book detail và patch builder đều tràn ngang (scrollWidth khoảng 2110px ở viewport 1023px).
Đã kiểm chứng đây là lỗi có sẵn: gỡ toàn bộ CSS mới thì con số không giảm.

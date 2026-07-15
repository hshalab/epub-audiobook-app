# Video Creator — Per-File Overlay Config + Full Feature Parity

**Date**: 2025-07-15
**Status**: Approved for implementation

---

## Context

`app/templates/video_creator.html` hiện tại chỉ có 1 config overlay global áp dụng cho tất cả files trong batch. `app/templates/book_detail.html` (Studio) có full overlay features: position, alignment, font, shadow, background box, marquee. Cần refactor video_creator để:

1. Mỗi file trong batch có overlay config riêng
2. Studio panel hiển thị full features như book_detail.html
3. Preview & edit multi-file đồng thời

---

## Design

### 1. Data Model (Client-side JS)

```javascript
// overlayConfigs[fileIndex] = {
  text: "",
  position: "top",        // top | center | bottom
  alignment: "center",    // left | center | right
  font_size: 52,
  text_color: "#FFFFFF",
  margin: 20,
  offset_x: 0,
  offset_y: 0,
  shadow: {
    enabled: false,
    color: "#000000",
    offset: 3
  },
  box: {
    enabled: false,
    color: "#000000",
    opacity: 60,
    padding_x: 16,
    padding_y: 8,
    radius: 8
  },
  marquee: {
    enabled: false,
    height: 60,
    font_size: 36,
    text_color: "#FFFFFF",
    bg_color: "#000000",
    bg_opacity: 80,
    speed_px_per_sec: 50
  }
}
```

- Khởi tạo `overlayConfigs[i]` với defaults khi `renderTable(files)` sau upload
- Global defaults vẫn dùng cho file mới thêm vào batch

### 2. Table — Edit Column + Active Row Indicator

| Change | Detail |
|--------|--------|
| New column | "Edit Overlay" — button "Chỉnh" per row |
| Row highlight | Active row: `border-left: 3px solid var(--accent)` |
| Studio header | Badge "Đang edit: `filename.mp3`" (click để đóng/clear) |
| Copy config | Dropdown "Copy từ..." clone config từ file khác |

### 3. Studio Panel — Overlay Section Full Parity

Replace current simple "Text overlay" section (lines 135-165) with:

```html
<form id="overlay-form" class="studio-section">
  <h4>Text overlay <span id="overlay-active-badge" class="badge" style="display:none"></span></h4>
  
  <!-- Basic -->
  <div class="form-grid">
    <div class="form-group"><label>Text</label><input id="ov-text" type="text" maxlength="200"></div>
    <div class="form-group"><label>Position</label><select id="ov-position"><option value="top">Trên</option><option value="center">Giữa</option><option value="bottom">Dưới</option></select></div>
    <div class="form-group"><label>Alignment</label><select id="ov-alignment"><option value="left">Trái</option><option value="center">Giữa</option><option value="right">Phải</option></select></div>
    <div class="form-group"><label>Font size</label><input id="ov-font-size" type="number" min="12" max="200" value="52"></div>
    <div class="form-group"><label>Text color</label><input id="ov-text-color" type="color" value="#FFFFFF"></div>
    <div class="form-group"><label>Margin (px)</label><input id="ov-margin" type="number" min="0" max="200" value="20"></div>
  </div>

  <div class="drag-offset">
    <span>Offset kéo: <code id="ov-offset-label">0, 0</code> px</span>
    <input type="hidden" id="ov-offset-x" value="0">
    <input type="hidden" id="ov-offset-y" value="0">
    <button type="button" id="ov-offset-reset" class="btn-outline btn-sm">Reset</button>
  </div>

  <!-- Shadow -->
  <details class="ov-details"><summary>Shadow (đổ bóng)</summary>
    <div class="form-grid">
      <label class="checkbox-inline"><input type="checkbox" id="ov-shadow-enabled"> Bật</label>
      <div class="form-group"><label>Màu</label><input id="ov-shadow-color" type="color" value="#000000"></div>
      <div class="form-group"><label>Offset (px)</label><input id="ov-shadow-offset" type="number" min="0" max="20" value="3"></div>
    </div>
  </details>

  <!-- Background Box -->
  <details class="ov-details"><summary>Background box (hộp nền)</summary>
    <div class="form-grid">
      <label class="checkbox-inline"><input type="checkbox" id="ov-box-enabled"> Bật</label>
      <div class="form-group"><label>Màu</label><input id="ov-box-color" type="color" value="#000000"></div>
      <div class="form-group"><label>Độ mờ: <span id="ov-box-opacity-label">60%</span></label><input id="ov-box-opacity" type="range" min="0" max="100" value="60" oninput="document.getElementById('ov-box-opacity-label').textContent=this.value+'%'"></div>
      <div class="form-group"><label>Padding X</label><input id="ov-box-px" type="number" min="0" max="200" value="16"></div>
      <div class="form-group"><label>Padding Y</label><input id="ov-box-py" type="number" min="0" max="200" value="8"></div>
      <div class="form-group"><label>Bo góc</label><input id="ov-box-radius" type="number" min="0" max="200" value="8"></div>
    </div>
  </details>

  <!-- Marquee -->
  <details class="ov-details"><summary>Marquee (chạy chữ ngang)</summary>
    <div class="form-grid">
      <label class="checkbox-inline"><input type="checkbox" id="ov-marquee-enabled"> Bật marquee</label>
      <div class="form-group"><label>Chiều cao bar</label><input id="ov-marquee-height" type="number" min="20" max="200" value="60"></div>
      <div class="form-group"><label>Cỡ chữ</label><input id="ov-marquee-font-size" type="number" min="12" max="120" value="36"></div>
      <div class="form-group"><label>Màu chữ</label><input id="ov-marquee-text-color" type="color" value="#FFFFFF"></div>
      <div class="form-group"><label>Màu nền</label><input id="ov-marquee-bg-color" type="color" value="#000000"></div>
      <div class="form-group"><label>Độ mờ nền: <span id="ov-marquee-opacity-label">80%</span></label><input id="ov-marquee-opacity" type="range" min="0" max="100" value="80" oninput="document.getElementById('ov-marquee-opacity-label').textContent=this.value+'%'"></div>
      <div class="form-group"><label>Tốc độ (px/s)</label><input id="ov-marquee-speed" type="number" min="10" max="500" value="50"></div>
    </div>
  </details>
</form>
```

### 4. JS Logic — Load/Save Per-File Config

```javascript
// Global state
let overlayConfigs = {};  // { [index]: configObject }
let activeEditIndex = null;  // currently editing file index

function initOverlayConfigs(files) {
  overlayConfigs = {};
  files.forEach(f => {
    overlayConfigs[f.index] = getDefaultOverlayConfig();
  });
}

function getDefaultOverlayConfig() {
  return {
    text: "", position: "top", alignment: "center", font_size: 52,
    text_color: "#FFFFFF", margin: 20, offset_x: 0, offset_y: 0,
    shadow: { enabled: false, color: "#000000", offset: 3 },
    box: { enabled: false, color: "#000000", opacity: 60, padding_x: 16, padding_y: 8, radius: 8 },
    marquee: { enabled: false, height: 60, font_size: 36, text_color: "#FFFFFF", bg_color: "#000000", bg_opacity: 80, speed_px_per_sec: 50 }
  };
}

function loadOverlayConfigToForm(index) {
  const cfg = overlayConfigs[index] || getDefaultOverlayConfig();
  // populate all form fields from cfg
  // update offset label
  // scheduleRefresh()
  activeEditIndex = index;
  updateStudioHeaderBadge(index);
}

function saveFormToOverlayConfig(index) {
  const cfg = overlayConfigs[index] || getDefaultOverlayConfig();
  // read all form fields into cfg
  overlayConfigs[index] = cfg;
}

// Event: row "Edit" button click
tbody.querySelectorAll('.btn-edit-overlay').forEach(btn => {
  btn.addEventListener('click', () => loadOverlayConfigToForm(parseInt(btn.dataset.index)));
});

// Form input handlers → saveFormToOverlayConfig(activeEditIndex) + scheduleRefresh()
```

### 5. Preview Refresh — Use Active Edit Index

- `previewParams()` đọc từ form fields (đang hiển thị config của `activeEditIndex`)
- `refreshPreview()` gọi `/video/overlay-preview` với params hiện tại
- Khi đổi `preview-row-select` dropdown → nếu `activeEditIndex !== null` thì save current form trước, rồi load mới

### 6. Generate Payload — Per-File Override

```javascript
// In btn-generate click handler
const overlayConfigsMap = {};
Object.entries(overlayConfigs).forEach(([idx, cfg]) => {
  if (cfg.text || cfg.marquee.enabled) {
    overlayConfigsMap[idx] = cfg;  // only send non-empty
  }
});

const config = {
  resolution: ...,
  // ... other global configs
  overlay: globalOverlay,  // fallback for files without per-file config
  overlay_configs: Object.keys(overlayConfigsMap).length ? overlayConfigsMap : null
};
```

### 7. Backend Change — `/video/generate-batch`

Route handler (`app/routes/video.py` → `generate_batch`):

```python
overlay_configs = payload.get("overlay_configs") or {}
# Per job:
per_overlay = overlay_configs.get(str(idx)) or payload.get("config", {}).get("overlay")
# Pass to video_gen.generate_video(..., overlay=per_overlay)
```

No changes to `video_gen.py` or `image_overlay.py` — they already accept full overlay dict.

---

## Acceptance Criteria

1. Upload 3+ audio files → table shows 3 rows with "Chỉnh" button each
2. Click "Chỉnh" row 1 → Studio shows file 1 config, badge "Đang edit: file1.mp3"
3. Edit shadow/box/marquee → preview updates live
4. Click "Chỉnh" row 2 → form saves file 1 config, loads file 2 config
5. Generate → each video uses its own overlay config
6. Files without per-file config fall back to global `overlay` (if set)
7. Visual parity: all fields from book_detail.html overlay form present

---

## Files to Modify

| File | Changes |
|------|---------|
| `app/templates/video_creator.html` | Table column + Studio overlay form replacement + JS logic |
| `app/routes/video.py` | `generate_batch` read `overlay_configs` and pass per-job |

---

## Rollback Plan

If issues: revert `video_creator.html` to previous version; backend change is backward-compatible (ignores `overlay_configs` if absent).
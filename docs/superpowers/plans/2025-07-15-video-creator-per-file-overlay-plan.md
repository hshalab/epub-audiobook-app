# Video Creator Per-File Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-file overlay config with full feature parity to video_creator.html (shadow, box, marquee, alignment, margin) and backend support for per-file override.

**Architecture:** Extend existing Studio panel in video_creator.html to load/save per-file overlay config. Backend reads `overlay_configs` map and passes per-job to video_gen. No changes to video_gen.py or image_overlay.py.

**Tech Stack:** Jinja2 templates, vanilla JS (ES6), Python Flask routes

## Global Constraints

- Preserve existing API: `config.overlay` remains global fallback
- New `overlay_configs` map is optional, backward-compatible
- All overlay fields match `app/templates/book_detail.html` exactly
- Preview uses existing `/video/overlay-preview` endpoint
- In-memory config only — no server persistence until Generate

---

### Task 1: Add "Edit Overlay" column to table + row highlight CSS

**Files:**
- Modify: `app/templates/video_creator.html:49-65` (table header)
- Modify: `app/templates/video_creator.html:376-402` (renderTable tbody rows)
- Modify: `app/templates/video_creator.html:258-288` (CSS section)

**Interfaces:**
- Produces: `tbody` rows with `<td class="col-edit"><button class="btn-edit-overlay" data-index="...">Chỉnh</button></td>`
- CSS: `.row-active { border-left: 3px solid var(--accent); }`, `#overlay-active-badge` styles

- [ ] **Step 1: Add table header column**

```html
<!-- In thead tr, after col-preview -->
<th class="col-edit">Edit Overlay</th>
```

- [ ] **Step 2: Add edit button column in renderTable**

```javascript
// In renderTable, inside tr.innerHTML template string:
<td class="col-edit">
  <button type="button" class="btn-edit-overlay btn-sm btn-outline" data-index="${f.index}" title="Chỉnh overlay cho file này">Chỉnh</button>
</td>
```

- [ ] **Step 3: Add CSS for active row + badge**

```css
/* In <style> block */
.row-active { border-left: 3px solid var(--accent); }
#overlay-active-badge { font-size: var(--font-size-xs); margin-left: var(--space-xs); vertical-align: middle; }
```

- [ ] **Step 4: Verify table renders with edit buttons**

Open `/video` → upload files → table shows "Chỉnh" button per row.

---

### Task 2: Replace Studio "Text overlay" section with full parity form

**Files:**
- Modify: `app/templates/video_creator.html:135-165` (replace lines 135-165 entirely)

**Interfaces:**
- Consumes: `overlayConfigs[index]` object structure from Task 3
- Produces: Form with id `overlay-form`, all fields with `ov-*` ids matching book_detail.html

- [ ] **Step 1: Delete current simple overlay section (lines 135-165)**

```html
<!-- REMOVE this block entirely -->
<div class="studio-section">
  <h4>Text overlay</h4>
  <input type="hidden" id="cfg-overlay-offset-x" value="0">
  <input type="hidden" id="cfg-overlay-offset-y" value="0">
  <div class="form-group">...</div>
  ...
</div>
```

- [ ] **Step 2: Insert full overlay form**

```html
<form id="overlay-form" class="studio-section">
  <h4>Text overlay <span id="overlay-active-badge" class="badge" style="display:none"></span></h4>
  
  <!-- Basic -->
  <div class="form-grid">
    <div class="form-group"><label for="ov-text">Text</label><input type="text" id="ov-text" maxlength="200" placeholder="VD: Tên sách - Tập 1"></div>
    <div class="form-group"><label for="ov-position">Position</label><select id="ov-position"><option value="top">Trên</option><option value="center">Giữa</option><option value="bottom">Dưới</option></select></div>
    <div class="form-group"><label for="ov-alignment">Alignment</label><select id="ov-alignment"><option value="left">Trái</option><option value="center">Giữa</option><option value="right">Phải</option></select></div>
    <div class="form-group"><label for="ov-font-size">Font size</label><input type="number" id="ov-font-size" min="12" max="200" value="52"></div>
    <div class="form-group"><label for="ov-text-color">Text color</label><input type="color" id="ov-text-color" value="#FFFFFF"></div>
    <div class="form-group"><label for="ov-margin">Margin (px)</label><input type="number" id="ov-margin" min="0" max="200" value="20"></div>
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
      <div class="form-group"><label for="ov-shadow-color">Màu</label><input type="color" id="ov-shadow-color" value="#000000"></div>
      <div class="form-group"><label for="ov-shadow-offset">Offset (px)</label><input type="number" id="ov-shadow-offset" min="0" max="20" value="3"></div>
    </div>
  </details>

  <!-- Background Box -->
  <details class="ov-details"><summary>Background box (hộp nền)</summary>
    <div class="form-grid">
      <label class="checkbox-inline"><input type="checkbox" id="ov-box-enabled"> Bật</label>
      <div class="form-group"><label for="ov-box-color">Màu</label><input type="color" id="ov-box-color" value="#000000"></div>
      <div class="form-group"><label>Độ mờ: <span id="ov-box-opacity-label">60%</span></label><input type="range" id="ov-box-opacity" min="0" max="100" value="60" oninput="document.getElementById('ov-box-opacity-label').textContent=this.value+'%'"></div>
      <div class="form-group"><label for="ov-box-px">Padding X</label><input type="number" id="ov-box-px" min="0" max="200" value="16"></div>
      <div class="form-group"><label for="ov-box-py">Padding Y</label><input type="number" id="ov-box-py" min="0" max="200" value="8"></div>
      <div class="form-group"><label for="ov-box-radius">Bo góc</label><input type="number" id="ov-box-radius" min="0" max="200" value="8"></div>
    </div>
  </details>

  <!-- Marquee -->
  <details class="ov-details"><summary>Marquee (chạy chữ ngang)</summary>
    <div class="form-grid">
      <label class="checkbox-inline"><input type="checkbox" id="ov-marquee-enabled"> Bật marquee</label>
      <div class="form-group"><label for="ov-marquee-height">Chiều cao bar</label><input type="number" id="ov-marquee-height" min="20" max="200" value="60"></div>
      <div class="form-group"><label for="ov-marquee-font-size">Cỡ chữ</label><input type="number" id="ov-marquee-font-size" min="12" max="120" value="36"></div>
      <div class="form-group"><label for="ov-marquee-text-color">Màu chữ</label><input type="color" id="ov-marquee-text-color" value="#FFFFFF"></div>
      <div class="form-group"><label for="ov-marquee-bg-color">Màu nền</label><input type="color" id="ov-marquee-bg-color" value="#000000"></div>
      <div class="form-group"><label>Độ mờ nền: <span id="ov-marquee-opacity-label">80%</span></label><input type="range" id="ov-marquee-opacity" min="0" max="100" value="80" oninput="document.getElementById('ov-marquee-opacity-label').textContent=this.value+'%'"></div>
      <div class="form-group"><label for="ov-marquee-speed">Tốc độ (px/s)</label><input type="number" id="ov-marquee-speed" min="10" max="500" value="50"></div>
    </div>
  </details>
</form>
```

- [ ] **Step 3: Add form-grid + checkbox-inline CSS**

```css
/* In <style> block */
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:var(--space-md); }
.checkbox-inline { display:flex; align-items:center; gap:var(--space-xs); font-weight:500; }
.ov-details { margin-top:var(--space-md); }
.ov-details summary { cursor:pointer; font-weight:500; margin-bottom:var(--space-sm); }
.drag-offset { display:flex; align-items:center; gap:var(--space-sm); margin-top:var(--space-sm); font-size:var(--font-size-xs); color:var(--text-muted); }
```

- [ ] **Step 4: Verify form renders without JS errors**

Open `/video` → upload files → click "Chỉnh" → form visible with all fields.

---

### Task 3: Add per-file overlay config JS state + load/save logic

**Files:**
- Modify: `app/templates/video_creator.html:290-735` (main IIFE, after line 735 add new logic)

**Interfaces:**
- Consumes: `batchFiles` array, `overlayConfigs` object, `activeEditIndex`
- Produces: `initOverlayConfigs(files)`, `loadOverlayConfigToForm(index)`, `saveFormToOverlayConfig(index)`, `updateStudioHeaderBadge(index)`

- [ ] **Step 1: Add global state variables (after line 302 `let batchFiles = [];`)**

```javascript
let overlayConfigs = {};      // { [index]: configObject }
let activeEditIndex = null;   // currently editing file index
```

- [ ] **Step 2: Add `getDefaultOverlayConfig()` function**

```javascript
function getDefaultOverlayConfig() {
  return {
    text: "", position: "top", alignment: "center", font_size: 52,
    text_color: "#FFFFFF", margin: 20, offset_x: 0, offset_y: 0,
    shadow: { enabled: false, color: "#000000", offset: 3 },
    box: { enabled: false, color: "#000000", opacity: 60, padding_x: 16, padding_y: 8, radius: 8 },
    marquee: { enabled: false, height: 60, font_size: 36, text_color: "#FFFFFF", bg_color: "#000000", bg_opacity: 80, speed_px_per_sec: 50 }
  };
}
```

- [ ] **Step 3: Add `initOverlayConfigs(files)` + call in `renderTable`**

```javascript
function initOverlayConfigs(files) {
  overlayConfigs = {};
  files.forEach(f => { overlayConfigs[f.index] = getDefaultOverlayConfig(); });
}
```

In `renderTable`, after `batchFiles = files;`: `initOverlayConfigs(files);`

- [ ] **Step 4: Add `loadOverlayConfigToForm(index)`**

```javascript
function loadOverlayConfigToForm(index) {
  // Save current form first if editing another file
  if (activeEditIndex !== null && activeEditIndex !== index) {
    saveFormToOverlayConfig(activeEditIndex);
  }
  const cfg = overlayConfigs[index] || getDefaultOverlayConfig();
  
  // Basic
  document.getElementById('ov-text').value = cfg.text;
  document.getElementById('ov-position').value = cfg.position;
  document.getElementById('ov-alignment').value = cfg.alignment;
  document.getElementById('ov-font-size').value = cfg.font_size;
  document.getElementById('ov-text-color').value = cfg.text_color;
  document.getElementById('ov-margin').value = cfg.margin;
  document.getElementById('ov-offset-x').value = cfg.offset_x;
  document.getElementById('ov-offset-y').value = cfg.offset_y;
  document.getElementById('ov-offset-label').textContent = `${cfg.offset_x}, ${cfg.offset_y}`;
  
  // Shadow
  document.getElementById('ov-shadow-enabled').checked = cfg.shadow.enabled;
  document.getElementById('ov-shadow-color').value = cfg.shadow.color;
  document.getElementById('ov-shadow-offset').value = cfg.shadow.offset;
  
  // Box
  document.getElementById('ov-box-enabled').checked = cfg.box.enabled;
  document.getElementById('ov-box-color').value = cfg.box.color;
  document.getElementById('ov-box-opacity').value = cfg.box.opacity;
  document.getElementById('ov-box-opacity-label').textContent = cfg.box.opacity + '%';
  document.getElementById('ov-box-px').value = cfg.box.padding_x;
  document.getElementById('ov-box-py').value = cfg.box.padding_y;
  document.getElementById('ov-box-radius').value = cfg.box.radius;
  
  // Marquee
  document.getElementById('ov-marquee-enabled').checked = cfg.marquee.enabled;
  document.getElementById('ov-marquee-height').value = cfg.marquee.height;
  document.getElementById('ov-marquee-font-size').value = cfg.marquee.font_size;
  document.getElementById('ov-marquee-text-color').value = cfg.marquee.text_color;
  document.getElementById('ov-marquee-bg-color').value = cfg.marquee.bg_color;
  document.getElementById('ov-marquee-opacity').value = cfg.marquee.bg_opacity;
  document.getElementById('ov-marquee-opacity-label').textContent = cfg.marquee.bg_opacity + '%';
  document.getElementById('ov-marquee-speed').value = cfg.marquee.speed_px_per_sec;
  
  activeEditIndex = index;
  updateStudioHeaderBadge(index);
  scheduleRefresh();
}
```

- [ ] **Step 5: Add `saveFormToOverlayConfig(index)`**

```javascript
function saveFormToOverlayConfig(index) {
  if (index === null) return;
  const cfg = overlayConfigs[index] || getDefaultOverlayConfig();
  
  cfg.text = document.getElementById('ov-text').value;
  cfg.position = document.getElementById('ov-position').value;
  cfg.alignment = document.getElementById('ov-alignment').value;
  cfg.font_size = parseInt(document.getElementById('ov-font-size').value) || 52;
  cfg.text_color = document.getElementById('ov-text-color').value;
  cfg.margin = parseInt(document.getElementById('ov-margin').value) || 20;
  cfg.offset_x = parseInt(document.getElementById('ov-offset-x').value) || 0;
  cfg.offset_y = parseInt(document.getElementById('ov-offset-y').value) || 0;
  
  cfg.shadow.enabled = document.getElementById('ov-shadow-enabled').checked;
  cfg.shadow.color = document.getElementById('ov-shadow-color').value;
  cfg.shadow.offset = parseInt(document.getElementById('ov-shadow-offset').value) || 3;
  
  cfg.box.enabled = document.getElementById('ov-box-enabled').checked;
  cfg.box.color = document.getElementById('ov-box-color').value;
  cfg.box.opacity = parseInt(document.getElementById('ov-box-opacity').value) || 60;
  cfg.box.padding_x = parseInt(document.getElementById('ov-box-px').value) || 16;
  cfg.box.padding_y = parseInt(document.getElementById('ov-box-py').value) || 8;
  cfg.box.radius = parseInt(document.getElementById('ov-box-radius').value) || 8;
  
  cfg.marquee.enabled = document.getElementById('ov-marquee-enabled').checked;
  cfg.marquee.height = parseInt(document.getElementById('ov-marquee-height').value) || 60;
  cfg.marquee.font_size = parseInt(document.getElementById('ov-marquee-font-size').value) || 36;
  cfg.marquee.text_color = document.getElementById('ov-marquee-text-color').value;
  cfg.marquee.bg_color = document.getElementById('ov-marquee-bg-color').value;
  cfg.marquee.bg_opacity = parseInt(document.getElementById('ov-marquee-opacity').value) || 80;
  cfg.marquee.speed_px_per_sec = parseInt(document.getElementById('ov-marquee-speed').value) || 50;
  
  overlayConfigs[index] = cfg;
}
```

- [ ] **Step 6: Add `updateStudioHeaderBadge(index)`**

```javascript
function updateStudioHeaderBadge(index) {
  const badge = document.getElementById('overlay-active-badge');
  const file = batchFiles.find(f => f.index === index);
  if (badge && file) {
    badge.textContent = 'Đang edit: ' + file.name;
    badge.style.display = 'inline-block';
  }
}
```

- [ ] **Step 7: Wire "Edit" button clicks (inside `renderTable` after row creation)**

```javascript
tbody.querySelectorAll('.btn-edit-overlay').forEach(btn => {
  btn.addEventListener('click', () => {
    const idx = parseInt(btn.dataset.index);
    // Highlight row
    tbody.querySelectorAll('tr').forEach(tr => tr.classList.remove('row-active'));
    btn.closest('tr').classList.add('row-active');
    loadOverlayConfigToForm(idx);
  });
});
```

- [ ] **Step 8: Wire form input handlers → save + scheduleRefresh**

```javascript
// After overlay-form exists in DOM (end of IIFE or after renderTable call)
document.querySelectorAll('#overlay-form input, #overlay-form select').forEach(el => {
  el.addEventListener('input', () => {
    if (activeEditIndex !== null) {
      saveFormToOverlayConfig(activeEditIndex);
      scheduleRefresh();
    }
  });
  el.addEventListener('change', () => {
    if (activeEditIndex !== null) {
      saveFormToOverlayConfig(activeEditIndex);
      scheduleRefresh();
    }
  });
});

// Offset reset
document.getElementById('ov-offset-reset').addEventListener('click', () => {
  if (activeEditIndex !== null) {
    document.getElementById('ov-offset-x').value = 0;
    document.getElementById('ov-offset-y').value = 0;
    document.getElementById('ov-offset-label').textContent = '0, 0';
    saveFormToOverlayConfig(activeEditIndex);
    scheduleRefresh();
  }
});

// Position/alignment change resets drag offset (like book_detail.html)
['ov-position', 'ov-alignment'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', () => {
    if (activeEditIndex !== null) {
      document.getElementById('ov-offset-x').value = 0;
      document.getElementById('ov-offset-y').value = 0;
      document.getElementById('ov-offset-label').textContent = '0, 0';
      saveFormToOverlayConfig(activeEditIndex);
      scheduleRefresh();
    }
  });
});
```

- [ ] **Step 9: Update `previewParams()` to use form fields (already reads form, no change needed) but ensure it uses `activeEditIndex` file's background**

```javascript
function currentBgPath() {
  // If actively editing a file, use its selected background
  if (activeEditIndex !== null) {
    const sel = document.querySelector(`.bg-select[data-index="${activeEditIndex}"]`);
    return sel ? sel.value : '';
  }
  // Fallback to preview row select
  const idx = previewRowSelect.value;
  if (idx === '') return '';
  const sel = document.querySelector(`.bg-select[data-index="${idx}"]`);
  return sel ? sel.value : '';
}
```

- [ ] **Step 10: Sync `previewRowSelect` change → save current, load new**

```javascript
previewRowSelect.addEventListener('change', () => {
  if (activeEditIndex !== null) {
    saveFormToOverlayConfig(activeEditIndex);
  }
  const newIdx = parseInt(previewRowSelect.value);
  loadOverlayConfigToForm(newIdx);
  // Also update studio's mix-ref if needed
});
```

---

### Task 4: Update Generate payload to include per-file overlay_configs

**Files:**
- Modify: `app/templates/video_creator.html:609-640` (btn-generate click handler)

**Interfaces:**
- Consumes: `overlayConfigs` object
- Produces: `config.overlay_configs` map in POST body

- [ ] **Step 1: Replace overlay config building in btn-generate handler**

```javascript
// REPLACE lines 621-639:
const globalOverlayText = document.getElementById('cfg-overlay-text').value.trim(); // legacy global
const globalOverlay = globalOverlayText ? {
  text: globalOverlayText,
  position: document.getElementById('cfg-overlay-position').value,
  font_size: parseInt(document.getElementById('cfg-overlay-size').value) || 52,
  text_color: document.getElementById('cfg-overlay-color').value,
  offset_x: parseInt(document.getElementById('cfg-overlay-offset-x').value) || 0,
  offset_y: parseInt(document.getElementById('cfg-overlay-offset-y').value) || 0,
} : null;

// Build per-file overlay_configs map
const overlayConfigsMap = {};
Object.entries(overlayConfigs).forEach(([idx, cfg]) => {
  // Only include if has text or marquee enabled
  if (cfg.text || cfg.marquee.enabled) {
    overlayConfigsMap[idx] = cfg;
  }
});

const config = {
  resolution: document.getElementById('cfg-resolution').value,
  fps: parseInt(document.getElementById('cfg-fps').value),
  codec: document.getElementById('cfg-codec').value,
  audio_bitrate: document.getElementById('cfg-audio-bitrate').value,
  image_type: document.getElementById('cfg-image-type').value,
  crf: parseInt(document.getElementById('cfg-crf').value),
  music_id: musicSel.value ? parseInt(musicSel.value) : null,
  music_volume: parseInt(document.getElementById('cfg-music-volume').value),
  overlay: globalOverlay,                    // fallback
  overlay_configs: Object.keys(overlayConfigsMap).length ? overlayConfigsMap : null
};
```

- [ ] **Step 2: Remove old global overlay fields from form (optional cleanup)**

Keep `cfg-overlay-*` fields for backward compatibility as global fallback, but they can be hidden or left as-is.

---

### Task 5: Backend — read overlay_configs in generate_batch

**Files:**
- Modify: `app/routes/video.py` — find `generate_batch` function

**Interfaces:**
- Consumes: `payload.get("overlay_configs")` dict
- Produces: Per-job `overlay` passed to `video_gen.generate_video`

- [ ] **Step 1: Locate generate_batch function**

```bash
grep -n "def generate_batch" app/routes/video.py
```

- [ ] **Step 2: Add overlay_configs extraction and per-job logic**

```python
# Inside generate_batch, after parsing payload:
overlay_configs = payload.get("overlay_configs") or {}

# Inside the loop over selected files:
for idx in selected:
    # ... existing code ...
    
    # Determine overlay for this job
    per_overlay = overlay_configs.get(str(idx)) or config.get("overlay")
    
    # Pass to video_gen.generate_video
    job = video_gen.generate_video(
        # ... existing args ...
        overlay=per_overlay,  # This already accepts full dict with shadow/box/marquee
    )
```

- [ ] **Step 3: Test backend accepts payload**

```bash
# Start server, test generate with overlay_configs in payload
```

---

### Task 6: Integration test + verify all acceptance criteria

**Files:**
- Test: Manual verification via browser

**Interfaces:**
- All tasks combined

- [ ] **Step 1: Start dev server**

```bash
python -m app.main
```

- [ ] **Step 2: Test upload 3+ files → table shows "Chỉnh" buttons**

- [ ] **Step 3: Click "Chỉnh" row 1 → Studio badge shows filename, form loads defaults**

- [ ] **Step 4: Edit shadow (enable, color, offset) → preview updates live**

- [ ] **Step 5: Edit box (enable, color, opacity, padding, radius) → preview updates**

- [ ] **Step 6: Edit marquee (enable, height, font, colors, speed) → preview updates**

- [ ] **Step 7: Click "Chỉnh" row 2 → row 1 config saved, row 2 config loaded**

- [ ] **Step 8: Click Generate → all videos created with respective overlay configs**

- [ ] **Step 9: Verify files without per-file config use global `overlay` fallback**

---

### Task 7: Commit

**Files:**
- `app/templates/video_creator.html`
- `app/routes/video.py`

- [ ] **Step 1: Stage and commit**

```bash
git add app/templates/video_creator.html app/routes/video.py
git commit -m "feat(video): per-file overlay config with full feature parity (shadow, box, marquee, alignment, margin)"
```

---

## Execution Options

**Plan complete and saved to `docs/superpowers/plans/2025-07-15-video-creator-per-file-overlay-plan.md`. Two execution options:**

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
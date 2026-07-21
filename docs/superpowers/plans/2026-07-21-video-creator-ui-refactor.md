# Video Creator UI Refactor + Overlay Config Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Video Creator's overlay-config bug (shadow/box/marquee settings silently dropped unless a specific file row is edited), and restructure `app/templates/video_creator.html` into a maintainable, two-tab (Create Video / Video Library) page.

**Architecture:** Extract the page's ~1300 lines of inline JS into `app/static/video_creator.js` unchanged, then fix the overlay data model in place (shared default config + explicit per-file overrides, saved on every change, always sent to the backend as the full nested shape), then wrap the existing markup in two tab panels, then clean up inline styles and two undefined CSS custom properties.

**Tech Stack:** FastAPI + Jinja2 templates, vanilla JS (no bundler/module system — classic scripts sharing state via `window.__studio*` globals, the codebase's existing convention), pytest for backend tests.

## Global Constraints

- No behavior change to `app/routes/video_api.py`, `app/video_repository.py`, `app/upload_worker.py`, or `app/routes/video.py`'s per-file overlay merge logic (`video.py:495-497`) — all confirmed already correct; only the frontend payload shape changes.
- No new JS test framework — this repo has no `package.json`/JS test runner; overlay JS changes are verified manually in-browser (Task 3/6), backend contract is verified with pytest (Task 1).
- Keep `app/static/video_creator.js` a classic script (no `type="module"`) — `uploadToYouTube` must remain an unwrapped top-level function since it's invoked from `onclick=` attributes in JS-generated HTML strings, and cross-section state must keep using the existing `window.__studio*` bridging convention (see `__studioSetBatchId`, `__studioRepopulateMixRef` already in the file).
- Design reference: `docs/superpowers/specs/2026-07-21-video-creator-ui-refactor-design.md`.

---

### Task 1: Backend characterization tests for the overlay config contract

**Files:**
- Modify: `tests/test_video_batch_extras.py`

**Interfaces:**
- Consumes: `app.routes.video._convert_overlay_config_to_flat(cfg: dict) -> dict` (existing, unchanged, `app/routes/video.py:136-173`).
- Produces: nothing new — these are regression/characterization tests documenting the exact contract the frontend fix in Task 3 must satisfy.

These tests exercise existing, already-correct backend code. They won't go
red→green; they characterize the contract so nobody reintroduces the bug by
sending an incomplete overlay shape from the frontend again.

- [ ] **Step 1: Write the characterization tests**

Add to `tests/test_video_batch_extras.py`:

```python
def test_convert_overlay_config_to_flat_maps_nested_shadow_box_marquee():
    """A full nested overlay config (the shape the frontend must always
    send, whether for a per-file override or the batch-wide default) maps
    every field through to the flat shape the renderer consumes."""
    cfg = {
        "position": "bottom", "alignment": "left", "font_size": 44,
        "text_color": "#00FF00", "margin": 10, "offset_x": 5, "offset_y": -5,
        "shadow": {"enabled": True, "color": "#111111", "offset": 4},
        "box": {"enabled": True, "color": "#222222", "opacity": 70,
                "padding_x": 12, "padding_y": 6, "radius": 4},
        "marquee": {"enabled": True, "height": 50, "font_size": 30,
                    "text_color": "#333333", "bg_color": "#444444",
                    "bg_opacity": 90, "speed_px_per_sec": 80},
    }
    flat = video_routes._convert_overlay_config_to_flat(cfg)
    assert flat["position"] == "bottom"
    assert flat["shadow_enabled"] == "on"
    assert flat["shadow_color"] == "#111111"
    assert flat["shadow_offset"] == 4
    assert flat["box_enabled"] == "on"
    assert flat["box_opacity"] == 70
    assert flat["marquee_enabled"] == "on"
    assert flat["marquee_speed"] == 80


def test_convert_overlay_config_to_flat_bare_text_only_loses_shadow_box():
    """Guards the historical bug: a payload shaped like {"text": ...} (no
    nested shadow/box/marquee) silently disables all three. This is why the
    frontend must always send the FULL nested shape as the batch-wide
    `overlay` fallback, not just {text}."""
    flat = video_routes._convert_overlay_config_to_flat({"text": "hello"})
    assert flat["shadow_enabled"] == "off"
    assert flat["box_enabled"] == "off"
    assert flat["marquee_enabled"] == "off"
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_video_batch_extras.py -v`
Expected: PASS (both new tests pass immediately — this documents existing correct backend behavior, it's not a red→green cycle since no backend code changes).

- [ ] **Step 3: Commit**

```bash
git add tests/test_video_batch_extras.py
git commit -m "test: characterize overlay config flat-conversion contract"
```

---

### Task 2: Extract inline JS into app/static/video_creator.js

**Files:**
- Create: `app/static/video_creator.js`
- Modify: `app/templates/video_creator.html:415-1711`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app/static/video_creator.js`, a classic (non-module) script loaded via `<script src="/static/video_creator.js"></script>`, containing every function/IIFE currently inline. No behavior change in this task — pure move.

- [ ] **Step 1: Move the JS verbatim**

Cut the full contents between (and not including) the two `<script>`/`</script>`
pairs at `video_creator.html:415-635` (Video Library IIFE) and
`video_creator.html:636-1711` (batch upload / Studio overlay / drag-preview /
mix-player / `uploadToYouTube`) into a new file `app/static/video_creator.js`,
concatenated in the same order they appear in the template (Video Library
IIFE first, then the second block). Do not alter a single line of logic in
this step — this is a mechanical move, verified byte-for-byte other than
whitespace at the seam between the two blocks.

Replace both `<script>...</script>` blocks in `video_creator.html` with a
single line in their place:

```html
<script src="/static/video_creator.js"></script>
```

Leave the static `<style>` block (`video_creator.html:374-413`) and the
`<details class="param-reference">` reference tables after line 1711
untouched — they are not part of either script.

- [ ] **Step 2: Confirm the static file is served and referenced correctly**

Run: `grep -n "static/autosave.js" "D:/Projects/epub-audiobook-app/app/templates/base.html"`
Expected: shows the existing convention (`<script src="/static/autosave.js">`)
this new line matches — confirms `/static/` is the correct mount path (FastAPI
static file mount already serves `app/static/`).

- [ ] **Step 3: Start the app and load the page**

Use the `run` skill (or `uvicorn app.main:app --reload` if no project-specific
launcher exists) to start the server, then open `/video` in a browser (or via
the claude-in-chrome tools) and check the browser console for errors. Also
confirm via the Network tab that `video_creator.js` returns 200, and exercise
one interaction from each of the three original IIFEs to confirm nothing
broke in the move:
  - Video Library: the table loads videos on page load (or after Task 4, on
    tab activation — at this point in the plan it should still load
    unconditionally since Task 4 hasn't run yet).
  - Batch upload: upload an audio file, confirm the file table renders.
  - Studio: confirm the overlay preview image loads for the uploaded file.

Expected: no console errors, all three areas behave exactly as before.

- [ ] **Step 4: Commit**

```bash
git add app/static/video_creator.js app/templates/video_creator.html
git commit -m "refactor: extract video_creator.html inline script to static file"
```

---

### Task 3: Fix the overlay config bug and data model

**Files:**
- Modify: `app/static/video_creator.js`

**Interfaces:**
- Consumes: DOM elements defined in `video_creator.html` (`ov-text`, `ov-position`, ... — unchanged ids), `batchFiles` (existing local state in the batch-upload IIFE).
- Produces: `window.__studioOnOverlayFieldChanged()` and `window.__studioGetActiveEditIndex()`, two new bridge globals the drag-preview IIFE calls into (following the file's existing `window.__studio*` convention). Later tasks don't depend on these directly.

This task also fixes a second, previously-undiscovered bug found while
implementing this: `previewRowSelect`'s `change` handler (in the drag-preview
IIFE) directly referenced `activeEditIndex`, `saveFormToOverlayConfig`, and
`loadOverlayConfigToForm` — all local variables of the *other*, separate
batch-upload IIFE. This throws `ReferenceError: activeEditIndex is not
defined` today whenever the "Preview với file" dropdown changes, silently
aborting that handler. The fix below decouples "which file's background
shows in the live preview" from "which overlay config the form edits"
(the latter is now driven only by the "Chỉnh" button / the new "back to
default" control), removing the cross-scope reference entirely rather than
patching it.

- [ ] **Step 1: Replace the overlay-config state and helper functions**

In `app/static/video_creator.js`, find this block (originally
`video_creator.html:652-717`):

```js
    // Per-file overlay config state
    let overlayConfigs = {};      // { [index]: configObject }
    let activeEditIndex = null;   // currently editing file index

    function getDefaultOverlayConfig() {
        return {
            text: "", position: "top", alignment: "center", font_size: 52,
            text_color: "#FFFFFF", margin: 20, offset_x: 0, offset_y: 0,
            shadow: { enabled: false, color: "#000000", offset: 3 },
            box: { enabled: false, color: "#000000", opacity: 60, padding_x: 16, padding_y: 8, radius: 8 },
            marquee: { enabled: false, height: 60, font_size: 36, text_color: "#FFFFFF", bg_color: "#000000", bg_opacity: 80, speed_px_per_sec: 50 }
        };
    }

    function initOverlayConfigs(files) {
        overlayConfigs = {};
        files.forEach(f => { overlayConfigs[f.index] = getDefaultOverlayConfig(); });
    }

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
        // Note: scheduleRefresh is defined in studio preview IIFE, not accessible here
        // The preview will be refreshed when user makes changes via input handlers
    }

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

    function updateStudioHeaderBadge(index) {
        const badge = document.getElementById('overlay-active-badge');
        const file = batchFiles.find(f => f.index === index);
        if (badge && file) {
            badge.textContent = 'Đang edit: ' + file.name;
            badge.style.display = 'inline-block';
        }
    }
```

Replace it with:

```js
    // Overlay config: one shared default applied to every file, plus a
    // sparse map of explicit per-file overrides. activeEditIndex is null
    // when editing the shared default, or a file index once the user has
    // clicked "Chỉnh" on that row.
    let defaultOverlayConfig = getDefaultOverlayConfig();
    let overlayConfigs = {};      // { [index]: configObject } -- overrides only
    let activeEditIndex = null;

    function getDefaultOverlayConfig() {
        return {
            text: "", position: "top", alignment: "center", font_size: 52,
            text_color: "#FFFFFF", margin: 20, offset_x: 0, offset_y: 0,
            shadow: { enabled: false, color: "#000000", offset: 3 },
            box: { enabled: false, color: "#000000", opacity: 60, padding_x: 16, padding_y: 8, radius: 8 },
            marquee: { enabled: false, height: 60, font_size: 36, text_color: "#FFFFFF", bg_color: "#000000", bg_opacity: 80, speed_px_per_sec: 50 }
        };
    }

    function initOverlayConfigs(files) {
        // New batch: drop per-file overrides from the previous batch, keep
        // the shared default (it's a deliberate user setting, not tied to
        // any one batch).
        overlayConfigs = {};
        activeEditIndex = null;
        updateOverrideDots();
    }

    function hasOverride(index) {
        return Object.prototype.hasOwnProperty.call(overlayConfigs, index);
    }

    // The config object the Studio form currently edits: a file's override
    // once one exists for it, otherwise the shared default.
    function currentOverlayTarget() {
        if (activeEditIndex !== null && overlayConfigs[activeEditIndex]) {
            return overlayConfigs[activeEditIndex];
        }
        return defaultOverlayConfig;
    }

    function updateOverrideDots() {
        document.querySelectorAll('.btn-edit-overlay').forEach(btn => {
            const idx = parseInt(btn.dataset.index);
            const cell = btn.closest('td');
            const dot = cell.querySelector('.override-dot');
            const resetBtn = cell.querySelector('.btn-reset-overlay');
            const has = hasOverride(idx);
            if (dot) dot.style.display = has ? 'inline-block' : 'none';
            if (resetBtn) resetBtn.style.display = has ? '' : 'none';
        });
    }

    function loadOverlayConfigToForm(cfg) {
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
    }

    // Save-on-every-change: writes the form's current values into whichever
    // config object is passed in. Called from every overlay field's input
    // handler (wired in the drag-preview IIFE below via the
    // window.__studioOnOverlayFieldChanged bridge) so nothing is lost
    // whether or not the user switches rows or clicks "Chỉnh" before
    // generating.
    function saveFormToOverlayConfig(cfg) {
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
    }

    function updateStudioHeaderBadge() {
        const badge = document.getElementById('overlay-active-badge');
        const backBtn = document.getElementById('ov-edit-default');
        if (!badge) return;
        if (activeEditIndex === null) {
            badge.textContent = 'Đang chỉnh: Mặc định (tất cả files)';
            if (backBtn) backBtn.style.display = 'none';
        } else {
            const file = batchFiles.find(f => f.index === activeEditIndex);
            badge.textContent = file ? 'Đang chỉnh: ' + file.name : '';
            if (backBtn) backBtn.style.display = file ? 'inline-block' : 'none';
        }
        badge.style.display = 'inline-block';
    }

    // index === null switches to editing the shared default.
    function switchOverlayEditTarget(index) {
        activeEditIndex = index;
        loadOverlayConfigToForm(currentOverlayTarget());
        updateStudioHeaderBadge();
        if (window.__studioRefreshPreview) window.__studioRefreshPreview();
    }

    // Bridge for the drag-preview IIFE (a separate closure further down the
    // file) to save into whichever config is currently active, and to know
    // which file (if any) is being edited, without reaching into this
    // IIFE's private variables directly.
    window.__studioOnOverlayFieldChanged = function() {
        saveFormToOverlayConfig(currentOverlayTarget());
    };
    window.__studioGetActiveEditIndex = function() { return activeEditIndex; };
```

- [ ] **Step 2: Update the "Chỉnh" button, add a reset-to-default button and override dot**

Find (originally `video_creator.html:923`, inside the `renderTable` row
template):

```js
                <td class="col-edit"><button type="button" class="btn-edit-overlay btn-sm btn-outline" data-index="${f.index}" title="Chỉnh overlay cho file này">Chỉnh</button></td>
```

Replace with:

```js
                <td class="col-edit">
                    <button type="button" class="btn-edit-overlay btn-sm btn-outline" data-index="${f.index}" title="Chỉnh overlay riêng cho file này">Chỉnh</button>
                    <button type="button" class="btn-reset-overlay btn-sm btn-outline" data-index="${f.index}" style="display:none" title="Bỏ overlay riêng, dùng mặc định">Mặc định</button>
                    <span class="status-dot status-dot-blue override-dot" style="display:none" title="File này có overlay tuỳ chỉnh riêng"></span>
                </td>
```

Find (originally `video_creator.html:949-956`):

```js
        tbody.querySelectorAll('.btn-edit-overlay').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                tbody.querySelectorAll('tr').forEach(tr => tr.classList.remove('row-active'));
                btn.closest('tr').classList.add('row-active');
                loadOverlayConfigToForm(idx);
            });
        });
```

Replace with:

```js
        tbody.querySelectorAll('.btn-edit-overlay').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                if (!hasOverride(idx)) {
                    overlayConfigs[idx] = JSON.parse(JSON.stringify(defaultOverlayConfig));
                    updateOverrideDots();
                }
                tbody.querySelectorAll('tr').forEach(tr => tr.classList.remove('row-active'));
                btn.closest('tr').classList.add('row-active');
                switchOverlayEditTarget(idx);
            });
        });

        tbody.querySelectorAll('.btn-reset-overlay').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                delete overlayConfigs[idx];
                updateOverrideDots();
                if (activeEditIndex === idx) {
                    btn.closest('tr').classList.remove('row-active');
                    switchOverlayEditTarget(null);
                }
            });
        });
```

- [ ] **Step 3: Add a "back to default" control in the Studio header, and wire it up**

In `app/templates/video_creator.html`, find (line 234):

```html
                <h4>Text overlay <span id="overlay-active-badge" class="badge" style="display:none"></span></h4>
```

Replace with:

```html
                <h4>Text overlay <span id="overlay-active-badge" class="badge"></span> <button type="button" id="ov-edit-default" class="btn-outline btn-sm" style="display:none">&larr; Overlay mặc định</button></h4>
```

In `app/static/video_creator.js`, find the `ov-apply-all` click handler
(originally `video_creator.html:764-812`):

```js
    // Apply overlay to all files
    document.getElementById('ov-apply-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        
        // Save current form to active edit index first
        if (activeEditIndex !== null) {
            saveFormToOverlayConfig(activeEditIndex);
        }
        
        // Get current form values
        const currentConfig = {
            text: document.getElementById('ov-text').value,
            position: document.getElementById('ov-position').value,
            alignment: document.getElementById('ov-alignment').value,
            font_size: parseInt(document.getElementById('ov-font-size').value) || 52,
            text_color: document.getElementById('ov-text-color').value,
            margin: parseInt(document.getElementById('ov-margin').value) || 20,
            offset_x: parseInt(document.getElementById('ov-offset-x').value) || 0,
            offset_y: parseInt(document.getElementById('ov-offset-y').value) || 0,
            shadow: {
                enabled: document.getElementById('ov-shadow-enabled').checked,
                color: document.getElementById('ov-shadow-color').value,
                offset: parseInt(document.getElementById('ov-shadow-offset').value) || 3,
            },
            box: {
                enabled: document.getElementById('ov-box-enabled').checked,
                color: document.getElementById('ov-box-color').value,
                opacity: parseInt(document.getElementById('ov-box-opacity').value) || 60,
                padding_x: parseInt(document.getElementById('ov-box-px').value) || 16,
                padding_y: parseInt(document.getElementById('ov-box-py').value) || 8,
                radius: parseInt(document.getElementById('ov-box-radius').value) || 8,
            },
            marquee: {
                enabled: document.getElementById('ov-marquee-enabled').checked,
                height: parseInt(document.getElementById('ov-marquee-height').value) || 60,
                font_size: parseInt(document.getElementById('ov-marquee-font-size').value) || 36,
                text_color: document.getElementById('ov-marquee-text-color').value,
                bg_color: document.getElementById('ov-marquee-bg-color').value,
                bg_opacity: parseInt(document.getElementById('ov-marquee-opacity').value) || 80,
                speed_px_per_sec: parseInt(document.getElementById('ov-marquee-speed').value) || 50,
            }
        };
        
        // Apply to all files
        batchFiles.forEach(f => {
            overlayConfigs[f.index] = JSON.parse(JSON.stringify(currentConfig));
        });
        
        alert(`Đã áp dụng overlay cho ${batchFiles.length} files`);
    });

    // Clear all overlays
    document.getElementById('ov-clear-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        
        batchFiles.forEach(f => {
            overlayConfigs[f.index] = getDefaultOverlayConfig();
        });
        
        // Reset form
        loadOverlayConfigToForm(activeEditIndex || batchFiles[0].index);
        alert('Đã xóa overlay tất cả files');
    });
```

Replace with:

```js
    // "Apply to all": take whatever is on the form right now, make it the
    // new shared default, and drop every per-file override so every file
    // uses exactly these settings.
    document.getElementById('ov-apply-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        saveFormToOverlayConfig(currentOverlayTarget());
        defaultOverlayConfig = JSON.parse(JSON.stringify(currentOverlayTarget()));
        overlayConfigs = {};
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        updateOverrideDots();
        switchOverlayEditTarget(null);
        alert(`Đã áp dụng overlay hiện tại cho tất cả ${batchFiles.length} files`);
    });

    // "Clear all": reset the shared default to factory defaults and drop
    // every per-file override.
    document.getElementById('ov-clear-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        defaultOverlayConfig = getDefaultOverlayConfig();
        overlayConfigs = {};
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        updateOverrideDots();
        switchOverlayEditTarget(null);
        alert('Đã xóa overlay tất cả files');
    });

    document.getElementById('ov-edit-default').addEventListener('click', () => {
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        switchOverlayEditTarget(null);
    });
```

- [ ] **Step 4: Load the default config into the form when a new batch is rendered**

Find (originally `video_creator.html:927`, inside `renderTable`):

```js
        initOverlayConfigs(files);
        refreshBgSelects();
        refreshAllPreviews();
        updateSelectedCount();
        refreshStudioFileLists(files);
```

Replace with:

```js
        initOverlayConfigs(files);
        switchOverlayEditTarget(null);
        refreshBgSelects();
        refreshAllPreviews();
        updateSelectedCount();
        refreshStudioFileLists(files);
```

- [ ] **Step 5: Fix the generate handler to always send the full nested overlay shape**

Find (originally `video_creator.html:1234-1257`):

```js
        // Build per-file overlay_configs map
        const overlayConfigsMap = {};
        Object.entries(overlayConfigs).forEach(([idx, cfg]) => {
            if (cfg.text || cfg.marquee.enabled) {
                overlayConfigsMap[idx] = cfg;
            }
        });

        // Get global overlay text directly from form input
        const globalOverlayText = document.getElementById('ov-text').value.trim();

        const config = {
            resolution: document.getElementById('cfg-resolution').value,
            fps: parseInt(document.getElementById('cfg-fps').value),
            codec: document.getElementById('cfg-codec').value,
            audio_bitrate: document.getElementById('cfg-audio-bitrate').value,
            image_type: document.getElementById('cfg-image-type').value,
            crf: parseInt(document.getElementById('cfg-crf').value),
            max_concurrent: parseInt(document.getElementById('cfg-concurrent').value) || 3,
            music_id: musicSel.value ? parseInt(musicSel.value) : null,
            music_volume: parseInt(document.getElementById('cfg-music-volume').value),
            overlay: globalOverlayText ? { text: globalOverlayText } : null,
            overlay_configs: Object.keys(overlayConfigsMap).length ? overlayConfigsMap : null
        };
```

Replace with:

```js
        // Make sure whatever's on the form right now is captured, even if
        // the user never switched rows or clicked "Chỉnh" before generating.
        saveFormToOverlayConfig(currentOverlayTarget());

        // Per-file overrides: every file the user explicitly customized,
        // sent as its full nested shape.
        const overlayConfigsMap = {};
        Object.entries(overlayConfigs).forEach(([idx, cfg]) => { overlayConfigsMap[idx] = cfg; });

        const config = {
            resolution: document.getElementById('cfg-resolution').value,
            fps: parseInt(document.getElementById('cfg-fps').value),
            codec: document.getElementById('cfg-codec').value,
            audio_bitrate: document.getElementById('cfg-audio-bitrate').value,
            image_type: document.getElementById('cfg-image-type').value,
            crf: parseInt(document.getElementById('cfg-crf').value),
            max_concurrent: parseInt(document.getElementById('cfg-concurrent').value) || 3,
            music_id: musicSel.value ? parseInt(musicSel.value) : null,
            music_volume: parseInt(document.getElementById('cfg-music-volume').value),
            // Full nested shape, matching overlay_configs entries — the bug
            // fix. Only sent when there's actual text (matches backend gate
            // at video.py:549, which skips rendering when text is empty).
            overlay: defaultOverlayConfig.text ? defaultOverlayConfig : null,
            overlay_configs: Object.keys(overlayConfigsMap).length ? overlayConfigsMap : null
        };
```

- [ ] **Step 6: Decouple the preview-file dropdown from the overlay edit target (fixes the ReferenceError)**

Find, in the drag-preview IIFE (originally `video_creator.html:1384-1395`):

```js
    function currentBgPath() {
        // If actively editing a file, use its selected background
        if (typeof activeEditIndex !== 'undefined' && activeEditIndex !== null) {
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

Replace with:

```js
    function currentBgPath() {
        // If actively editing a specific file's overlay override, preview
        // against that file's background (via the batch-upload IIFE's
        // bridge — activeEditIndex lives in that other closure).
        const activeIdx = window.__studioGetActiveEditIndex ? window.__studioGetActiveEditIndex() : null;
        if (activeIdx !== null && activeIdx !== undefined) {
            const sel = document.querySelector(`.bg-select[data-index="${activeIdx}"]`);
            if (sel) return sel.value;
        }
        // Otherwise (editing the shared default) fall back to whichever
        // file is chosen in the "Preview với file" dropdown.
        const idx = previewRowSelect.value;
        if (idx === '') return '';
        const sel = document.querySelector(`.bg-select[data-index="${idx}"]`);
        return sel ? sel.value : '';
    }
```

Find (originally `video_creator.html:1480-1490`, the per-field listeners
that currently only trigger `scheduleRefresh`):

```js
    overlayFormIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.addEventListener('input', scheduleRefresh); el.addEventListener('change', scheduleRefresh); }
    });
    
    // Changing anchor position/alignment resets drag offset
    ['ov-position', 'ov-alignment'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => {
            document.getElementById('ov-offset-x').value = 0;
            document.getElementById('ov-offset-y').value = 0;
            document.getElementById('ov-offset-label').textContent = '0, 0';
            scheduleRefresh();
        });
    });
    
    previewRowSelect.addEventListener('change', () => {
        if (activeEditIndex !== null) {
            saveFormToOverlayConfig(activeEditIndex);
        }
        const newIdx = parseInt(previewRowSelect.value);
        loadOverlayConfigToForm(newIdx);
        // Also update studio's mix-ref if needed
    });
    
    document.getElementById('ov-offset-reset').addEventListener('click', () => {
        document.getElementById('ov-offset-x').value = 0;
        document.getElementById('ov-offset-y').value = 0;
        document.getElementById('ov-offset-label').textContent = '0, 0';
        scheduleRefresh();
    });
```

Replace with:

```js
    function saveOverlayField() {
        if (window.__studioOnOverlayFieldChanged) window.__studioOnOverlayFieldChanged();
    }

    overlayFormIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', () => { saveOverlayField(); scheduleRefresh(); });
            el.addEventListener('change', () => { saveOverlayField(); scheduleRefresh(); });
        }
    });
    
    // Changing anchor position/alignment resets drag offset
    ['ov-position', 'ov-alignment'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => {
            document.getElementById('ov-offset-x').value = 0;
            document.getElementById('ov-offset-y').value = 0;
            document.getElementById('ov-offset-label').textContent = '0, 0';
            saveOverlayField();
            scheduleRefresh();
        });
    });
    
    // Only controls which file's background shows in the live preview now
    // — which overlay config is being edited is controlled solely by the
    // "Chỉnh" button / "Overlay mặc định" control (batch-upload IIFE).
    previewRowSelect.addEventListener('change', () => {
        refreshPreview();
    });
    
    document.getElementById('ov-offset-reset').addEventListener('click', () => {
        document.getElementById('ov-offset-x').value = 0;
        document.getElementById('ov-offset-y').value = 0;
        document.getElementById('ov-offset-label').textContent = '0, 0';
        saveOverlayField();
        scheduleRefresh();
    });
```

Find (originally `video_creator.html:1540-1547`):

```js
    dragRect.addEventListener('pointerup', () => {
        if (!drag) return;
        offX.value = Math.round((parseInt(offX.value, 10) || 0) + drag.dx);
        offY.value = Math.round((parseInt(offY.value, 10) || 0) + drag.dy);
        drag = null;
        dragRect.classList.remove('dragging');
        refreshPreview();
    });
```

Replace with:

```js
    dragRect.addEventListener('pointerup', () => {
        if (!drag) return;
        offX.value = Math.round((parseInt(offX.value, 10) || 0) + drag.dx);
        offY.value = Math.round((parseInt(offY.value, 10) || 0) + drag.dy);
        drag = null;
        dragRect.classList.remove('dragging');
        saveOverlayField();
        refreshPreview();
    });
```

- [ ] **Step 7: Manual verification in-browser**

Use the `run` skill to start the app (if not already running from Task 2),
open `/video`, and using the claude-in-chrome tools (or manually):

1. Upload one audio file.
2. In the Studio panel, **without clicking "Chỉnh" on any row**, type text
   in "Text", enable Shadow, enable Background box (pick a non-default
   color), enable Marquee.
3. Open the browser's Network tab, click "Generate Selected Videos", and
   inspect the JSON body sent to `POST /video/generate-batch`.
4. Confirm `config.overlay` is the full nested object (has `shadow`, `box`,
   `marquee` sub-objects with `enabled: true` where you enabled them) — not
   just `{"text": "..."}`.
5. Click "Chỉnh" on the file's row, confirm the badge switches to the
   filename and a blue dot appears next to that row's "Chỉnh" button, along
   with a new "Mặc định" button.
6. Change the "Preview với file" dropdown (with 2+ uploaded files) and
   confirm no console error appears and the preview image updates.

Expected: overlay payload carries the full config; override dot/reset button
appear correctly; no console errors.

- [ ] **Step 8: Commit**

```bash
git add app/static/video_creator.js app/templates/video_creator.html
git commit -m "fix: send full overlay config to backend, not just text

Shadow/box/marquee settings were silently dropped during video generation
unless the user clicked into a specific file's row first, because the
batch-wide overlay fallback only ever sent {text}. Also replaces the
per-file-always-independent config model with a shared default + explicit
per-file overrides, saved on every form change, and fixes a latent
ReferenceError in the preview-file dropdown caused by two separate script
closures reaching into each other's local variables directly."
```

---

### Task 4: Two-tab page structure (Create Video / Video Library)

**Files:**
- Modify: `app/templates/video_creator.html`
- Modify: `app/static/video_creator.js`

**Interfaces:**
- Consumes: `loadVideos()` (existing function in the Video Library IIFE).
- Produces: `window.__videoLibraryLoad` (bridge so the tab switcher can lazily trigger the Library's first load) and `window.__videoSwitchToLibraryTab` (bridge so the "Xem trong Video Library" result link can switch tabs).

- [ ] **Step 1: Wrap the two sections in tab panels**

In `app/templates/video_creator.html`, find (lines 4-25, the page header
through the start of the Video Library card):

```html
<h2>Video Creator</h2>
<p style="color:var(--text-muted);margin-bottom:var(--space-lg)">Tạo video từ file âm thanh + hình ảnh với cài đặt ffmpeg tuỳ chỉnh.</p>

{% if error %}
<div class="error-block"><p style="margin:0"><strong>Error:</strong> {{ error }}</p></div>
{% endif %}

{% if video_url %}
<div class="video-preview-section">
    <h3 style="margin-top:0">Video created successfully!</h3>
    <video controls width="640" src="{{ video_url }}"></video>
    <div class="btn-group" style="margin-top:var(--space-md)">
        <a href="{{ video_url }}" download class="btn-download">Download .mp4</a>
        <form method="get" action="/video" style="margin:0">
            <button type="submit" class="btn-outline">Tạo video khác</button>
        </form>
    </div>
</div>
{% endif %}

<!-- Video Library -->
<div class="card" id="video-library">
```

Replace with:

```html
<h2>Video Creator</h2>
<p class="vc-muted-intro">Tạo video từ file âm thanh + hình ảnh với cài đặt ffmpeg tuỳ chỉnh.</p>

<div class="view-toggle" role="tablist" aria-label="Video Creator sections" style="margin-bottom:var(--space-lg)">
    <button type="button" id="tab-btn-create" class="active" role="tab" aria-selected="true" aria-controls="tab-panel-create">Create Video</button>
    <button type="button" id="tab-btn-library" role="tab" aria-selected="false" aria-controls="tab-panel-library">Video Library</button>
</div>

<div id="tab-panel-create" role="tabpanel" aria-labelledby="tab-btn-create">

{% if error %}
<div class="error-block"><p style="margin:0"><strong>Error:</strong> {{ error }}</p></div>
{% endif %}

{% if video_url %}
<div class="video-preview-section">
    <h3 style="margin-top:0">Video created successfully!</h3>
    <video controls width="640" src="{{ video_url }}"></video>
    <div class="btn-group" style="margin-top:var(--space-md)">
        <a href="{{ video_url }}" download class="btn-download">Download .mp4</a>
        <form method="get" action="/video" style="margin:0">
            <button type="submit" class="btn-outline">Tạo video khác</button>
        </form>
    </div>
</div>
{% endif %}
```

Then find the end of the Create Video flow and the start of the Video
Library card's closing structure — the Video Library `<div class="card"
id="video-library">` block (originally ends right before `<!-- Edit Modal
-->` at line 87) needs to move to *after* `<!-- Step 4: Results -->` closes
(originally line 370). Concretely: cut the entire `<!-- Video Library -->
<div class="card" id="video-library"> ... </div>` block (originally lines
24-85) from its current position (before `<!-- Edit Modal -->`) and paste it
immediately after the `<!-- Step 4: Results -->` section's closing `</div>`
(originally line 370), followed by a closing `</div>` for `tab-panel-create`,
then the tab-panel-library wrapper:

```html
</div><!-- /Step 4: Results -->

</div><!-- /tab-panel-create -->

<div id="tab-panel-library" role="tabpanel" aria-labelledby="tab-btn-library" hidden>
<!-- Video Library -->
<div class="card" id="video-library">
    ... (unchanged contents) ...
</div>
</div><!-- /tab-panel-library -->

<!-- Edit Modal -->
<div id="edit-modal" ...>
    ... (unchanged) ...
</div>
```

The Edit Modal (video edit dialog, `id="edit-modal"`) stays outside both tab
panels at the end of the page (it's a global overlay, not tied to either
tab's visibility).

- [ ] **Step 2: Add the tab-switching JS and lazy-load the library**

In `app/static/video_creator.js`, find the end of the Video Library IIFE
(originally `video_creator.html:632-634`):

```js
    // Initial load
    loadVideos();
})();
```

Replace with:

```js
    // Loaded lazily the first time the Video Library tab is activated
    // (see the tab-switcher IIFE below), not unconditionally on page load.
    window.__videoLibraryLoad = loadVideos;
})();

(function() {
    const btnCreate = document.getElementById('tab-btn-create');
    const btnLibrary = document.getElementById('tab-btn-library');
    const panelCreate = document.getElementById('tab-panel-create');
    const panelLibrary = document.getElementById('tab-panel-library');
    if (!btnCreate || !btnLibrary || !panelCreate || !panelLibrary) return;
    let libraryLoaded = false;

    function activate(tab) {
        const isCreate = tab === 'create';
        btnCreate.classList.toggle('active', isCreate);
        btnLibrary.classList.toggle('active', !isCreate);
        btnCreate.setAttribute('aria-selected', String(isCreate));
        btnLibrary.setAttribute('aria-selected', String(!isCreate));
        panelCreate.hidden = !isCreate;
        panelLibrary.hidden = isCreate;
        if (!isCreate && !libraryLoaded) {
            libraryLoaded = true;
            if (window.__videoLibraryLoad) window.__videoLibraryLoad();
        }
    }

    btnCreate.addEventListener('click', () => activate('create'));
    btnLibrary.addEventListener('click', () => activate('library'));
    window.__videoSwitchToLibraryTab = () => activate('library');
})();
```

- [ ] **Step 3: Link to the Library tab from a finished batch**

In `app/static/video_creator.js`, find (in `pollBatchResults`, originally
`video_creator.html:1108-1113`):

```js
                let summaryHtml = '';
                if (batchDone) {
                    summaryHtml = `<p><strong>Hoàn thành!</strong> ${doneCount} thành công, ${errCount} lỗi.</p>`;
                } else {
                    summaryHtml = `<p>Đang xử lý: ${procCount} đang chạy, ${doneCount} xong, ${errCount} lỗi.</p>`;
                }
                
                resultsList.innerHTML = summaryHtml;
```

Replace with:

```js
                let summaryHtml = '';
                if (batchDone) {
                    summaryHtml = `<p><strong>Hoàn thành!</strong> ${doneCount} thành công, ${errCount} lỗi. <a href="#" id="link-view-library">Xem trong Video Library</a></p>`;
                } else {
                    summaryHtml = `<p>Đang xử lý: ${procCount} đang chạy, ${doneCount} xong, ${errCount} lỗi.</p>`;
                }
                
                resultsList.innerHTML = summaryHtml;
                const libLink = document.getElementById('link-view-library');
                if (libLink) libLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (window.__videoSwitchToLibraryTab) window.__videoSwitchToLibraryTab();
                });
```

- [ ] **Step 4: Manual verification in-browser**

Start the app, open `/video`. Confirm:
1. Page loads on the "Create Video" tab; Video Library table does NOT fetch
   until you click the "Video Library" tab (check Network tab — no
   `/video/api/videos` request until the tab is clicked).
2. Clicking "Video Library" shows the table, pagination, filters — all
   working as before.
3. Clicking back to "Create Video" preserves any in-progress upload/batch
   state (it was never destroyed, just hidden).
4. Generate a video, confirm the "Xem trong Video Library" link appears and
   switches tabs on click.

- [ ] **Step 5: Commit**

```bash
git add app/templates/video_creator.html app/static/video_creator.js
git commit -m "feat: split video creator into Create Video / Video Library tabs"
```

---

### Task 5: CSS cleanup — fix undefined tokens, remove inline styles

**Files:**
- Modify: `app/static/style.css`
- Modify: `app/templates/video_creator.html`

**Interfaces:**
- Consumes: none new.
- Produces: none new (pure cleanup); the `--radius-md`/`--radius-lg`/
  `--bg-primary` fixes below also apply to `app/templates/book_detail.html`,
  which references `--radius-md`/`--radius-lg` too (grep confirmed) — check
  it after this task's CSS edit since it relies on the same undefined tokens
  silently falling back to browser defaults today.

- [ ] **Step 1: Add small layout helper classes**

In `app/static/style.css`, near the existing video-creator-specific rules
(after `.video-creator-form` around line 1085), add:

```css
/* Video Creator: layout helpers replacing ad hoc inline styles */
.vc-muted-intro { color: var(--text-muted); margin-bottom: var(--space-lg); }
.vc-row-between { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-md); }
.vc-row-wrap { display: flex; flex-wrap: wrap; gap: var(--space-sm); margin-bottom: var(--space-md); }
.vc-inline-flex { display: flex; align-items: center; gap: var(--space-sm); }
.vc-flex-1 { flex: 1; min-width: 200px; }
.hidden { display: none; }
```

- [ ] **Step 2: Fix the undefined CSS custom properties**

In `app/templates/video_creator.html`, find (`studio-card`'s inline
`<style>` block, originally line 193):

```html
<div id="ov-preview-stage" style="position:relative;line-height:0;border:1px solid var(--border-color);border-radius:var(--radius-md);overflow:hidden">
```

Replace `var(--radius-md)` with `var(--border-radius)`.

Find (the Edit Modal, originally line 89):

```html
    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--bg-primary);padding:var(--space-lg);border-radius:var(--radius-lg);width:90%;max-width:500px">
```

Replace `var(--bg-primary)` with `var(--bg-card)` and `var(--radius-lg)`
with `var(--border-radius-lg)`.

Run: `grep -n "radius-md\|radius-lg\|bg-primary" "D:/Projects/epub-audiobook-app/app/templates/book_detail.html"`

If it also references `var(--radius-md)`/`var(--radius-lg)`/
`var(--bg-primary)`, apply the same three substitutions there (this task is
explicitly allowed to touch `book_detail.html` for this one fix, since it's
the same pre-existing undefined-token bug — nothing else in that file).

- [ ] **Step 3: Replace inline styles with the new helper classes**

In `app/templates/video_creator.html`, apply this mapping (each row: exact
original `style="..."` value at its original line number &rarr; replacement):

| Original line (pre-Task-4) | `style="..."` value | Replacement |
|---|---|---|
| 5 | `color:var(--text-muted);margin-bottom:var(--space-lg)` | class `vc-muted-intro` (already done in Task 4 step 1) |
| 28 | `display:flex;gap:var(--space-sm)` | class `vc-inline-flex` |
| 35 | `display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-md)` | class `vc-row-between` |
| 41 | `display:flex;flex-wrap:wrap;gap:var(--space-sm);margin-bottom:var(--space-md)` | class `vc-row-wrap` |
| 42 | (keep `flex:1;min-width:200px` inline — it's a one-off on `#filter-search`, not worth a class) | leave as-is |
| 112 | `display:flex;gap:var(--space-sm);justify-content:flex-end` | classes `vc-inline-flex vc-justify-end` (both applied together; `vc-justify-end` added in this step, see below) |
| 133 | `display:none` | class `hidden` |
| 140 | `display:none` | class `hidden` |
| 205 | `display:flex;align-items:center;gap:var(--space-sm);flex-wrap:wrap` | class `vc-row-wrap` |
| 209 | `display:flex;align-items:center;gap:var(--space-sm);margin-top:var(--space-sm)` | class `vc-inline-flex` |
| 246 | `display:flex;gap:var(--space-sm);margin-bottom:var(--space-md)` | class `vc-row-wrap` |
| 367 | `display:none` | class `hidden` |

Leave every other `style="..."` occurrence as-is (single-purpose one-liners
like `margin:0`, `width:100%`, `font-size:...` on a single element, and the
handful inside dynamically-generated JS template literals in
`video_creator.js`) — converting those would add more classes than the
inline style they replace is worth, per the design's "no new visual
language" constraint.

Add the one extra helper class referenced above:

```css
.vc-justify-end { justify-content: flex-end; }
```

- [ ] **Step 4: Manual verification in-browser**

Reload `/video`, visually compare against a screenshot taken before this
task (or just confirm layout/spacing looks unchanged — this is a pure
class-for-inline-style swap, zero visual difference expected). Confirm the
Studio preview image's corners are now actually rounded (previously
silently un-rounded due to the undefined `--radius-md`), and the Edit Video
modal's background/corners render correctly (previously silently using
browser defaults due to `--bg-primary`/`--radius-lg`).

- [ ] **Step 5: Commit**

```bash
git add app/static/style.css app/templates/video_creator.html app/templates/book_detail.html
git commit -m "style: fix undefined CSS custom properties, replace inline styles with classes"
```

---

### Task 6: Full end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: all tests pass, including the two new ones from Task 1.

- [ ] **Step 2: Full manual walkthrough**

Using the `run` skill / claude-in-chrome tools against a running instance:

1. Load `/video` — lands on "Create Video" tab, Video Library not yet
   fetched.
2. Upload 2+ audio files.
3. In Studio, without clicking "Chỉnh" on any row: set text, enable+
   configure Shadow, Background box, and Marquee. Drag the text box in the
   preview to a new position.
4. Click "Chỉnh" on file #1's row — confirm the badge shows its filename,
   the blue override dot and "Mặc định" button appear on that row, and the
   form still shows the settings from step 3 (copied in as the starting
   override).
5. Change file #1's override (e.g. different text), click "&larr; Overlay
   mặc định" — confirm the form reverts to the shared default's settings
   (from step 3, unaffected by file #1's override).
6. Click "Mặc định" on file #1's row to remove its override — confirm the
   dot disappears.
7. Select all files, click "Generate Selected Videos".
8. Once done, download and play at least one generated video (or inspect
   with `ffprobe`) — confirm the text overlay is visible with the shadow and
   background box actually rendered (not just plain text), verifying the
   bug is fixed end-to-end, not just at the payload level.
9. Click "Xem trong Video Library" — confirm it switches tabs and the new
   video appears in the table.
10. In the Video Library tab: search, filter by status, sort, paginate (if
    enough rows), open the Edit modal and save a title change, select rows
    and confirm bulk buttons enable/disable correctly.

Expected: every step behaves as described, no console errors at any point.

- [ ] **Step 3: Report results**

Summarize pass/fail for each numbered item in Step 2. If anything fails, fix
it in the relevant earlier task's file, re-run the affected verification
step, and amend that task's commit history with a new fix commit (do not
rewrite already-pushed history).

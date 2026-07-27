# Book Detail UX Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the book detail page around setup, patch production, and selected-patch export workflows without changing its backend contracts.

**Architecture:** Keep all existing server routes and runtime constants intact. Restructure `book_detail.html` markup and its small page-local JavaScript so advanced per-patch media controls are opened through a dialog and batch controls are visually grouped while continuing to use their existing IDs and event handlers.

**Tech Stack:** FastAPI, Jinja2, browser DOM APIs, existing CSS custom properties.

## Global Constraints

- Modify `app/templates/book_detail.html` only for the UI implementation.
- Leave `app/templates/patch_builder.html`, database schema, backend routes, and TTS/video pipelines untouched.
- Preserve existing form IDs, endpoint URLs, `BOOK_ID`, `ACTIVE_TASKS`, and batch button IDs.
- Do not add dependencies.
- Selected actions operate only on selected rows.

---

### Task 1: Group Workflow Navigation

**Files:**
- Modify: `app/templates/book_detail.html:26-34`
- Test: `tests/test_routes_preview.py`

**Interfaces:**
- Consumes: Existing dialog IDs `studio-modal`, `normalization-modal`, `light-tts-modal`, `video-config-modal`, and `rules-modal`.
- Produces: A `Tools` native details menu without changing dialog launch attributes.

- [ ] **Step 1: Write the failing template assertion**

```python
def test_book_detail_groups_supporting_tools(client):
    response = client.get('/books/1')
    assert 'class="book-tools"' in response.text
    assert 'data-open-dialog="video-config-modal"' in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_preview.py -q`
Expected: FAIL because `book-tools` is absent.

- [ ] **Step 3: Write minimal implementation**

```html
<details class="book-tools">
    <summary>Tools</summary>
    <div class="book-tools-menu">
        <a href="/books/{{ book.id }}/text-studio">Text Studio</a>
        <button type="button" data-open-dialog="normalization-modal">Cài đặt TTS</button>
        <button type="button" data-open-dialog="light-tts-modal">Cài đặt LightTTS</button>
        <button type="button" data-open-dialog="video-config-modal">Cấu hình video</button>
        <button type="button" data-open-dialog="rules-modal">Replace rules</button>
    </div>
</details>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes_preview.py -q`
Expected: PASS.

### Task 2: Move Advanced Patch Actions Into a Dialog

**Files:**
- Modify: `app/templates/book_detail.html:172-230,362-396,1863-1935,2115-2133`
- Test: `tests/test_routes_preview.py`

**Interfaces:**
- Consumes: Existing `/upload-audio`, `/background`, `/video`, and `/youtube-upload` routes.
- Produces: `openPatchMediaModal(patchId, patchName, patchStatus)` and click handling for `.patch-media-btn`.

- [ ] **Step 1: Write the failing template assertion**

```python
def test_book_detail_uses_media_dialog_not_inline_popover(client):
    response = client.get('/books/1')
    assert 'id="patch-media-modal"' in response.text
    assert 'patch-inline-upload-body' not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_preview.py -q`
Expected: FAIL because the media dialog is absent and the inline popover remains.

- [ ] **Step 3: Write minimal implementation**

```html
<button type="button" class="btn-outline btn-sm patch-media-btn"
        data-patch-id="{{ patch.id }}" data-patch-name="{{ patch.name or patch.patch_index }}"
        data-patch-status="{{ patch.status }}">More</button>
```

```javascript
function openPatchMediaModal(pid, name, status) {
    document.getElementById('pm-title').textContent = name;
    document.getElementById('pm-audio-section').hidden = status === 'done';
    document.getElementById('patch-media-modal').showModal();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes_preview.py -q`
Expected: PASS.

### Task 3: Group Selected-Patch Actions

**Files:**
- Modify: `app/templates/book_detail.html:410-438,470-482`
- Test: `tests/test_routes_preview.py`

**Interfaces:**
- Consumes: Existing `btnRunSelectedLightTTS`, `btnGenSelectedImages`, `btnGenSelectedVideos`, and export form/button IDs.
- Produces: Generate and Export layout groups that preserve all existing handlers.

- [ ] **Step 1: Write the failing template assertion**

```python
def test_book_detail_groups_selected_actions(client):
    response = client.get('/books/1')
    assert 'class="batch-action-group"' in response.text
    assert '>Generate<' in response.text
    assert '>Export<' in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_routes_preview.py -q`
Expected: FAIL because action groups are absent.

- [ ] **Step 3: Write minimal implementation**

```html
<div class="batch-action-group">
    <span class="batch-action-label">Generate</span>
    <!-- Existing selected TTS, image, video, and YouTube controls retain their IDs. -->
</div>
<div class="batch-action-group">
    <span class="batch-action-label">Export</span>
    <!-- Existing zip, Drive, and description controls retain their form bindings and IDs. -->
</div>
```

```css
.patch-bottom-nav { align-items:center; flex-wrap:wrap; }
.batch-action-group { display:flex; align-items:center; gap:var(--space-xs); flex-wrap:wrap; }
.batch-action-label { color:var(--text-muted); font-size:var(--font-size-xs); font-weight:600; text-transform:uppercase; }
@media (max-width:640px) { .batch-action-group { width:100%; } }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes_preview.py -q`
Expected: PASS.

### Task 4: Verify Rendering and Regression Coverage

**Files:**
- Test: `tests/test_routes_preview.py`

**Interfaces:**
- Consumes: The route fixture used by existing book-detail tests.
- Produces: Evidence that the template renders and selected controls remain present.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_routes_preview.py tests/test_patch_preview_actions.py -q`
Expected: PASS.

- [ ] **Step 2: Compile Python routes**

Run: `python -m compileall app/routes`
Expected: all route modules compile successfully.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check && git diff -- app/templates/book_detail.html`
Expected: no whitespace errors and no backend/template files outside the scoped UI change.

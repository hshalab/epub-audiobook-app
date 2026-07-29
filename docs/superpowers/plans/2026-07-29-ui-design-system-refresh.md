# UI Design System Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add shared UI primitives and use them to make the video configuration modal tabbed with a footer.

**Architecture:** Keep the app server-rendered and dependency-free. Add small CSS primitives to `style.css`, a small tab initializer to existing plain JavaScript, and update `book_detail.html` without changing backend field IDs.

**Tech Stack:** Jinja templates, native `<dialog>`, CSS, plain JavaScript.

## Global Constraints

- No Bootstrap, shadcn, or frontend build step.
- Keep existing element IDs and form fields used by current JavaScript.
- Do not change backend video configuration behavior.
- Avoid full visual redesign of every template in one pass.

---

### Task 1: Shared Modal And Tab Primitives

**Files:**
- Modify: `app/static/style.css`
- Modify: `app/static/autosave.js`

**Interfaces:**
- Consumes: Existing CSS variables in `style.css`.
- Produces: `.ui-modal-header`, `.ui-modal-body`, `.ui-modal-footer`, `.ui-tabs`, `.ui-tab-list`, `.ui-tab`, `.ui-tab-panel`, and a `data-tabs` initializer.

- [x] **Step 1: Add CSS primitives**

Add reusable modal and tab CSS to `app/static/style.css` near existing form/modal component styles.

- [x] **Step 2: Add tab JavaScript**

Add a DOMContentLoaded initializer to `app/static/autosave.js` that switches `[data-tab-target]` buttons inside `[data-tabs]` containers and keeps `hidden`, `aria-selected`, and `tabindex` in sync.

- [x] **Step 3: Verify no syntax errors**

Run: `node --check app/static/autosave.js`

Expected: no output and exit code 0.

### Task 2: Tab Video Config Modal

**Files:**
- Modify: `app/templates/book_detail.html`

**Interfaces:**
- Consumes: CSS/JS primitives from Task 1.
- Produces: `video-config-modal` with three tab panels and footer save action. Existing IDs such as `vc-save`, `vc-voice`, `vc-backgrounds`, `vc-resolution`, and related controls remain unchanged.

- [x] **Step 1: Replace fieldsets with tab panels**

Update `video-config-modal` so the current `Nội dung`, `Background`, and `Render` fieldsets become tab panels under a `data-tabs` container.

- [x] **Step 2: Add modal footer**

Move the `Lưu cấu hình` button to `.ui-modal-footer`, preserving `id="vc-save"`. Add a `Đóng` button with `data-close-dialog`.

- [x] **Step 3: Remove redundant local CSS**

Delete local `.vc-section` styling that is fully replaced by shared primitives. Keep video-config-specific styles for help text and background thumbnails.

- [x] **Step 4: Verify template references**

Search for `vc-save`, `vc-backgrounds`, `vc-resolution`, and `data-tabs` to ensure IDs remain present exactly once where expected.

### Task 3: Project-Wide Foundation Pass

**Files:**
- Modify: `app/templates/book_detail.html`
- Modify: `app/static/style.css`

**Interfaces:**
- Consumes: Shared modal structure from Tasks 1 and 2.
- Produces: Book detail modals that can progressively adopt the shared modal header/body/footer classes without behavior changes.

- [x] **Step 1: Apply shared header class where direct**

For modal headers already using `class="card-header"`, add `ui-modal-header` only when it does not change the element structure or JavaScript hooks.

- [x] **Step 2: Keep non-modal components unchanged**

Do not refactor tables, sidebar, or page-level navigation in this pass.

- [x] **Step 3: Verify visual-risk boundaries**

Confirm the diff changes only `style.css`, `autosave.js`, `book_detail.html`, and plan/spec docs.

### Task 4: Verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes: All prior tasks.
- Produces: Evidence that JavaScript parses and template changes are structurally sane.

- [x] **Step 1: Run JS syntax check**

Run: `node --check app/static/autosave.js`

Expected: no output and exit code 0.

- [x] **Step 2: Run available tests**

Run the repository's available test command after inspecting project files. If no test command is discoverable, report that clearly.

- [x] **Step 3: Inspect git diff**

Run: `git diff -- app/templates/book_detail.html app/static/style.css app/static/autosave.js docs/superpowers/specs/2026-07-29-ui-design-system-refresh-design.md docs/superpowers/plans/2026-07-29-ui-design-system-refresh.md`

Expected: only intended UI primitive and modal changes.

## Self-Review

- Spec coverage: Tasks cover shared primitives, tabbed video config modal, modal footer, dependency-free JavaScript, and verification.
- Placeholder scan: no placeholder implementation details remain.
- Type consistency: CSS class names and `data-tabs`/`data-tab-target` naming are consistent across tasks.

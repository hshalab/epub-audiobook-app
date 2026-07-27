# Data Table Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable client-side search, automatic filters, sorting, entries selection, pagination, and per-table filter persistence to all opted-in data tables.

**Architecture:** Add one reusable controller in `app/static/data_tables.js`, loaded by `base.html`, and opt data-list tables into it with `.data-table` plus stable keys. Existing bespoke dynamic tables remain on their current data flow unless explicitly adapted, preventing conflicting pagination and event handlers.

**Tech Stack:** Jinja2 templates, vanilla JavaScript, existing CSS variables and `localStorage`.

## Global Constraints

- Only explicitly marked data-list tables are enhanced.
- Persist search and filters only; do not persist sort or entries-per-page.
- Entries options are `25 / 50 / 100 / All`, default `25`.
- Automatic filters require 2-10 distinct values and exclude action, control, primarily-link, and long-content columns.
- Preserve links, forms, checkboxes, inline actions, and existing business filters.
- Do not add dependencies or change backend pagination APIs.

---

### Task 1: Add Shared Table Controller Core

**Files:**
- Create: `app/static/data_tables.js`
- Modify: `app/templates/base.html:12,105-106`
- Modify: `app/static/style.css` near existing table and responsive styles

**Interfaces:**
- Produces global initialization for `table.data-table` elements.
- Each table may set `data-table-key`; otherwise use its stable DOM index as a fallback.
- Exposes `window.DataTables.refresh(table)` for pages that replace `<tbody>` content.

- [ ] **Step 1: Add the controller behavior**

Implement a no-dependency controller that:

```js
const ENTRY_OPTIONS = [25, 50, 100, Infinity];
const STORAGE_PREFIX = 'data-table-state:';

function initDataTables(root = document) { /* initialize table.data-table */ }
window.DataTables = { init: initDataTables, refresh: refreshDataTable };
```

Capture original body rows once, normalize cell text, ignore placeholder empty-state rows, and render only matching rows. Search must inspect all cell text. Filters must be stored as `{search: '', filters: {}}` under `${STORAGE_PREFIX}${location.pathname}:${tableKey}`. Read storage defensively and ignore malformed values.

Build the toolbar before the table with search input, eligible-column selects, entries select, result count, and pagination buttons. Make `<th>` headers sortable except action/control columns. Compare numeric values numerically, ISO/date-like values by timestamp, and everything else case-insensitive text; retain original order for equal values.

- [ ] **Step 2: Add shared styles**

Add compact responsive styles for `.data-table-toolbar`, `.data-table-filters`, `.data-table-pagination`, sort indicators, and disabled pagination buttons. Reuse existing CSS variables; allow toolbar controls to wrap on narrow screens.

- [ ] **Step 3: Load the script globally**

Add `/static/data_tables.js` after `autosave.js` in `base.html`, then initialize on `DOMContentLoaded`. Use a cache-busting version matching the current static asset convention.

- [ ] **Step 4: Verify the standalone core**

Run the existing Python test suite and load a page with a browser or local server. Confirm an unmarked table is unchanged and a marked table receives controls without console errors.

### Task 2: Opt In Static Data Tables

**Files:**
- Modify: `app/templates/book_list.html:11`
- Modify: `app/templates/drive.html` data-list tables around lines 62 and 82
- Modify: `app/templates/music.html:28`
- Modify: `app/templates/photos.html:28`
- Modify: `app/templates/voices.html` data-list table
- Modify: `app/templates/effects.html` data-list table
- Modify: `app/templates/youtube.html` data-list tables
- Modify: `app/templates/chunk_manager.html` list tables around lines 54 and 135
- Modify: `app/templates/patch_builder.html:32`

**Interfaces:**
- Each opted-in table supplies `class="data-table"` and unique `data-table-key` values stable across reloads.
- Tables used for forms, instructions, parameter references, or other layout-only content remain unmarked.

- [ ] **Step 1: Mark only actual list tables**

Add stable keys such as `books`, `drive-targets`, `drive-exports`, `music-assets`, `photos-assets`, `voices`, `effects`, `chunks`, `patches`, and `youtube-items`. Do not mark tables containing only explanatory text or form controls.

- [ ] **Step 2: Check action and control columns**

Ensure the controller can identify action headers/cells from their controls and labels, while keeping links and form controls clickable after sorting/pagination.

- [ ] **Step 3: Verify static pages**

For each page, check search, one automatic filter, header sorting, `All`, page navigation, empty results, and persistence after reload. Confirm state on one table does not appear on another.

### Task 3: Integrate Existing Patch Table

**Files:**
- Modify: `app/templates/book_detail.html:279-294,900-963`

**Interfaces:**
- Existing `filterAndPaginate()` remains the owner of the patch-selection table unless converted fully to the shared controller.
- Its existing patch search and checkbox behavior must not be double-bound.

- [ ] **Step 1: Inspect the current patch table row model**

Confirm the existing function’s search, pagination, selection, and row visibility assumptions before changing markup.

- [ ] **Step 2: Choose one owner**

Either mark the table for the shared controller and remove the bespoke pagination path, or leave it unmarked and add only the requested persistence/entries behavior to the existing function. Prefer the first option only if checkbox selection and dynamic patch actions remain intact.

- [ ] **Step 3: Verify patch workflows**

Test search, select-all-visible, selected patch actions, page changes, and reload persistence. Confirm no duplicate toolbar or conflicting page controls.

### Task 4: Keep Video Library Dynamic Flow Compatible

**Files:**
- Modify: `app/templates/video_creator.html:218-261`
- Modify: `app/static/video_creator.js` around video-library rendering and filtering

**Interfaces:**
- The video library is API-rendered and currently owns search/status/sort/per-page controls; it must not receive a second generic controller.
- Existing selectors `filter-search`, `filter-status`, `filter-sort`, and `filter-per-page` remain valid for `video_creator.js`.

- [ ] **Step 1: Leave the dynamic table outside generic initialization**

Do not add `.data-table` to the video library table while its rows are generated from API data and its controls are already custom.

- [ ] **Step 2: Align its entries and persistence behavior**

Change its per-page choices to `25 / 50 / 100 / All` and persist only search/status filter values under the same page/table naming convention. Keep sort reset on reload. If its existing API pagination prevents `All`, request/load all records only for the `All` option and retain current behavior for bounded sizes.

- [ ] **Step 3: Verify dynamic refreshes**

Test API reload, search/status filtering, sorting, bulk selection, upload/delete actions, and persistence after switching tabs and reloading.

### Task 5: Regression Verification

**Files:**
- Test: existing repository test locations; add `tests/test_data_tables.js` only if a JS test runner already exists

- [ ] **Step 1: Run repository checks**

Run the project’s existing test command discovered from `pyproject.toml` or package configuration. Also run `git diff --check`.

- [ ] **Step 2: Manually verify all supported pages**

Check desktop and narrow viewport behavior, table controls, action links/forms, empty states, and localStorage isolation for every opted-in table.

- [ ] **Step 3: Review the final diff**

Run `git status --short` and `git diff --stat`; confirm only the shared assets, intended templates, and tests/docs changed.

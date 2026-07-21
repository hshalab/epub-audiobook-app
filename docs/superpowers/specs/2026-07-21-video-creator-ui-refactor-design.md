# Video Creator UI/UX Refactor + Overlay Config Bug Fix — Design Spec

## Overview

`app/templates/video_creator.html` has grown into a single 1855-line page that
crams the Video Library table, a multi-step batch-upload wizard, and a
"Studio" text-overlay editor into one long scroll, with ~1400 lines of inline
`<script>` and ~49 inline `style="..."` attributes. It also has a confirmed
bug: shadow/background-box/marquee settings configured in the Studio panel
are silently dropped during video generation unless the user explicitly
clicks into a specific file's row first.

This refactor fixes the bug at its root and restructures the page for
maintainability and usability, without changing the Video Library's REST API
(`app/routes/video_api.py`) or backend generation logic
(`app/routes/video.py`), which are already correct and out of scope.

## Bug root cause

In the `btn-generate` click handler (`video_creator.html:1221-1257`), the
batch-wide overlay fallback sent to the backend is:

```js
overlay: globalOverlayText ? { text: globalOverlayText } : null,
```

On the backend (`app/routes/video.py:464-497`), `raw_cfg.get("overlay")`
becomes `effective_overlay_opts` for any file with no per-file
`overlay_configs` entry, and is passed whole into
`_convert_overlay_config_to_flat` (`video.py:136-173`), which expects a
nested shape with `position`, `alignment`, `font_size`, `text_color`,
`margin`, `offset_x`, `offset_y`, and `shadow{}`/`box{}`/`marquee{}`
sub-objects. Given only `{text}`, every other field silently defaults (e.g.
`shadow_enabled: "off"`, `box_enabled: "off"`), which is exactly why
shadow/box/marquee never apply. The backend merge logic itself
(`per_overlay if per_overlay else overlay_opts`) is already correct — this is
a frontend payload-shape bug only.

**Fix:** always send the full nested config object as `overlay`, matching
the shape already used for `overlay_configs[idx]` entries.

## Overlay config data model (replaces per-file-always-independent model)

Today, `initOverlayConfigs()` gives every uploaded file its own independent
config object initialized to defaults, and the form only writes back into
`overlayConfigs[idx]` when the user switches away from a row
(`loadOverlayConfigToForm`) — never on Generate itself. This is fragile and
confusing ("did my edit actually get saved anywhere?").

New model:

- `defaultOverlayConfig` — one shared config object. The Studio form edits
  this whenever no specific file is selected for override.
- `overlayConfigs = {}` — sparse map; a file only gets an entry once the user
  clicks "Chỉnh" on its row. That copies the current default in as a
  starting point; further edits while that file is active go only into its
  entry. A "Bỏ tuỳ chỉnh" action removes the entry, reverting the file to the
  shared default.
- **Save-on-every-change**: every form input handler writes directly into
  whichever target (default or active file override) is selected, replacing
  the old "only synced when you switch rows" behavior. This removes the bug
  class entirely, not just the specific box/shadow symptom.
- **Generate**: for each selected file, use its override if present, else
  `defaultOverlayConfig` — sent as the full nested shape either way.
- Table gets a small marker (dot/badge) on the `col-edit` cell showing which
  rows currently have a per-file override vs. use the default.

## Page structure: two tabs

Split into `Create Video` / `Video Library` tabs, switched client-side (both
panels render server-side in the same page load; JS toggles visibility, and
the Video Library's own `loadVideos()` polling/fetch only needs to run when
its tab is active — call it once on first activation rather than
unconditionally on page load).

- New tab bar built on top of the existing `.view-toggle` component (already
  used in `voices.html` for a Card/Table switch), extended with proper
  `role="tablist"`/`role="tab"`/`aria-selected` semantics and matching
  `role="tabpanel"`/`hidden` panels, since this is the first true
  content-switching tab component in the app — establish it cleanly so it's
  reusable elsewhere later.
- `Create Video` tab: Upload → Configure Files table → Studio → Video Config
  → Generate → Results (current steps 1-4), unchanged in order.
- `Video Library` tab: existing table/filters/pagination/bulk actions/edit
  modal, unchanged in behavior and API usage.
- After a batch finishes generating, show a small inline link ("Xem trong
  Video Library") in the Results section rather than auto-switching tabs.

## Code structure: extract inline JS

Move the ~1400 lines of inline `<script>` (lines 636-1711) verbatim into
`app/static/video_creator.js`, loaded via `<script src="/static/video_creator.js">`
at the same point in the page (matches the existing `app/static/autosave.js`
convention already used site-wide). Constraints confirmed by inspection:

- Keep it a classic script (no `type="module"`) — cross-section
  communication relies on implicit-global `function` declarations
  (`uploadToYouTube`, called from `onclick=` attributes in
  server/JS-generated HTML) and `window.__studio*` assignments
  (`__studioSetBatchId`, `__studioRepopulateMixRef`, `__studioRefreshPreview`,
  `__studioOnRowBgChanged`), guarded with `if (window.__studioX) ...` checks
  at call sites.
- Preserve exact source order when moving, since these globals are wired up
  sequentially through the file even though the guards make load order
  somewhat forgiving.
- Internally reorganize into clearly separated, commented sections (Video
  Library, Batch Upload/Table, Studio/Overlay incl. the new default+override
  model, Generate/Results) rather than one undifferentiated block — but this
  is organizational only, not a module split.

## Visual polish

- Replace inline `style="..."` attributes with classes in `style.css`,
  following the page's existing conventions (`.batch-*`, `.col-*`, `.bg-*`,
  `.form-group`/`.form-row`) rather than inventing a new visual language.
- Fix the `--radius-md` bug: `studio-card`'s inline `<style>` block
  (`video_creator.html:176-181`) references `var(--radius-md)`, which is
  undefined in `style.css` (only `--border-radius`/`-sm`/`-lg` exist) and
  silently falls back to the browser default. Replace with
  `var(--border-radius)`.
- No other visual-language changes — colors, spacing scale, card/button
  styles all stay as defined in `style.css`.

## Testing / verification

- Add a test covering the `overlay`/`overlay_configs` merge and full-config
  shape in `app/routes/video.py` (e.g. in `tests/test_video_batch_extras.py`
  or `tests/test_video_studio.py`, whichever fits better once implementing)
  — this exact gap (no test asserts on the fallback merge or on
  `_convert_overlay_config_to_flat` receiving an incomplete input) is what
  let the bug ship. At minimum: a batch-wide `overlay` with shadow/box
  enabled and no per-file override should render with shadow/box applied.
- Manual verification in-browser after implementation: upload files, set
  shadow+box+marquee in Studio without clicking "Chỉnh" on any row, generate,
  and confirm the rendered video/overlay preview reflects those settings.
  Also verify tab switching, per-file override/revert, and that Video
  Library behavior (pagination/filter/search/bulk actions/edit modal) is
  unchanged.

## Out of scope

- `app/routes/video_api.py`, `app/video_repository.py`, `app/upload_worker.py`
  — Video Library backend is correct and unchanged.
- `app/routes/video.py`'s per-file merge logic (`:495-497`) — already
  correct; only the frontend payload shape changes.
- No new features (no new overlay options, no new library filters).

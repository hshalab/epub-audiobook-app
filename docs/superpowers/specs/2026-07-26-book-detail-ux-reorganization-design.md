# Book Detail — UX Reorganization Design

Date: 2026-07-26
Status: Approved for planning
Scope: `app/templates/book_detail.html` first. Avoid backend/database changes unless implementation discovers a required wiring fix. Leave `patch_builder.html` untouched.

## Goal

Reorganize the book detail page so the many existing features do not compete with each other visually or behaviorally, while preserving the current production workflow:

- Configure book-level media/TTS/video settings.
- Manage patches.
- Run LightTTS.
- Generate overlay images.
- Generate or upload videos.
- Export/download/upload results.
- Access supporting text tools and replace rules.

The page should feel like a guided workspace rather than a flat list of unrelated controls.

## Current Problems

`app/templates/book_detail.html` has accumulated many features at the same visual level:

- The sticky nav exposes Studio, Patches, Text Studio, TTS normalization, LightTTS settings, video config, and replace rules as peer actions.
- Several independent settings dialogs fragment configuration across the page.
- The patch table mixes primary per-row actions with advanced actions such as upload audio, override background, animation, image generation, MP4 upload, and YouTube upload.
- Inline row popovers use absolute positioning and elevated z-index, which can overlap table content, dialogs, and the fixed bottom batch bar.
- The fixed bottom batch bar contains generation and export actions in one long row.
- Page-level CSS is mixed into the Studio modal area, making layout ownership harder to understand.
- Some batch labels say “selected”, so behavior should clearly operate on selected rows and avoid surprising all-row actions.

## Design Direction

Use a moderate reorganization: keep the current backend and JavaScript behavior where possible, but reshape the UI around a workflow.

Recommended information architecture:

```text
Book Detail
├── Header
│   ├── Book title / rename
│   └── Book status
│
├── Sticky Workflow Nav
│   ├── Studio Setup
│   ├── Patches
│   ├── Export
│   └── Tools
│       ├── Text Studio
│       ├── TTS Settings
│       ├── Video Settings
│       └── Replace Rules
│
├── Studio Modal
│   ├── Voice
│   ├── Background
│   ├── Overlay basic
│   ├── Overlay advanced
│   ├── Music
│   └── Preview / Save
│
├── Patches Card
│   ├── Search/filter toolbar
│   ├── Patch table
│   │   └── Row More modal for advanced per-patch actions
│   └── Pagination
│
├── Fixed Batch Bar
│   ├── Selection count
│   ├── Generate group
│   └── Export group
│
└── Supporting Modals
    ├── Settings modal(s)
    ├── Patch media settings modal
    ├── Patch image preview modal
    ├── Patch video preview modal
    └── Chunks modal
```

## Detailed Design

### 1. Sticky Workflow Nav

Replace the current long nav with grouped workflow-level entry points.

Target nav:

```text
[Studio Setup] [Patches] [Export] [Tools]
```

Behavior:

- `Studio Setup` opens the existing Studio modal.
- `Patches` scrolls to the patches card.
- `Export` scrolls to the batch/export area or focuses the batch controls when there is a selection.
- `Tools` groups supporting actions:
  - Text Studio link.
  - TTS/normalization settings.
  - LightTTS settings.
  - Video settings.
  - Replace rules.

Implementation can use a native `<details>` dropdown for Tools to avoid adding dependencies.

### 2. Settings Organization

Do not surface all settings as top-level nav buttons.

Preferred grouping:

```text
Settings
├── TTS
│   ├── Normalization
│   └── LightTTS backend / voice / max chars / FX
├── Video
│   ├── Resolution
│   ├── FPS
│   ├── Default animation
│   └── YouTube defaults
└── Text
    └── Replace rules
```

For the first implementation pass, it is acceptable to preserve existing modal markup and only move their launch buttons into the Tools group. If merging modals creates unnecessary risk, defer the merge and focus on discoverability and conflict reduction.

### 3. Studio Modal

Keep Studio as the book-level setup cockpit.

Sections:

- Voice.
- Background.
- Overlay basic.
- Overlay advanced as collapsible details, not a competing nested dialog unless needed.
- Music.
- Preview / Save.

Guidelines:

- Keep `voice-form`, `bg-form`, `music-form`, and `overlay-form` IDs stable because existing save logic posts these forms by ID.
- Avoid moving controls in a way that breaks `previewParams()`, `refreshPreview()`, or drag/preview behavior.
- Move page-level CSS rules out of the visual Studio section so CSS ownership is clearer.

### 4. Patches Table

Keep the existing table concept and columns close to the recent video refactor spec:

```text
☑ | Patch | Chapters | Image | Progress | Status | Video | Actions
```

Design intent by column:

- `Patch`: patch name, audio player, compact badges for audio/chunks/custom background.
- `Chapters`: chapter range/count as today.
- `Image`: compact thumbnail and preview/regenerate entry point.
- `Progress`: TTS/chunk progress.
- `Status`: short status text, not long multi-action UI.
- `Video`: video state and primary video actions.
- `Actions`: only the most common actions plus a More button.

Primary row actions should remain immediately visible:

- Run TTS.
- Open chunks.
- Preview/download ready assets where already compact.

Advanced row actions should move behind a More/Settings affordance:

- Upload custom audio.
- Override/remove background.
- Select animation.
- Generate/refresh overlay image.
- Upload external MP4.
- Upload to YouTube.

### 5. Per-Patch Media Settings Modal

Replace inline absolute-positioned upload/background popovers with a small per-patch modal opened from the row’s More button.

Modal contents:

```text
Patch Media Settings
├── Upload custom audio
├── Override background
├── Remove custom background
├── Animation type
├── Generate / refresh overlay image
├── Upload external MP4
└── Upload to YouTube
```

The modal should be populated from the selected row’s existing data attributes and controls where practical.

Benefits:

- Avoid table overflow and z-index conflicts.
- Keep row height stable.
- Reduce accidental clicks.
- Provide one predictable place for per-patch advanced media actions.

### 6. Batch Bottom Bar

Keep the fixed bottom bar, but group actions by purpose.

Target layout:

```text
[3 selected]
Generate: [Run TTS] [Gen Images] [Gen Videos] [Auto YouTube]
Export:   [ZIP] [Drive] [Copy Description]
```

Responsive behavior:

- Wrap groups cleanly on narrow screens.
- Keep selection count visible.
- Use body bottom padding only while the bar is visible.

Behavior guidance:

- The bar appears only when one or more rows are selected.
- Actions labeled selected should operate on selected patches.
- If an all-patches action is needed, it should be a separate explicit control such as `Run all visible` or `Select all filtered`.

### 7. Search, Filter, and Pagination

Enhance the patches toolbar to communicate selection and filtering more clearly.

Recommended controls:

```text
Search patches...
Filter: All / Missing audio / Failed / Has video
[Select visible] [Clear]
```

First implementation pass can keep existing client-side search and pagination, but should make selection state clear:

- Show selected count.
- If selection can span pages, say so: `5 selected across pages`.
- Avoid implying only the current page is affected when selected IDs can include hidden rows.

### 8. Status and Progress

Keep `ACTIVE_TASKS` as the client-side guard that prevents the poller from overwriting local in-flight statuses.

UX guidance:

- Keep status messages short in the row.
- Use a retry button or details affordance for errors instead of long inline errors.
- Prefer separate labels for TTS/video state when possible:
  - `TTS: Running / Ready / Failed`
  - `Video: Rendering / Ready / Failed / Uploaded`

## Non-Goals

Do not include in this redesign pass unless required by implementation safety:

- No database changes.
- No backend route redesign.
- No `patch_builder.html` changes.
- No full extraction of all inline JavaScript into `app/static/book_detail.js`.
- No rewrite of the TTS/chunk/video generation pipelines.
- No server-side pagination migration.

## Implementation Constraints

- Preserve existing form IDs used by JavaScript autosave and Studio save-all behavior.
- Preserve existing endpoint URLs and request payloads.
- Preserve `BOOK_ID`, `ACTIVE_TASKS`, `YOUTUBE_CONFIGURED`, and related runtime constants.
- Preserve current selection, pagination, polling, LightTTS streaming, image generation, video generation, and YouTube upload behavior unless a small wiring adjustment is required after moving controls.
- Match the existing template style and avoid introducing new dependencies.

## Acceptance Criteria

The redesign is successful when:

- The top nav exposes workflow groups instead of a long list of peer feature buttons.
- Settings are discoverable under Tools/Settings rather than competing with primary generation actions.
- The patch table remains readable with many patches and many feature states.
- Advanced per-patch actions no longer rely on absolute-positioned inline popovers in table rows.
- The bottom batch bar clearly separates generation from export actions.
- Batch action labels and behavior are consistent with selected rows.
- Existing features remain available:
  - Studio setup.
  - TTS normalization.
  - LightTTS settings.
  - Video settings.
  - Replace rules.
  - Text Studio.
  - Run selected LightTTS.
  - Generate selected overlay images.
  - Generate selected videos.
  - Download selected ZIP.
  - Export selected to Drive/rclone/API.
  - Copy YouTube description.
  - Per-patch chunks/audio/image/video/YouTube actions.
- Existing async safeguards such as `ACTIVE_TASKS` continue to work.
- No unrelated files or workflows are changed.

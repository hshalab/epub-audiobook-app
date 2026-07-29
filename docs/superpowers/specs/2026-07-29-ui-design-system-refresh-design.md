# UI Design System Refresh

## Goal

Standardize the app UI without adding Bootstrap, shadcn, or a frontend build step. The first visible improvement is the book video configuration modal: split it into tabs and move actions into a modal footer.

## Scope

- Add reusable CSS/JS primitives for modal layout and tabs.
- Apply the new primitives to `app/templates/book_detail.html`, especially `video-config-modal`.
- Reduce obvious inline layout styles where the new primitives replace them directly.
- Keep existing element IDs and form fields so current JavaScript and backend routes continue to work.

## Out Of Scope

- No new UI dependency.
- No frontend toolchain.
- No full visual redesign of every template in one pass.
- No changes to backend video configuration behavior.

## Components

- `ui-modal-header`, `ui-modal-body`, `ui-modal-footer` define a consistent dialog structure.
- `ui-tabs`, `ui-tab-list`, `ui-tab`, `ui-tab-panel` define accessible tab navigation.
- Existing button, form, table, card, and badge classes remain the base design system.

## Video Config Modal

The modal will use three tabs:

- `Nội dung`: voice reference, voice transcript, background music, music volume.
- `Background`: shared media backgrounds, background mode, image duration, intro voice, outro voice.
- `Render`: resolution, codec, audio bitrate, quality, concurrency, FPS, default animation, crossfade, Ken Burns, progress bar.

The footer contains `Lưu cấu hình` with the existing `id="vc-save"` and a `Đóng` button. Keeping `vc-save` preserves the existing save handler.

## JavaScript

Add a small dependency-free tab initializer that:

- Finds containers marked with `data-tabs`.
- Switches panels when a tab button is clicked.
- Maintains `aria-selected`, `tabindex`, and `hidden`.
- Supports keyboard navigation with left/right arrows.

## Testing

- Verify templates parse by running the existing test suite if available.
- If there is no focused template test, run a lightweight syntax/import check available in the project.
- Manually inspect that the video config modal keeps the expected control IDs used by existing JavaScript.

## Risks

- A wide UI refactor can introduce visual regressions across unrelated pages. This pass avoids that by introducing shared primitives and applying them first where needed.
- Native `<dialog>` behavior differs slightly across browsers, so the implementation keeps the existing dialog mechanism and only changes internal layout.

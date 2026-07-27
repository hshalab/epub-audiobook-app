# Patch Media Modal Cleanup

## Goal

Remove the duplicate patch-background control from the per-row media modal and replace the vague `More` label with `Media`.

## UI Changes

- Rename the per-patch `More` button to `Media`.
- Keep manual result-audio upload in the Media modal.
- Keep manual MP4 upload in the Media modal.
- Remove the Media modal's patch-background selector and save button.
- Continue managing patch backgrounds through the existing thumbnail/image modal.

## Behavior

The audio and MP4 upload endpoints, request payloads, status updates, video-cell updates, and modal-close behavior remain unchanged. No backend API or database schema changes are required.

## Cleanup

Remove JavaScript used only by the deleted Media-modal background form. Keep shared patch-background functions used by the thumbnail/image modal.

## Testing

- Verify the rendered row uses the `Media` label and no longer renders the duplicate background controls.
- Verify manual audio upload still posts the selected file and marks the patch ready.
- Verify manual MP4 upload still posts the selected file and makes the video available.

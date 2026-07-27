# Unified Background Media Library

## Goal

Expand the existing `/photos` background library to manage both images and loopable videos, and use it as the source for all book, patch, and shared-video background selectors.

## Compatibility

- Keep URL `/photos` and all existing `/photos/*` routes.
- Keep files in `data/backgrounds`; no file migration or database table is required.
- Existing image uploads and stored absolute paths remain valid.

## Media Support

- Images: `.jpg`, `.jpeg`, `.png`, `.webp`.
- Videos: `.mp4`, `.webm`, `.mov`.
- Upload, serve, rename, and delete operate on both types.
- Preview uses `<img>` for images and muted looping `<video>` for videos.
- File responses use the correct MIME type.

## Reference Updates

Rename and delete update all persisted background references:

- `book.background_image_path`.
- `patch.image_path`.
- `automation_config.video.backgrounds` arrays on books.

Rename replaces the old absolute path with the new path. Delete removes the path from arrays and clears scalar references.

## Selection UI

- Book and patch selectors continue using the existing shared background listing.
- Shared video configuration replaces the free-text background textarea with a checkbox list.
- Each checkbox shows image/video preview, filename, and type.
- Sequential playback follows the library filename order.

## Testing

- Upload accepts images/videos and ignores unsupported files.
- Preview serves correct MIME types.
- Book Detail renders selectable media checkboxes and submits selected paths.
- Rename updates files and all persisted references.
- Delete removes files and all persisted references.

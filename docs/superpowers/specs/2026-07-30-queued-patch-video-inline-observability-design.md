# Queued Patch Video With Inline Observability

## Goal

Move patch video rendering out of the request lifecycle and into the unified job queue. The book detail page must show each patch video job's status, phase, progress, errors, and log without navigating to `/queue`.

## Scope

- `POST /books/{book_id}/patches/{patch_id}/generate-video` validates input and enqueues a `patch_video` job instead of invoking FFmpeg.
- Per-row and selected-patch video actions use the same queue path.
- Existing generated-video preview, download, deletion, and YouTube behavior remains available.
- The existing `/queue` page and job APIs remain authoritative and compatible.

## Queue Contract

The enqueue endpoint returns HTTP 202 JSON for AJAX requests:

```json
{"status":"queued","job_id":123,"deduplicated":false}
```

The live-job dedupe key is `patch_video:patch={patch_id}`. If a pending or running job already has that key, the endpoint returns its ID with `deduplicated: true`. Terminal jobs do not block a new render.

The payload contains `patch_id`, `upload_youtube`, and `privacy`. `book_id` is stored in the queue row for filtering and page restoration. The endpoint validates patch ownership, ready audio, book existence, and available background before enqueueing so obvious user errors are returned immediately.

## Render Snapshot And Handler

The `patch_video` handler becomes the single implementation of manual patch rendering. It must not require a pre-existing publish pipeline row. At job execution it loads the patch and book, resolves the current shared video configuration, background/overlay, music, intro/outro, output path, and render settings using the same behavior as the former synchronous route.

The handler renders to an atomic temporary path, validates the result, registers the patch video, and then optionally seeds/runs the YouTube publish stage. An existing valid MP4 remains untouched until the new render validates.

Publish-recovery jobs that already contain a pipeline snapshot remain supported by the same handler. Pipeline fields are updated only when a pipeline row exists or publishing is requested; manual rendering alone does not require creating YouTube pipeline state.

## Progress And Logs

The handler reports these phases through `JobContext`:

1. `preparing`
2. `overlay`
3. `encoding`
4. `validating`
5. `registering`
6. `publishing` when requested
7. `done`

Progress is phase-based because current FFmpeg helpers do not expose reliable frame percentages for every render mode. Video generation callbacks write FFmpeg start/failure details and heartbeats where supported. Unhandled exceptions are captured by the queue runner in the per-job log and `error_message`.

## Inline UI

Each `.patch-video-cell` stores its active job ID. After enqueueing, the cell shows:

- Human-readable status and phase.
- A progress element when totals are available.
- A `Log` toggle whose `<pre>` is updated from job events.
- Error text and a `Thử lại` button for terminal failures.

The browser opens `/queue/jobs/{job_id}/stream` with `EventSource`. Progress events update the row. Log events append escaped text. On stream failure, polling `/queue/jobs/{job_id}` continues until a terminal state. Terminal success marks the cell as having video and restores preview/download/action controls.

On page load, the frontend requests `/queue/jobs?type=patch_video&book_id={book_id}`. It maps the newest job for each payload `patch_id`, attaching only pending, running, cancelling, or the newest failed job. This restores observability after reload.

Batch generation enqueues all selected audio-ready patches with bounded request concurrency. It reports how many were newly queued, deduplicated, or rejected; it does not wait for rendering to finish.

## Retry And Cancellation

Automatic queue retries use the existing `max_attempts` and backoff behavior. The inline retry button calls `/queue/jobs/{job_id}/retry` for a terminal failed job and reconnects monitoring. Cancellation remains available on `/queue`; inline cancellation is out of scope.

## Error Handling

- Validation failures remain HTTP 4xx and are shown as toasts.
- Queue insertion failures return HTTP 500 with a logged traceback.
- Job errors remain associated with the row through job ID, including FFmpeg output in the per-job log.
- SSE disconnection does not change job state and falls back to polling.
- Missing or deleted source media at execution time fails the job with an actionable message.

## Testing

- Route tests prove enqueueing returns 202, does not render synchronously, and deduplicates live jobs.
- Handler tests cover manual rendering without `patch_pipeline`, shared configuration, successful registration, progress, optional YouTube continuation, and actionable failures.
- Queue API tests cover filtering and payload exposure needed for restoration.
- Template/JavaScript tests assert queued UI controls, stream/poll restoration, retry, and batch enqueue behavior.
- Existing patch video, integrity, publishing, and queue tests must remain green.

## Non-Goals

- Parsing FFmpeg frame output into exact percentage completion.
- Adding inline cancellation.
- Changing queue persistence schema.
- Reworking unrelated publish-pipeline UI.

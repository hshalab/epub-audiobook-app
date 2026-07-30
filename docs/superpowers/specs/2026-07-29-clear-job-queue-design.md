# Clear Job Queue Design

## Goal

Allow an operator to clear queued and historical jobs from the queue page without interrupting work that is currently running or stopping.

## Behavior

- Add a **Xóa hàng đợi** button to `/queue`.
- Require browser confirmation before clearing.
- Delete jobs whose status is `pending`, `done`, `failed`, or `cancelled`.
- Preserve jobs whose status is `running` or `cancelling`.
- Do not modify patches, book jobs, books, generated files, or job log files.
- Refresh the displayed job list after a successful request.
- Return JSON containing the number of deleted jobs.

## Implementation

Add `store.clear_inactive(conn)` in `app/jobqueue/store.py`. It will issue one `DELETE` statement that preserves `running` and `cancelling` rows, commit the transaction, and return the affected row count.

Add `POST /queue/clear` in `app/routes/queue.py`. The route will call the store function under `locked_conn` and return `{"cleared": count}`. It will not call queue cancellation APIs because active jobs are explicitly preserved.

Add the button and request handling to `app/templates/queue.html`, following the page's existing plain JavaScript approach. On success, the page refreshes the table. On an HTTP error, it shows an alert and leaves the current table intact.

## Safety

The database operation excludes `running` and `cancelling` at execution time, so a job claimed before the delete statement runs is preserved. The endpoint does not reset source records or trigger backfill; clearing is an administrative deletion, not a pipeline reset.

## Testing

Extend queue route tests to create jobs in each relevant status, call `POST /queue/clear`, and verify:

- The response reports the correct deletion count.
- Inactive rows are deleted.
- `running` and `cancelling` rows remain.
- The queue page exposes the clear control.

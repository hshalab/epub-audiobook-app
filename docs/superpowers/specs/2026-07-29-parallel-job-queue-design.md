# Thiết kế: Queue job song song có giới hạn theo loại

Ngày: 2026-07-29

## Mục tiêu

Gom mọi tác vụ chạy nền của ứng dụng vào **một** queue duy nhất, chạy song song với số
worker cấu hình được (mặc định 10), và ghi lại tiến độ lẫn lỗi của từng job để tra cứu
được sau khi nó đã chạy xong.

Bốn loại tác vụ: sinh audio VoxCPM (`voxcpm_tts`), sinh audio LightTTS (`light_tts`),
render video (`video`), upload YouTube (`youtube_upload`).

## Hiện trạng

| Thành phần | Cách chạy |
|---|---|
| `PatchWorker` (`app/worker.py`) | một loop async; patch TTS tuần tự; `book_job` video spawn `asyncio.create_task` **không giới hạn** |
| `UploadWorker` (`app/upload_worker.py`) | loop riêng, upload tuần tự, `sleep(2)` giữa các lần |
| LightTTS (`app/routes/text_studio.py:394`) | **không có queue** — synthesize ngay trong HTTP request qua SSE; đóng tab là job chết |
| Log | một file `app.log` phẳng, format `event=k=v`, xem qua `/logs` (tail thô) |
| DB | một `sqlite3.Connection` dùng chung + một `threading.Lock` toàn cục; WAL bật, `busy_timeout=15000` |

Ba vấn đề cần giải quyết: không có trần song song cho video; LightTTS không sống sót
qua việc đóng tab; không tra được lịch sử tiến độ/lỗi của một job cụ thể.

## Quyết định thiết kế

1. **Giới hạn song song riêng theo từng loại task**, không dùng một pool chung 10.
   Bốn loại có đặc tính tài nguyên khác hẳn nhau: VoxCPM bám GPU, video ngốn CPU qua
   ffmpeg, upload YouTube bị chặn bởi quota API, LightTTS thuần network.
2. **LightTTS vào queue**, endpoint SSE hiện tại đổi vai thành cửa sổ xem tiến độ.
3. **Bảng `job` là lớp điều phối**, các bảng nghiệp vụ (`patch`, `book_job`,
   `youtube_uploads`) giữ nguyên. `PatchWorker`/`UploadWorker` bị thay bằng handler.
4. **Tiến độ ghi vào DB (có throttle), log chi tiết ghi ra file riêng mỗi job.**

## 1. Mô hình dữ liệu

```sql
CREATE TABLE IF NOT EXISTS job (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type         TEXT NOT NULL,        -- voxcpm_tts | video | youtube_upload | light_tts
    status           TEXT NOT NULL DEFAULT 'pending',
                                           -- pending | running | done | failed | cancelling | cancelled
    priority         INTEGER NOT NULL DEFAULT 100,   -- số nhỏ = ưu tiên cao
    book_id          INTEGER,              -- nullable; để lọc theo sách trên UI
    payload_json     TEXT NOT NULL DEFAULT '{}',
    dedupe_key       TEXT,                 -- vd 'video:book_job=12'

    phase            TEXT,                 -- 'synthesizing' | 'encoding' | 'uploading' | ...
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,

    result_json      TEXT,
    error_message    TEXT,

    attempt_count    INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    next_retry_at    TEXT,

    worker_id        TEXT,                 -- 'video#1'
    heartbeat_at     TEXT,

    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_claim   ON job(status, job_type, priority, id);
CREATE INDEX IF NOT EXISTS idx_job_book    ON job(book_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_dedupe ON job(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending','running');
```

`dedupe_key` là partial unique index: một job cùng khóa chỉ tồn tại một bản ở trạng thái
chưa kết thúc. Đây là thứ chặn double-click và làm cho backfill lúc khởi động chạy được
nhiều lần mà không sinh trùng.

### Claim nguyên tử

```sql
UPDATE job
   SET status='running', worker_id=?, started_at=?, heartbeat_at=?,
       attempt_count=attempt_count+1, updated_at=?
 WHERE id = (SELECT id FROM job
              WHERE status='pending' AND job_type=?
                AND (next_retry_at IS NULL OR next_retry_at <= ?)
              ORDER BY priority, id
              LIMIT 1)
RETURNING *;
```

Một câu lệnh, không cần lock ở tầng Python. `UPDATE ... RETURNING` cần sqlite ≥ 3.35;
môi trường hiện tại là 3.45.1 và repo đã dùng cú pháp này ở
`repository.requeue_stuck_processing_returning`.

## 2. Pool worker

```
JobQueue
├─ registry: job_type → HandlerSpec(fn, concurrency, max_attempts)
├─ dispatcher loop cho mỗi job_type:
│     chờ slot rảnh → claim → spawn task → trả slot khi xong
└─ reaper loop: job 'running' có heartbeat quá hạn → trả về 'pending'
```

Cấu hình:

```
QUEUE_CONCURRENCY="voxcpm_tts=1,video=2,youtube_upload=1"
QUEUE_DEFAULT_CONCURRENCY=10
QUEUE_LOG_RETENTION_DAYS=7
QUEUE_REAP_AFTER_SECONDS=120
```

Dispatcher dùng lại `worker_poll_interval` (2.0s) đang có, không thêm setting mới —
`/health` tính ngưỡng heartbeat bằng `3 × settings.worker_poll_interval`, hai chỗ phải
là cùng một con số.

Mọi loại không khai báo trong `QUEUE_CONCURRENCY` nhận `QUEUE_DEFAULT_CONCURRENCY` = 10,
nên `light_tts` chạy 10 worker. Ba loại còn lại ghi đè vì có ràng buộc tài nguyên cứng:

- `voxcpm_tts=1` — VoxCPM chiếm VRAM; hai instance song song gây OOM hoặc chậm hơn tuần tự.
- `video=2` — mỗi job là một tiến trình ffmpeg; 10 tiến trình cùng lúc làm thrash CPU.
- `youtube_upload=1` — quota YouTube Data API mặc định 10.000 đơn vị/ngày, mỗi
  `videos.insert` tốn 1.600, tức khoảng 6 video/ngày. Song song không tăng thông lượng
  mà chỉ làm tăng nguy cơ 429.

### Hai ràng buộc kỹ thuật bắt buộc

**a. Worker dùng connection riêng, không đi qua `db_lock` chung.** Hiện
`app.state.conn` + một `threading.Lock` toàn cục phục vụ cả routes lẫn worker; nếu 10
worker chen qua khóa đó thì song song bằng không. WAL đã bật và `busy_timeout=15000` đã
set, nên mỗi job đang chạy tự mở connection riêng (`db.connect(settings.db_path)`) và
đóng khi xong. Routes HTTP **không đổi**, vẫn dùng `deps.locked_conn`.

Đánh đổi: hai connection cùng ghi có thể gặp `SQLITE_BUSY`. WAL cho phép nhiều reader
song song với một writer, và `busy_timeout` tự retry 15 giây — đủ xa so với thời lượng
mọi transaction của queue (đều là UPDATE một dòng).

**b. Executor riêng, kích thước bằng tổng capacity.** Mọi handler đều blocking: ffmpeg
qua subprocess, `googleapiclient` đồng bộ, VoxCPM đồng bộ, và `edge_tts` bị gọi qua
`asyncio.run()` bên trong hàm sync (`light_tts._edge_tts_synthesize`). Tất cả chạy qua
`loop.run_in_executor` với `ThreadPoolExecutor(max_workers=sum(concurrency)+4)` **của
riêng queue**. Nếu dùng executor mặc định của asyncio (`min(32, cpu+4)`, dùng chung với
mọi `asyncio.to_thread` khác trong app) thì job sẽ xếp hàng vô hình bên trong executor
trong khi `/queue` vẫn báo `running`.

## 3. Handler

```python
def handle(ctx: JobContext) -> dict: ...   # sync; giá trị trả về → result_json
```

`JobContext` cấp đúng năm thứ:

| API | Ý nghĩa |
|---|---|
| `ctx.job` | dòng job đã claim, `payload` đã parse thành dict |
| `ctx.conn` | connection sqlite riêng của job này |
| `ctx.progress(current, total=None, phase=None)` | cập nhật tiến độ, có throttle |
| `ctx.log(msg, level=INFO)` / `ctx.emit(dict)` | ghi log dòng chữ / event có cấu trúc |
| `ctx.should_cancel()` | cờ hủy; handler tự kiểm ở ranh giới an toàn |
| `ctx.heartbeat()` | báo còn sống mà không đổi tiến độ; xem mục 4 |

Lỗi:

- `raise JobFatalError(msg)` → `failed` ngay, không retry. Dùng cho: quota YouTube hết,
  file nguồn không tồn tại, payload sai, patch không có text đọc được.
- `Exception` khác → retry nếu `attempt_count < max_attempts`, với
  `next_retry_at = now + min(30 * 2**attempt, 600)` giây. Hết lượt → `failed`.

Bốn handler, thân hàm bê nguyên từ code đang chạy:

| Handler | Payload | Nguồn code | Song song | Tiến độ | Hủy được? |
|---|---|---|---|---|---|
| `voxcpm_tts` | `{patch_id}` | `PatchWorker._synthesize` + `mark_patch_done` + `on_patch_audio_ready` + `_maybe_finalize_book` | 1 | chunk `i/n` | có (ranh giới chunk) |
| `light_tts` | `{patch_id, backend, voice, max_chars, with_effects}` | thân `preview_stream._generate()`, bỏ `yield _sse` | 10 | chunk `i/n` | có (ranh giới chunk) |
| `video` | `{book_job_id}` | `PatchWorker._run_video_job` | 2 | callback `on_progress` có sẵn trong `video_gen.generate_full_video` | có (kill ffmpeg) |
| `youtube_upload` | `{upload_id}` | `UploadWorker._process_upload` | 1 | byte đã đẩy / tổng | **không** — chạy hết rồi mới dừng |

Chuỗi tự động giữ nguyên hành vi, chỉ đổi cách nối — handler enqueue job kế tiếp thay vì
gọi inline:

```
voxcpm_tts xong → mọi patch của sách done? → merge final.wav → enqueue video
video xong      → settings.youtube_auto_upload và youtube.is_configured()? → enqueue youtube_upload
```

Việc `youtube.enqueue_upload` ghi vào bảng `youtube_uploads` vẫn giữ; job
`youtube_upload` chỉ trỏ tới dòng đó qua `upload_id`.

## 4. Tiến độ và log

### Throttle

`ctx.progress()` cập nhật biến trong bộ nhớ mỗi lần gọi, nhưng chỉ ghi DB khi một trong
ba điều kiện đúng:

1. đã quá 1.0 giây kể từ lần ghi trước, **hoặc**
2. `phase` thay đổi, **hoặc**
3. job chuyển sang trạng thái kết thúc.

Lần ghi đó cập nhật luôn `heartbeat_at`, nên không cần loop heartbeat riêng: job nào còn
báo tiến độ là job đó còn sống. Handler không gọi `progress()` trong thời gian dài (upload
một file lớn) phải tự gọi `ctx.heartbeat()` — hoặc gọi `progress()` với cùng giá trị.

### File log theo job

`data/logs/jobs/{job_id}.log`, mở lười ở dòng đầu tiên:

```
2026-07-29T10:22:31Z [INFO ] phase=encoding | ffmpeg pass 1/2, 34%
2026-07-29T10:22:33Z [ERROR] phase=encoding | ffmpeg exit 1: No such filter 'zoompan'
@@EVENT {"type":"chunk","index":7,"total":42,"url":"/books/3/patches/91/chunk-audio/7"}
```

- Dòng `WARNING`/`ERROR` được nhân bản sang logger chính (`event=job.log job_id=… job_type=…`)
  nên `app.log` vẫn là cái nhìn toàn cục. Dòng `INFO`/`DEBUG` chỉ nằm ở file riêng.
- Dòng bắt đầu/kết thúc job luôn ghi cả hai nơi.
- Dòng `@@EVENT` là event có cấu trúc do `ctx.emit()` ghi ra.
- Dọn rác lúc khởi động: xóa file log của job đã kết thúc quá `QUEUE_LOG_RETENTION_DAYS`
  (mặc định 7 ngày).

### Cách LightTTS giữ nguyên giao diện

Endpoint `GET /books/{book_id}/text-studio/patches/{patch_id}/preview-stream` không tự
synthesize nữa. Nó:

1. tìm job `light_tts` còn sống với `dedupe_key='light_tts:patch={patch_id}'`, không có
   thì enqueue một job mới;
2. tail file log của job đó, forward mọi dòng `@@EVENT` sang browser dưới dạng SSE;
3. đóng stream khi job đạt trạng thái kết thúc.

Handler `light_tts` `emit()` đúng những message mà frontend đang chờ — `chunk`,
`chunk_error`, `done`, `error` — với đúng các khóa như hiện tại. **Frontend không phải
sửa dòng nào.** Đổi lại: job sống tiếp khi đóng tab, và mở lại trang là attach lại được
vào job đang chạy.

## 5. API và UI

```
GET  /queue                      trang HTML: bảng job, lọc theo type/status/book_id,
                                 thanh tiến độ, nút retry/cancel, tự refresh 2s
GET  /queue/jobs?type=&status=&book_id=&limit=      JSON danh sách
GET  /queue/jobs/{id}                                JSON chi tiết
GET  /queue/jobs/{id}/log?tail=500                   text/plain
GET  /queue/jobs/{id}/stream                         SSE: @@EVENT + tiến độ
POST /queue/jobs/{id}/cancel
POST /queue/jobs/{id}/retry                          reset về pending, attempt_count=0
```

`/health` thêm mảng `pools`, mỗi phần tử `{job_type, running, capacity, pending}`. Các
khóa cũ (`worker_state`, `current_patch_id`, `current_chunk_index`, `current_chunk_count`,
`queue_depth`, `last_heartbeat_at`) **giữ nguyên** để không vỡ UI và test hiện có.

`/queue/stats` giữ nguyên shape cũ, thêm khóa `jobs`.

Các nút hiện có (`/queue/pause`, `/queue/resume`, `/queue/requeue-stuck`,
`/books/{id}/patches/retry-failed`, `/books/{id}/video/regenerate`) giữ nguyên đường dẫn
và hành vi; phần thân chuyển sang thao tác trên bảng `job`. Cờ `app_state['queue.paused']`
vẫn là công tắc dừng claim của **mọi** dispatcher.

### Hủy job

Hợp tác, không cưỡng bức:

- `pending` → `cancelled` ngay.
- `running` → đặt `cancelling`; handler kiểm `should_cancel()` ở ranh giới chunk; handler
  `video` kill tiến trình ffmpeg.
- `youtube_upload` không hỗ trợ hủy — chạy hết rồi mới dừng. UI hiển thị nút hủy ở trạng
  thái disabled cho loại này thay vì giả vờ hủy được.

## 6. Migration

Không phá gì. Ba bảng nghiệp vụ giữ nguyên; chỉ các vòng lặp bị gỡ:

| Xóa | Giữ, chuyển thành handler |
|---|---|
| `PatchWorker.run_forever`, `_spawn_book_job`, `_run_book_job_wrapper`, `_should_exit` | `_synthesize`, `_run_video_job`, `_merge_final_audio`, `_maybe_finalize_book` |
| `UploadWorker._run_loop`, `start`, `stop` | `_process_upload`, `_execution_connection` |
| phần synthesize trong `preview_stream._generate()` | logic chunk-reuse + `.light_tts_meta` + `_finish_patch_audio` |

`app.state.worker` vẫn tồn tại, trỏ vào `JobQueue`, và expose các thuộc tính cũ
(`current_patch_id`, `state`, `last_heartbeat_at`, `current_chunk_index`,
`current_chunk_count`) tính từ job `voxcpm_tts` đang chạy.

### Backfill lúc khởi động

Idempotent, chạy mọi lần boot, sau `requeue_stuck_*`:

| Điều kiện | Job được tạo |
|---|---|
| `patch.status='pending'` | `voxcpm_tts`, dedupe `voxcpm_tts:patch={id}` |
| `book_job.status='pending'` | `video`, dedupe `video:book_job={id}` |
| `youtube_uploads.status='pending'` | `youtube_upload`, dedupe `youtube_upload:upload={id}` |

Unique index trên `dedupe_key` làm cho việc chạy lại vô hại. Đây chính là cách công việc
đang tồn đọng chuyển sang hệ mới ở lần chạy đầu — không cần script migrate riêng.

### Shutdown và crash

Shutdown: dispatcher ngừng claim, chờ job đang chạy tối đa
`worker_shutdown_timeout_seconds` (300s, setting đã có).

Job còn `running` khi hết thời gian chờ, hoặc khi tiến trình bị kill: để nguyên trạng
thái `running` với `heartbeat_at` cũ. Reaper ở lần boot sau (và định kỳ trong lúc chạy)
trả nó về `pending` khi heartbeat quá `QUEUE_REAP_AFTER_SECONDS`, giữ nguyên
`patch.next_chunk_index` và các file chunk trên đĩa — resume đúng như cơ chế hiện tại.

`requeue_stuck_processing_returning` và `requeue_stuck_book_jobs` trong `lifespan` vẫn
giữ vì chúng sửa bảng nghiệp vụ; chúng chạy trước backfill.

## 7. Cấu trúc file

```
app/jobqueue/__init__.py
           /models.py            Job, JobStatus, JobFatalError, HandlerSpec
           /store.py             SQL thuần: enqueue/claim/heartbeat/finish/fail/reap/list
           /context.py           JobContext — progress throttle, should_cancel
           /joblog.py            logger theo job, @@EVENT, retention
           /runner.py            JobQueue — registry, dispatcher/loại, executor, shutdown
           /handlers/voxcpm_tts.py
           /handlers/video.py
           /handlers/youtube_upload.py
           /handlers/light_tts.py
app/routes/queue.py              mở rộng
app/templates/queue.html         mới
```

Tên `jobqueue` chứ không phải `queue` để khỏi che module chuẩn của Python. Không thêm gì
vào `repository.py` — file đó đã 72KB.

## 8. Test

| Nhóm | Kiểm |
|---|---|
| `store` | 20 thread claim đồng thời → không job nào bị claim hai lần; `dedupe_key` chặn enqueue trùng; backoff đúng công thức; reaper bắt đúng job quá hạn heartbeat |
| `runner` | handler giả đếm số job đồng thời → không bao giờ vượt cap của loại đó; pause dừng claim; shutdown drain hết job đang chạy |
| `context` | gọi `progress()` 100 lần trong 1 giây → DB chỉ bị ghi 1–2 lần; đổi `phase` và lúc kết thúc thì luôn flush |
| `joblog` | `emit()` → tail ra đúng JSON; dòng ERROR có mặt trong app.log; retention xóa đúng file |
| handlers | mỗi handler với fake engine/ffmpeg/YouTube, theo pattern test đang có |
| integration | enqueue 30 job `light_tts` → đồng thời ≤ 10; trộn cả bốn loại → mỗi loại đúng cap |
| regression | `preview-stream` vẫn trả đúng chuỗi SSE cũ (`chunk` / `chunk_error` / `done`) |
| regression | `/health` giữ nguyên mọi khóa cũ |

Chạy bằng `pytest tests/` — `pytest` trần sẽ lội vào `build/` và `.venv/` rồi chết trước
khi chạy được test nào.

## Ngoài phạm vi

- Không migrate `patch.status` / `book_job` / `youtube_uploads` vào bảng `job`. Chúng vẫn
  là nguồn sự thật cho dữ liệu nghiệp vụ.
- Không có queue phân tán / đa tiến trình. Queue sống trong tiến trình FastAPI, giống hiện tại.
- Không có lịch chạy theo giờ, không có job phụ thuộc job (DAG). Chuỗi tự động vẫn là
  handler tự enqueue bước kế tiếp.

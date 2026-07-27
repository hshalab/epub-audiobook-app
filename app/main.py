from __future__ import annotations

import asyncio
import logging
import logging.handlers
import threading
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, repository
from app.config import settings
from app.routes import books, database_io, downloads, drive, effects, logs, music, patches, photos, queue, text_studio, video, video_api, voices, youtube
from app.tts_engine import VoxCPMEngine
from app.worker import PatchWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            settings.log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logging.getLogger("watchfiles").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    Path(settings.data_root, "preview_tmp").mkdir(parents=True, exist_ok=True)
    requeued_patches = repository.requeue_stuck_processing_returning(conn)
    if requeued_patches:
        logging.info(
            "requeued %s patch(es) left 'processing' from a previous crashed run; "
            "next_chunk_index preserved for chunk-level resume",
            len(requeued_patches),
        )
        for r in requeued_patches:
            if r["next_chunk_index"] > 0 and r["chunk_count"] > 0:
                logging.info(
                    "  resume patch_id=%s book_id=%s chunk %s/%s",
                    r["patch_id"], r["book_id"],
                    r["next_chunk_index"], r["chunk_count"],
                )
    requeued_bj = repository.requeue_stuck_book_jobs(conn)
    if requeued_bj:
        logging.info("requeued %s book_job(s) left 'processing' from a previous crashed run", requeued_bj)

    if settings.reset_all_jobs_on_startup:
        summary = repository.reset_all_jobs(conn)
        logging.info(
            "event=reset_all_jobs_on_startup patches_reset=%s book_jobs_reset=%s "
            "books_reset=%s files_deleted=%s",
            summary["patches_reset"],
            summary["book_jobs_reset"],
            summary["books_reset"],
            summary["files_deleted"],
        )
    else:
        backfilled = repository.backfill_video_book_jobs(conn)
        if backfilled:
            logging.info(
                "event=backfill.video_jobs_inserted count=%s",
                backfilled,
            )

    db_lock = threading.Lock()
    app.state.conn = conn
    app.state.db_lock = db_lock
    worker_task: asyncio.Task | None = None
    if settings.enable_worker:
        engine = VoxCPMEngine()
        worker = PatchWorker(
            conn,
            engine,
            settings.data_root,
            settings.worker_poll_interval,
            db_lock,
            shutdown_timeout=settings.worker_shutdown_timeout_seconds,
        )
        app.state.worker = worker
        worker_task = asyncio.create_task(worker.run_forever())
        logging.info(
            "worker started (poll_interval=%s s, shutdown_timeout=%s s)",
            settings.worker_poll_interval,
            settings.worker_shutdown_timeout_seconds,
        )
    else:
        app.state.worker = None

    try:
        yield
    finally:
        if worker_task is not None:
            worker.stop()
            try:
                await asyncio.wait_for(worker_task, timeout=settings.worker_shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                logger = logging.getLogger(__name__)
                logger.warning(
                    "worker did not stop within %s s; cancelling",
                    settings.worker_shutdown_timeout_seconds,
                )
                worker.log_shutdown_timeout()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        conn.close()


app = FastAPI(title="EPUB Audiobook App", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(request, "404.html", {"request": request}, status_code=404)
app.include_router(books.router)
app.include_router(patches.router)
app.include_router(downloads.router)
app.include_router(queue.router)
app.include_router(logs.router)
app.include_router(video.router)
app.include_router(video_api.router)
app.include_router(music.router)
app.include_router(photos.router)
app.include_router(voices.router)
app.include_router(youtube.router)
app.include_router(text_studio.router)
app.include_router(drive.router)
app.include_router(database_io.router)
app.include_router(effects.router)


@app.get("/")
def root():
    return RedirectResponse(url="/books")

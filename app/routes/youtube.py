"""YouTube OAuth and upload routes."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import youtube
from app.config import settings
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _enqueue(request: Request, video_path: str, title: str, description: str, tags: str, privacy_status: str) -> dict:
    """Queue a video for the upload worker and return immediately.

    The upload itself must not run here: these handlers hold the shared db_lock via
    locked_conn, so a multi-minute network upload inside one would block every other
    request (including the progress poll this feature depends on).
    """
    from app.upload_worker import upload_worker

    if upload_worker is None or not upload_worker.get_status().get("running"):
        raise HTTPException(status_code=503, detail="Upload worker is unavailable")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    with locked_conn(request) as conn:
        if not youtube.get_creds_from_db(conn):
            raise HTTPException(status_code=400, detail="YouTube not connected")
        upload_id = youtube.enqueue_upload(
            conn,
            video_path=video_path,
            title=title,
            description=description,
            tags=tag_list,
            privacy_status=privacy_status,
        )
    return {"upload_id": upload_id, "status": "pending"}


@router.get("/youtube", response_class=HTMLResponse)
def youtube_page(request: Request):
    with locked_conn(request) as conn:
        creds = youtube.get_creds_from_db(conn)
        connected = creds is not None and bool(creds.get("channel_name"))
        uploads = youtube.list_uploads(conn, limit=30)
    return templates.TemplateResponse(request, "youtube.html", {
        "request": request,
        "connected": connected,
        "channel_name": creds.get("channel_name") if creds else None,
        "uploads": uploads,
        "configured": youtube.is_configured(),
        "auto_upload": settings.youtube_auto_upload,
    })


@router.get("/youtube/connect")
def youtube_connect(request: Request):
    if not youtube.is_configured():
        raise HTTPException(status_code=400, detail="YouTube not configured. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET.")
    redirect_uri = str(request.base_url) + "youtube/callback"
    url = youtube.get_authorization_url(redirect_uri)
    return RedirectResponse(url=url)


@router.get("/youtube/callback")
def youtube_callback(request: Request, code: str = "", error: str = ""):
    if error:
        return RedirectResponse(url=f"/youtube?error={error}")
    if not code:
        return RedirectResponse(url="/youtube?error=no_code")

    redirect_uri = str(request.base_url) + "youtube/callback"
    try:
        result = youtube.exchange_code(code, redirect_uri)
    except Exception as exc:
        logger.exception("YouTube OAuth callback failed")
        return RedirectResponse(url=f"/youtube?error={str(exc)}")

    try:
        with locked_conn(request) as conn:
            youtube.save_credentials(
                conn,
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                token_expiry=result["token_expiry"],
                channel_id=result["channel_id"],
                channel_name=result["channel_name"],
            )
    except Exception as exc:
        logger.exception("Failed to save YouTube credentials")
        return RedirectResponse(url=f"/youtube?error={str(exc)}")
    return RedirectResponse(url="/youtube?connected=1")


@router.post("/youtube/disconnect")
def youtube_disconnect(request: Request):
    with locked_conn(request) as conn:
        youtube.delete_credentials(conn)
    return JSONResponse({"status": "disconnected"})


@router.post("/youtube/upload")
async def youtube_upload_manual(
    request: Request,
    video_path: str = Form(...),
    title: str = Form(...),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    privacy_status: str = Form(default="private"),
):
    if not youtube.is_configured():
        raise HTTPException(status_code=400, detail="YouTube not configured")

    return JSONResponse(_enqueue(request, video_path, title, description, tags, privacy_status))


@router.post("/youtube/upload-file")
async def youtube_upload_file(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(default=""),
    tags: str = Form(default=""),
    privacy_status: str = Form(default="private"),
):
    """Upload a video file directly (for standalone videos not yet on disk)."""
    if not youtube.is_configured():
        raise HTTPException(status_code=400, detail="YouTube not configured")

    # Save to tmp
    from app.routes.video import _TMP_DIR
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    import uuid
    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    tmp_path = _TMP_DIR / f"yt_upload_{uuid.uuid4().hex[:8]}{ext}"

    def _save():
        import shutil
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out)

    # Off the event loop: a large file otherwise stalls every concurrent request.
    await asyncio.to_thread(_save)

    return JSONResponse(_enqueue(request, str(tmp_path), title, description, tags, privacy_status))


@router.get("/youtube/uploads")
def youtube_uploads_list(request: Request):
    with locked_conn(request) as conn:
        uploads = youtube.list_uploads(conn)
    return JSONResponse({"uploads": uploads})


@router.get("/youtube/kaggle-credentials")
def youtube_kaggle_credentials(request: Request):
    """Credentials JSON for use as YOUTUBE_CREDS Kaggle/Colab Secret so the
    batch notebook can upload rendered MP4s directly to YouTube."""
    if not youtube.is_configured():
        raise HTTPException(status_code=400, detail="YouTube chưa được cấu hình")
    with locked_conn(request) as conn:
        creds = youtube.get_creds_from_db(conn)
    if creds is None or not creds.get("refresh_token"):
        raise HTTPException(status_code=400, detail="YouTube chưa được kết nối. Kết nối trước tại /youtube.")
    return JSONResponse({
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "refresh_token": creds["refresh_token"],
    })


@router.delete("/youtube/uploads/{upload_id}")
def youtube_delete_upload(request: Request, upload_id: int):
    """Delete a single upload history record."""
    with locked_conn(request) as conn:
        if not youtube.delete_upload(conn, upload_id):
            raise HTTPException(status_code=404, detail="Upload not found")
    return JSONResponse({"deleted": 1})


@router.post("/youtube/uploads/bulk-delete")
def youtube_bulk_delete_uploads(request: Request, ids: list[int]):
    """Delete multiple upload history records."""
    with locked_conn(request) as conn:
        deleted = youtube.delete_uploads(conn, ids)
    return JSONResponse({"deleted": deleted})


@router.post("/youtube/uploads/bulk-retry")
def youtube_bulk_retry_uploads(request: Request, ids: list[int]):
    """Reset failed uploads to pending status for retry."""
    with locked_conn(request) as conn:
        retried = youtube.reset_upload_status(conn, ids)
    return JSONResponse({"retried": retried})

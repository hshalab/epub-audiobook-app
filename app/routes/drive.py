"""Google Drive OAuth routes (Colab/Kaggle export round trip settings page)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import google_drive, repository
from app.config import settings
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/drive", response_class=HTMLResponse)
def drive_page(request: Request):
    with locked_conn(request) as conn:
        creds = google_drive.get_creds_from_db(conn)
        connected = creds is not None
        exports = repository.list_all_patch_exports(conn, limit=30)
    return templates.TemplateResponse(request, "drive.html", {
        "request": request,
        "connected": connected,
        "account_email": creds.get("account_email") if creds else None,
        "exports": exports,
        "configured": google_drive.is_configured(),
    })


@router.get("/drive/connect")
def drive_connect(request: Request):
    if not google_drive.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Google Drive not configured. Set GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET.",
        )
    redirect_uri = str(request.base_url) + "drive/callback"
    try:
        url = google_drive.get_authorization_url(redirect_uri)
    except Exception as exc:
        logger.exception("drive_connect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return RedirectResponse(url=url)


@router.get("/drive/callback")
def drive_callback(request: Request, code: str = "", error: str = ""):
    if error:
        return RedirectResponse(url=f"/drive?error={error}")
    if not code:
        return RedirectResponse(url="/drive?error=no_code")

    redirect_uri = str(request.base_url) + "drive/callback"
    try:
        result = google_drive.exchange_code(code, redirect_uri)
    except Exception as exc:
        logger.exception("Google Drive OAuth callback failed")
        return RedirectResponse(url=f"/drive?error={str(exc)}")

    try:
        with locked_conn(request) as conn:
            google_drive.save_credentials(
                conn,
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                token_expiry=result["token_expiry"],
                account_email=result["account_email"],
            )
    except Exception as exc:
        logger.exception("Failed to save Google Drive credentials")
        return RedirectResponse(url=f"/drive?error={str(exc)}")
    return RedirectResponse(url="/drive?connected=1")


@router.get("/drive/kaggle-credentials")
def drive_kaggle_credentials(request: Request):
    """Credentials JSON for the batch notebook's Kaggle Drive mode: the user pastes
    this into a private Kaggle secret named GDRIVE_CREDS so the notebook can download
    the exported batch from Drive and upload synthesized audio back (same drive.file
    scope and OAuth client as the app itself)."""
    if not google_drive.is_configured():
        raise HTTPException(status_code=400, detail="Google Drive not configured")
    with locked_conn(request) as conn:
        creds = google_drive.get_creds_from_db(conn)
    if creds is None or not creds.get("refresh_token"):
        raise HTTPException(status_code=400, detail="Google Drive not connected. Connect it first.")
    return JSONResponse({
        "client_id": settings.google_drive_client_id,
        "client_secret": settings.google_drive_client_secret,
        "refresh_token": creds["refresh_token"],
    })


@router.post("/drive/disconnect")
def drive_disconnect(request: Request):
    with locked_conn(request) as conn:
        google_drive.delete_credentials(conn)
    return JSONResponse({"status": "disconnected"})

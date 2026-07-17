"""Google Drive OAuth routes (Colab/Kaggle export round trip settings page)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
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
        accounts = google_drive.list_accounts(conn)
        pending_counts = {
            a["id"]: repository.count_pending_exports_for_account(conn, a["id"])
            for a in accounts
        }
        exports = repository.list_all_patch_exports(conn, limit=30)
    return templates.TemplateResponse(request, "drive.html", {
        "request": request,
        "accounts": accounts,
        "pending_counts": pending_counts,
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
def drive_kaggle_credentials(request: Request, account_id: int | None = None):
    """Credentials JSON for the batch notebook's Kaggle Drive mode: the user pastes
    this into a private Kaggle secret named GDRIVE_CREDS so the notebook can download
    the exported batch from Drive and upload synthesized audio back (same drive.file
    scope and OAuth client as the app itself). Each account has its own credentials -
    the secret must match the account that holds the export."""
    if not google_drive.is_configured():
        raise HTTPException(status_code=400, detail="Google Drive not configured")
    with locked_conn(request) as conn:
        if account_id is not None:
            creds = google_drive.get_account(conn, account_id)
            if creds is None:
                raise HTTPException(status_code=404, detail="Google Drive account not found")
        else:
            accounts = google_drive.list_accounts(conn)
            if not accounts:
                raise HTTPException(status_code=400, detail="Google Drive not connected. Connect it first.")
            if len(accounts) > 1:
                raise HTTPException(
                    status_code=400,
                    detail="Multiple Google Drive accounts connected; pass ?account_id=...",
                )
            creds = accounts[0]
    if not creds.get("refresh_token"):
        raise HTTPException(status_code=400, detail="This account has no refresh token. Reconnect it.")
    return JSONResponse({
        "client_id": settings.google_drive_client_id,
        "client_secret": settings.google_drive_client_secret,
        "refresh_token": creds["refresh_token"],
    })


@router.post("/drive/disconnect")
def drive_disconnect(request: Request, account_id: int = Form(...)):
    with locked_conn(request) as conn:
        google_drive.delete_credentials(conn, account_id)
    return RedirectResponse(url="/drive", status_code=303)

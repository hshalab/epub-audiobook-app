"""Google Drive OAuth routes (Colab/Kaggle export round trip settings page)."""
from __future__ import annotations

import logging
import threading
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import drive_export, google_drive, repository
from app.config import settings
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/drive/pick-folder")
def pick_drive_folder():
    """Open a native folder picker on the machine running the app."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="Native folder picker is unavailable") from exc

    selected: list[str] = []
    error: list[Exception] = []

    def choose() -> None:
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected.append(filedialog.askdirectory(title="Chọn thư mục Google Drive Desktop"))
            root.destroy()
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(target=choose)
    thread.start()
    thread.join()
    if error:
        raise HTTPException(status_code=500, detail=str(error[0]))
    return {"folder_path": selected[0] if selected else ""}


@router.get("/drive", response_class=HTMLResponse)
def drive_page(request: Request):
    with locked_conn(request) as conn:
        targets = repository.list_drive_sync_targets(conn)
        exports = repository.list_all_patch_exports(conn, limit=30)
    return templates.TemplateResponse(request, "drive.html", {
        "request": request,
        "targets": targets,
        "exports": exports,
    })


def _target_fields(name: str, account_email: str, folder_path: str) -> tuple[str, str, str]:
    name, account_email = name.strip(), account_email.strip()
    if not name or not account_email:
        raise ValueError("Name and account email are required")
    return name, account_email, str(drive_export.validate_sync_folder(folder_path))


@router.post("/drive/targets")
def create_sync_target(request: Request, name: str = Form(...), account_email: str = Form(...), folder_path: str = Form(...)):
    try:
        fields = _target_fields(name, account_email, folder_path)
        with locked_conn(request) as conn:
            repository.create_drive_sync_target(conn, *fields)
    except ValueError as exc:
        return RedirectResponse(url=f"/drive?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url="/drive", status_code=303)


@router.post("/drive/targets/{target_id}/edit")
def update_sync_target(request: Request, target_id: int, name: str = Form(...), account_email: str = Form(...), folder_path: str = Form(...)):
    try:
        fields = _target_fields(name, account_email, folder_path)
        with locked_conn(request) as conn:
            if not repository.update_drive_sync_target(conn, target_id, *fields):
                raise HTTPException(status_code=404, detail="Sync target not found")
    except ValueError as exc:
        return RedirectResponse(url=f"/drive?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url="/drive", status_code=303)


@router.post("/drive/targets/{target_id}/delete")
def delete_sync_target(request: Request, target_id: int):
    with locked_conn(request) as conn:
        if not repository.delete_drive_sync_target(conn, target_id):
            raise HTTPException(status_code=404, detail="Sync target not found")
    return RedirectResponse(url="/drive", status_code=303)


@router.get("/drive/connect")
def drive_connect(request: Request, oauth_client_id: int | None = None):
    with locked_conn(request) as conn:
        if oauth_client_id:
            client = google_drive.get_client(conn, oauth_client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="OAuth client not found")
            cid, cs = client["client_id"], client["client_secret"]
        else:
            if not google_drive.is_configured(conn):
                raise HTTPException(
                    status_code=400,
                    detail="Google Drive not configured. Set up an OAuth client at /drive first.",
                )
            cid, cs = None, None
    redirect_uri = str(request.base_url) + "drive/callback"
    state = str(oauth_client_id) if oauth_client_id else ""
    try:
        url = google_drive.get_authorization_url(redirect_uri, client_id=cid, client_secret=cs, state=state)
    except Exception as exc:
        logger.exception("drive_connect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return RedirectResponse(url=url)


@router.get("/drive/callback")
def drive_callback(request: Request, code: str = "", error: str = "", state: str = ""):
    if error:
        return RedirectResponse(url=f"/drive?error={error}")
    if not code:
        return RedirectResponse(url="/drive?error=no_code")

    oauth_client_id = int(state) if state and state.isdigit() else None
    redirect_uri = str(request.base_url) + "drive/callback"

    if oauth_client_id:
        with locked_conn(request) as conn:
            client = google_drive.get_client(conn, oauth_client_id)
            cid, cs = (client["client_id"], client["client_secret"]) if client else (None, None)
    else:
        cid, cs = None, None

    try:
        result = google_drive.exchange_code(code, redirect_uri, client_id=cid, client_secret=cs)
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
                oauth_client_id=oauth_client_id,
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
    with locked_conn(request) as conn:
        if not google_drive.is_configured(conn):
            raise HTTPException(status_code=400, detail="Google Drive not configured")
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
        cid = settings.google_drive_client_id
        cs = settings.google_drive_client_secret
        if creds.get("oauth_client_id"):
            client = google_drive.get_client(conn, creds["oauth_client_id"])
            if client:
                cid, cs = client["client_id"], client["client_secret"]
        return JSONResponse({
            "client_id": cid,
            "client_secret": cs,
            "refresh_token": creds["refresh_token"],
        })


@router.post("/drive/disconnect")
def drive_disconnect(request: Request, account_id: int = Form(...)):
    with locked_conn(request) as conn:
        google_drive.delete_credentials(conn, account_id)
    return RedirectResponse(url="/drive", status_code=303)


@router.post("/drive/clients")
def drive_create_client(request: Request, name: str = Form(...), client_id: str = Form(...), client_secret: str = Form("")):
    with locked_conn(request) as conn:
        google_drive.create_client(conn, name, client_id, client_secret)
    return RedirectResponse(url="/drive#clients", status_code=303)


@router.post("/drive/clients/{client_id}/edit")
def drive_update_client(request: Request, client_id: int, name: str = Form(...), cid: str = Form(...), client_secret: str = Form("")):
    with locked_conn(request) as conn:
        google_drive.update_client(conn, client_id, name, cid, client_secret)
    return RedirectResponse(url="/drive#clients", status_code=303)


@router.post("/drive/clients/{client_id}/delete")
def drive_delete_client(request: Request, client_id: int):
    with locked_conn(request) as conn:
        try:
            google_drive.delete_client(conn, client_id)
        except ValueError as exc:
            return RedirectResponse(url=f"/drive?error={exc}", status_code=303)
    return RedirectResponse(url="/drive#clients", status_code=303)


@router.post("/drive/exports/{export_id}/delete")
def drive_delete_export(request: Request, export_id: int):
    with locked_conn(request) as conn:
        repository.delete_patch_export(conn, export_id)
    return RedirectResponse(url="/drive", status_code=303)

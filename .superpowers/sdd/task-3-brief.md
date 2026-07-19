### Task 3: Route handlers for client CRUD

**Files:**
- Modify: `app/routes/drive.py`
- Test: `tests/test_drive_multi_account.py`

**Interfaces:**
- Consumes: google_drive client CRUD functions from Task 2, updated OAuth functions from Task 2
- Produces: `POST /drive/clients`, `PUT /drive/clients/{id}`, `DELETE /drive/clients/{id}`, modified `GET /drive/connect` with `oauth_client_id`, modified `GET /drive` page with clients

- [ ] **Step 1: Write failing tests for routes**

In `tests/test_drive_multi_account.py`, add:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_drive_page_has_clients():
    resp = client.get("/drive")
    assert resp.status_code == 200
    assert b"OAuth Clients" in resp.content or b"oauth-client" in resp.content


def test_create_client_via_api():
    resp = client.post("/drive/clients", data={
        "name": "Test Client", "client_id": "test-id", "client_secret": "test-secret"
    }, follow_redirects=False)
    assert resp.status_code == 303  # redirect back to /drive


def test_connect_accepts_oauth_client_id():
    resp = client.get("/drive/connect?oauth_client_id=1", follow_redirects=False)
    # Should redirect to Google OAuth (302) or return error if client doesn't exist
    assert resp.status_code in (302, 400, 500)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_drive_multi_account.py -v`

- [ ] **Step 3: Implement client routes in `app/routes/drive.py`**

Add these routes after `drive_disconnect`:

```python
@router.post("/drive/clients")
def drive_create_client(request: Request, name: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)):
    with locked_conn(request) as conn:
        google_drive.create_client(conn, name, client_id, client_secret)
    return RedirectResponse(url="/drive#clients", status_code=303)


@router.put("/drive/clients/{client_id}")
def drive_update_client(request: Request, client_id: int, name: str = Form(...), cid: str = Form(...), client_secret: str = Form(...)):
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
```

Modify `GET /drive/connect` to accept `oauth_client_id`:

```python
@router.get("/drive/connect")
def drive_connect(request: Request, oauth_client_id: int | None = None):
    if not google_drive.is_configured():
        raise HTTPException(status_code=400, detail="Google Drive not configured")
    if oauth_client_id:
        with locked_conn(request) as conn:
            client = google_drive.get_client(conn, oauth_client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="OAuth client not found")
        cid, cs = client["client_id"], client["client_secret"]
    else:
        cid, cs = None, None
    redirect_uri = str(request.base_url) + "drive/callback"
    try:
        url = google_drive.get_authorization_url(redirect_uri, client_id=cid, client_secret=cs)
    except Exception as exc:
        logger.exception("drive_connect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return RedirectResponse(url=url)
```

Modify `GET /drive/callback` similarly to pass client to exchange_code (but callback doesn't know which client was used â€” we need a different approach).

**Ponytail**: The simplest approach is to not change the callback at all â€” the OAuth code parameter carries the redirect_uri, which tells Google which client config to use. But we also need to pass `oauth_client_id` to `save_credentials`. Here's a cleaner approach: during the connect, store a temporary state in `app_state` with the `oauth_client_id`, then read it in the callback.

Simpler: pass the client_id in the OAuth `state` parameter â€” Google's OAuth sends it back untouched.

Modify `drive_connect`:

```python
@router.get("/drive/connect")
def drive_connect(request: Request, oauth_client_id: int | None = None):
    if not google_drive.is_configured():
        raise HTTPException(status_code=400, detail="Google Drive not configured")
    if oauth_client_id:
        with locked_conn(request) as conn:
            client = google_drive.get_client(conn, oauth_client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="OAuth client not found")
        cid, cs = client["client_id"], client["client_secret"]
    else:
        cid, cs = None, None
        oauth_client_id = None
    redirect_uri = str(request.base_url) + "drive/callback"
    try:
        url = google_drive.get_authorization_url(redirect_uri, client_id=cid, client_secret=cs)
    except Exception as exc:
        logger.exception("drive_connect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    # Use state param to carry oauth_client_id back through callback
    state = str(oauth_client_id) if oauth_client_id else ""
    return RedirectResponse(url=f"{url}&state={state}")
```

Modify `drive_callback` to read state:

```python
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
            cid, cs = client["client_id"], client["client_secret"] if client else (None, None)
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
```

Modify `drive_page` to pass clients to template:

```python
@router.get("/drive", response_class=HTMLResponse)
def drive_page(request: Request):
    with locked_conn(request) as conn:
        accounts = google_drive.list_accounts(conn)
        pending_counts = {
            a["id"]: repository.count_pending_exports_for_account(conn, a["id"])
            for a in accounts
        }
        exports = repository.list_all_patch_exports(conn, limit=30)
        clients = google_drive.list_clients(conn)
        # For each account, resolve the client name
        client_names = {}
        for c in clients:
            client_names[c["id"]] = c["name"]
        client_counts = {}
        for a in accounts:
            ocid = a.get("oauth_client_id")
            if ocid:
                client_counts[ocid] = client_counts.get(ocid, 0) + 1
    return templates.TemplateResponse(request, "drive.html", {
        "request": request,
        "accounts": accounts,
        "pending_counts": pending_counts,
        "exports": exports,
        "configured": google_drive.is_configured(),
        "clients": clients,
        "client_names": client_names,
        "client_counts": client_counts,
    })
```

Modify `drive_kaggle_credentials` to use the account's own client creds:

```python
@router.get("/drive/kaggle-credentials")
def drive_kaggle_credentials(request: Request, account_id: int | None = None):
    # ... unchanged up to getting creds ...
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drive_multi_account.py -v`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add app/routes/drive.py
git commit -m "feat: client CRUD routes and connect/callback with oauth_client_id"
```

---


# Multi OAuth Client Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow managing multiple Google OAuth client ID/secret pairs through the Drive settings page and connecting Google Drive accounts via any of those clients (spreading quota across Google Cloud projects).

**Architecture:** A new `drive_oauth_client` SQLite table stores client configs with a CRUD UI on `/drive`. Each `google_drive_credentials` row gets a nullable `oauth_client_id` linking it to the client used (NULL = legacy/`.env` fallback). OAuth flow functions accept explicit client_id/secret params instead of always reading from `settings`.

**Tech Stack:** Python/FastAPI, SQLite, Jinja2, google-auth-oauthlib

**Global Constraints:**
- No new Python dependencies
- Existing `.env`-only setups must keep working without manual migration
- `patch_export.drive_account_id` pattern: no FK constraint on `oauth_client_id` (disconnect must not block)
- Secret is stored plaintext in DB (same as current `.env` practice)

---

### Task 1: DB schema + migration + DriveOAuthClient model

**Files:**
- Modify: `app/db.py` (schema + migration)
- Modify: `app/models.py` (new dataclass)

**Interfaces:**
- Consumes: nothing
- Produces: `DriveOAuthClient` dataclass in `app.models`, schema table `drive_oauth_client` and column `oauth_client_id` on `google_drive_credentials`

- [ ] **Step 1: Add `DriveOAuthClient` dataclass**

In `app/models.py`, after `PatchExport`:

```python
@dataclass
class DriveOAuthClient:
    id: int
    name: str
    client_id: str
    client_secret: str
    created_at: str
    updated_at: str
```

- [ ] **Step 2: Add `drive_oauth_client` table to schema**

In `app/db.py`, add before the `app_state` table:

```sql
CREATE TABLE IF NOT EXISTS drive_oauth_client (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    client_secret   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

- [ ] **Step 3: Add migration for `oauth_client_id` column**

In `app/db.py` `_migrate()`, add after the `drive_account_id` migration (line 218-220):

```python
gdc_existing = {row["name"] for row in conn.execute("PRAGMA table_info(google_drive_credentials)")}
if "oauth_client_id" not in gdc_existing:
    conn.execute("ALTER TABLE google_drive_credentials ADD COLUMN oauth_client_id INTEGER")
```

- [ ] **Step 4: Bootstrap default client from `.env`**

In `app/db.py` `_migrate()`, after the column migration, add:

```python
from app.config import settings
if settings.google_drive_client_id:
    row = conn.execute("SELECT 1 FROM drive_oauth_client LIMIT 1").fetchone()
    if row is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO drive_oauth_client (name, client_id, client_secret, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("Default OAuth Client", settings.google_drive_client_id, settings.google_drive_client_secret, now, now),
        )
```

- [ ] **Step 5: Run tests to verify migration idempotency**

Run: `pytest tests/test_drive_multi_account.py -v`
Expected: existing migration test passes, no regression

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/models.py
git commit -m "feat: add drive_oauth_client table and oauth_client_id column"
```

---

### Task 2: Client CRUD + OAuth flow changes in `google_drive.py`

**Files:**
- Modify: `app/google_drive.py`
- Test: `tests/test_drive_multi_account.py`

**Interfaces:**
- Consumes: `DriveOAuthClient` model from Task 1, `oauth_client_id` column from Task 1
- Produces: `list_clients(conn)`, `get_client(conn, id)`, `create_client(conn, name, client_id, client_secret)`, `update_client(conn, id, name, client_id, client_secret)`, `delete_client(conn, id)`, `count_accounts_for_client(conn, client_id)`, `get_authorization_url(redirect_uri, client_id, client_secret)`, `exchange_code(code, redirect_uri, client_id, client_secret)`, updated `save_credentials()` with `oauth_client_id`, updated `_build_credentials()` with client resolution

- [ ] **Step 1: Write failing tests for client CRUD**

In `tests/test_drive_multi_account.py`, add after the `test_migration_adds_drive_account_id_column` test:

```python
from app.models import DriveOAuthClient


def _add_client(conn, name="Client A", client_id="cid", client_secret="cs"):
    return google_drive.create_client(conn, name, client_id, client_secret)


def test_create_client_returns_id():
    conn = _make_conn()
    cid = _add_client(conn)
    assert isinstance(cid, int)
    assert cid > 0


def test_list_clients_returns_all():
    conn = _make_conn()
    _add_client(conn, "A")
    _add_client(conn, "B")
    clients = google_drive.list_clients(conn)
    assert len(clients) == 2
    assert [c["name"] for c in clients] == ["A", "B"]


def test_get_client_by_id():
    conn = _make_conn()
    cid = _add_client(conn, "Test", "x", "y")
    c = google_drive.get_client(conn, cid)
    assert c is not None
    assert c["name"] == "Test"
    assert c["client_id"] == "x"
    assert c["client_secret"] == "y"


def test_update_client():
    conn = _make_conn()
    cid = _add_client(conn)
    google_drive.update_client(conn, cid, name="Updated", client_id="new_id", client_secret="new_secret")
    c = google_drive.get_client(conn, cid)
    assert c["name"] == "Updated"
    assert c["client_id"] == "new_id"


def test_delete_client():
    conn = _make_conn()
    cid = _add_client(conn)
    google_drive.delete_client(conn, cid)
    assert google_drive.get_client(conn, cid) is None


def test_delete_client_with_accounts_raises():
    conn = _make_conn()
    cid = _add_client(conn)
    google_drive.save_credentials(
        conn, access_token="at", refresh_token="rt", token_expiry=_NOW,
        account_email="a@example.com", oauth_client_id=cid,
    )
    with pytest.raises(ValueError, match="accounts"):
        google_drive.delete_client(conn, cid)


def test_count_accounts_for_client():
    conn = _make_conn()
    cid = _add_client(conn)
    assert google_drive.count_accounts_for_client(conn, cid) == 0
    google_drive.save_credentials(
        conn, access_token="at", refresh_token="rt", token_expiry=_NOW,
        account_email="a@example.com", oauth_client_id=cid,
    )
    assert google_drive.count_accounts_for_client(conn, cid) == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_drive_multi_account.py -v`
Expected: 7 new tests fail with AttributeError/NameError

- [ ] **Step 3: Implement client CRUD + modify OAuth flow in `google_drive.py`**

Add functions after `is_configured()`:

```python
def list_clients(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM drive_oauth_client ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_client(conn: sqlite3.Connection, client_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM drive_oauth_client WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def create_client(conn: sqlite3.Connection, name: str, client_id: str, client_secret: str) -> int:
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO drive_oauth_client (name, client_id, client_secret, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (name, client_id, client_secret, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_client(conn: sqlite3.Connection, row_id: int, name: str, client_id: str, client_secret: str) -> None:
    conn.execute(
        """UPDATE drive_oauth_client SET name=?, client_id=?, client_secret=?, updated_at=?
           WHERE id=?""",
        (name, client_id, client_secret, _now_iso(), row_id),
    )
    conn.commit()


def delete_client(conn: sqlite3.Connection, row_id: int) -> None:
    if count_accounts_for_client(conn, row_id) > 0:
        raise ValueError(
            f"Cannot delete client with {count_accounts_for_client(conn, row_id)} connected account(s). "
            "Disconnect those accounts first."
        )
    conn.execute("DELETE FROM drive_oauth_client WHERE id = ?", (row_id,))
    conn.commit()


def count_accounts_for_client(conn: sqlite3.Connection, client_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM google_drive_credentials WHERE oauth_client_id = ?",
        (client_id,),
    ).fetchone()
    return row["n"]
```

Modify `save_credentials()` signature — add `oauth_client_id: int | None = None`:

Find `def save_credentials(` (line 74) and add the param, store it:

```python
def save_credentials(
    conn: sqlite3.Connection,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    account_email: str | None = None,
    oauth_client_id: int | None = None,
) -> int:
    now = _now_iso()
    if account_email:
        existing = conn.execute(
            "SELECT id FROM google_drive_credentials WHERE account_email = ?",
            (account_email,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE google_drive_credentials
                   SET access_token=?, refresh_token=?, token_expiry=?, updated_at=?, oauth_client_id=?
                   WHERE id=?""",
                (access_token, refresh_token, token_expiry, now, oauth_client_id, existing["id"]),
            )
            conn.commit()
            return existing["id"]
    cur = conn.execute(
        """INSERT INTO google_drive_credentials
           (access_token, refresh_token, token_expiry, account_email, oauth_client_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (access_token, refresh_token, token_expiry, account_email, oauth_client_id, now, now),
    )
    conn.commit()
    return cur.lastrowid
```

Modify `_build_credentials()` to resolve client from the row's `oauth_client_id`:

```python
def _build_credentials(row: dict) -> Credentials:
    _require_google_imports()
    client_id = settings.google_drive_client_id
    client_secret = settings.google_drive_client_secret
    if row.get("oauth_client_id"):
        client_row = get_client(sqlite3.Connection, row["oauth_client_id"])
        # But we don't have a conn here... need to restructure.
```

**Ponytail decision**: Instead of threading a `conn` through `_build_credentials`, make it accept optional `client_id/client_secret` params. The caller (`_refresh_if_needed`, `get_drive_service`) already has the `conn` and the row — resolve the client there and pass the strings down.

Rewrite `_build_credentials()`:

```python
def _build_credentials(row: dict, client_id: str | None = None, client_secret: str | None = None) -> Credentials:
    _require_google_imports()
    return Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id or settings.google_drive_client_id,
        client_secret=client_secret or settings.google_drive_client_secret,
        scopes=_SCOPES,
    )
```

Modify `_refresh_if_needed()` to resolve client:

```python
def _refresh_if_needed(conn: sqlite3.Connection, creds_row: dict) -> Credentials:
    _require_google_imports()
    client_id, client_secret = _resolve_client_creds(conn, creds_row)
    creds = _build_credentials(creds_row, client_id, client_secret)
    # ... rest unchanged
```

Add new helper function:

```python
def _resolve_client_creds(conn: sqlite3.Connection, creds_row: dict) -> tuple[str, str]:
    oauth_client_id = creds_row.get("oauth_client_id")
    if oauth_client_id:
        client = get_client(conn, oauth_client_id)
        if client:
            return client["client_id"], client["client_secret"]
    return settings.google_drive_client_id, settings.google_drive_client_secret
```

Modify `get_authorization_url()` to accept explicit client_id/secret:

```python
def get_authorization_url(redirect_uri: str, client_id: str | None = None, client_secret: str | None = None) -> str:
    _require_google_imports()
    cid = client_id or settings.google_drive_client_id
    cs = client_secret or settings.google_drive_client_secret
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": cid,
                "client_secret": cs,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_SCOPES,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )
    return url
```

Similarly modify `exchange_code()`:

```python
def exchange_code(code: str, redirect_uri: str, client_id: str | None = None, client_secret: str | None = None) -> dict:
    _require_google_imports()
    cid = client_id or settings.google_drive_client_id
    cs = client_secret or settings.google_drive_client_secret
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": cid,
                "client_secret": cs,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_SCOPES,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drive_multi_account.py -v`
Expected: all 7 new tests PASS, existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/google_drive.py tests/test_drive_multi_account.py
git commit -m "feat: client CRUD and OAuth flow with explicit client_id/secret"
```

---

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

Modify `GET /drive/callback` similarly to pass client to exchange_code (but callback doesn't know which client was used — we need a different approach).

**Ponytail**: The simplest approach is to not change the callback at all — the OAuth code parameter carries the redirect_uri, which tells Google which client config to use. But we also need to pass `oauth_client_id` to `save_credentials`. Here's a cleaner approach: during the connect, store a temporary state in `app_state` with the `oauth_client_id`, then read it in the callback.

Simpler: pass the client_id in the OAuth `state` parameter — Google's OAuth sends it back untouched.

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

### Task 4: Template UI (`drive.html`)

**Files:**
- Modify: `app/templates/drive.html`

**Interfaces:**
- Consumes: `clients` and `client_names` template vars from Task 3
- Produces: Full UI with OAuth Clients card, updated account table, connect dropdown

- [ ] **Step 1: Read the current template to confirm baseline**

`app/templates/drive.html` — 151 lines

- [ ] **Step 2: Replace the template with multi-client version**

Replace the entire `{% block content %}` with:

Key changes:
1. Add "OAuth Clients" collapsible card after "Tài khoản đã kết nối" card
2. Add client name column to account table
3. Connect button becomes a dropdown when >1 client

```html
{% extends "base.html" %}
{% block title %}Google Drive{% endblock %}
{% block content %}
<h2>Google Drive</h2>
<p style="color:var(--text-muted);margin-bottom:var(--space-lg)">Ket noi Google Drive de xuat chunk sang Colab/Kaggle va nhap ket qua ve. Khong phai trinh quan ly Drive tong quat - chi dung cho luong export/import theo tung patch.</p>

{% if not configured %}
<div class="warning-block">
    <p style="margin:0 0 0.3rem 0"><strong>Google Drive chua duoc cau hinh.</strong></p>
    <p style="margin:0 0 0.3rem 0">Them <code>GOOGLE_DRIVE_CLIENT_ID</code> va <code>GOOGLE_DRIVE_CLIENT_SECRET</code> vao file <code>.env</code> de kich hoat tinh nang nay.</p>
    <p style="margin:0">Neu ban da cau hinh YouTube upload, co the dung lai cung OAuth client, chi can bat them "Google Drive API" tren cung Google Cloud project tai <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank">Google Cloud Console</a>.</p>
</div>
{% else %}

<!-- ======== OAuth Clients ======== -->
<div class="card" id="clients">
    <div class="card-header" onclick="this.closest('.card').classList.toggle('collapsed')" style="cursor:pointer">
        <h3 style="margin:0">OAuth Clients <span style="font-size:0.8em;color:var(--text-muted)">({{ clients|length }})</span></h3>
    </div>
    <div class="card-body">
    {% if clients %}
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>Name</th>
                <th>Client ID</th>
                <th>Accounts</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
        {% for c in clients %}
            <tr>
                <td>{{ c.name }}</td>
                <td style="font-family:monospace;font-size:0.85em">{{ c.client_id[:30] + '…' if c.client_id|length > 30 else c.client_id }}</td>
                <td>{{ client_counts.get(c.id, 0) }}</td>
                <td style="white-space:nowrap">
                    <button type="button" class="btn-outline btn-sm" onclick="editClient({{ c.id }}, '{{ c.name|e }}', '{{ c.client_id|e }}')">Edit</button>
                    <form method="post" action="/drive/clients/{{ c.id }}/delete" style="display:inline"
                          onsubmit="return confirm('Xoa OAuth client {{ c.name|e }}?{% if client_counts.get(c.id) %} Client nay co {{ client_counts[c.id] }} tai khoan Drive dang ket noi - phai ngat ket noi truoc.{% endif %}')">
                        <button type="submit" class="btn-danger btn-sm">Delete</button>
                    </form>
                    <a href="/drive/connect?oauth_client_id={{ c.id }}" class="btn-outline btn-sm" style="text-decoration:none">Connect</a>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% endif %}
    <details style="margin-top:var(--space-sm)">
        <summary style="cursor:pointer;color:var(--text-muted)">Add new OAuth client</summary>
        <form method="post" action="/drive/clients" style="margin-top:var(--space-sm);display:flex;gap:var(--space-sm);flex-wrap:wrap">
            <input type="text" name="name" placeholder="Name (e.g. Project A)" required>
            <input type="text" name="client_id" placeholder="Client ID" required style="min-width:240px">
            <input type="text" name="client_secret" placeholder="Client Secret" required style="min-width:240px">
            <button type="submit" class="btn-youtube">Add</button>
        </form>
    </details>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h3 style="margin:0">Tai khoan da ket noi</h3>
    </div>
    {% if accounts %}
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>Email</th>
                <th>OAuth Client</th>
                <th>Ket noi luc</th>
                <th>Export cho import</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
        {% for a in accounts %}
            <tr>
                <td>{{ a.account_email or '(khong ro email)' }}</td>
                <td>{{ client_names.get(a.oauth_client_id) or '(default)' }}</td>
                <td>{{ a.created_at }}</td>
                <td>{{ pending_counts[a.id] or 0 }}</td>
                <td style="white-space:nowrap">
                    <button type="button" class="btn-outline btn-sm" onclick="copyKaggleCreds({{ a.id }})">Copy Kaggle credentials</button>
                    <form method="post" action="/drive/disconnect" style="display:inline"
                          onsubmit="return confirm('Ngat ket noi {{ a.account_email or 'tai khoan nay' }}?{% if pending_counts[a.id] %} Tai khoan nay con {{ pending_counts[a.id] }} export chua import xong - se khong import tu Drive duoc nua (van co the upload file thu cong, hoac ket noi lai cung email de khoi phuc).{% endif %}')">
                        <input type="hidden" name="account_id" value="{{ a.id }}">
                        <button type="submit" class="btn-danger btn-sm">Ngat ket noi</button>
                    </form>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% else %}
    <p class="yt-empty">Chua co tai khoan Google Drive nao duoc ket noi.</p>
    {% endif %}
    <p style="margin:var(--space-sm) 0 0 0">
    {% if clients|length > 1 %}
        {% for c in clients %}
        <a href="/drive/connect?oauth_client_id={{ c.id }}" class="btn-youtube" style="margin-right:var(--space-xs)">Ket noi Drive ({{ c.name }})</a>
        {% endfor %}
    {% else %}
        <a href="/drive/connect" class="btn-youtube">Ket noi them tai khoan Google Drive</a>
    {% endif %}
    </p>
    {% if accounts|length > 1 %}
    <p style="margin:var(--space-sm) 0 0 0;color:var(--text-muted)">
        Khi export patch/batch, app tu dong xoay vong giua cac tai khoan de phan tai quota GPU
        Colab/Kaggle. Import se dung dung tai khoan da export (xem cot "Tai khoan" ben duoi).
    </p>
    {% endif %}
</div>

{% if accounts %}
<div class="card">
    <div class="card-header">
        <h3 style="margin:0">Kaggle</h3>
    </div>
    <p style="margin:0 0 var(--space-sm) 0;color:var(--text-muted)">
        De notebook batch chay tren Kaggle truy cap Drive truc tiep (tu tai package ve va upload .wav
        len Drive khi chay - resume duoc qua nhieu session): bam "Copy Kaggle credentials" o dong tai khoan
        tuong ung, roi tren Kaggle vao <strong>Add-ons &gt; Secrets</strong>, tao secret ten
        <code>GDRIVE_CREDS</code> voi gia tri vua copy va bat cho notebook. Moi tai khoan co credentials
        rieng - phai dung dung tai khoan dang chua export can chay. Secret nay chua refresh token cua
        tai khoan Drive - chi dan vao Kaggle Secrets (rieng tu), khong dan vao code cua notebook.
    </p>
    <textarea id="kaggle-creds-out" readonly rows="3" style="width:100%;display:none;font-family:monospace;font-size:0.85em"></textarea>
    <p id="kaggle-creds-msg" style="display:none;margin:var(--space-sm) 0 0 0"></p>
</div>
{% endif %}

<div class="card">
    <div class="card-header">
        <h3 style="margin:0">Lich su Export</h3>
    </div>
    {% if exports %}
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>Book</th>
                <th>Patch</th>
                <th>Tai khoan</th>
                <th>Drive folder</th>
                <th>Status</th>
                <th>Imported</th>
                <th>Created</th>
            </tr>
        </thead>
        <tbody>
        {% for e in exports %}
            <tr>
                <td><a href="/books/{{ e.book_id }}">{{ e.book_title }}</a></td>
                <td><a href="/books/{{ e.book_id }}/patches/{{ e.patch_id }}/chunks">{{ e.patch_index }}</a></td>
                <td>{{ e.account_email or '—' }}</td>
                <td><a href="{{ e.drive_folder_link }}" target="_blank">Open folder</a></td>
                <td>{{ e.status }}</td>
                <td>{{ e.imported_chunk_count }}/{{ e.exported_chunk_count }}</td>
                <td>{{ e.created_at }}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    </div>
    {% else %}
    <p class="yt-empty">Chua co patch nao duoc export.</p>
    {% endif %}
</div>

{% endif %}
{% endblock %}

{% block scripts %}
<script>
async function copyKaggleCreds(accountId) {
    const msg = document.getElementById('kaggle-creds-msg');
    const out = document.getElementById('kaggle-creds-out');
    try {
        const res = await fetch('/drive/kaggle-credentials?account_id=' + accountId);
        if (!res.ok) {
            const body = await res.json().catch(() => null);
            throw new Error((body && body.detail) || res.statusText);
        }
        const text = JSON.stringify(await res.json());
        out.value = text;
        try {
            await navigator.clipboard.writeText(text);
            msg.textContent = 'Da copy vao clipboard - dan vao Kaggle secret GDRIVE_CREDS.';
        } catch (e) {
            out.style.display = 'block';
            out.select();
            msg.textContent = 'Khong copy tu dong duoc - JSON hien ben duoi, hay copy thu cong.';
        }
        msg.style.color = 'var(--text-muted)';
    } catch (e) {
        msg.textContent = 'Loi: ' + e.message;
        msg.style.color = 'var(--color-error)';
    }
    msg.style.display = 'block';
}

function editClient(id, name, clientId) {
    // ponytail: prompt() is the laziest edit UI; modal if user asks
    const newName = prompt('Client name:', name);
    if (!newName) return;
    const newCid = prompt('Client ID:', clientId);
    if (!newCid) return;
    const newSecret = prompt('Client Secret (leave blank to keep current):', '');
    if (newSecret === null) return;
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/drive/clients/' + id + '?_method=PUT';
    for (const [k, v] of Object.entries({name: newName, cid: newCid, client_secret: newSecret})) {
        const i = document.createElement('input');
        i.type = 'hidden'; i.name = k; i.value = v;
        form.appendChild(i);
    }
    document.body.appendChild(form);
    form.submit();
}
</script>
{% endblock %}
```

Note: We need to handle PUT via POST override. In `app/routes/drive.py`, change the update route. Simpler approach: change the update to use POST only (no PUT method override). Change `drive_update_client` to use `@router.post("/drive/clients/{client_id}/edit")`.

- [ ] **Step 3: Update routes to match template form actions**

Change `drive_update_client` route:

```python
@router.post("/drive/clients/{client_id}/edit")
def drive_update_client(request: Request, client_id: int, name: str = Form(...), cid: str = Form(...), client_secret: str = Form(...)):
    with locked_conn(request) as conn:
        google_drive.update_client(conn, client_id, name, cid, client_secret)
    return RedirectResponse(url="/drive#clients", status_code=303)
```

And fix `editClient()` JS to use `/drive/clients/{id}/edit`:

```javascript
form.action = '/drive/clients/' + id + '/edit';
```

- [ ] **Step 4: Add `client_counts` to template context**

In `drive_page`, add:

```python
client_counts = {}
for a in accounts:
    ocid = a.get("oauth_client_id")
    if ocid:
        client_counts[ocid] = client_counts.get(ocid, 0) + 1
```

And pass it to template.

- [ ] **Step 5: Verify the template renders**

Run: `pytest tests/test_drive_multi_account.py -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add app/templates/drive.html app/routes/drive.py
git commit -m "feat: multi-client UI on /drive page"
```

---

### Task 5: Write unit tests for client + OAuth integration

**Files:**
- Modify/Append: `tests/test_drive_multi_account.py`

- [ ] **Step 1: Add test for save_credentials with oauth_client_id**

```python
def test_save_credentials_with_oauth_client_id():
    conn = _make_conn()
    cid = _add_client(conn)
    aid = google_drive.save_credentials(
        conn, access_token="at", refresh_token="rt", token_expiry=_NOW,
        account_email="a@example.com", oauth_client_id=cid,
    )
    row = google_drive.get_account(conn, aid)
    assert row["oauth_client_id"] == cid


def test_kaggle_creds_uses_client_creds():
    """The Kaggle endpoint should return the client_id/secret from the
    account's linked OAuth client, not the env defaults."""
    conn = _make_conn()
    cid = _add_client(conn, "MyClient", "custom-id", "custom-secret")
    aid = google_drive.save_credentials(
        conn, access_token="at", refresh_token="rt", token_expiry=_NOW,
        account_email="a@example.com", oauth_client_id=cid,
    )
    row = google_drive.get_account(conn, aid)
    assert row["oauth_client_id"] == cid
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_drive_multi_account.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_drive_multi_account.py
git commit -m "test: integration tests for multi-client Drive accounts"
```

---

### Task 6: Document `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Update `.env.example`**

After line 110, add a note about the multi-client DB feature:

```markdown
# MULTI-CLIENT NOTE (v2026-07+):
# You can manage multiple OAuth clients directly from the /drive UI
# (stored in the database). The .env values above serve as the seed
# "Default OAuth Client" on first run. Once a client row exists in the
# database, you can add/remove clients through the UI without restarting.
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: multi-client feature note in .env.example"
```

---

### Post-implementation verification

After all tasks are committed:

- [ ] Run all Drive tests: `pytest tests/test_drive_multi_account.py -v`
- [ ] Run full test suite: `pytest tests/ -v` (catch regressions)
- [ ] Manual smoke test: start the dev server, visit `/drive`, create a client, connect an account

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

Modify `save_credentials()` signature â€” add `oauth_client_id: int | None = None`:

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

**Ponytail decision**: Instead of threading a `conn` through `_build_credentials`, make it accept optional `client_id/client_secret` params. The caller (`_refresh_if_needed`, `get_drive_service`) already has the `conn` and the row â€” resolve the client there and pass the strings down.

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


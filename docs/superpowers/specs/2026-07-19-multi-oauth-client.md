# Multi OAuth Client Support for Google Drive

## Motivation

Allow connecting multiple Google Drive accounts using **different OAuth clients**
(from different Google Cloud projects). Each Google Cloud project has its own
Drive API quota — using multiple clients spreads the quota and lets more
concurrent Colab/Kaggle notebook runs complete without hitting rate limits.

## Design

### DB Schema

New table `drive_oauth_client`:

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

Existing `google_drive_credentials` gets a nullable `oauth_client_id` column:

```sql
ALTER TABLE google_drive_credentials ADD COLUMN oauth_client_id INTEGER;
```

- `NULL` = legacy account connected before this feature (falls back to `.env` or the first client)
- No FK constraint — disconnecting must not block on export history (same pattern as `drive_account_id`)

### Bootstrap

During `_migrate()` in `db.py`: if `drive_oauth_client` table is empty AND
`.env` has `GOOGLE_DRIVE_CLIENT_ID` set, insert a row named
"Default OAuth Client" with the env values — so existing setups get a working
client row without manual action.

### UI (`/drive`)

A new collapsible **"OAuth Clients"** card below the "Tài khoản đã kết nối" card:

- **Client table**: Name | Client ID (masked) | Connected accounts count | Actions (Edit, Delete)
- **Add form**: 3 fields (Name, Client ID, Client Secret), inline or modal
- **Connect button per row**: "Connect with this client" → initiates OAuth using that client
- **Edit**: modal pre-filled with current values (secret shown masked, can be left blank to keep)

Changes to the "Tài khoản đã kết nối" card:

- New column **"OAuth Client"** shows the client name
- "Kết nối thêm tài khoản Google Drive" button becomes a dropdown listing clients
  (only when >1 client exists; otherwise stays a simple link)

### Backend

**`google_drive.py`** changes:

- `get_authorization_url(redirect_uri, client_id, client_secret)` — explicit params
- `exchange_code(code, redirect_uri, client_id, client_secret)` — explicit params
- `_build_credentials(row)` — reads `oauth_client_id` from row, resolves client;
  if NULL, falls back to `settings.google_drive_client_id/secret` for backward compat
- New functions: `list_clients()`, `get_client()`, `create_client()`, `update_client()`,
  `delete_client()`, `count_accounts_for_client()`

**`routes/drive.py`** changes:

- `GET /drive` — also pass `clients` list to template
- `POST /drive/clients` — create client
- `PUT /drive/clients/{id}` — update client
- `DELETE /drive/clients/{id}` — delete (only if no accounts linked to it)
- `GET /drive/connect` — accept optional `?oauth_client_id=N` to pick which client

**`db.py`** changes:

- Add `drive_oauth_client` CREATE TABLE in schema
- Migration: add `oauth_client_id` column to `google_drive_credentials`
- Bootstrap logic: seed default client from `.env` on first run if table empty

### Kaggle Credentials

No format change — Kaggle JSON already returns `{client_id, client_secret, refresh_token}`.
After this change, the `client_id`/`client_secret` come from the account's linked
OAuth client instead of `settings.*`.

### Export / Import

The round-robin export (`pick_export_account`) and import resolution
(`resolve_import_account`) are unchanged — they operate on the
`google_drive_credentials` table, which now includes accounts from all clients.

## Files changed

| File | Change |
|------|--------|
| `app/db.py` | New table + migration + bootstrap |
| `app/google_drive.py` | Client-aware OAuth flow, client CRUD, fallback to `.env` |
| `app/routes/drive.py` | Client CRUD routes, connect with client selection |
| `app/templates/drive.html` | OAuth Clients card, updated account table, connect dropdown |
| `.env.example` | Document the new feature |

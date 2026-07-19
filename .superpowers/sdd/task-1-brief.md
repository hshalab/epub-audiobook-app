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


# Database Import/Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add export (SQL/JSON) and import (SQL/JSON with overwrite/merge) for SQLite database via API and Web UI.

**Architecture:** New `app/database_io.py` module handles all export/import logic. New `app/routes/database_io.py` exposes REST endpoints. New template `database_io.html` provides Web UI.

**Tech Stack:** Python `sqlite3` (stdlib), FastAPI, Jinja2

## Global Constraints

- Use Python stdlib `sqlite3` only — no new dependencies
- Follow existing codebase patterns: `locked_conn` for DB access, `APIRouter`, sync routes
- SQL export wraps each table's block with `-- TABLE: <name>` comment for parseable import with table filtering
- Test with `:memory:` SQLite for unit tests, `TestClient` for route tests

---

### Task 1: Core export functions — `app/database_io.py`

**Files:**
- Create: `app/database_io.py`
- Test: `tests/test_database_io.py`

**Interfaces:**
- Produces: `user_table_names(conn) -> list[str]`, `export_sql(conn, tables=None) -> str`, `export_json(conn, tables=None) -> dict`

- [ ] **Step 1: Write failing tests for export**

```python
"""Tests for database import/export."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db
from app.database_io import user_table_names, export_sql, export_json

_NOW = datetime.now(timezone.utc).isoformat()

def _conn():
    c = db.connect(":memory:")
    db.init_schema(c)
    c.execute("INSERT INTO app_state (key, value) VALUES ('k1', 'v1')")
    c.execute("INSERT INTO music (name, file_path, created_at) VALUES ('m1', '/tmp/m1.mp3', ?)", (_NOW,))
    c.commit()
    return c

def test_user_table_names():
    conn = _conn()
    names = user_table_names(conn)
    assert "book" in names
    assert "app_state" in names
    assert "music" in names
    assert not any(n.startswith("sqlite_") for n in names)

def test_export_sql_all_tables():
    conn = _conn()
    sql = export_sql(conn)
    assert sql.startswith("-- TABLE:")
    assert "app_state" in sql
    assert "music" in sql
    assert "INSERT INTO" in sql

def test_export_sql_selected_tables():
    conn = _conn()
    sql = export_sql(conn, tables=["app_state"])
    assert "app_state" in sql
    assert "music" not in sql

def test_export_sql_includes_create_and_indexes():
    conn = _conn()
    sql = export_sql(conn, tables=["patch"])
    assert "CREATE TABLE" in sql
    # patch has 3 indexes in sqlite_master
    idx_count = sql.count("CREATE INDEX")
    assert idx_count >= 1

def test_export_sql_empty_table():
    conn = _conn()
    sql = export_sql(conn, tables=["voice_meta"])
    assert "voice_meta" in sql
    # empty table → no INSERT statements
    insert_lines = [l for l in sql.split("\n") if l.startswith("INSERT")]
    assert len(insert_lines) == 0

def test_export_json_all_tables():
    conn = _conn()
    data = export_json(conn)
    assert isinstance(data, dict)
    assert "app_state" in data
    assert "music" in data
    assert data["app_state"] == [{"key": "k1", "value": "v1"}]

def test_export_json_selected_tables():
    conn = _conn()
    data = export_json(conn, tables=["music"])
    assert "music" in data
    assert "app_state" not in data

def test_export_json_returns_dicts():
    conn = _conn()
    data = export_json(conn, tables=["app_state"])
    row = data["app_state"][0]
    assert row["key"] == "k1"
    assert row["value"] == "v1"
```

- [ ] **Step 2: Run tests to confirm failures**

Run: `python -m pytest tests/test_database_io.py::test_user_table_names tests/test_database_io.py::test_export_sql_all_tables tests/test_database_io.py::test_export_sql_selected_tables tests/test_database_io.py::test_export_sql_includes_create_and_indexes tests/test_database_io.py::test_export_sql_empty_table tests/test_database_io.py::test_export_json_all_tables tests/test_database_io.py::test_export_json_selected_tables tests/test_database_io.py::test_export_json_returns_dicts -v`
Expected: All FAIL with ImportError (no module)

- [ ] **Step 3: Write export functions**

```python
"""Import/export SQLite database."""
from __future__ import annotations

import json
import re
import sqlite3


def user_table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _resolve_tables(conn: sqlite3.Connection, tables: list[str] | None) -> list[str]:
    all_tables = user_table_names(conn)
    if tables is None:
        return all_tables
    unknown = set(tables) - set(all_tables)
    if unknown:
        raise ValueError(f"Unknown tables: {', '.join(sorted(unknown))}")
    return tables


def _sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    escaped = str(v).replace("'", "''")
    return f"'{escaped}'"


def export_sql(conn: sqlite3.Connection, tables: list[str] | None = None) -> str:
    selected = _resolve_tables(conn, tables)
    lines: list[str] = []
    for table in selected:
        create = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if create is None or not create["sql"]:
            continue
        lines.append(f"-- TABLE: {table}")
        lines.append(create["sql"] + ";")
        for idx in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        ):
            lines.append(idx["sql"] + ";")
        cols = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            vals = [_sql_val(v) for v in row]
            lines.append(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join(vals)});')
    return "\n".join(lines)


def export_json(conn: sqlite3.Connection, tables: list[str] | None = None) -> dict[str, list[dict]]:
    selected = _resolve_tables(conn, tables)
    result: dict[str, list[dict]] = {}
    for table in selected:
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        result[table] = [dict(r) for r in rows]
    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_database_io.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add app/database_io.py tests/test_database_io.py
git commit -m "feat: add database export (SQL + JSON)"
```

---

### Task 2: Core import functions — `app/database_io.py`

**Files:**
- Modify: `app/database_io.py`
- Modify: `tests/test_database_io.py`

**Interfaces:**
- Produces: `import_sql(conn, sql, mode='overwrite', tables=None)`, `import_json(conn, data, mode='overwrite', tables=None)`
- Consumes: `user_table_names()`, `_resolve_tables()`, `_sql_val()` from Task 1

- [ ] **Step 1: Write failing tests for import**

```python
def test_import_sql_overwrite():
    conn = _conn()
    sql = export_sql(conn, tables=["music"])
    # Clear and re-import
    import_sql(conn, sql, mode="overwrite", tables=["music"])
    rows = conn.execute("SELECT name FROM music").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "m1"

def test_import_sql_overwrite_clears_old_data():
    conn = _conn()
    conn.execute("INSERT INTO music (name, file_path, created_at) VALUES ('old', '/tmp/o.mp3', ?)", (_NOW,))
    conn.commit()
    sql = export_sql(conn, tables=["music"])
    # Only row 'm1' was in the export, 'old' should be gone after overwrite
    import_sql(conn, sql, mode="overwrite", tables=["music"])
    rows = [r["name"] for r in conn.execute("SELECT name FROM music").fetchall()]
    assert rows == ["m1"]

def test_import_sql_merge_appends_new_data():
    conn = _conn()
    sql = export_sql(conn, tables=["music"])
    conn.execute("INSERT INTO music (name, file_path, created_at) VALUES ('existing', '/tmp/e.mp3', ?)", (_NOW,))
    conn.commit()
    import_sql(conn, sql, mode="merge", tables=["music"])
    rows = [r["name"] for r in conn.execute("SELECT name FROM music ORDER BY name").fetchall()]
    assert "existing" in rows
    assert "m1" in rows

def test_import_json_overwrite():
    conn = _conn()
    data = export_json(conn, tables=["app_state"])
    conn.execute("INSERT INTO app_state (key, value) VALUES ('existing', 'ev')")
    conn.commit()
    import_json(conn, data, mode="overwrite", tables=["app_state"])
    rows = dict(conn.execute("SELECT key, value FROM app_state").fetchall())
    assert rows == {"k1": "v1"}

def test_import_json_merge():
    conn = _conn()
    data = export_json(conn, tables=["app_state"])
    conn.execute("INSERT INTO app_state (key, value) VALUES ('existing', 'ev')")
    conn.commit()
    import_json(conn, data, mode="merge", tables=["app_state"])
    rows = dict(conn.execute("SELECT key, value FROM app_state").fetchall())
    assert rows == {"k1": "v1", "existing": "ev"}

def test_import_json_merge_ignores_duplicate_pk():
    conn = _conn()
    data = export_json(conn, tables=["app_state"])
    # Same PK as exported data
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('k1', 'modified')")
    conn.commit()
    import_json(conn, data, mode="merge", tables=["app_state"])
    row = conn.execute("SELECT value FROM app_state WHERE key='k1'").fetchone()
    # Original value preserved (INSERT OR IGNORE)
    assert row["value"] == "modified"

def test_import_sql_filter_tables():
    conn = _conn()
    full_sql = export_sql(conn)
    # Import only music, app_state should be unchanged
    conn.execute("INSERT INTO app_state (key, value) VALUES ('keep', 'me')")
    conn.commit()
    import_sql(conn, full_sql, mode="overwrite", tables=["music"])
    row = conn.execute("SELECT value FROM app_state WHERE key='keep'").fetchone()
    assert row is not None
    assert row["value"] == "me"

def test_import_json_filter_tables():
    conn = _conn()
    full_json = export_json(conn)
    conn.execute("INSERT INTO app_state (key, value) VALUES ('keep', 'me')")
    conn.commit()
    import_json(conn, full_json, mode="overwrite", tables=["music"])
    row = conn.execute("SELECT value FROM app_state WHERE key='keep'").fetchone()
    assert row is not None
```

- [ ] **Step 2: Run new tests to confirm failures**

Run: `python -m pytest tests/test_database_io.py::test_import_sql_overwrite tests/test_database_io.py::test_import_sql_overwrite_clears_old_data tests/test_database_io.py::test_import_sql_merge_appends_new_data tests/test_database_io.py::test_import_json_overwrite tests/test_database_io.py::test_import_json_merge tests/test_database_io.py::test_import_json_merge_ignores_duplicate_pk tests/test_database_io.py::test_import_sql_filter_tables tests/test_database_io.py::test_import_json_filter_tables -v`
Expected: All FAIL with ImportError

- [ ] **Step 3: Write import functions**

Add to `app/database_io.py`:

```python
def _table_order() -> list[str]:
    """Reverse-dependency order for safe DELETE/DROP."""
    return [
        "voice_meta",
        "patch_export",
        "patch",
        "chapter",
        "book_job",
        "text_replace_rule",
        "google_drive_credentials",
        "youtube_uploads",
        "youtube_credentials",
        "drive_oauth_client",
        "app_state",
        "music",
        "book",
    ]


def _clear_tables(conn: sqlite3.Connection, tables: list[str]) -> None:
    order = _table_order()
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in order:
        if table in tables:
            conn.execute(f'DELETE FROM "{table}"')
    conn.execute("PRAGMA foreign_keys = ON")


def import_sql(
    conn: sqlite3.Connection,
    sql: str,
    mode: str = "overwrite",
    tables: list[str] | None = None,
) -> None:
    selected = _resolve_tables(conn, tables) if tables else None
    _TABLE_MARKER_RE = re.compile(r"^-- TABLE:\s*(\w+)", re.MULTILINE)
    blocks = re.split(_TABLE_MARKER_RE, sql)[1:]  # [name, body, name, body, ...]
    for i in range(0, len(blocks), 2):
        table = blocks[i]
        body = blocks[i + 1]
        if selected is not None and table not in selected:
            continue
        if mode == "overwrite":
            _clear_tables(conn, [table])
            conn.executescript(body)
        else:
            stmts = [s.strip() for s in body.split(";") if s.strip()]
            for stmt in stmts:
                if stmt.upper().startswith("INSERT"):
                    stmt = stmt.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
                    conn.execute(stmt)
    conn.commit()


def import_json(
    conn: sqlite3.Connection,
    data: dict[str, list[dict]],
    mode: str = "overwrite",
    tables: list[str] | None = None,
) -> None:
    selected = _resolve_tables(conn, tables) if tables else None
    for table, rows in data.items():
        if selected is not None and table not in selected:
            continue
        if mode == "overwrite":
            _clear_tables(conn, [table])
        for row in rows:
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" for _ in row)
            sql = f'INSERT OR IGNORE INTO "{table}" ({cols}) VALUES ({placeholders})'
            conn.execute(sql, list(row.values()))
    conn.commit()
```

- [ ] **Step 4: Run all tests to confirm they pass**

Run: `python -m pytest tests/test_database_io.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add app/database_io.py tests/test_database_io.py
git commit -m "feat: add database import (SQL + JSON) with overwrite/merge"
```

---

### Task 3: API routes — `app/routes/database_io.py`

**Files:**
- Create: `app/routes/database_io.py`
- Modify: `app/main.py`
- Test: `tests/test_database_io.py`

**Interfaces:**
- Consumes: `export_sql()`, `export_json()`, `import_sql()`, `import_json()` from Task 2
- Produces: `GET /api/db/export`, `POST /api/db/import`

- [ ] **Step 1: Write failing route tests**

Add to `tests/test_database_io.py`:

```python
import threading
import io
import json as json_mod

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings_mod = __import__("app.config", fromlist=["settings"])
    monkeypatch.setattr(settings_mod.settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings_mod.settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings_mod.settings, "enable_worker", False)
    with TestClient(app) as c:
        yield c


def test_export_sql_via_api(client):
    resp = client.get("/api/db/export?format=sql")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/sql"
    assert resp.headers["content-disposition"].startswith("attachment")
    assert "CREATE TABLE" in resp.text


def test_export_json_via_api(client):
    resp = client.get("/api/db/export?format=json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    data = resp.json()
    assert isinstance(data, dict)


def test_export_filter_tables_via_api(client):
    resp = client.get("/api/db/export?format=json&tables=app_state,music")
    assert resp.status_code == 200
    data = resp.json()
    assert "app_state" in data
    assert "music" in data
    assert "book" not in data


def test_export_invalid_format_returns_400(client):
    resp = client.get("/api/db/export?format=csv")
    assert resp.status_code == 400


def test_import_sql_overwrite_via_api(client):
    # First get an export
    resp = client.get("/api/db/export?format=sql")
    sql_content = resp.text

    # Modify a value to verify overwrite
    resp = client.post("/api/db/import", files={
        "file": ("dump.sql", sql_content, "application/sql"),
    }, data={"format": "sql", "mode": "overwrite"})
    assert resp.status_code == 200


def test_import_json_merge_via_api(client):
    resp = client.get("/api/db/export?format=json")
    content = json_mod.dumps(resp.json())

    resp = client.post("/api/db/import", files={
        "file": ("dump.json", content, "application/json"),
    }, data={"format": "json", "mode": "merge"})
    assert resp.status_code == 200


def test_import_with_table_filter_via_api(client):
    resp = client.get("/api/db/export?format=json")
    content = json_mod.dumps(resp.json())

    resp = client.post("/api/db/import", files={
        "file": ("dump.json", content, "application/json"),
    }, data={"format": "json", "mode": "overwrite", "tables": "music"})
    assert resp.status_code == 200


def test_import_invalid_file_returns_400(client):
    resp = client.post("/api/db/import", files={
        "file": ("dump.txt", b"invalid", "text/plain"),
    }, data={"format": "sql", "mode": "overwrite"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run route tests to confirm failures**

Run: `python -m pytest tests/test_database_io.py::test_export_sql_via_api -v`
Expected: FAIL with 404 (no route)

- [ ] **Step 3: Write route module**

Create `app/routes/database_io.py`:

```python
"""Database import/export REST API."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.database_io import export_json, export_sql, import_json, import_sql, user_table_names
from app.deps import locked_conn

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_EXTENSIONS = {".sql", ".json"}


@router.get("/api/db/export")
def db_export(
    request: Request,
    format: str = "sql",
    tables: str | None = None,
):
    if format not in ("sql", "json"):
        raise HTTPException(status_code=400, detail="format must be 'sql' or 'json'")
    table_list = tables.split(",") if tables else None
    with locked_conn(request) as conn:
        if format == "sql":
            content = export_sql(conn, tables=table_list)
            return Response(
                content=content,
                media_type="application/sql",
                headers={"Content-Disposition": "attachment; filename=export.sql"},
            )
        else:
            content = export_json(conn, tables=table_list)
            return Response(
                content=json.dumps(content, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=export.json"},
            )


@router.post("/api/db/import")
def db_import(
    request: Request,
    file: UploadFile = File(...),
    format: str = Form("sql"),
    mode: str = Form("overwrite"),
    tables: str | None = Form(None),
):
    if format not in ("sql", "json"):
        raise HTTPException(status_code=400, detail="format must be 'sql' or 'json'")
    if mode not in ("overwrite", "merge"):
        raise HTTPException(status_code=400, detail="mode must be 'overwrite' or 'merge'")

    ext = "." + file.filename.split(".")[-1].lower() if file.filename else ""
    if ext not in _VALID_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"file extension must be .sql or .json, got '{ext}'")

    raw = file.file.read()
    table_list = tables.split(",") if tables else None

    with locked_conn(request) as conn:
        try:
            if format == "sql":
                import_sql(conn, raw.decode("utf-8"), mode=mode, tables=table_list)
            else:
                import_json(conn, json.loads(raw), mode=mode, tables=table_list)
        except Exception as exc:
            logger.exception("db import failed")
            raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "mode": mode}
```

- [ ] **Step 4: Register router in main.py**

Edit `app/main.py`:

Add import line after existing route imports:
```python
from app.routes import books, database_io, downloads, drive, logs, music, patches, photos, queue, video, voices, youtube
```

Add router registration after existing `app.include_router(...)` lines:
```python
app.include_router(database_io.router)
```

- [ ] **Step 5: Run route tests**

Run: `python -m pytest tests/test_database_io.py -v`
Expected: 24 passed (16 core + 8 route)

- [ ] **Step 6: Commit**

```bash
git add app/routes/database_io.py app/main.py tests/test_database_io.py
git commit -m "feat: add database import/export API routes"
```

---

### Task 4: Web UI — template + sidebar

**Files:**
- Create: `app/templates/database_io.html`
- Modify: `app/templates/base.html`

- [ ] **Step 1: Add sidebar link to base.html**

Before the Logs link in `app/templates/base.html`:
```html
<a href="/database-io" {% if request.url.path == '/database-io' %}class="active"{% endif %}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="18" height="18"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
    Database
</a>
```

- [ ] **Step 2: Create the template**

Create `app/templates/database_io.html`:

```html
{% extends "base.html" %}
{% block title %}Database{% endblock %}
{% block content %}
<h2>Database Import / Export</h2>

<div class="card">
    <div class="card-header"><h3 style="margin:0">Export</h3></div>
    <div class="card-body">
        <p style="color:var(--text-muted);margin-bottom:var(--space-sm)">Chọn table và format để tải xuống.</p>
        <div id="table-checkboxes" style="display:flex;flex-wrap:wrap;gap:0.5rem 1rem;margin-bottom:var(--space-md)">
            {% for table in tables %}
            <label style="display:flex;align-items:center;gap:0.3rem;cursor:pointer">
                <input type="checkbox" class="table-cb" value="{{ table }}" checked>
                {{ table }}
            </label>
            {% endfor %}
            <label style="display:flex;align-items:center;gap:0.3rem;cursor:pointer;color:var(--color-primary)">
                <input type="checkbox" id="select-all" checked onchange="document.querySelectorAll('.table-cb').forEach(cb => cb.checked = this.checked)">
                All
            </label>
        </div>
        <div style="display:flex;gap:var(--space-sm);flex-wrap:wrap">
            <button class="btn-youtube" onclick="doExport('sql')">Download SQL</button>
            <button class="btn-outline" onclick="doExport('json')">Download JSON</button>
        </div>
    </div>
</div>

<div class="card">
    <div class="card-header"><h3 style="margin:0">Import</h3></div>
    <div class="card-body">
        <p style="color:var(--text-muted);margin-bottom:var(--space-sm)">Tải lên file .sql hoặc .json để import.</p>
        <form id="import-form" enctype="multipart/form-data" style="display:flex;flex-direction:column;gap:var(--space-sm);max-width:480px">
            <input type="file" name="file" accept=".sql,.json" required style="padding:0.3rem 0">
            <div style="display:flex;gap:var(--space-sm);flex-wrap:wrap">
                <select name="format" required>
                    <option value="sql">SQL</option>
                    <option value="json">JSON</option>
                </select>
                <select name="mode" required>
                    <option value="overwrite">Overwrite</option>
                    <option value="merge">Merge</option>
                </select>
            </div>
            <div style="display:flex;gap:var(--space-sm)">
                <button type="submit" class="btn-youtube">Import</button>
                <span id="import-status" style="align-self:center;color:var(--text-muted);font-size:0.9em"></span>
            </div>
        </form>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
async function doExport(format) {
    const checked = [...document.querySelectorAll('.table-cb:checked')].map(cb => cb.value);
    const tables = checked.length > 0 ? '&tables=' + checked.join(',') : '';
    window.location.href = '/api/db/export?format=' + format + tables;
}

document.getElementById('import-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    const status = document.getElementById('import-status');
    status.textContent = 'Importing...';
    try {
        const resp = await fetch('/api/db/import', { method: 'POST', body: new FormData(this) });
        const data = await resp.json();
        if (resp.ok) {
            status.textContent = 'OK: ' + data.mode;
        } else {
            status.textContent = 'Error: ' + (data.detail || resp.statusText);
        }
    } catch (e) {
        status.textContent = 'Error: ' + e.message;
    }
});
</script>
{% endblock %}
```

- [ ] **Step 3: Add database-io route to serve the page**

In `app/routes/database_io.py`, add a page endpoint:

```python
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


@router.get("/database-io", response_class=HTMLResponse)
def database_io_page(request: Request):
    with locked_conn(request) as conn:
        tables = user_table_names(conn)
    return templates.TemplateResponse(request, "database_io.html", {
        "request": request,
        "tables": tables,
    })
```

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v --timeout=30`
Expected: All existing tests pass

- [ ] **Step 5: Commit**

```bash
git add app/routes/database_io.py app/templates/database_io.html app/templates/base.html
git commit -m "feat: add database import/export web UI"
```

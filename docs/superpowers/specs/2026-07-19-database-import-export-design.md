# Database Import/Export

## Overview

Tính năng cho phép người dùng export và import SQLite database qua REST API và Web UI.

## Requirements

- **Export**: SQL dump format (.sql) và JSON format (.json), có thể chọn table cụ thể
- **Import**: Cả SQL và JSON, hỗ trợ mode overwrite (thay thế) hoặc merge (thêm dữ liệu mới), có thể chọn table
- **UI**: API endpoints + nút trên Web UI (trang Settings riêng)
- **Secrets**: Export tất cả, không loại trừ

## New Files

### `app/database_io.py`

Core export/import logic:

```python
def user_table_names(conn: sqlite3.Connection) -> list[str]:
    """Return list of user table names (excludes sqlite_*)."""

def export_sql(conn: sqlite3.Connection, tables: list[str] | None = None) -> str:
    """Generate SQL dump for given tables (or all user tables)."""

def export_json(conn: sqlite3.Connection, tables: list[str] | None = None) -> dict[str, list[dict]]:
    """Generate {table: [row_dict, ...]} for given tables (or all)."""

def import_sql(conn: sqlite3.Connection, sql: str, mode: str = "overwrite", tables: list[str] | None = None):
    """
    Import from SQL dump.
    - overwrite: clear selected tables, then execute filtered INSERT statements
    - merge: execute INSERT OR IGNORE for selected tables
    - tables: if None, process all tables in the dump
    """

def import_json(conn: sqlite3.Connection, data: dict, mode: str = "overwrite", tables: list[str] | None = None):
    """
    Import from JSON dict.
    - overwrite: clear selected tables, then bulk insert
    - merge: INSERT OR IGNORE per row
    """
```

### `app/routes/database_io.py`

REST API endpoints:

| Method | Path | Params | Description |
|--------|------|--------|-------------|
| GET | `/api/db/export` | `format=sql\|json`, `tables=...` (comma-sep) | Download export file |
| POST | `/api/db/import` | multipart: file + `format` + `mode` + `tables` (comma-sep) | Upload & import |

### `app/templates/database_io.html`

Web UI page with:
- **Export section**: danh sách checkbox các table, nút "Download SQL" / "Download JSON"
- **Import section**: file upload input, dropdown format (SQL/JSON), dropdown mode (overwrite/merge), checkbox chọn table, nút "Import"

## Modified Files

### `app/templates/base.html`

- Thêm link "Database" vào sidebar nav

### `app/main.py`

- Import và include router database_io

## Key Design Decisions

1. **SQL export**: Viết thủ công CREATE TABLE + INSERT thay vì `iterdump()` vì cần filter theo table. Lấy schema từ `sqlite_master` cho từng table được chọn.
2. **Overwrite**: Tắt `PRAGMA foreign_keys` tạm thời, DELETE FROM từng table theo thứ tự reverse-dependency, sau đó INSERT dữ liệu mới.
3. **Merge**: Dùng `INSERT OR IGNORE` — không ghi đè row đã tồn tại (theo PRIMARY KEY).
4. **File response**: Export trả về file attachment với mimetype thích hợp (`application/sql` hoặc `application/json`).

## Import Flow

```
POST /api/db/import (multipart)
  → parse file, detect format from filename/param
  → acquire db_lock
  → call import_sql or import_json with specified mode & tables
  → commit
  → release lock
  → return success/error
```

## Export Flow

```
GET /api/db/export?format=sql&tables=book,chapter
  → acquire db_lock (read-only)
  → call export_sql with selected tables
  → release lock
  → return StreamingResponse with file attachment
```

## Error Handling

- File upload validation: chỉ chấp nhận `.sql` và `.json`
- SQL parse error: báo lỗi dòng bị lỗi, rollback transaction
- JSON format error: validate structure trước khi import
- Mode không hợp lệ: mặc định về overwrite

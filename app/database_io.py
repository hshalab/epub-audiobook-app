"""Import/export SQLite database."""
from __future__ import annotations

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

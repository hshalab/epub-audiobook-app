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

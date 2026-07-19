"""Unit tests for multi-account Google Drive support: email-keyed credential upsert,
round-robin export account selection, import account resolution, and the
patch_export.drive_account_id linkage (see app/google_drive.py, app/repository.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import db, google_drive, repository

_NOW = datetime.now(timezone.utc).isoformat()


def _make_conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


def _add_account(conn, email):
    return google_drive.save_credentials(
        conn,
        access_token="at",
        refresh_token=f"rt-{email}",
        token_expiry=_NOW,
        account_email=email,
    )


def _insert_book_and_patch(conn):
    conn.execute(
        """INSERT INTO book (id, title, original_filename, epub_path, patch_size, status,
                              created_at, updated_at)
           VALUES (1, 't', 'f.epub', '/tmp/f.epub', 10, 'ready', ?, ?)""",
        (_NOW, _NOW),
    )
    cur = conn.execute(
        """INSERT INTO patch (book_id, patch_index, chapter_start, chapter_end, status,
                               created_at, updated_at)
           VALUES (1, 0, 0, 0, 'pending', ?, ?)""",
        (_NOW, _NOW),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_adds_drive_account_id_column():
    conn = db.connect(":memory:")
    # Simulate a pre-multi-account DB: patch_export without the column.
    conn.executescript(
        """CREATE TABLE patch_export (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               patch_id INTEGER NOT NULL,
               drive_folder_id TEXT NOT NULL,
               drive_folder_link TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'exported',
               exported_chunk_count INTEGER NOT NULL DEFAULT 0,
               imported_chunk_count INTEGER NOT NULL DEFAULT 0,
               error_message TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           );"""
    )
    db.init_schema(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(patch_export)")}
    assert "drive_account_id" in cols
    # Idempotent on re-run.
    db.init_schema(conn)


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

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
    names = [c["name"] for c in google_drive.list_clients(conn)]
    assert "A" in names
    assert "B" in names


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


# ---------------------------------------------------------------------------
# save_credentials: multi-row, upsert by email
# ---------------------------------------------------------------------------


def test_save_credentials_adds_rows_per_email():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    assert id_a != id_b
    assert len(google_drive.list_accounts(conn)) == 2


def test_save_credentials_reconnect_same_email_updates_in_place():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    _add_account(conn, "b@example.com")
    id_a2 = google_drive.save_credentials(
        conn, access_token="at2", refresh_token="rt2", token_expiry=_NOW,
        account_email="a@example.com",
    )
    assert id_a2 == id_a
    accounts = google_drive.list_accounts(conn)
    assert len(accounts) == 2
    row_a = google_drive.get_account(conn, id_a)
    assert row_a["refresh_token"] == "rt2"


def test_save_credentials_empty_email_always_inserts():
    conn = _make_conn()
    id1 = _add_account(conn, "")
    id2 = _add_account(conn, "")
    assert id1 != id2
    assert len(google_drive.list_accounts(conn)) == 2


def test_delete_credentials_removes_only_that_account():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    google_drive.delete_credentials(conn, id_a)
    remaining = google_drive.list_accounts(conn)
    assert [a["id"] for a in remaining] == [id_b]
    assert google_drive.any_account_connected(conn)
    google_drive.delete_credentials(conn, id_b)
    assert not google_drive.any_account_connected(conn)


# ---------------------------------------------------------------------------
# pick_export_account: round-robin
# ---------------------------------------------------------------------------


def test_round_robin_cycles_through_accounts():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    id_c = _add_account(conn, "c@example.com")
    picked = [google_drive.pick_export_account(conn)["id"] for _ in range(4)]
    assert picked == [id_a, id_b, id_c, id_a]
    assert repository.get_app_state(conn, "drive.rr_last_account_id") == str(id_a)


def test_round_robin_skips_deleted_account():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    id_c = _add_account(conn, "c@example.com")
    assert google_drive.pick_export_account(conn)["id"] == id_a
    google_drive.delete_credentials(conn, id_b)
    assert google_drive.pick_export_account(conn)["id"] == id_c
    assert google_drive.pick_export_account(conn)["id"] == id_a


def test_round_robin_pointer_at_deleted_account_self_heals():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    google_drive.pick_export_account(conn)  # pointer -> a
    google_drive.pick_export_account(conn)  # pointer -> b
    google_drive.delete_credentials(conn, id_b)
    # Pointer is at the deleted id; next pick wraps to the first account.
    assert google_drive.pick_export_account(conn)["id"] == id_a


def test_round_robin_single_account():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    assert google_drive.pick_export_account(conn)["id"] == id_a
    assert google_drive.pick_export_account(conn)["id"] == id_a


def test_round_robin_no_accounts_raises():
    conn = _make_conn()
    with pytest.raises(ValueError):
        google_drive.pick_export_account(conn)


# ---------------------------------------------------------------------------
# resolve_import_account
# ---------------------------------------------------------------------------


def test_resolve_import_account_explicit_id():
    conn = _make_conn()
    _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")
    assert google_drive.resolve_import_account(conn, id_b)["id"] == id_b


def test_resolve_import_account_dangling_id_raises():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    google_drive.delete_credentials(conn, id_a)
    _add_account(conn, "b@example.com")
    with pytest.raises(ValueError, match="disconnected"):
        google_drive.resolve_import_account(conn, id_a)


def test_resolve_import_account_null_falls_back_to_oldest():
    conn = _make_conn()
    id_a = _add_account(conn, "a@example.com")
    _add_account(conn, "b@example.com")
    assert google_drive.resolve_import_account(conn, None)["id"] == id_a


def test_resolve_import_account_null_no_accounts_raises():
    conn = _make_conn()
    with pytest.raises(ValueError):
        google_drive.resolve_import_account(conn, None)


# ---------------------------------------------------------------------------
# patch_export linkage
# ---------------------------------------------------------------------------


def test_create_patch_export_records_account_and_joins_email():
    conn = _make_conn()
    patch_id = _insert_book_and_patch(conn)
    id_a = _add_account(conn, "a@example.com")

    export = repository.create_patch_export(
        conn, patch_id, "fid", "https://link", 3, drive_account_id=id_a
    )
    assert export.drive_account_id == id_a

    listed = repository.list_patch_exports(conn, patch_id)
    assert listed[0].account_email == "a@example.com"

    all_exports = repository.list_all_patch_exports(conn)
    assert all_exports[0]["account_email"] == "a@example.com"


def test_create_patch_export_without_account_is_legacy_null():
    conn = _make_conn()
    patch_id = _insert_book_and_patch(conn)
    export = repository.create_patch_export(conn, patch_id, "fid", "https://link", 3)
    assert export.drive_account_id is None
    listed = repository.list_patch_exports(conn, patch_id)
    assert listed[0].account_email is None


def test_count_pending_exports_for_account():
    conn = _make_conn()
    patch_id = _insert_book_and_patch(conn)
    id_a = _add_account(conn, "a@example.com")
    id_b = _add_account(conn, "b@example.com")

    e1 = repository.create_patch_export(conn, patch_id, "f1", "l1", 3, drive_account_id=id_a)
    repository.create_patch_export(conn, patch_id, "f2", "l2", 3, drive_account_id=id_a)
    repository.create_patch_export(conn, patch_id, "f3", "l3", 3, drive_account_id=id_b)

    assert repository.count_pending_exports_for_account(conn, id_a) == 2
    assert repository.count_pending_exports_for_account(conn, id_b) == 1

    repository.update_patch_export(conn, e1.id, status="imported", imported_chunk_count=3)
    assert repository.count_pending_exports_for_account(conn, id_a) == 1


# ---------------------------------------------------------------------------
# Route-level client CRUD tests
# ---------------------------------------------------------------------------

import threading

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    settings_mod = __import__("app.config", fromlist=["settings"])
    monkeypatch.setattr(settings_mod.settings, "db_path", str(db_path))
    monkeypatch.setattr(settings_mod.settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings_mod.settings, "enable_worker", False)
    monkeypatch.setattr(settings_mod.settings, "google_drive_client_id", "test-client-id")
    monkeypatch.setattr(settings_mod.settings, "google_drive_client_secret", "test-client-secret")
    with TestClient(app) as c:
        yield c


def test_create_client_via_api(client):
    resp = client.post("/drive/clients", data={
        "name": "Route Test Client",
        "client_id": "route-test-id",
        "client_secret": "route-test-secret",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location", "").startswith("/drive")


def test_connect_with_nonexistent_client_returns_404(client):
    resp = client.get("/drive/connect?oauth_client_id=999999", follow_redirects=False)
    assert resp.status_code == 404


def test_connect_with_valid_client_redirects_to_google(client):
    client.post("/drive/clients", data={
        "name": "OAuth Test", "client_id": "oauth-test-id", "client_secret": "oauth-test-secret",
    })
    resp = client.get("/drive/connect?oauth_client_id=1", follow_redirects=False)
    # RedirectResponse defaults to 307, older redirects use 302/303
    assert resp.status_code in (302, 303, 307)
    location = resp.headers.get("location", "")
    assert "accounts.google.com" in location or "google.com/o/oauth2" in location


# ---------------------------------------------------------------------------
# Client + OAuth credential integration
# ---------------------------------------------------------------------------


def test_save_credentials_with_oauth_client_id():
    conn = _make_conn()
    cid = google_drive.create_client(conn, "Test", "tid", "ts")
    aid = google_drive.save_credentials(
        conn, access_token="at", refresh_token="rt", token_expiry=_NOW,
        account_email="a@example.com", oauth_client_id=cid,
    )
    row = google_drive.get_account(conn, aid)
    assert row["oauth_client_id"] == cid


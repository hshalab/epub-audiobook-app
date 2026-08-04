from datetime import datetime, timezone
import threading

import pytest
from fastapi.testclient import TestClient

from app import db
from app.jobqueue import store


@pytest.fixture
def client(tmp_path):
    from app.config import settings
    from app.main import app
    settings.db_path = str(tmp_path / "flows.db")
    settings.enable_worker = False
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    with TestClient(app) as c:
        app.state.conn = conn
        app.state.db_lock = threading.Lock()
        yield c, conn, None


def _book_and_patches(conn):
    now = datetime.now(timezone.utc).isoformat()
    book = conn.execute(
        """INSERT INTO book(title,original_filename,epub_path,status,created_at,updated_at)
           VALUES('Book','b.epub','b.epub','ready',?,?)""", (now, now)).lastrowid
    ids = []
    for index in range(2):
        ids.append(conn.execute(
            """INSERT INTO patch(book_id,patch_index,chapter_start,chapter_end,status,created_at,updated_at)
               VALUES(?,?,0,0,'pending',?,?)""", (book, index, now, now)).lastrowid)
    conn.commit()
    return book, ids


def test_flow_run_expands_each_patch_node_into_dependent_jobs(client):
    c, conn, _ = client
    book, patches = _book_and_patches(conn)
    created = c.post("/flows/api", json={"name": "Publish", "nodes": ["audio", "video", "youtube"]})
    assert created.status_code == 201
    flow_id = created.json()["id"]
    response = c.post(f"/flows/{flow_id}/runs", json={
        "book_id": book, "patch_ids": patches, "privacy": "unlisted",
    })
    assert response.status_code == 201
    assert response.json()["job_count"] == 6
    jobs = sorted(store.list_jobs(conn), key=lambda job: job.id)
    assert [job.node_id for job in jobs] == ["audio", "video", "youtube"] * 2
    assert all(job.flow_run_id == response.json()["run_id"] for job in jobs)
    assert store.claim(conn, "flow_video", "worker") is None
    store.finish(conn, jobs[0].id)
    assert store.claim(conn, "flow_video", "worker").id == jobs[1].id


def test_deleting_upstream_pending_job_cancels_direct_dependent(client):
    _, conn, _ = client
    first = store.enqueue(conn, "flow_audio")
    second = store.enqueue(conn, "flow_video", depends_on=first)
    assert store.delete_pending(conn, first)
    assert store.get(conn, second).status == "cancelled"


def test_local_bridge_reports_native_capabilities(client):
    response = client[0].get("/local-bridge/health")
    assert response.status_code == 200
    assert "pick-files" in response.json()["capabilities"]

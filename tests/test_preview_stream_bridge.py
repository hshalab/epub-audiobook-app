"""preview-stream giờ enqueue job rồi forward @@EVENT — hợp đồng SSE cũ không đổi."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.jobqueue import store
from app.jobqueue.joblog import JobLogger


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "enable_worker", False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.main import app
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "app.db"))
    conn = db.connect(str(tmp_path / "app.db")); db.init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO book (id,title,original_filename,epub_path,patch_size,status,created_at,updated_at) VALUES (1,'Sách','a.epub','/tmp/a.epub',10,'ready',?,?)", (now, now))
    conn.execute("INSERT INTO patch (id,book_id,patch_index,chapter_start,chapter_end,status,attempt_count,created_at,updated_at) VALUES (5,1,0,0,0,'pending',0,?,?)", (now, now)); conn.commit()
    app.state.conn = conn; app.state.db_lock = threading.Lock(); app.state.worker = None; app.state.job_queue = None
    with TestClient(app) as c: yield c, conn


def _payloads(resp):
    return [json.loads(line[len("data: "):]) for line in resp.iter_lines() if line.startswith("data: ")]


def _finish_next_job(db_path):
    conn = db.connect(str(db_path))
    for _ in range(100):
        jobs = store.list_jobs(conn, job_type="light_tts")
        if jobs:
            store.finish(conn, jobs[0].id, {"ok": 0})
            conn.close()
            return
        threading.Event().wait(0.01)
    conn.close()
    pytest.fail("preview stream did not enqueue a job")


def test_preview_stream_enqueues_a_light_tts_job(client, monkeypatch):
    c, conn = client
    original_enqueue = store.enqueue

    def enqueue_and_finish(*args, **kwargs):
        job_id = original_enqueue(*args, **kwargs)
        store.finish(conn, job_id, {"ok": 0})
        return job_id

    monkeypatch.setattr(store, "enqueue", enqueue_and_finish)
    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream?voice=v1") as resp:
        assert resp.status_code == 200
    jobs = store.list_jobs(conn, job_type="light_tts")
    assert len(jobs) == 1 and jobs[0].payload["patch_id"] == 5 and jobs[0].payload["voice"] == "v1" and jobs[0].dedupe_key == "light_tts:patch=5"


def test_a_second_request_attaches_to_the_same_job(client, monkeypatch):
    c, conn = client
    job_id = store.enqueue(conn, "light_tts", payload={"patch_id": 5}, book_id=1, dedupe_key="light_tts:patch=5")
    job = store.get(conn, job_id)
    store.finish(conn, job_id, {"ok": 0})
    monkeypatch.setattr(store, "find_live_by_dedupe", lambda _conn, _key: job)
    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp:
        assert resp.status_code == 200
    assert len(store.list_jobs(conn, job_type="light_tts")) == 1


def test_events_written_by_the_handler_reach_the_client(client):
    c, conn = client
    job_id = store.enqueue(conn, "light_tts", payload={"patch_id": 5}, book_id=1, dedupe_key="light_tts:patch=5")
    assert store.claim(conn, "light_tts", "bridge-test") is not None
    result = {}

    def consume():
        with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp:
            result["status"] = resp.status_code
            result["payloads"] = _payloads(resp)

    thread = threading.Thread(target=consume)
    thread.start()
    time.sleep(0.1)
    lg = JobLogger(job_id, "light_tts")
    lg.emit({"type":"chunk","index":0,"total":2,"url":"/u/0"})
    lg.emit({"type":"chunk","index":1,"total":2,"url":"/u/1"})
    lg.emit({"type":"done","saved":True,"complete":True,"ok":2,"failed":0})
    lg.close()
    store.finish(conn, job_id, {"ok":2})
    thread.join(5)
    assert not thread.is_alive()
    assert result["status"] == 200
    payloads = result["payloads"]
    assert [p["type"] for p in payloads] == ["chunk", "chunk", "done"] and payloads[0]["url"] == "/u/0"


def test_a_failed_job_closes_the_stream_with_an_error(client, monkeypatch):
    c, conn = client
    job_id = store.enqueue(conn, "light_tts", payload={"patch_id": 5}, book_id=1, dedupe_key="light_tts:patch=5")
    store.claim(conn, "light_tts", "w"); store.fail(conn, job_id, "engine không khả dụng", fatal=True)
    monkeypatch.setattr(store, "find_live_by_dedupe", lambda _conn, _key: store.get(conn, job_id))
    with c.stream("GET", "/books/1/text-studio/patches/5/preview-stream") as resp: payloads = _payloads(resp)
    assert payloads[-1]["type"] == "error" and "engine không khả dụng" in payloads[-1]["message"]


def test_unknown_patch_still_404s(client):
    c, _ = client
    with c.stream("GET", "/books/1/text-studio/patches/999/preview-stream") as resp: assert resp.status_code == 404

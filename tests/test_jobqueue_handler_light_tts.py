"""Handler light_tts: emit đúng hợp đồng SSE, dùng lại chunk, không gộp khi có lỗ."""
from __future__ import annotations

from datetime import datetime, timezone
import io

import numpy as np
import pytest
import soundfile as sf

from app import db, repository
from app.jobqueue import joblog, store
from app.jobqueue.context import JobContext
from app.jobqueue.joblog import JobLogger
from app.jobqueue.handlers import light_tts as handler
from app.jobqueue.models import JobFatalError


def _wav_bytes(seconds=0.1, sr=24000):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(sr * seconds), dtype="float32"), sr, format="WAV")
    return buf.getvalue()


class _FakeEngine:
    def __init__(self, fail_indices=()):
        self.fail_indices = set(fail_indices)
        self.calls = 0

    def synthesize_to_wav_bytes(self, text, voice=None):
        index = self.calls
        self.calls += 1
        if index in self.fail_indices:
            raise RuntimeError(f"chunk {index} hỏng")
        return _wav_bytes(), 24000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "tts_max_chars", 10)
    monkeypatch.setattr(settings, "light_tts_chunk_retries", 1)


def _book_with_patch(conn, text="Câu một rất dài. Câu hai rất dài. Câu ba rất dài."):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO book (id,title,original_filename,epub_path,patch_size,status,created_at,updated_at) VALUES (1,'Sách','a.epub','/tmp/a.epub',10,'ready',?,?)", (now, now))
    conn.execute("INSERT INTO chapter (book_id,chapter_index,title,text,char_count) VALUES (1,0,'C1',?,?)", (text, len(text)))
    cur = conn.execute("INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,status,attempt_count,created_at,updated_at) VALUES (1,0,0,0,'pending',0,?,?)", (now, now))
    conn.commit()
    return cur.lastrowid


def _ctx(conn, patch_id, **extra):
    payload = {"patch_id": patch_id, "book_id": 1}
    payload.update(extra)
    job_id = store.enqueue(conn, "light_tts", payload=payload, book_id=1)
    job = store.claim(conn, "light_tts", "light_tts#1")
    return JobContext(job, conn, JobLogger(job_id, "light_tts"), lambda: False), job_id


def test_dedupe_key_shape():
    assert handler.dedupe_key(91) == "light_tts:patch=91"


def test_missing_patch_is_fatal(tmp_path):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    ctx, _ = _ctx(conn, 999)
    with pytest.raises(JobFatalError): handler.handle(ctx)


def test_emits_one_chunk_event_per_chunk_then_done(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine())
    ctx, job_id = _ctx(conn, patch_id); handler.handle(ctx); ctx.close()
    events, _ = joblog.read_events(job_id)
    chunks = [e for e in events if e["type"] == "chunk"]
    done = [e for e in events if e["type"] == "done"]
    assert chunks and all({"index", "total", "url"} <= set(e) for e in chunks)
    assert len(done) == 1 and done[0]["saved"] and done[0]["complete"] and done[0]["failed"] == 0


def test_a_failed_chunk_blocks_the_merge(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine(fail_indices={1}))
    ctx, job_id = _ctx(conn, patch_id, max_chars=10); result = handler.handle(ctx); ctx.close()
    events, _ = joblog.read_events(job_id)
    assert any(e["type"] == "chunk_error" and e["index"] == 1 for e in events)
    done = [e for e in events if e["type"] == "done"][0]
    assert not done["saved"] and not done["complete"] and result["failed"] == 1
    assert repository.get_patch(conn, patch_id).status != "done"


def test_all_chunks_failing_emits_an_error_event(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine(fail_indices=range(50)))
    ctx, job_id = _ctx(conn, patch_id); handler.handle(ctx); ctx.close()
    events, _ = joblog.read_events(job_id)
    assert any(e["type"] == "error" for e in events)


def test_existing_chunks_are_reused_and_flagged(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn); engine = _FakeEngine()
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: engine)
    ctx, _ = _ctx(conn, patch_id); handler.handle(ctx); first = engine.calls
    ctx2, job2 = _ctx(conn, patch_id); handler.handle(ctx2); ctx2.close()
    assert engine.calls == first
    events, _ = joblog.read_events(job2)
    assert all(e.get("reused") for e in events if e["type"] == "chunk")


def test_changing_the_voice_invalidates_the_reuse_marker(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn); engine = _FakeEngine()
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: engine)
    ctx, _ = _ctx(conn, patch_id, voice="vi-VN-NamMinhNeural"); handler.handle(ctx); first = engine.calls
    ctx2, _ = _ctx(conn, patch_id, voice="vi-VN-HoaiMyNeural"); handler.handle(ctx2)
    assert engine.calls > first


def test_progress_tracks_chunks(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    patch_id = _book_with_patch(conn)
    monkeypatch.setattr(handler, "_build_engine", lambda b, v: _FakeEngine())
    ctx, job_id = _ctx(conn, patch_id); handler.handle(ctx); ctx.flush()
    job = store.get(conn, job_id)
    assert job.progress_total > 0 and job.progress_current == job.progress_total

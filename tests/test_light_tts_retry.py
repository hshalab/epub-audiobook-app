"""LightTTS chunk resilience: per-chunk retries, persisted chunks, resume-on-rerun.

Covers _synth_chunk_with_retries and the preview-stream route's new semantics:
chunks persist in {patch_id}_chunks, failed runs don't mark the patch done, and
a re-run only synthesizes the still-missing indices.
"""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import repository
from app.config import settings
from app.db import connect, init_schema
from app.routes import text_studio as ts


def _wav_bytes(seconds: float = 0.05, sr: int = 22050) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(sr * seconds), dtype=np.float32), sr, format="WAV")
    return buf.getvalue()


class FlakyEngine:
    """Fails specific chunk texts a set number of times, then succeeds."""

    def __init__(self, fail_counts: dict[str, int] | None = None, fail_forever: set[str] | None = None):
        self.fail_counts = dict(fail_counts or {})
        self.fail_forever = set(fail_forever or ())
        self.calls: list[str] = []

    def synthesize_to_wav_bytes(self, text, voice=None):
        self.calls.append(text)
        if text in self.fail_forever:
            raise RuntimeError("permanent failure")
        remaining = self.fail_counts.get(text, 0)
        if remaining > 0:
            self.fail_counts[text] = remaining - 1
            raise RuntimeError("transient failure")
        return _wav_bytes(), 22050


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ts.time, "sleep", lambda *_: None)


class TestSynthChunkWithRetries:
    def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 3)
        engine = FlakyEngine(fail_counts={"xin chào": 2})
        wav = ts._synth_chunk_with_retries(engine, "xin chào")
        assert wav.startswith(b"RIFF")
        assert len(engine.calls) == 3

    def test_raises_after_exhausting_attempts(self, monkeypatch):
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 3)
        engine = FlakyEngine(fail_forever={"hỏng"})
        with pytest.raises(RuntimeError, match="permanent failure"):
            ts._synth_chunk_with_retries(engine, "hỏng")
        assert len(engine.calls) == 3

    def test_zero_config_still_tries_once(self, monkeypatch):
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 0)
        engine = FlakyEngine()
        wav = ts._synth_chunk_with_retries(engine, "một lần")
        assert wav.startswith(b"RIFF")
        assert len(engine.calls) == 1


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    c = connect(str(tmp_path / "test.db"))
    init_schema(c)
    app.state.conn = c
    app.state.db_lock = threading.Lock()
    yield TestClient(app)
    c.close()


@pytest.fixture()
def book_and_patch(client):
    from app.epub_parser import ParsedChapter

    conn = client.app.state.conn
    ch = ParsedChapter(title="Ch1", text="Câu thứ nhất. Câu thứ hai. Câu thứ ba.")
    book = repository.create_book(
        conn, title="Test", original_filename="t.epub", epub_path="/tmp/t.epub",
        patch_size=1, chapters=[ch], background_image_path=None,
    )
    repository.rebuild_patches(conn, book.id, [(0, 0)])
    patch = repository.list_patches(conn, book.id)[0]
    return book, patch


def _stream_events(client, book, patch, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/books/{book.id}/text-studio/patches/{patch.id}/preview-stream"
    if qs:
        url += f"?{qs}"
    events = []
    with client.stream("GET", url) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def _use_engine(monkeypatch, engine):
    monkeypatch.setattr(ts, "LightTTSEngine", lambda *a, **kw: engine)


class TestPreviewStreamResilience:
    def test_transient_failure_retried_within_run(self, client, book_and_patch, monkeypatch):
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 3)
        # Every chunk fails once, then succeeds — retries absorb it in one run.
        conn = client.app.state.conn
        from app.chunker import split_into_tts_chunks
        text = repository.get_effective_patch_text(conn, repository.get_patch(conn, patch.id))
        chunks = split_into_tts_chunks(text, max_chars=settings.tts_max_chars)
        engine = FlakyEngine(fail_counts={c: 1 for c in chunks})
        _use_engine(monkeypatch, engine)

        events = _stream_events(client, book, patch)
        done = events[-1]
        assert done["type"] == "done"
        assert done["complete"] is True and done["failed"] == 0
        assert repository.get_patch(conn, patch.id).status == "done"

    def test_incomplete_run_persists_chunks_and_does_not_mark_done(
        self, client, book_and_patch, monkeypatch
    ):
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 2)
        conn = client.app.state.conn
        from app.chunker import split_into_tts_chunks
        text = repository.get_effective_patch_text(conn, repository.get_patch(conn, patch.id))
        chunks = split_into_tts_chunks(text, max_chars=20)
        assert len(chunks) >= 2, "test text must split into multiple chunks"

        # First run: last chunk fails permanently.
        engine = FlakyEngine(fail_forever={chunks[-1]})
        _use_engine(monkeypatch, engine)
        events = _stream_events(client, book, patch, max_chars=20)
        done = events[-1]
        assert done["type"] == "done"
        assert done["complete"] is False and done["failed"] == 1
        assert repository.get_patch(conn, patch.id).status != "done"

        chunk_dir = Path(settings.data_root) / "books" / str(book.id) / "patches" / f"{patch.id}_chunks"
        ok_files = sorted(p.name for p in chunk_dir.glob("chunk_*.wav"))
        assert len(ok_files) == len(chunks) - 1
        patch_wav = chunk_dir.parent / f"{patch.id}.wav"
        assert not patch_wav.exists(), "partial audio must not be merged/saved"
        # Only the last chunk is missing, so the contiguous prefix (persisted for
        # the progress column) is every chunk before it.
        assert repository.get_patch(conn, patch.id).next_chunk_index == len(chunks) - 1

        # Second run: engine healthy — only the missing chunk is synthesized.
        engine2 = FlakyEngine()
        _use_engine(monkeypatch, engine2)
        events2 = _stream_events(client, book, patch, max_chars=20)
        done2 = events2[-1]
        assert done2["type"] == "done"
        assert done2["complete"] is True
        assert engine2.calls == [chunks[-1]], "reused chunks must not be re-synthesized"
        reused = [e for e in events2 if e.get("reused")]
        assert len(reused) == len(chunks) - 1
        assert repository.get_patch(conn, patch.id).status == "done"
        assert patch_wav.exists()

    def test_progress_reflects_contiguous_prefix_not_total_present(
        self, client, book_and_patch, monkeypatch
    ):
        # A gap in the middle: chunk 1 fails but chunks 0 and 2 succeed. The
        # persisted progress must be the contiguous prefix (1), not the count of
        # chunks present (2), matching the worker's next_chunk_index semantics.
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 1)
        conn = client.app.state.conn
        from app.chunker import split_into_tts_chunks
        text = repository.get_effective_patch_text(conn, repository.get_patch(conn, patch.id))
        chunks = split_into_tts_chunks(text, max_chars=20)
        assert len(chunks) >= 3, "test text must split into at least three chunks"

        engine = FlakyEngine(fail_forever={chunks[1]})
        _use_engine(monkeypatch, engine)
        events = _stream_events(client, book, patch, max_chars=20)
        assert events[-1]["complete"] is False
        assert repository.get_patch(conn, patch.id).next_chunk_index == 1

    def test_settings_change_invalidates_persisted_chunks(
        self, client, book_and_patch, monkeypatch
    ):
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 1)
        engine = FlakyEngine()
        _use_engine(monkeypatch, engine)
        _stream_events(client, book, patch, voice="voice-a")
        first_calls = len(engine.calls)
        assert first_calls > 0

        # Same voice again: everything reused, no new synth calls.
        _stream_events(client, book, patch, voice="voice-a")
        assert len(engine.calls) == first_calls

        # Different voice: meta mismatch → full re-synthesis.
        _stream_events(client, book, patch, voice="voice-b")
        assert len(engine.calls) == first_calls * 2

    def test_all_chunks_failed_yields_error(self, client, book_and_patch, monkeypatch):
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 1)

        class AlwaysFail:
            def synthesize_to_wav_bytes(self, text, voice=None):
                raise RuntimeError("engine down")

        _use_engine(monkeypatch, AlwaysFail())
        events = _stream_events(client, book, patch)
        assert events[-1]["type"] == "error"
        conn = client.app.state.conn
        assert repository.get_patch(conn, patch.id).status != "done"

    def test_chunk_audio_route_serves_persisted_chunk(self, client, book_and_patch, monkeypatch):
        book, patch = book_and_patch
        monkeypatch.setattr(settings, "light_tts_chunk_retries", 1)
        _use_engine(monkeypatch, FlakyEngine())
        _stream_events(client, book, patch)
        resp = client.get(f"/books/{book.id}/patches/{patch.id}/chunk-audio/0")
        assert resp.status_code == 200
        assert resp.content.startswith(b"RIFF")
        resp = client.get(f"/books/{book.id}/patches/{patch.id}/chunk-audio/999")
        assert resp.status_code == 404

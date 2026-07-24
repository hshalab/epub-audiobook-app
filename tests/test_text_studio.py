"""Tests for Text Studio: text analysis, routes, and clean text persistence."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import repository, text_analysis
from app.db import connect, init_schema


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = connect(db_path)
    init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def book_and_patch(conn):
    from app.epub_parser import ParsedChapter
    ch = ParsedChapter(title="Ch1", text="Trời tối dần. Cô ấy khóc nức nở bên cửa sổ.\n\nĐoạn văn thứ hai.")
    book = repository.create_book(
        conn, title="Test", original_filename="t.epub", epub_path="/tmp/t.epub",
        patch_size=2, chapters=[ch], background_image_path=None,
    )
    repository.rebuild_patches(conn, book.id, [(0, 0)])
    patches = repository.list_patches(conn, book.id)
    return book, patches[0]


class TestTextAnalysis:
    def test_junk_detection(self):
        warnings = text_analysis.analyze_text("Hello @@world ##test")
        kinds = [w["kind"] for w in warnings]
        assert "junk" in kinds

    def test_effect_marker_detection(self):
        warnings = text_analysis.analyze_text("Cô ấy [tiếng khóc] rồi [tiếng hét]")
        markers = [w for w in warnings if w["kind"] == "effect_marker"]
        assert len(markers) == 2

    def test_sound_desc_detection_vi(self):
        warnings = text_analysis.analyze_text("Cô ấy khóc nức nở bên cửa sổ rồi thở dài")
        sounds = [w for w in warnings if w["kind"] == "sound_desc"]
        assert len(sounds) >= 2
        originals = [w["original"] for w in sounds]
        assert any("khóc" in o for o in originals)
        assert any("thở dài" in o for o in originals)

    def test_sound_desc_detection_en(self):
        warnings = text_analysis.analyze_text("She was sobbing and screaming loudly")
        sounds = [w for w in warnings if w["kind"] == "sound_desc"]
        assert len(sounds) >= 2

    def test_sound_desc_detection_impact(self):
        warnings = text_analysis.analyze_text("Rầm! Cánh cửa đóng sầm lại")
        sounds = [w for w in warnings if w["kind"] == "sound_desc"]
        assert len(sounds) >= 1

    def test_sound_desc_no_false_positive(self):
        warnings = text_analysis.analyze_text("Trời tối dần. Cô ấy đi về nhà một mình.")
        sounds = [w for w in warnings if w["kind"] == "sound_desc"]
        assert len(sounds) == 0

    def test_sound_desc_interjections(self):
        warnings = text_analysis.analyze_text("Ha ha ha! Cô ấy hi hi cười rồi hừ hừ tức giận")
        sounds = [w for w in warnings if w["kind"] == "sound_desc"]
        originals = [w["original"] for w in sounds]
        assert any("ha ha" in o.lower() for o in originals)
        assert any("hi hi" in o.lower() for o in originals)
        assert any("hừ hừ" in o.lower() for o in originals)

    def test_chapter_title_normalization(self):
        from app.normalization import normalize_chapter_titles
        text = "Chương 1\n\nTrời tối dần. Cô ấy đi về nhà."
        result = normalize_chapter_titles(text)
        assert "chương một" in result.lower() or "một" in result
        assert "..." in result

    def test_chapter_title_with_number(self):
        from app.normalization import normalize_chapter_titles
        text = "Chương 12\n\nNội dung ở đây."
        result = normalize_chapter_titles(text)
        assert "mười hai" in result.lower()

    def test_empty_text(self):
        assert text_analysis.analyze_text("") == []

    def test_clean_text_no_warnings(self):
        warnings = text_analysis.analyze_text("Trời tối dần. Cô ấy đi về nhà một mình.")
        assert len(warnings) == 0

    def test_text_hash_deterministic(self):
        h1 = text_analysis.text_hash("hello")
        h2 = text_analysis.text_hash("hello")
        assert h1 == h2

    def test_text_hash_different(self):
        h1 = text_analysis.text_hash("hello")
        h2 = text_analysis.text_hash("world")
        assert h1 != h2


class TestCleanTextPersistence:
    def test_save_and_get_clean_text(self, conn, book_and_patch):
        book, patch = book_and_patch
        assert patch.clean_text is None
        repository.save_patch_clean_text(conn, patch.id, "Clean text here")
        updated = repository.get_patch(conn, patch.id)
        assert updated.clean_text == "Clean text here"
        assert updated.clean_text_hash is not None

    def test_get_effective_falls_back(self, conn, book_and_patch):
        book, patch = book_and_patch
        text = repository.get_effective_patch_text(conn, patch)
        assert len(text) > 0

    def test_get_effective_uses_clean(self, conn, book_and_patch):
        book, patch = book_and_patch
        repository.save_patch_clean_text(conn, patch.id, "Edited text")
        updated = repository.get_patch(conn, patch.id)
        assert repository.get_effective_patch_text(conn, updated) == "Edited text"

    def test_reset_clean_text(self, conn, book_and_patch):
        book, patch = book_and_patch
        repository.save_patch_clean_text(conn, patch.id, "Edited")
        repository.reset_patch_clean_text(conn, patch.id)
        updated = repository.get_patch(conn, patch.id)
        assert updated.clean_text is None


class TestPatchWarnings:
    def test_save_and_list_warnings(self, conn, book_and_patch):
        book, patch = book_and_patch
        warnings = [{"kind": "junk", "position": 0, "length": 2, "original": "@@", "suggestion": ""}]
        repository.save_patch_warnings(conn, patch.id, warnings)
        loaded = repository.list_patch_warnings(conn, patch.id)
        assert len(loaded) == 1
        assert loaded[0]["kind"] == "junk"

    def test_update_warning_status(self, conn, book_and_patch):
        book, patch = book_and_patch
        warnings = [{"kind": "junk", "position": 0, "length": 2, "original": "@@", "suggestion": ""}]
        repository.save_patch_warnings(conn, patch.id, warnings)
        loaded = repository.list_patch_warnings(conn, patch.id)
        repository.update_patch_warning_status(conn, loaded[0]["id"], 1)
        updated = repository.list_patch_warnings(conn, patch.id)
        assert updated[0]["accepted"] == 1


class TestTextStudioRoutes:
    @pytest.fixture()
    def client(self, tmp_path):
        from app.main import app
        from app import db
        c = db.connect(str(tmp_path / "test.db"))
        db.init_schema(c)
        app.state.conn = c
        import threading
        app.state.db_lock = threading.Lock()
        yield TestClient(app)
        c.close()

    def test_page_loads(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.get(f"/books/{book.id}/text-studio")
        assert resp.status_code == 200
        assert "Text Studio" in resp.text

    def test_get_patch_text(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.get(f"/books/{book.id}/text-studio/patches/{patch.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data

    def test_save_patch_text(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.put(
            f"/books/{book.id}/text-studio/patches/{patch.id}",
            json={"text": "New text"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_analyze_patch(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(f"/books/{book.id}/text-studio/patches/{patch.id}/analyze")
        assert resp.status_code == 200
        assert "warnings" in resp.json()

    def test_replace(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/replace",
            json={"search": "Trời", "replace": "Trời đêm"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["replacements"] == 1
        assert "Trời đêm" in data["text"]

    def test_reset(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        repository.save_patch_clean_text(conn, patch.id, "Edited")
        resp = client.post(f"/books/{book.id}/text-studio/patches/{patch.id}/reset")
        assert resp.status_code == 200

    def test_list_backends(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.get("/text-studio/light-tts/backends")
        assert resp.status_code == 200
        data = resp.json()
        assert "backends" in data
        assert len(data["backends"]) > 0
        assert all("id" in b and "label" in b and "available" in b for b in data["backends"])

    def test_preview_paragraph_empty_text(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/preview-paragraph",
            json={"text": ""},
        )
        assert resp.status_code == 400

    def test_preview_patch_not_found(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/99999/preview-patch",
            json={},
        )
        assert resp.status_code == 404

    def test_preview_unavailable(self, client, conn, book_and_patch):
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/preview-paragraph",
            json={"text": "Hello world", "backend": "nonexistent-backend"},
        )
        assert resp.status_code == 503

    def test_list_voices_endpoint(self, client, monkeypatch):
        import app.light_tts as lt
        monkeypatch.setattr(lt, "list_voices", lambda b: [{"id": "x", "label": "X", "language": "vi"}])
        resp = client.get("/text-studio/light-tts/voices?backend=edge-tts")
        assert resp.status_code == 200
        assert resp.json()["voices"][0]["id"] == "x"

    def test_list_voices_unknown_backend(self, client):
        resp = client.get("/text-studio/light-tts/voices?backend=nope")
        assert resp.status_code == 400

    def test_backends_excludes_kokoro(self, client):
        resp = client.get("/text-studio/light-tts/backends")
        ids = [b["id"] for b in resp.json()["backends"]]
        assert "kokoro" not in ids

    def test_preview_paragraph_forwards_voice(self, client, conn, book_and_patch, monkeypatch):
        import app.routes.text_studio as ts
        book, patch = book_and_patch
        app = client.app
        app.state.conn = conn
        import threading
        app.state.db_lock = threading.Lock()

        captured = {}

        class FakeEngine:
            def synthesize_to_wav_bytes(self, text, voice=None):
                captured["text"] = text
                captured["voice"] = voice
                return b"RIFF0000WAVEfmt ", 22050

        monkeypatch.setattr(ts, "LightTTSEngine", lambda backend=None: FakeEngine())
        resp = client.post(
            f"/books/{book.id}/text-studio/patches/{patch.id}/preview-paragraph",
            json={"text": "Xin chào", "backend": "edge-tts", "voice": "vi-VN-NamMinhNeural"},
        )
        assert resp.status_code == 200
        assert captured["voice"] == "vi-VN-NamMinhNeural"


class TestLightSynthesizeResilience:
    """A single failing chunk must be skipped, not abort the whole patch."""

    @staticmethod
    def _wav_bytes():
        import io
        import numpy as np
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, np.zeros(2205, dtype="float32"), 22050, format="WAV")
        return buf.getvalue()

    def test_skips_failed_chunk_and_saves(self, conn, book_and_patch, monkeypatch, tmp_path):
        import threading
        from pathlib import Path
        import soundfile as sf
        import app.routes.text_studio as ts
        from app.config import settings

        book, patch = book_and_patch
        wav = self._wav_bytes()

        class FakeEngine:
            def __init__(self, backend="edge-tts", voice=None):
                self.backend, self.voice = backend, voice

            def synthesize_to_wav_bytes(self, text, voice=None):
                if "BOOM" in text:
                    raise RuntimeError("synth failed")
                return wav, 22050

        monkeypatch.setattr(ts, "LightTTSEngine", FakeEngine)
        monkeypatch.setattr("app.chunker.split_into_tts_chunks",
                            lambda text, max_chars: ["one", "BOOM", "three"])
        monkeypatch.setattr(settings, "data_root", str(tmp_path))

        audio_path = ts._light_synthesize_patch(
            patch.id, book.id, "edge-tts", None, False, conn, threading.Lock(),
        )

        assert Path(audio_path).exists()
        data, _ = sf.read(audio_path)
        assert len(data) == 4410  # 2 good chunks × 2205 frames; failed chunk omitted
        assert repository.get_patch(conn, patch.id).status == "done"

    def test_all_chunks_fail_raises(self, conn, book_and_patch, monkeypatch, tmp_path):
        import threading
        import app.routes.text_studio as ts
        from app.config import settings

        book, patch = book_and_patch

        class FakeEngine:
            def __init__(self, backend="edge-tts", voice=None):
                pass

            def synthesize_to_wav_bytes(self, text, voice=None):
                raise RuntimeError("nope")

        monkeypatch.setattr(ts, "LightTTSEngine", FakeEngine)
        monkeypatch.setattr("app.chunker.split_into_tts_chunks",
                            lambda text, max_chars: ["a", "b"])
        monkeypatch.setattr(settings, "data_root", str(tmp_path))

        with pytest.raises(RuntimeError, match="all chunks failed"):
            ts._light_synthesize_patch(
                patch.id, book.id, "edge-tts", None, False, conn, threading.Lock(),
            )


class TestChunkCountReconcile:
    """The stored chunk_count is an estimate until the real split reconciles it."""

    def test_update_marks_exact_and_clears_stale(self, conn, book_and_patch):
        book, patch = book_and_patch
        # A freshly built patch holds the estimate and is flagged stale.
        assert patch.chunk_count_exact == 0
        assert patch.id in repository.list_stale_chunk_count_patch_ids(conn, book.id)

        repository.update_patch_chunk_count(conn, patch.id, 7)
        updated = repository.get_patch(conn, patch.id)
        assert updated.chunk_count == 7
        assert updated.chunk_count_exact == 1
        assert patch.id not in repository.list_stale_chunk_count_patch_ids(conn, book.id)

    def test_set_max_chars_resets_exact(self, conn, book_and_patch):
        book, patch = book_and_patch
        repository.update_patch_chunk_count(conn, patch.id, 7)
        assert repository.get_patch(conn, patch.id).chunk_count_exact == 1

        # Changing max_chars re-estimates the count, so it needs reconciling again.
        assert repository.set_patch_max_chars(conn, patch.id, 100)
        assert repository.get_patch(conn, patch.id).chunk_count_exact == 0
        assert patch.id in repository.list_stale_chunk_count_patch_ids(conn, book.id)

"""Tests for LightTTSEngine."""
from __future__ import annotations

import pytest


class TestLightTTSEngine:
    def test_list_backends(self):
        from app.light_tts import LightTTSEngine

        engine = LightTTSEngine.__new__(LightTTSEngine)
        engine.backend = "edge-tts"
        engine.voice = "vi-VN-HoaiMyNeural"
        backends = engine.list_backends()
        names = [b["name"] for b in backends]
        assert "edge-tts" in names
        assert "gtts" in names

    def test_kokoro_removed(self):
        from app.light_tts import _BACKENDS, _BACKEND_SYNTH

        assert "kokoro" not in _BACKENDS
        assert "kokoro" not in _BACKEND_SYNTH
        assert set(_BACKENDS) == {"edge-tts", "gtts", "piper"}

    def test_piper_voices_dir_setting_default(self):
        from app.config import settings

        assert settings.piper_voices_dir == ""

    def test_synthesize_unavailable_backend(self):
        from app.light_tts import _check_backend

        with pytest.raises(RuntimeError, match="Unknown TTS backend"):
            _check_backend("nonexistent")

    def test_synthesize_with_edge_tts(self):
        pytest.importorskip("edge_tts")
        from app.light_tts import LightTTSEngine

        engine = LightTTSEngine(backend="edge-tts")
        wav_bytes, sr = engine.synthesize_to_wav_bytes("Xin chào")
        assert len(wav_bytes) > 44  # WAV header is 44 bytes
        assert sr > 0


class TestListVoices:
    def test_edge_tts_sorted_vietnamese_first(self, monkeypatch):
        import app.light_tts as lt

        fake = [
            {"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"},
            {"ShortName": "vi-VN-NamMinhNeural", "Gender": "Male", "Locale": "vi-VN"},
            {"ShortName": "vi-VN-HoaiMyNeural", "Gender": "Female", "Locale": "vi-VN"},
        ]
        lt._EDGE_VOICES_CACHE = None
        monkeypatch.setattr(lt, "_edge_list_voices_raw", lambda: fake)
        voices = lt.list_voices("edge-tts")
        assert voices[0]["language"] == "vi-VN"
        assert voices[1]["language"] == "vi-VN"
        assert voices[-1]["id"] == "en-US-AriaNeural"
        assert voices[0]["id"].startswith("vi-VN-")
        assert "(" in voices[0]["label"]  # gender in label

    def test_gtts_lists_languages(self, monkeypatch):
        import app.light_tts as lt

        monkeypatch.setattr(lt, "_gtts_langs", lambda: {"vi": "Vietnamese", "en": "English"})
        voices = lt.list_voices("gtts")
        ids = {v["id"] for v in voices}
        assert ids == {"vi", "en"}
        vi = next(v for v in voices if v["id"] == "vi")
        assert vi["label"] == "Vietnamese"

    def test_piper_constant_list(self):
        import app.light_tts as lt

        voices = lt.list_voices("piper")
        assert len(voices) >= 1
        assert all("id" in v and "label" in v for v in voices)
        assert any(v["id"].startswith("vi_VN") for v in voices)

    def test_fallback_on_enumeration_error(self, monkeypatch):
        import app.light_tts as lt

        def _boom():
            raise RuntimeError("network down")

        lt._EDGE_VOICES_CACHE = None
        monkeypatch.setattr(lt, "_edge_list_voices_raw", _boom)
        voices = lt.list_voices("edge-tts")
        assert voices == [{
            "id": "vi-VN-HoaiMyNeural",
            "label": "vi-VN-HoaiMyNeural",
            "language": "",
        }]


class TestPiperResolve:
    def test_returns_id_when_no_dir(self, monkeypatch):
        import app.light_tts as lt
        from app.config import settings

        monkeypatch.setattr(settings, "piper_voices_dir", "")
        assert lt._resolve_piper_model("vi_VN-vais1000-medium") == "vi_VN-vais1000-medium"

    def test_resolves_to_onnx_path(self, monkeypatch, tmp_path):
        import app.light_tts as lt
        from app.config import settings

        model = tmp_path / "vi_VN-vais1000-medium.onnx"
        model.write_bytes(b"fake")
        monkeypatch.setattr(settings, "piper_voices_dir", str(tmp_path))
        assert lt._resolve_piper_model("vi_VN-vais1000-medium") == str(model)

    def test_returns_id_when_file_missing(self, monkeypatch, tmp_path):
        import app.light_tts as lt
        from app.config import settings

        monkeypatch.setattr(settings, "piper_voices_dir", str(tmp_path))
        assert lt._resolve_piper_model("vi_VN-missing") == "vi_VN-missing"

    def test_synthesize_uses_synthesize_wav_api(self, monkeypatch):
        """piper>=1.3 dropped synthesize(text, wav_file); the WAV-writing method
        is synthesize_wav. Lock that in so the API can't silently regress."""
        import sys
        import types
        import app.light_tts as lt

        used = {}

        class FakeVoice:
            def synthesize(self, *a, **k):
                used["synthesize"] = True

            def synthesize_wav(self, text, wav_file, **k):
                used["synthesize_wav"] = text
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                wav_file.writeframes(b"\x00\x00" * 100)

        class FakePiperVoice:
            @staticmethod
            def load(path):
                return FakeVoice()

        monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=FakePiperVoice))
        wav, sr = lt._piper_synthesize("Xin chào", "vi_VN-vais1000-medium")
        assert used.get("synthesize_wav") == "Xin chào"
        assert "synthesize" not in used
        assert len(wav) > 44 and sr == 22050

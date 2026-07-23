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

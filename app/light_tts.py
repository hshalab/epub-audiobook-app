"""Lightweight TTS engine for preview: no GPU, fast response, pluggable backends."""
from __future__ import annotations

import asyncio
import io
from typing import Any

_BACKENDS: dict[str, dict[str, Any]] = {
    "edge-tts": {
        "description": "Microsoft Edge TTS (online, high quality)",
        "default_voice": "vi-VN-HoaiMyNeural",
    },
    "gtts": {
        "description": "Google Translate TTS (online, simple)",
        "default_voice": "vi",
    },
    "kokoro": {
        "description": "Kokoro ONNX (local CPU, ~100MB model)",
        "default_voice": "vi",
    },
    "piper": {
        "description": "Piper TTS (local CPU, Vietnamese support)",
        "default_voice": "vi_VN-vaisrex-medium",
    },
}


def _check_backend(name: str) -> None:
    """Import-check a backend lazily; raise RuntimeError if missing."""
    if name == "edge-tts":
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            raise RuntimeError("edge-tts is not installed. pip install edge-tts")
    elif name == "gtts":
        try:
            from gtts import gTTS  # noqa: F401
        except ImportError:
            raise RuntimeError("gTTS is not installed. pip install gTTS")
    elif name == "kokoro":
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            raise RuntimeError("kokoro-onnx is not installed. pip install kokoro-onnx")
    elif name == "piper":
        try:
            import piper  # noqa: F401
        except ImportError:
            raise RuntimeError("piper-tts is not installed. pip install piper-tts")
    else:
        raise RuntimeError(f"Unknown TTS backend: {name}")


def _edge_tts_synthesize(text: str, voice: str) -> tuple[bytes, int]:
    """Synthesize text via edge-tts, return (wav_bytes, sample_rate)."""
    import edge_tts

    async def _run() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    mp3_bytes = asyncio.run(_run())
    return _mp3_to_wav_bytes(mp3_bytes)


def _gtts_synthesize(text: str, voice: str) -> tuple[bytes, int]:
    """Synthesize text via gTTS, return (wav_bytes, sample_rate)."""
    from gtts import gTTS

    tts = gTTS(text=text, lang=voice)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    mp3_bytes = buf.getvalue()
    return _mp3_to_wav_bytes(mp3_bytes)


def _kokoro_synthesize(text: str, voice: str) -> tuple[bytes, int]:
    """Synthesize text via kokoro-onnx, return (wav_bytes, sample_rate)."""
    import kokoro_onnx

    model = kokoro_onnx.Kokoro("kokoro-v0_19.onnx", "voices.bin")
    samples, sr = model.create(text, voice=voice)
    wav_buf = io.BytesIO()
    import soundfile as sf
    sf.write(wav_buf, samples, sr, format="WAV")
    return wav_buf.getvalue(), sr


def _piper_synthesize(text: str, voice: str) -> tuple[bytes, int]:
    """Synthesize text via piper-tts, return (wav_bytes, sample_rate)."""
    from piper import PiperVoice
    import wave

    voice_model = PiperVoice.load(voice)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice_model.synthesize(text, wav_file)
    buf.seek(0)
    import soundfile as sf
    data, sr = sf.read(buf)
    out_buf = io.BytesIO()
    sf.write(out_buf, data, sr, format="WAV")
    return out_buf.getvalue(), sr


def _mp3_to_wav_bytes(mp3_bytes: bytes) -> tuple[bytes, int]:
    """Convert MP3 bytes to WAV bytes using soundfile."""
    import soundfile as sf

    audio, sr = sf.read(io.BytesIO(mp3_bytes))
    wav_buf = io.BytesIO()
    sf.write(wav_buf, audio, sr, format="WAV")
    return wav_buf.getvalue(), sr


_BACKEND_SYNTH: dict[str, Any] = {
    "edge-tts": _edge_tts_synthesize,
    "gtts": _gtts_synthesize,
    "kokoro": _kokoro_synthesize,
    "piper": _piper_synthesize,
}


class LightTTSEngine:
    """Lightweight TTS for preview. No GPU, supports pluggable backends."""

    def __init__(self, backend: str = "edge-tts", voice: str | None = None):
        _check_backend(backend)
        self.backend = backend
        self.voice = voice or _BACKENDS[backend]["default_voice"]

    def list_backends(self) -> list[dict[str, Any]]:
        result = []
        for name, info in _BACKENDS.items():
            available = True
            try:
                _check_backend(name)
            except RuntimeError:
                available = False
            result.append({"name": name, "available": available, **info})
        return result

    def synthesize_to_wav_bytes(self, text: str, voice: str | None = None) -> tuple[bytes, int]:
        """Synthesize text to WAV bytes. Returns (wav_bytes, sample_rate)."""
        synth_fn = _BACKEND_SYNTH[self.backend]
        return synth_fn(text, voice or self.voice)

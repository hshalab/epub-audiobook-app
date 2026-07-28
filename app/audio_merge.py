"""Audio concatenation: chunk wavs -> patch wav (in-memory), patch wavs -> final book wav (streamed)."""
from __future__ import annotations

import logging
import shutil

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

_BLOCK_FRAMES = 65536


def concat_chunks_to_wav(
    chunks: list[np.ndarray], sample_rate: int, out_path: str, pause_ms: int = 0
) -> None:
    """Small scale (tens of chunks, seconds each) - safe to hold in memory."""
    if chunks:
        channel_shape = None
        for chunk in chunks:
            if chunk.ndim not in (1, 2):
                raise ValueError("audio chunks must have one or two dimensions")
            shape = chunk.shape[1:]
            if channel_shape is None:
                channel_shape = shape
            elif shape != channel_shape:
                raise ValueError("audio chunks must have the same channel shape")
        pause = np.zeros((round(sample_rate * pause_ms / 1000),) + chunks[0].shape[1:], dtype=chunks[0].dtype)
        parts = []
        for i, chunk in enumerate(chunks):
            if i:
                parts.append(pause)
            parts.append(chunk)
        audio = np.concatenate(parts)
    else:
        audio = np.zeros(0, dtype=np.float32)
    sf.write(out_path, audio, sample_rate)


def concat_wavs(input_paths: list[str], out_path: str, pause_ms: int = 0) -> None:
    if not input_paths:
        raise ValueError("no input paths to merge")
    headers = []
    for path in input_paths:
        with sf.SoundFile(path) as probe:
            headers.append((probe.samplerate, probe.channels))
    sample_rate, channels = headers[0]
    if any(header != (sample_rate, channels) for header in headers[1:]):
        raise ValueError("input samplerate/channels mismatch")
    pause_frames = round(sample_rate * pause_ms / 1000)
    with sf.SoundFile(out_path, mode="w", samplerate=sample_rate, channels=channels, subtype="PCM_16") as out_f:
        for index, path in enumerate(input_paths):
            with sf.SoundFile(path, mode="r") as in_f:
                if index and pause_frames:
                    out_f.write(np.zeros((pause_frames, channels), dtype=np.float32) if channels > 1 else np.zeros(pause_frames, dtype=np.float32))
                while True:
                    block = in_f.read(frames=_BLOCK_FRAMES, dtype="float32")
                    if block.size == 0:
                        break
                    out_f.write(block)


def cleanup_chunk_dir(chunk_dir: str) -> None:
    """Delete a chunk working directory and all contents.  Best-effort — logs a warning
    if removal fails, but does not raise (the patch may already be complete)."""
    try:
        shutil.rmtree(chunk_dir, ignore_errors=True)
    except Exception:
        logger.warning("failed to clean up chunk directory %s", chunk_dir, exc_info=True)




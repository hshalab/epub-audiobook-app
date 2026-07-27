"""Audio concatenation: chunk wavs -> patch wav (in-memory), patch wavs -> final book wav (streamed)."""
from __future__ import annotations

import logging
import shutil

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

_BLOCK_FRAMES = 65536


def concat_chunks_to_wav(chunks: list[np.ndarray], sample_rate: int, out_path: str) -> None:
    """Small scale (tens of chunks, seconds each) - safe to hold in memory."""
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    sf.write(out_path, audio, sample_rate)


def concat_wavs(input_paths: list[str], out_path: str) -> None:
    if not input_paths:
        raise ValueError("no input paths to merge")
    with sf.SoundFile(input_paths[0]) as probe:
        sample_rate = probe.samplerate
        channels = probe.channels
    with sf.SoundFile(out_path, mode="w", samplerate=sample_rate, channels=channels, subtype="PCM_16") as out_f:
        for path in input_paths:
            with sf.SoundFile(path, mode="r") as in_f:
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




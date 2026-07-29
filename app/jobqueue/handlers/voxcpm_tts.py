"""Sinh audio cho một patch bằng VoxCPM."""
from __future__ import annotations

import asyncio
from pathlib import Path

import soundfile as sf

from app import audio_merge, repository
from app.config import settings
from app.jobqueue import store
from app.jobqueue.models import JobFatalError

_CHUNK_PAUSE_MS = 300
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from app.tts_engine import VoxCPMEngine
        _engine = VoxCPMEngine()
    return _engine


def handle(ctx) -> dict:
    patch_id = ctx.job.payload.get("patch_id")
    if patch_id is None:
        raise JobFatalError("payload thiếu patch_id")
    patch = repository.get_patch(ctx.conn, patch_id)
    if patch is None:
        raise JobFatalError(f"patch {patch_id} không tồn tại")

    ctx.log(f"synthesize patch {patch_id} (book {patch.book_id})")
    try:
        audio_path, chunk_count = synthesize_patch(ctx, patch, get_engine(), Path(settings.data_root))
    except asyncio.CancelledError:
        raise
    except JobFatalError:
        repository.mark_patch_failed(ctx.conn, patch_id, "không có nội dung đọc được")
        raise
    except Exception as exc:
        repository.mark_patch_failed(ctx.conn, patch_id, str(exc))
        raise

    from app.patch_publishing import fetch_thumbnail_inputs, on_patch_audio_ready, warm_patch_thumbnail
    thumbnail_inputs = fetch_thumbnail_inputs(ctx.conn, patch_id)
    warm_patch_thumbnail(thumbnail_inputs)
    repository.mark_patch_done(ctx.conn, patch_id, audio_path)
    on_patch_audio_ready(ctx.conn, patch_id)
    ctx.log(f"patch {patch_id} xong -> {audio_path}")
    final_path = finalize_book_if_ready(ctx, patch.book_id)
    ctx.progress(chunk_count, chunk_count, phase="synthesizing")
    return {"audio_path": audio_path, "chunks": chunk_count, "final_audio_path": final_path}


def synthesize_patch(ctx, patch, engine, data_root: Path) -> tuple[str, int]:
    plan_inputs = repository.fetch_patch_chunk_inputs(ctx.conn, patch)
    book = repository.get_book(ctx.conn, patch.book_id)
    plan = repository.build_chunk_plan_from_inputs(plan_inputs)
    if not plan:
        raise JobFatalError("patch không có nội dung đọc được")

    ref_wav = book.voice_clip_path if book else None
    ref_text = book.voice_transcript if book else None
    book_dir = data_root / "books" / str(patch.book_id) / "patches"
    book_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(book_dir / f"{patch.id}.wav")
    timeline_path = Path(audio_path).with_suffix(".timeline.json")
    total = len(plan)
    ctx.progress(patch.next_chunk_index, total, phase="synthesizing")

    if not settings.tts_write_chunk_files:
        wavs = []
        for index, item in enumerate(plan):
            if ctx.should_cancel():
                raise asyncio.CancelledError()
            wavs.append(engine.synthesize_chunk(item["text"], reference_wav_path=ref_wav, prompt_text=ref_text))
            ctx.progress(index + 1, total)
        chapters, _ = audio_merge.build_chapter_marks(plan, [len(a) for a in wavs], engine.sample_rate, _CHUNK_PAUSE_MS)
        audio_merge.concat_chunks_to_wav(wavs, engine.sample_rate, audio_path, pause_ms=_CHUNK_PAUSE_MS)
        audio_merge.try_write_timeline(timeline_path, engine.sample_rate, chapters, sf.info(audio_path).frames)
        ctx.progress(total, total, phase="synthesizing")
        return audio_path, total

    chunk_dir = book_dir / f"{patch.id}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / ".light_tts_meta").unlink(missing_ok=True)
    repository.update_patch_chunk_count(ctx.conn, patch.id, total)
    start_index = max(0, min(patch.next_chunk_index, total))
    if start_index > 0:
        ctx.log(f"resume từ chunk {start_index}/{total}")

    frame_counts = []
    for index, item in enumerate(plan):
        chunk_path = chunk_dir / f"chunk_{index:03d}.wav"
        if index >= start_index:
            if ctx.should_cancel():
                raise asyncio.CancelledError()
            arr = engine.synthesize_chunk(item["text"], reference_wav_path=ref_wav, prompt_text=ref_text)
            sf.write(chunk_path, arr, engine.sample_rate)
            repository.update_patch_chunk_progress(ctx.conn, patch.id, index + 1)
            ctx.progress(index + 1, total)
        frame_counts.append(sf.info(str(chunk_path)).frames)

    chapters, _ = audio_merge.build_chapter_marks(plan, frame_counts, engine.sample_rate, _CHUNK_PAUSE_MS)
    chunk_paths = [str(chunk_dir / f"chunk_{i:03d}.wav") for i in range(total)]
    ctx.progress(total, total, phase="merging")
    audio_merge.concat_wavs(chunk_paths, audio_path, pause_ms=_CHUNK_PAUSE_MS)
    audio_merge.try_write_timeline(timeline_path, engine.sample_rate, chapters, sf.info(audio_path).frames)
    ctx.progress(total, total, phase="synthesizing")
    return audio_path, total


def finalize_book_if_ready(ctx, book_id: int) -> str | None:
    if not repository.all_patches_done(ctx.conn, book_id):
        return None
    patches = repository.list_patches(ctx.conn, book_id)
    book = repository.get_book(ctx.conn, book_id)
    paths = [p.audio_path for p in patches if p.audio_path]
    if len(paths) != len(patches):
        return None

    book_dir = Path(settings.data_root) / "books" / str(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    final_path = str(book_dir / "final.wav")
    ctx.progress(ctx.job.progress_current, ctx.job.progress_total, phase="merging_book")
    audio_merge.concat_wavs(paths, final_path)
    repository.set_book_final_audio(ctx.conn, book_id, final_path)
    ctx.log(f"gộp xong final.wav cho sách {book_id}")
    if book is None:
        return final_path
    has_image = bool(book.background_image_path) or any(p.image_path for p in patches)
    if not has_image:
        ctx.log("sách không có ảnh nào dùng được — bỏ qua bước tạo video")
        return final_path
    book_job = repository.enqueue_book_job(ctx.conn, book_id, "video")
    job_id = store.enqueue(ctx.conn, "video", payload={"book_job_id": book_job.id}, book_id=book_id, dedupe_key=f"video:book_job={book_job.id}")
    ctx.log(f"đã xếp hàng job video (book_job={book_job.id}, job={job_id})")
    return final_path

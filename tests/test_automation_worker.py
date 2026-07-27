import asyncio
import json
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest

from app import automation_repository, db
from app.automation_worker import AutomationWorker


@pytest.fixture(autouse=True)
def valid_probe(monkeypatch):
    monkeypatch.setattr(
        "app.video_compositor.probe_media",
        lambda path: {
            "duration": 1.0,
            "streams": [{"codec_type": "video"}],
            "kind": "image" if Path(path).suffix.lower() in {".jpg", ".png"} else "video",
        },
    )


def seed(tmp_path, *, audio=True, media=True):
    conn = db.connect(str(tmp_path / "app.db"))
    db.init_schema(conn)
    now = "2026-07-26T00:00:00+00:00"
    book_id = conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,status,created_at,updated_at) VALUES ('Book','b.epub','b.epub','ready',?,?)",
        (now, now),
    ).lastrowid
    audio_path = tmp_path / "audio.wav"
    if audio:
        audio_path.write_bytes(b"wav")
    patch_id = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) VALUES (?,0,0,0,'Part','done',?,?,?)",
        (book_id, str(audio_path), now, now),
    ).lastrowid
    if media:
        background = tmp_path / "backgrounds" / "snapshot.jpg"
        background.parent.mkdir(exist_ok=True)
        background.write_bytes(b"jpg")
        asset = automation_repository.upsert_media_asset(conn, str(background), background.name, "image")
        automation_repository.set_book_media(conn, book_id, "background", [asset["id"]])
    pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)
    return conn, threading.Lock(), book_id, patch_id, pipeline


def write_thumbnail(*args, **kwargs):
    output = Path(kwargs["out_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(png_bytes())
    return str(output)


def png_bytes():
    from PIL import Image
    data = BytesIO()
    Image.new("RGB", (8, 8), "red").save(data, "PNG")
    return data.getvalue()


def test_thumbnail_then_video_uses_snapshots_and_exact_output(tmp_path, monkeypatch):
    async def run():
        conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
        calls = []

        def thumbnail(*args, **kwargs):
            assert not lock.locked()
            calls.append(("thumbnail", kwargs["background_path"]))
            Path(kwargs["out_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["out_path"]).write_bytes(png_bytes())
            return kwargs["out_path"]

        def render(*args, **kwargs):
            assert not lock.locked()
            calls.append(("video", kwargs["backgrounds"], kwargs["config"].video.fps))
            Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_path"]).write_bytes(b"mp4")

        monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", thumbnail)
        monkeypatch.setattr("app.video_compositor.render_composite", render)
        conn.execute("UPDATE book SET automation_config=? WHERE id=?", (json.dumps({"video": {"fps": 60}}), book_id))
        conn.execute("DELETE FROM book_media_selection WHERE book_id=?", (book_id,))
        conn.commit()
        worker = AutomationWorker(conn, lock, tmp_path)

        await worker.run_once()
        await worker.run_once()

        row = dict(conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone())
        expected = tmp_path / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"
        assert [call[0] for call in calls] == ["thumbnail", "video"]
        assert calls[0][1].endswith("snapshot.jpg")
        assert calls[1][1][0]["file_path"].endswith("snapshot.jpg")
        assert calls[1][2] == 30
        assert row["stage"] == "upload"
        assert row["video_status"] == "done"
        assert row["video_path"] == str(expected)
        assert row["video_id"] is not None
        video = conn.execute("SELECT * FROM videos WHERE id=?", (row["video_id"],)).fetchone()
        assert (video["book_id"], video["patch_id"], video["file_path"]) == (book_id, patch_id, str(expected))
    asyncio.run(run())


def test_thumbnail_is_generated_before_audio_exists(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path, audio=False)
    monkeypatch.setattr(
        "app.image_overlay.ensure_patch_overlay",
        write_thumbnail,
    )
    worker = AutomationWorker(conn, lock, tmp_path)

    assert asyncio.run(worker.run_once()) is True

    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert row["thumbnail_status"] == "done"
    assert row["stage"] == "video"


def test_no_selected_background_uses_existing_overlay_fallback(tmp_path, monkeypatch):
    conn, lock, _, patch_id, pipeline = seed(tmp_path, media=False)
    overlay = tmp_path / "books" / "1" / "patch_overlays" / f"{patch_id}.png"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(png_bytes())
    conn.execute(
        "UPDATE patch_pipeline SET stage='video', thumbnail_status='done', thumbnail_path=? WHERE id=?",
        (str(overlay), pipeline["id"]),
    )
    conn.commit()
    seen = {}

    def render(*args, **kwargs):
        seen["backgrounds"] = kwargs["backgrounds"]
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"mp4")

    monkeypatch.setattr("app.video_compositor.render_composite", render)

    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is True
    assert seen["backgrounds"] == [{"file_path": str(overlay), "kind": "image"}]


def test_invalid_overlay_fallback_waits_for_media(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path, media=False)
    overlay = tmp_path / "invalid.png"
    overlay.write_bytes(b"bad")
    conn.execute(
        "UPDATE patch_pipeline SET stage='video',thumbnail_status='done',thumbnail_path=? WHERE id=?",
        (str(overlay), pipeline["id"]),
    )
    conn.commit()
    monkeypatch.setattr("app.video_compositor.probe_media", lambda path: (_ for _ in ()).throw(ValueError("invalid")))

    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is False
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert row["video_status"] == "waiting_for_media"
    assert row["attempt_count"] == 0


def test_missing_background_and_overlay_waits_without_attempt(tmp_path):
    conn, lock, _, _, pipeline = seed(tmp_path, media=False)

    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is False

    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert row["thumbnail_status"] == "waiting_for_media"
    assert row["attempt_count"] == 0


def test_waiting_first_row_does_not_starve_second(tmp_path, monkeypatch):
    conn, lock, _, _, first = seed(tmp_path, media=False)
    now = "2026-07-26T00:00:00+00:00"
    background = tmp_path / "backgrounds" / "second.png"
    background.parent.mkdir()
    background.write_bytes(png_bytes())
    asset = automation_repository.upsert_media_asset(conn, str(background), background.name, "image")
    automation_repository.set_book_media(conn, 1, "background", [asset["id"]])
    second_patch = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) "
        "SELECT book_id,1,0,0,'Second','done',audio_path,?,? FROM patch LIMIT 1", (now, now),
    ).lastrowid
    second = automation_repository.enqueue_patch_pipeline(conn, second_patch)
    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", write_thumbnail)

    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is True
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (first["id"],)).fetchone()[0] == "waiting_for_media"
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (second["id"],)).fetchone()[0] == "done"


def test_keyset_scan_reaches_ready_row_after_101_blocked(tmp_path, monkeypatch):
    conn, lock, book_id, _, first = seed(tmp_path, media=False)
    now = "2026-07-26T00:00:00+00:00"
    for index in range(1, 102):
        patch_id = conn.execute(
            "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) VALUES (?,?,?,?,?,'done',NULL,?,?)",
            (book_id, index, 0, 0, str(index), now, now),
        ).lastrowid
        automation_repository.enqueue_patch_pipeline(conn, patch_id)
    background = tmp_path / "backgrounds" / "ready.png"
    background.parent.mkdir()
    background.write_bytes(png_bytes())
    asset = automation_repository.upsert_media_asset(conn, str(background), background.name, "image")
    automation_repository.set_book_media(conn, book_id, "background", [asset["id"]])
    ready_patch = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) VALUES (?,102,0,0,'ready','done',NULL,?,?)",
        (book_id, now, now),
    ).lastrowid
    ready = automation_repository.enqueue_patch_pipeline(conn, ready_patch)
    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", write_thumbnail)

    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is True
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (first["id"],)).fetchone()[0] == "waiting_for_media"
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (ready["id"],)).fetchone()[0] == "done"


def test_failure_is_bounded_and_restart_reuses_valid_outputs(tmp_path, monkeypatch):
    conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
    thumbnail = tmp_path / "thumb.png"
    thumbnail.write_bytes(b"png")
    conn.execute(
        "UPDATE patch_pipeline SET stage='video', thumbnail_status='done', thumbnail_path=? WHERE id=?",
        (str(thumbnail), pipeline["id"]),
    )
    conn.commit()
    monkeypatch.setattr(
        "app.video_compositor.render_composite",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x" * 10000)),
    )

    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())
    failed = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert failed["video_status"] == "failed"
    assert len(failed["last_error"]) <= 2000

    output = tmp_path / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"existing")
    conn.execute("UPDATE patch_pipeline SET video_status='pending', last_error=NULL WHERE id=?", (pipeline["id"],))
    conn.commit()
    monkeypatch.setattr("app.video_compositor.render_composite", lambda *a, **k: pytest.fail("rerendered"))

    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())
    recovered = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert recovered["stage"] == "upload"
    assert recovered["video_id"] is not None


def test_video_upsert_is_idempotent(tmp_path):
    from app.video_repository import upsert_patch_video

    conn, _, book_id, patch_id, _ = seed(tmp_path)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"mp4")
    first = upsert_patch_video(conn, book_id=book_id, patch_id=patch_id, file_path=str(path), resolution="1280x720")
    second = upsert_patch_video(conn, book_id=book_id, patch_id=patch_id, file_path=str(path), resolution="1920x1080")
    assert first["id"] == second["id"]
    assert second["resolution"] == "1920x1080"
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE patch_id=?", (patch_id,)).fetchone()[0] == 1


def test_restart_requeues_processing_stage(tmp_path):
    conn, lock, _, _, pipeline = seed(tmp_path)
    conn.execute(
        "UPDATE patch_pipeline SET thumbnail_status='processing', attempt_count=1 WHERE id=?",
        (pipeline["id"],),
    )
    conn.commit()

    AutomationWorker(conn, lock, tmp_path)

    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert row["thumbnail_status"] == "pending"
    assert row["attempt_count"] == 1


def test_validated_candidate_is_the_claimed_row(tmp_path, monkeypatch):
    conn, lock, _, _, first = seed(tmp_path)
    now = "2026-07-26T00:00:00+00:00"
    second_patch = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) "
        "SELECT book_id,1,0,0,'Second','done',audio_path,?,? FROM patch LIMIT 1",
        (now, now),
    ).lastrowid
    second = automation_repository.enqueue_patch_pipeline(conn, second_patch)
    conn.execute("UPDATE patch_pipeline SET thumbnail_status='waiting_for_media' WHERE id=?", (first["id"],))
    conn.commit()
    rendered = []

    def thumbnail(*args, **kwargs):
        rendered.append(kwargs["out_path"])
        Path(kwargs["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["out_path"]).write_bytes(png_bytes())
        return kwargs["out_path"]

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", thumbnail)
    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())

    assert Path(rendered[0]).name.startswith(f".{first['patch_id']}.")
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (first["id"],)).fetchone()[0] == "done"
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (second["id"],)).fetchone()[0] == "pending"


def test_malformed_snapshot_fails_row_and_continues(tmp_path, monkeypatch):
    conn, lock, _, _, first = seed(tmp_path)
    now = "2026-07-26T00:00:00+00:00"
    second_patch = conn.execute(
        "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) "
        "SELECT book_id,1,0,0,'Second','done',audio_path,?,? FROM patch LIMIT 1",
        (now, now),
    ).lastrowid
    second = automation_repository.enqueue_patch_pipeline(conn, second_patch)
    conn.execute("UPDATE patch_pipeline SET media_snapshot='{' WHERE id=?", (first["id"],))
    conn.commit()
    monkeypatch.setattr(
        "app.image_overlay.ensure_patch_overlay",
        write_thumbnail,
    )
    worker = AutomationWorker(conn, lock, tmp_path)

    assert asyncio.run(worker.run_once()) is True

    bad = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (first["id"],)).fetchone()
    good = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (second["id"],)).fetchone()
    assert bad["thumbnail_status"] == "failed"
    assert len(bad["last_error"]) <= 2000
    assert good["thumbnail_status"] == "done"


def test_corrupt_final_is_rerendered_via_temp_and_replaced(tmp_path, monkeypatch):
    conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
    overlay = tmp_path / "overlay.png"
    overlay.write_bytes(b"png")
    output = tmp_path / "books" / str(book_id) / "patch_videos" / f"{patch_id}.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"corrupt")
    conn.execute("UPDATE patch_pipeline SET stage='video',thumbnail_status='done',thumbnail_path=? WHERE id=?", (str(overlay), pipeline["id"]))
    conn.commit()
    rendered = []

    def probe(path):
        if Path(path) == output:
            raise ValueError("corrupt")
        return {"duration": 1.0, "streams": [{"codec_type": "video"}], "kind": "video"}

    def render(*args, **kwargs):
        rendered.append(kwargs["output_path"])
        assert kwargs["output_path"] != str(output)
        assert Path(kwargs["output_path"]).parent == output.parent
        Path(kwargs["output_path"]).write_bytes(b"valid")

    monkeypatch.setattr("app.video_compositor.probe_media", probe)
    monkeypatch.setattr("app.video_compositor.render_composite", render)
    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())

    assert output.read_bytes() == b"valid"
    assert not Path(rendered[0]).exists()


def test_snapshot_descriptors_survive_asset_row_changes(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path)
    snapshot_path = json.loads(pipeline["media_snapshot"])["background"][0]["file_path"]
    conn.execute("UPDATE media_assets SET file_path=?,media_type='video'", (str(tmp_path / "changed.mp4"),))
    conn.commit()
    seen = []

    def thumbnail(*args, **kwargs):
        seen.append(kwargs["background_path"])
        Path(kwargs["out_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["out_path"]).write_bytes(b"png")
        return kwargs["out_path"]

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", thumbnail)
    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())
    assert seen == [snapshot_path]


def test_cancellation_returns_promptly_and_temp_is_removed_after_render(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path)
    overlay = tmp_path / "overlay.png"
    overlay.write_bytes(b"png")
    conn.execute("UPDATE patch_pipeline SET stage='video',thumbnail_status='done',thumbnail_path=? WHERE id=?", (str(overlay), pipeline["id"]))
    conn.commit()
    started = threading.Event()
    release = threading.Event()
    temp_path = []

    def render(*args, **kwargs):
        started.set()
        temp_path.append(Path(kwargs["output_path"]))
        Path(kwargs["output_path"]).write_bytes(b"partial")
        release.wait()
        Path(kwargs["output_path"]).write_bytes(b"valid")

    monkeypatch.setattr("app.video_compositor.render_composite", render)

    async def run():
        task = asyncio.create_task(AutomationWorker(conn, lock, tmp_path).run_once())
        await asyncio.to_thread(started.wait)
        before = conn.total_changes
        task.cancel()
        threading.Timer(0.5, release.set).start()
        started_at = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started_at < 0.2
        assert conn.total_changes == before

    asyncio.run(run())
    deadline = time.monotonic() + 3
    while temp_path[0].exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not temp_path[0].exists(), "temp file was not cleaned up within 3 s"
    assert conn.execute("SELECT video_status FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()[0] == "processing"


def test_corrupt_existing_thumbnail_is_regenerated_atomically(tmp_path, monkeypatch):
    conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
    output = tmp_path / "books" / str(book_id) / "patch_overlays" / f"{patch_id}.png"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"corrupt")
    conn.execute("UPDATE patch_pipeline SET thumbnail_path=? WHERE id=?", (str(output), pipeline["id"]))
    conn.commit()
    generated = []

    def render(*args, **kwargs):
        generated.append(Path(kwargs["out_path"]))
        Path(kwargs["out_path"]).write_bytes(png_bytes())
        return kwargs["out_path"]

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", render)
    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is True
    assert generated[0] != output
    assert generated[0].parent == output.parent
    assert output.read_bytes() == png_bytes()
    assert not generated[0].exists()


def test_invalid_generated_thumbnail_never_marks_done(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path)
    monkeypatch.setattr(
        "app.image_overlay.ensure_patch_overlay",
        lambda *a, **k: Path(k["out_path"]).write_bytes(b"bad") or k["out_path"],
    )
    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())
    row = conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()
    assert row["thumbnail_status"] == "failed"


def test_invalid_automation_thumbnail_does_not_touch_canonical_marquee(tmp_path, monkeypatch):
    conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
    marquee = tmp_path / "books" / str(book_id) / "patch_overlays" / f"{patch_id}.marquee.png"
    meta = marquee.with_suffix(".json")
    marquee.parent.mkdir(parents=True)
    marquee.write_bytes(b"keep-band")
    meta.write_bytes(b"keep-meta")

    def invalid(*args, **kwargs):
        assert kwargs["include_marquee"] is False
        Path(kwargs["out_path"]).write_bytes(b"bad")
        return kwargs["out_path"]

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", invalid)
    asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once())
    assert marquee.read_bytes() == b"keep-band"
    assert meta.read_bytes() == b"keep-meta"
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()[0] == "failed"


def test_external_snapshotted_media_is_rejected(tmp_path, monkeypatch):
    conn, lock, _, _, pipeline = seed(tmp_path, media=False)
    external = tmp_path.parent / "external.png"
    external.write_bytes(png_bytes())
    conn.execute(
        "UPDATE patch_pipeline SET media_snapshot=? WHERE id=?",
        (json.dumps({"background": [{"id": 9, "file_path": str(external), "media_type": "image"}], "webcam": []}), pipeline["id"]),
    )
    conn.commit()
    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is False
    assert conn.execute("SELECT thumbnail_status FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()[0] == "waiting_for_media"


def test_cross_book_overlay_fallback_is_rejected(tmp_path):
    conn, lock, _, _, pipeline = seed(tmp_path, media=False)
    other = tmp_path / "books" / "999" / "patch_overlays" / "999.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(png_bytes())
    conn.execute("UPDATE patch_pipeline SET stage='video',thumbnail_status='done',thumbnail_path=? WHERE id=?", (str(other), pipeline["id"]))
    conn.commit()
    assert asyncio.run(AutomationWorker(conn, lock, tmp_path).run_once()) is False
    assert conn.execute("SELECT video_status FROM patch_pipeline WHERE id=?", (pipeline["id"],)).fetchone()[0] == "waiting_for_media"


def test_thumbnail_cancellation_cleans_temp_without_touching_marquee(tmp_path, monkeypatch):
    conn, lock, book_id, patch_id, pipeline = seed(tmp_path)
    started = threading.Event()
    release = threading.Event()
    temp = []
    marquee = tmp_path / "books" / str(book_id) / "patch_overlays" / f"{patch_id}.marquee.png"
    marquee.parent.mkdir(parents=True)
    marquee.write_bytes(b"keep")

    def render(*args, **kwargs):
        assert kwargs["include_marquee"] is False
        temp.append(Path(kwargs["out_path"]))
        temp[0].write_bytes(png_bytes())
        started.set()
        release.wait()
        temp[0].write_bytes(png_bytes())
        return str(temp[0])

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", render)

    async def run():
        task = asyncio.create_task(AutomationWorker(conn, lock, tmp_path).run_once())
        await asyncio.to_thread(started.wait)
        task.cancel()
        threading.Timer(0.3, release.set).start()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not temp[0].exists()

    asyncio.run(run())
    deadline = time.monotonic() + 2
    while temp[0].exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not temp[0].exists()
    assert marquee.read_bytes() == b"keep"

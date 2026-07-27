import json
from pathlib import Path

import pytest

from app import automation_repository, db


def _ffmpeg_available():
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _png_bytes():
    from io import BytesIO
    from PIL import Image
    data = BytesIO()
    Image.new("RGB", (8, 8), "red").save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def db_conn(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_schema(conn)
    return conn


@pytest.fixture
def fake_youtube(monkeypatch):
    from tests.test_youtube_postprocess import FakeYouTubeService
    service = FakeYouTubeService()
    monkeypatch.setattr("app.youtube.get_youtube_service", lambda conn: service)
    return service


@pytest.fixture
def seeded_data(db_conn, tmp_path):
    now = "2026-07-26T00:00:00+00:00"

    book_id = db_conn.execute(
        "INSERT INTO book (title,original_filename,epub_path,patch_size,status,created_at,updated_at) "
        "VALUES ('Test Book','b.epub','b.epub',1,'ready',?,?)",
        (now, now),
    ).lastrowid

    patch_ids = []
    for idx in range(2):
        audio_path = tmp_path / f"audio_{idx}.wav"
        audio_path.write_bytes(b"wav")
        pid = db_conn.execute(
            "INSERT INTO patch (book_id,patch_index,chapter_start,chapter_end,name,status,audio_path,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'done',?,?,?)",
            (book_id, idx, idx, idx, f"Patch {idx}", str(audio_path), now, now),
        ).lastrowid
        patch_ids.append(pid)

    thumbnail = tmp_path / "thumb.png"
    thumbnail.write_bytes(_png_bytes())

    db_conn.execute(
        "INSERT INTO youtube_credentials (access_token,refresh_token,token_expiry,channel_id,channel_name,created_at,updated_at) "
        "VALUES ('tok','ref','2099-01-01T00:00:00+00:00','ch_fake','Chan',?,?)",
        (now, now),
    )

    db_conn.execute(
        "INSERT INTO automation_settings (id,schema_version,config_json,created_at,updated_at) "
        "VALUES (1,1,?,?,?)",
        (json.dumps({"video": {"fps": 30}}), now, now),
    )

    db_conn.commit()
    return {"book_id": book_id, "patch_ids": patch_ids, "thumbnail": thumbnail}


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not available")
def test_pipeline_enqueue_creates_distinct_pipelines(db_conn, seeded_data, fake_youtube, monkeypatch):
    patch_ids = seeded_data["patch_ids"]
    thumbnail = seeded_data["thumbnail"]

    def mock_overlay(*args, **kwargs):
        out = Path(kwargs["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(thumbnail.read_bytes())
        return str(out)

    def mock_composite(*args, **kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"dummy video content")
        return None

    monkeypatch.setattr("app.image_overlay.ensure_patch_overlay", mock_overlay)
    monkeypatch.setattr("app.video_compositor.render_composite", mock_composite)

    pipelines = []
    for pid in patch_ids:
        pipeline = automation_repository.enqueue_patch_pipeline(db_conn, pid)
        assert pipeline is not None, f"no pipeline created for patch {pid}"
        pipelines.append(pipeline)

    assert len(pipelines) == 2
    assert pipelines[0]["id"] != pipelines[1]["id"]
    assert pipelines[0]["patch_id"] != pipelines[1]["patch_id"]

    for p in pipelines:
        assert p["stage"] == "thumbnail"
        assert p["thumbnail_status"] == "pending"

    for p in pipelines:
        claimed = automation_repository.claim_pipeline_stage(db_conn, p["id"], "thumbnail")
        assert claimed is not None, f"could not claim thumbnail stage for pipeline {p['id']}"
        assert claimed["thumbnail_status"] == "processing"

    for p in pipelines:
        out_path = str(thumbnail)
        automation_repository.update_pipeline_stage(db_conn, p["id"], "thumbnail", "processing", output_path=out_path)
        automation_repository.advance_pipeline_stage(db_conn, p["id"], "thumbnail", "video")

    for p in pipelines:
        row = db_conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (p["id"],)).fetchone()
        assert row["thumbnail_status"] == "done"
        assert row["stage"] == "video"
        assert row["video_status"] == "pending"

    for p in pipelines:
        claimed = automation_repository.claim_pipeline_stage(db_conn, p["id"], "video")
        assert claimed is not None, f"could not claim video stage for pipeline {p['id']}"
        assert claimed["video_status"] == "processing"

    for p in pipelines:
        out_path = str(thumbnail)
        automation_repository.update_pipeline_stage(db_conn, p["id"], "video", "processing", output_path=out_path)
        automation_repository.advance_pipeline_stage(db_conn, p["id"], "video", "upload")

    for p in pipelines:
        row = db_conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (p["id"],)).fetchone()
        assert row["video_status"] == "done"
        assert row["stage"] == "upload"

    # Upload → playlist transition (done by upload_worker after postprocess)
    for p in pipelines:
        automation_repository.advance_pipeline_stage(db_conn, p["id"], "upload", "playlist")
        db_conn.execute("UPDATE patch_pipeline SET playlist_status='done' WHERE id=?", (p["id"],))
        db_conn.commit()

    for p in pipelines:
        row = db_conn.execute("SELECT * FROM patch_pipeline WHERE id=?", (p["id"],)).fetchone()
        assert row["upload_status"] == "done"
        assert row["stage"] == "playlist"
        assert row["playlist_status"] == "done"

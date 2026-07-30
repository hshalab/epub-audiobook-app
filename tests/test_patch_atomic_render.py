from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app
from app.patch_publishing import enqueue_patch_publish, run_patch_publish_stage
from app.routes import patches
from app.video_integrity import ValidationFacts, ValidationResult


VALID = ValidationResult(True, None, "", (), ValidationFacts(), 0)


def _seed(conn, audio):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO book (id,title,original_filename,epub_path,patch_size,status,video_resolution,created_at,updated_at) VALUES (1,'B','b','b',1,'done','1280x720',?,?)", (now, now))
    conn.execute("INSERT INTO patch (id,book_id,patch_index,chapter_start,chapter_end,status,audio_path,created_at,updated_at) VALUES (1,1,0,0,0,'done',?,?,?)", (str(audio), now, now)); conn.commit()


def test_generate_patch_route_publishes_only_validated_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db")); monkeypatch.setattr(settings, "data_root", str(tmp_path)); monkeypatch.setattr(settings, "enable_worker", False)
    audio = tmp_path / "a.wav"; audio.write_bytes(b"a"); image = tmp_path / "i.jpg"; image.write_bytes(b"i"); seen = {}
    monkeypatch.setattr(patches.image_overlay, "ensure_patch_overlay", lambda *a, **k: str(image))
    monkeypatch.setattr(patches.video_gen, "generate_segment", lambda *a, **k: (seen.update(render=Path(a[2])), Path(a[2]).write_bytes(b"new")))
    monkeypatch.setattr(patches, "validate_video", lambda p: seen.update(validated=Path(p)) or VALID)
    with TestClient(app) as client:
        _seed(client.app.state.conn, audio)
        assert client.post("/books/1/patches/1/generate-video?ajax=1").status_code == 200
    final = tmp_path / "books" / "1" / "patch_videos" / "1.mp4"
    assert seen["render"] == seen["validated"] != final
    assert final.read_bytes() == b"new"


def test_publish_stage_publishes_only_validated_temp(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn)
    audio = tmp_path / "a.wav"; audio.write_bytes(b"a"); thumb = tmp_path / "t.png"; thumb.write_bytes(b"t"); final = tmp_path / "v.mp4"; final.write_bytes(b"old")
    _seed(conn, audio)
    monkeypatch.setattr("app.patch_publishing.ensure_patch_overlay", lambda *a, **k: str(thumb))
    enqueue_patch_publish(conn, 1)
    conn.execute("UPDATE patch_pipeline SET thumbnail_status='done',thumbnail_path=?,video_status='pending',video_path=? WHERE patch_id=1", (str(thumb), str(final))); conn.commit(); seen = {}
    monkeypatch.setattr("app.patch_publishing.video_gen.generate_segment", lambda *a, **k: (seen.update(render=Path(a[2])), Path(a[2]).write_bytes(b"new")))
    monkeypatch.setattr("app.patch_publishing.validate_video", lambda p: seen.update(validated=Path(p)) or VALID, raising=False)
    run_patch_publish_stage(conn, 1)
    assert seen["render"] == seen["validated"] != final
    assert final.read_bytes() == b"new"

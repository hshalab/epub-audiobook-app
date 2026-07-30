import json
from datetime import datetime, timezone
from pathlib import Path

from app import db, youtube
from app.jobqueue import store
from app.jobqueue.context import JobContext
from app.jobqueue.handlers import standalone_video
from app.jobqueue.joblog import JobLogger
from app.video_integrity import ValidationFacts, ValidationResult


def test_standalone_recovery_uses_persisted_sources_and_config(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn); now = datetime.now(timezone.utc).isoformat()
    audio = tmp_path / "a.wav"; audio.write_bytes(b"a"); bg = tmp_path / "b.jpg"; bg.write_bytes(b"b")
    output = tmp_path / "v.mp4"; config = {"resolution": "1280x720", "fps": 24, "codec": "libx264", "quality": 20, "audio_bitrate": "192k"}
    video_id = conn.execute("INSERT INTO videos (filename,file_path,source_audio,background_path,render_config_json,created_at,updated_at) VALUES ('v.mp4',?,?,?,?,?,?)", (str(output), str(audio), str(bg), json.dumps(config), now, now)).lastrowid; conn.commit()
    upload_id = youtube.enqueue_upload(conn, str(output), "T", video_id=video_id, render_source_type="standalone", render_source_id=video_id)
    conn.execute("UPDATE youtube_uploads SET status='waiting_for_rerender',validation_status='waiting_for_rerender',integrity_retry_count=1 WHERE id=?", (upload_id,)); conn.commit()
    seen = {}
    monkeypatch.setattr(standalone_video.video_gen, "generate_standalone_video", lambda a, b, out, **kw: (seen.update(a=a,b=b,out=out,kw=kw), Path(out).write_bytes(b"new")))
    monkeypatch.setattr(standalone_video, "validate_video", lambda p: ValidationResult(True, None, "", (), ValidationFacts(), 0))
    job_id = store.enqueue(conn, "standalone_video", payload={"video_id": video_id, "recovery_upload_id": upload_id})
    job = store.claim(conn, "standalone_video", "w")
    standalone_video.handle(JobContext(job, conn, JobLogger(job_id, "standalone_video"), lambda: False))
    assert (seen["a"], seen["b"]) == (str(audio), str(bg))
    assert seen["out"] != str(output)
    assert seen["kw"]["fps"] == 24
    assert output.read_bytes() == b"new"
    assert conn.execute("SELECT status FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()[0] == "pending"

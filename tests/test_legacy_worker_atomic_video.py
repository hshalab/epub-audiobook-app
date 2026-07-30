import threading
from datetime import datetime, timezone
from pathlib import Path

from app import db, repository
from app.video_integrity import ValidationFacts, ValidationResult
from app.worker import PatchWorker
from app import worker as worker_module


def test_legacy_worker_publishes_only_validated_temp(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "a.db")); db.init_schema(conn); now = datetime.now(timezone.utc).isoformat()
    audio = tmp_path / "final.wav"; audio.write_bytes(b"a")
    conn.execute("INSERT INTO book (id,title,original_filename,epub_path,patch_size,status,final_audio_path,created_at,updated_at) VALUES (1,'B','b','b',1,'done',?,?,?)", (str(audio), now, now)); conn.commit()
    job = repository.enqueue_book_job(conn, 1, "video"); seen = {}
    monkeypatch.setattr(worker_module.video_gen, "generate_full_video", lambda *a, **k: (seen.update(render=Path(a[2])), Path(a[2]).write_bytes(b"new")))
    monkeypatch.setattr(worker_module, "validate_video", lambda p: seen.update(validated=Path(p)) or ValidationResult(True,None,"",(),ValidationFacts(),0), raising=False)
    worker = PatchWorker(conn, object(), str(tmp_path), db_lock=threading.Lock())
    output = Path(worker._run_video_job(job))
    assert seen["render"] == seen["validated"] != output
    assert output.read_bytes() == b"new"

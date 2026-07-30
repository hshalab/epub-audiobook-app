"""Flag rendered videos YouTube will refuse to process.

A concat of segments with mismatched framerates makes the MP4 muxer stamp every
packet with the first segment's frame duration, so part of the video track plays
at the wrong speed and its duration drifts away from the audio. The file uploads
fine and then fails processing, so nothing in the app notices.

Usage:
    python scripts/check_video_integrity.py            # scan everything
    python scripts/check_video_integrity.py --uploaded # only files already on YouTube
"""
import argparse
import io
import json
import sqlite3
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

# A video track this far from the audio track means the timeline is wrong, not
# just the usual sub-frame rounding at a segment boundary.
DRIFT_FATAL_SECONDS = 5.0
DRIFT_WARN_SECONDS = 1.0
# r_frame_rate vs avg_frame_rate: anything above this is a variable-rate file.
VFR_TOLERANCE = 0.005


def _probe(path: Path) -> dict | None:
    result = subprocess.run(
        [settings.get_ffprobe_path(), "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _rate(value: str | None) -> float:
    try:
        f = Fraction(value or "0/0")
        return float(f) if f.denominator else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def inspect(path: Path) -> dict:
    """Return {path, verdict, reasons, ...} for one video file."""
    row: dict = {"path": path, "verdict": "ok", "reasons": []}
    info = _probe(path)
    if info is None:
        row["verdict"] = "broken"
        row["reasons"].append("ffprobe cannot read the file (truncated or no moov atom)")
        return row

    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        row["verdict"] = "broken"
        row["reasons"].append("no video stream")
        return row

    r_fps = _rate(video.get("r_frame_rate"))
    avg_fps = _rate(video.get("avg_frame_rate"))
    vdur = float(video.get("duration") or 0)
    adur = float(audio.get("duration") or 0) if audio else 0.0
    row.update(r_fps=r_fps, avg_fps=avg_fps, vdur=vdur, adur=adur,
               res=f"{video.get('width')}x{video.get('height')}")

    if audio is None:
        row["verdict"] = "broken"
        row["reasons"].append("no audio stream")
        return row

    drift = vdur - adur
    if abs(drift) >= DRIFT_FATAL_SECONDS:
        row["verdict"] = "broken"
        ratio = vdur / adur if adur else 0
        row["reasons"].append(
            f"video is {drift:+.1f}s off the audio ({ratio:.3f}x) -- segments were "
            "concatenated at mismatched framerates"
        )
    elif abs(drift) >= DRIFT_WARN_SECONDS:
        row["verdict"] = max(row["verdict"], "suspect", key=["ok", "suspect", "broken"].index)
        row["reasons"].append(f"video is {drift:+.1f}s off the audio")

    if r_fps and avg_fps and abs(r_fps - avg_fps) / r_fps > VFR_TOLERANCE:
        row["verdict"] = max(row["verdict"], "suspect", key=["ok", "suspect", "broken"].index)
        row["reasons"].append(
            f"variable framerate (declared {r_fps:.3f}, actual {avg_fps:.3f})"
        )
    return row


def uploaded_paths(db: Path) -> dict[Path, str]:
    """Map video_path -> youtube_video_id for everything actually on YouTube."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT video_path, youtube_video_id FROM youtube_uploads "
        "WHERE youtube_video_id IS NOT NULL AND youtube_video_id != ''"
    ).fetchall()
    conn.close()
    return {Path(r["video_path"]): r["youtube_video_id"] for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uploaded", action="store_true",
                    help="only check files that already have a YouTube video id")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    data = Path(settings.data_root)
    on_youtube = uploaded_paths(data / "app.db")

    if args.uploaded:
        targets = [p for p in on_youtube if p.is_file()]
    else:
        targets = sorted(data.glob("videos/*.mp4"))
        targets += sorted(data.glob("books/*/patch_videos/*.mp4"))
        targets += sorted(data.glob("books/*/video_*.mp4"))

    if not targets:
        print("no video files found")
        return 0

    buckets: dict[str, list[dict]] = {"broken": [], "suspect": [], "ok": []}
    for path in targets:
        row = inspect(path)
        buckets[row["verdict"]].append(row)

    for verdict, label in (("broken", "MUST RE-RENDER"), ("suspect", "WORTH A LOOK")):
        rows = buckets[verdict]
        if not rows:
            continue
        print(f"\n=== {label} ({len(rows)}) ===")
        for row in rows:
            try:
                shown = row["path"].relative_to(root)
            except ValueError:
                shown = row["path"]
            vid = on_youtube.get(row["path"])
            suffix = f"  [on YouTube: {vid}]" if vid else ""
            print(f"  {shown}{suffix}")
            for reason in row["reasons"]:
                print(f"      - {reason}")

    print(f"\nchecked {len(targets)}: {len(buckets['broken'])} broken, "
          f"{len(buckets['suspect'])} suspect, {len(buckets['ok'])} ok")
    return 1 if buckets["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

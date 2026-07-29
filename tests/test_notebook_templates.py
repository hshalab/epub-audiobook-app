"""Regression tests for the exported Colab/Kaggle notebook templates.

The platform is chosen by ONE manual global flag, IS_KAGGLE, defined in the
first code cell (True = Kaggle, False = Colab). No cell may auto-detect the
platform: Kaggle images ship the google.colab package, so `from google.colab
import drive` SUCCEEDS on Kaggle and drive.mount() then raises
NotImplementedError - `except ImportError` can never tell the two platforms
apart, and per-cell re-detection drifted between cells. Every cell that mounts
Drive must therefore be guarded by the IS_KAGGLE global instead.

The voice reference clip is also mandatory: the manifest-loading cell must stop
the run when the clip is missing instead of silently synthesizing with a random
(inconsistent) voice.
"""
from __future__ import annotations

import json
import re
import tempfile
import ast
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "app" / "assets"
TEMPLATES = [
    ASSETS / "colab_kaggle_tts_template.ipynb",
    ASSETS / "colab_kaggle_batch_tts_template.ipynb",
]


def _code_cells(path: Path) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_is_kaggle_is_a_manual_global_set_in_cell_1(template):
    cells = _code_cells(template)
    assert "IS_KAGGLE = False" in cells[0], (
        f"{template.name}: the first code cell must define the global "
        "IS_KAGGLE = True/False flag used by every other cell"
    )
    for src in cells:
        assert 'os.path.isdir("/kaggle")' not in src, (
            f"{template.name}: cells must use the global IS_KAGGLE flag from "
            "Cell 1 instead of auto-detecting the platform per cell"
        )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_drive_mount_never_guarded_by_importerror(template):
    for src in _code_cells(template):
        if "drive.mount(" in src:
            assert "except ImportError" not in src, (
                f"{template.name}: google.colab imports fine on Kaggle, so "
                "except ImportError cannot distinguish Kaggle from Colab"
            )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_drive_mount_cells_guarded_by_is_kaggle(template):
    for src in _code_cells(template):
        if "drive.mount(" not in src:
            continue
        assert "IS_KAGGLE" in src, (
            f"{template.name}: a cell mounting Drive must branch on the "
            "global IS_KAGGLE flag so 'Run all' works on both platforms"
        )
        assert src.index("IS_KAGGLE") < src.index("drive.mount("), (
            f"{template.name}: the IS_KAGGLE check must come BEFORE drive.mount()"
        )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_manifest_cell_requires_reference_wav(template):
    cells = [
        src for src in _code_cells(template)
        if '.get("reference_wav")' in src
    ]
    assert cells, f"{template.name}: no manifest-loading cell found"
    for src in cells:
        assert "raise" in src, (
            f"{template.name}: the manifest cell must raise when the voice "
            "reference clip is missing (it is mandatory for consistent audio)"
        )


def test_batch_cell_8_is_deterministic_and_streams_atomic_merge():
    src = _code_cells(TEMPLATES[1])[7]
    assert "seed=42" in src
    assert "cfg_value=2.0" in src
    assert "inference_timesteps=10" in src
    assert "_CHUNK_PAUSE_MS = 300" in src
    assert "sf.SoundFile" in src
    assert "PCM_16" in src
    assert "NamedTemporaryFile" in src or "tempfile" in src
    assert "os.replace" in src
    assert "np.concatenate" not in src


def test_batch_cell_8_validates_metadata_and_writes_version_1_timeline():
    src = _code_cells(TEMPLATES[1])[7]
    for token in (
        "chunk_metadata", "chapter_index", "chapter_title", "is_chapter_start",
        "timeline", '"version": 1', "flush", "fsync", "warning",
    ):
        assert token in src
    assert "actual frame" in src.lower() or "frames" in src.lower()
    assert "drive_persist" in src
    assert '"total_frames"' in src
    assert '"title"' in src
    assert '"start_seconds"' in src
    assert "validate_chunk_metadata" in src
    assert "merge_wav_files" in src


def test_batch_cell_8_preserves_legacy_fallback_and_skip_warning():
    src = _code_cells(TEMPLATES[1])[7]
    assert 'manifest.get("chunk_metadata")' in src
    assert "SKIP_EXISTING" in src
    assert "delete result and rerun" in src.lower()
    assert "chunk_metadata missing" in src.lower()
    assert "finally" in src


def _cell8_helpers():
    src = _code_cells(TEMPLATES[1])[7]
    match = re.search(r"^# BEGIN CELL 8 HELPERS$(.*?)^# END CELL 8 HELPERS$", src, re.M | re.S)
    assert match, "Cell 8 helper block missing"
    namespace = {}
    exec(match.group(1), namespace)
    return namespace


def test_cell8_helpers_merge_mono_and_stereo_with_exact_pause_and_timeline():
    import numpy as np
    import soundfile as sf

    helpers = _cell8_helpers()
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for channels in (1, 2):
            paths.clear()
            for index, value in enumerate((1000, 2000)):
                path = Path(directory) / f"{channels}-{index}.wav"
                data = np.full((3, channels), value, dtype=np.int16) if channels == 2 else np.full(3, value, dtype=np.int16)
                sf.write(path, data, 10, subtype="PCM_16")
                paths.append(str(path))
            result = Path(directory) / f"result-{channels}.wav"
            timeline = helpers["merge_wav_files"](paths, str(result), 300, [{"filename": Path(p).name, "chapter_index": i, "chapter_title": f"C{i}", "is_chapter_start": True, "text": "hello"} for i, p in enumerate(paths)])
            audio, rate = sf.read(result, dtype="int16", always_2d=(channels == 2))
            assert rate == 10 and len(audio) == 9
            assert np.all(audio[3:6] == 0)
            assert timeline["total_frames"] == 9
            assert set(timeline) == {"version", "sample_rate", "total_frames", "chapters"}
            assert timeline["chapters"][1]["start_frame"] == 6
            assert timeline["chapters"][1]["start_seconds"] == 0.6


def test_cell8_helper_rejects_invalid_metadata_without_raising():
    validate = _cell8_helpers()["validate_chunk_metadata"]
    cases = [None, {}, True, [None], [{"filename": "x"}], [{"filename": "x", "chapter_index": True, "chapter_title": "", "is_chapter_start": True}]]
    for metadata in cases:
        assert validate(metadata, ["x", "y"]) is None


def test_cell8_merge_format_failures_preserve_result_and_clean_temp():
    import numpy as np
    import soundfile as sf

    merge = _cell8_helpers()["merge_wav_files"]
    with tempfile.TemporaryDirectory() as directory:
        result = Path(directory) / "result.wav"
        result.write_bytes(b"old-result")
        first = Path(directory) / "first.wav"
        second = Path(directory) / "second.wav"
        sf.write(first, np.ones(3, dtype=np.int16), 10, subtype="PCM_16")
        sf.write(second, np.ones((3, 2), dtype=np.int16), 10, subtype="PCM_16")
        for paths in ((first, second),):
            with pytest.raises(RuntimeError, match="sample rates/channels"):
                merge([str(path) for path in paths], str(result), 300)
        assert result.read_bytes() == b"old-result"
        assert not list(Path(directory).glob(".result-*.wav"))


def test_cell8_merge_sample_rate_and_corrupt_failures_preserve_result():
    import numpy as np
    import soundfile as sf

    merge = _cell8_helpers()["merge_wav_files"]
    with tempfile.TemporaryDirectory() as directory:
        result = Path(directory) / "result.wav"
        result.write_bytes(b"old-result")
        first = Path(directory) / "first.wav"
        second = Path(directory) / "second.wav"
        sf.write(first, np.ones(3, dtype=np.int16), 10, subtype="PCM_16")
        sf.write(second, np.ones(3, dtype=np.int16), 20, subtype="PCM_16")
        with pytest.raises(RuntimeError, match="sample rates/channels"):
            merge([str(first), str(second)], str(result), 300)
        corrupt = Path(directory) / "corrupt.wav"
        corrupt.write_bytes(b"not-a-wav")
        with pytest.raises(RuntimeError, match="preflight"):
            merge([str(first), str(corrupt)], str(result), 300)
        assert result.read_bytes() == b"old-result"
        assert not list(Path(directory).glob(".result-*.wav"))


def test_cell8_merge_failure_after_temp_creation_preserves_result_and_cleans_temp(monkeypatch):
    import numpy as np
    import soundfile as sf

    helpers = _cell8_helpers()
    merge = helpers["merge_wav_files"]
    with tempfile.TemporaryDirectory() as directory:
        result = Path(directory) / "result.wav"
        result.write_bytes(b"old-result")
        first = Path(directory) / "first.wav"
        sf.write(first, np.ones(3, dtype=np.int16), 10, subtype="PCM_16")
        original_replace = helpers["os"].replace
        monkeypatch.setattr(helpers["os"], "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))
        try:
            with pytest.raises(OSError, match="replace failed"):
                merge([str(first)], str(result), 300)
        finally:
            helpers["os"].replace = original_replace
        assert result.read_bytes() == b"old-result"
        assert not list(Path(directory).glob(".result-*.wav"))


def test_cell8_sidecar_atomic_write_preserves_old_file_on_replace_failure():
    import json

    helpers = _cell8_helpers()
    assert "write_timeline_atomic" in helpers
    with tempfile.TemporaryDirectory() as directory:
        sidecar = Path(directory) / "result.timeline.json"
        sidecar.write_text('{"old": true}', encoding="utf-8")
        original_replace = helpers["os"].replace
        helpers["os"].replace = lambda *_args: (_ for _ in ()).throw(OSError("replace failed"))
        try:
            with pytest.raises(OSError):
                helpers["write_timeline_atomic"]({"version": 1}, str(sidecar))
        finally:
            helpers["os"].replace = original_replace
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {"old": True}
        assert not list(Path(directory).glob(".timeline-*.json"))


@pytest.mark.parametrize("failure", ["dump", "fsync"])
def test_cell8_sidecar_pre_replace_failures_preserve_old_file_and_cleanup(monkeypatch, failure):
    helpers = _cell8_helpers()
    with tempfile.TemporaryDirectory() as directory:
        sidecar = Path(directory) / "result.timeline.json"
        sidecar.write_text('{"old": true}', encoding="utf-8")
        if failure == "dump":
            monkeypatch.setattr(helpers["json"], "dump", lambda *_args: (_ for _ in ()).throw(OSError("dump failed")))
        else:
            monkeypatch.setattr(helpers["os"], "fsync", lambda *_args: (_ for _ in ()).throw(OSError("fsync failed")))
        with pytest.raises(OSError):
            helpers["write_timeline_atomic"]({"version": 1}, str(sidecar))
        assert sidecar.read_text(encoding="utf-8") == '{"old": true}'
        assert not list(Path(directory).glob(".timeline-*.json"))


def test_cell8_validator_requires_non_empty_text():
    helpers = _cell8_helpers()
    validate = helpers["validate_chunk_metadata"]
    chunks = ["chunk_000.txt", "chunk_001.txt"]

    def entry(offset, **overrides):
        item = {
            "filename": chunks[offset],
            "chapter_index": 0,
            "chapter_title": "One",
            "is_chapter_start": offset == 0,
            "text": "hello",
        }
        item.update(overrides)
        return item

    assert validate([entry(0), entry(1)], chunks) is not None
    # four-field legacy metadata is no longer valid
    legacy = entry(0)
    del legacy["text"]
    assert validate([legacy, entry(1)], chunks) is None
    assert validate([entry(0, text=""), entry(1)], chunks) is None
    assert validate([entry(0, text="   "), entry(1)], chunks) is None
    assert validate([entry(0, text=123), entry(1)], chunks) is None


def test_cell8_chunk_text_prefers_manifest_and_falls_back_to_txt(tmp_path):
    helpers = _cell8_helpers()
    chunk_text_for = helpers["chunk_text_for"]

    inlined = {
        "chunks": ["chunk_000.txt"],
        "chunk_metadata": [{"filename": "chunk_000.txt", "text": "from manifest"}],
    }
    assert chunk_text_for(inlined, 0, str(tmp_path)) == "from manifest"

    (tmp_path / "chunk_000.txt").write_text("from disk", encoding="utf-8")
    legacy = {"chunks": ["chunk_000.txt"], "chunk_metadata": [{"filename": "chunk_000.txt"}]}
    assert chunk_text_for(legacy, 0, str(tmp_path)) == "from disk"
    assert chunk_text_for({"chunks": ["chunk_000.txt"]}, 0, str(tmp_path)) == "from disk"


def test_cell8_available_wavs_merges_local_dirs_and_remote_inventory(tmp_path):
    helpers = _cell8_helpers()
    available_wavs = helpers["available_wavs"]

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "chunk_000.wav").write_bytes(b"")
    (out_dir / "notes.txt").write_bytes(b"")

    remote = {
        "patches/patch_000/output/chunk_001.wav": "id1",
        "patches/patch_000/output/nested/chunk_009.wav": "id9",
        "patches/patch_001/output/chunk_002.wav": "id2",
        "result/000 - a.wav": "idr",
    }
    names = available_wavs(
        [str(out_dir), str(tmp_path / "missing")], remote, "patches/patch_000/output"
    )
    assert names == {"chunk_000.wav", "chunk_001.wav"}


def test_batch_cell_8_uses_remote_inventory_and_lazy_fetch():
    src = _code_cells(TEMPLATES[1])[7]
    assert "_drive_file_ids" in src
    assert "drive_fetch_many" in src
    assert 'entry["result_wav"] in REMOTE' in src
    assert "available_wavs(" in src
    assert "chunk_text_for(" in src
    assert "find_wav" not in src
    assert "os.listdir" in src


def test_cell8_persist_failures_are_caught_separately():
    src = _code_cells(TEMPLATES[1])[7]
    assert "Result persistence failed" in src
    assert "Timeline persistence failed after local install" in src
    assert "try: persist(result_path, \"result\")" in src
    tree = ast.parse(src)
    persist_try_bodies = [
        node.body for node in ast.walk(tree)
        if isinstance(node, ast.Try) and any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "persist"
            for call in ast.walk(node)
        )
    ]
    assert len(persist_try_bodies) >= 2


def _cell4_helpers():
    src = _code_cells(TEMPLATES[1])[3]
    match = re.search(r"^# BEGIN CELL 4 HELPERS$(.*?)^# END CELL 4 HELPERS$", src, re.M | re.S)
    assert match, "Cell 4 helper block missing"
    namespace = {}
    exec(match.group(1), namespace)
    return namespace


def _fake_batch_inventory(patch_count=3, merged=0, chunks_per_patch=2):
    """Remote inventory for a batch where the first `merged` patches are finished."""
    manifest = {
        "reference_wav": "reference.wav",
        "patches": [
            {
                "patch_id": i,
                "patch_index": i,
                "folder": f"patches/patch_{i:03d}",
                "result_wav": f"result/{i:03d} - p.wav",
            }
            for i in range(patch_count)
        ],
    }
    remote = {"batch_manifest.json": "id", "reference.wav": "id", "music/bg.mp3": "id"}
    for i, entry in enumerate(manifest["patches"]):
        remote[f"{entry['folder']}/manifest.json"] = "id"
        remote[f"{entry['folder']}/background.jpg"] = "id"
        if i < merged:
            remote[entry["result_wav"]] = "id"
            for c in range(chunks_per_patch):
                remote[f"{entry['folder']}/output/chunk_{c:03d}.wav"] = "id"
    return manifest, remote


def test_cell4_plan_downloads_only_manifests_and_reference():
    plan_batch_downloads = _cell4_helpers()["plan_batch_downloads"]
    manifest, remote = _fake_batch_inventory(patch_count=3, merged=3)

    planned = plan_batch_downloads(manifest, remote)

    assert set(planned) == {
        "batch_manifest.json",
        "reference.wav",
        "patches/patch_000/manifest.json",
        "patches/patch_001/manifest.json",
        "patches/patch_002/manifest.json",
    }
    # A fully merged batch downloads no chunk WAV, no result, no background, no music.
    for rel in planned:
        assert "/output/" not in rel
        assert not rel.startswith("result/")
        assert not rel.startswith("music/")
        assert "background" not in rel


def test_cell4_plan_skips_paths_absent_from_the_inventory():
    plan_batch_downloads = _cell4_helpers()["plan_batch_downloads"]
    manifest, remote = _fake_batch_inventory(patch_count=2, merged=0)
    del remote["patches/patch_001/manifest.json"]
    del remote["reference.wav"]

    planned = plan_batch_downloads(manifest, remote)

    assert planned == ["batch_manifest.json", "patches/patch_000/manifest.json"]


def test_cell4_lists_before_downloading_and_is_thread_safe():
    src = _code_cells(TEMPLATES[1])[3]
    assert "plan_batch_downloads(" in src
    assert "ThreadPoolExecutor" in src
    assert "threading.local()" in src
    assert "def drive_fetch_many(" in src
    assert "def drive_persist(" in src
    # the old walk that downloaded while listing must be gone
    assert "def _sync_down(" not in src

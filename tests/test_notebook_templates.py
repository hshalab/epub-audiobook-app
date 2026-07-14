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

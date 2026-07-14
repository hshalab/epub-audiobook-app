"""Regression tests for the exported Colab/Kaggle notebook templates.

Kaggle images ship the google.colab package, so `from google.colab import drive`
SUCCEEDS on Kaggle and drive.mount() then raises NotImplementedError - meaning
`except ImportError` can never be used to tell the two platforms apart. Every
cell that mounts Drive must therefore detect Kaggle first (via the /kaggle
mount) and only touch Drive on real Colab.

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
def test_drive_mount_never_guarded_by_importerror(template):
    for src in _code_cells(template):
        if "drive.mount(" in src:
            assert "except ImportError" not in src, (
                f"{template.name}: google.colab imports fine on Kaggle, so "
                "except ImportError cannot distinguish Kaggle from Colab"
            )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_drive_mount_cells_detect_kaggle_first(template):
    for src in _code_cells(template):
        if "drive.mount(" not in src:
            continue
        assert "/kaggle" in src, (
            f"{template.name}: a cell mounting Drive must check for Kaggle "
            "so 'Run all' works on both platforms"
        )
        assert src.index("/kaggle") < src.index("drive.mount("), (
            f"{template.name}: the Kaggle check must come BEFORE drive.mount()"
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

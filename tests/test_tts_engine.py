import ast
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from app.tts_engine import VoxCPMEngine


class FakeModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return np.array([1.0])


def _installed_generate_parameters() -> set[str]:
    """Parameter names of the installed VoxCPM._generate, read straight off its source.

    Parsed with ast rather than imported because importing voxcpm pulls in torch and
    costs ~30s. FakeModel.generate(**kwargs) swallows anything, so without checking the
    real signature a kwarg the library rejects (this happened with 'seed') passes every
    test here and only blows up on the GPU box mid-synthesis."""
    spec = importlib.util.find_spec("voxcpm")  # does not execute voxcpm/__init__.py
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("voxcpm is not installed")
    source = Path(spec.submodule_search_locations[0], "core.py")
    if not source.exists():
        pytest.skip(f"voxcpm layout changed: {source} is missing")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for klass in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "VoxCPM"):
        for func in (n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_generate"):
            args = func.args
            return {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs} - {"self"}
    pytest.skip("voxcpm.core.VoxCPM._generate not found")


def test_synthesize_chunk_only_sends_kwargs_the_installed_voxcpm_accepts(monkeypatch):
    monkeypatch.setattr("app.tts_engine._seed_rng", lambda seed: None)  # torch not needed here
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    engine.synthesize_chunk("hello", reference_wav_path="voice.wav", prompt_text="hello")

    unsupported = set(model.calls[0]) - _installed_generate_parameters()
    assert unsupported == set()


def test_synthesize_chunk_passes_generation_defaults(monkeypatch):
    monkeypatch.setattr("app.tts_engine._seed_rng", lambda seed: None)
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    result = engine.synthesize_chunk("hello")

    assert result.tolist() == [1.0]
    assert model.calls == [
        {
            "text": "hello",
            "cfg_value": 2.0,
            "inference_timesteps": 10,
        }
    ]


def test_synthesize_chunk_seeds_the_rng_before_each_generate(monkeypatch):
    """VoxCPM 2.x dropped the per-call seed argument, so reproducibility now depends on
    seeding torch's global RNG ourselves - and on doing it before sampling starts."""
    events = []
    monkeypatch.setattr("app.tts_engine._seed_rng", lambda seed: events.append(("seed", seed)))

    class RecordingModel(FakeModel):
        def generate(self, **kwargs):
            events.append(("generate", kwargs["text"]))
            return super().generate(**kwargs)

    engine = VoxCPMEngine(seed=7)
    engine._model = RecordingModel()

    engine.synthesize_chunk("một")
    engine.synthesize_chunk("hai")

    assert events == [("seed", 7), ("generate", "một"), ("seed", 7), ("generate", "hai")]


def test_synthesize_chunk_passes_ultimate_cloning_prompt_arguments(monkeypatch):
    monkeypatch.setattr("app.tts_engine._seed_rng", lambda seed: None)
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    engine.synthesize_chunk("hello", reference_wav_path="voice.wav", prompt_text="hello")

    assert model.calls[0] == {
        "text": "hello",
        "cfg_value": 2.0,
        "inference_timesteps": 10,
        "reference_wav_path": "voice.wav",
        "prompt_wav_path": "voice.wav",
        "prompt_text": "hello",
    }


def test_synthesize_chunk_without_prompt_text_omits_prompt_arguments(monkeypatch):
    monkeypatch.setattr("app.tts_engine._seed_rng", lambda seed: None)
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    engine.synthesize_chunk("hello", reference_wav_path="voice.wav")

    assert "prompt_wav_path" not in model.calls[0]
    assert "prompt_text" not in model.calls[0]

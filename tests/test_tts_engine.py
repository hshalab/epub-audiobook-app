import numpy as np

from app.tts_engine import VoxCPMEngine


class FakeModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return np.array([1.0])


def test_synthesize_chunk_passes_default_seed_and_generation_defaults():
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
            "seed": 42,
        }
    ]


def test_synthesize_chunk_passes_custom_seed():
    model = FakeModel()
    engine = VoxCPMEngine(seed=7)
    engine._model = model

    engine.synthesize_chunk("hello")

    assert model.calls[0]["seed"] == 7


def test_synthesize_chunk_passes_ultimate_cloning_prompt_arguments():
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    engine.synthesize_chunk("hello", reference_wav_path="voice.wav", prompt_text="hello")

    assert model.calls[0] == {
        "text": "hello",
        "cfg_value": 2.0,
        "inference_timesteps": 10,
        "seed": 42,
        "reference_wav_path": "voice.wav",
        "prompt_wav_path": "voice.wav",
        "prompt_text": "hello",
    }


def test_synthesize_chunk_without_prompt_text_omits_prompt_arguments():
    model = FakeModel()
    engine = VoxCPMEngine()
    engine._model = model

    engine.synthesize_chunk("hello", reference_wav_path="voice.wav")

    assert "prompt_wav_path" not in model.calls[0]
    assert "prompt_text" not in model.calls[0]

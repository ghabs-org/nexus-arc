"""Inference markers must follow real output and preserve model return values."""

import sys
from types import SimpleNamespace

from tests.fixtures import studio_voice_interrupt_worker as fixture


def test_piper_marker_follows_first_output_and_preserves_chunks(tmp_path, monkeypatch):
    marker = tmp_path / "ready"
    first, second = object(), object()

    class Voice:
        def synthesize(self, text):
            assert text == "selected text" and not marker.exists()
            yield first
            yield second

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=Voice))
    fixture.observe_piper(marker)
    output = Voice().synthesize("selected text")
    assert not marker.exists()
    assert next(output) is first and marker.exists()
    assert next(output) is second


def test_whisper_marker_follows_encoder_output_without_replacing_tensor(tmp_path, monkeypatch):
    marker = tmp_path / "ready"
    callbacks = []
    model = SimpleNamespace(encoder=SimpleNamespace(register_forward_hook=callbacks.append))
    calls = []

    def load_model(*args, **kwargs):
        assert not marker.exists()
        calls.append((args, kwargs))
        return model

    whisper = SimpleNamespace(load_model=load_model)
    monkeypatch.setitem(sys.modules, "whisper", whisper)
    fixture.observe_whisper(marker)
    assert whisper.load_model("pinned.pt", device="cpu") is model
    assert not marker.exists() and calls == [(("pinned.pt",), {"device": "cpu"})]
    assert callbacks[0](model.encoder, (), object()) is None
    assert marker.exists()

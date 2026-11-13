"""Test-only inference markers; execute real model code without pausing or replacing outputs."""

import os
import sys
from pathlib import Path


def mark(path):
    if not path.exists():
        path.write_text(str(os.getpid()), encoding="ascii")


def observe_piper(path):
    from piper import PiperVoice

    original = PiperVoice.synthesize

    def synthesize(self, *args, **kwargs):
        for chunk in original(self, *args, **kwargs):
            # The real ONNX call already produced this chunk. Do not introduce
            # a fixture sleep or replace any audio/model output.
            mark(path)
            yield chunk

    PiperVoice.synthesize = synthesize


def observe_whisper(path):
    import whisper

    original = whisper.load_model

    def load_model(*args, **kwargs):
        model = original(*args, **kwargs)

        def after_encoder(_module, _inputs, _output):
            # A real encoder forward has completed; return None so PyTorch
            # retains the original tensor and transcription continues normally.
            mark(path)

        model.encoder.register_forward_hook(after_encoder)
        return model

    whisper.load_model = load_model


def main():
    kind, ready, *arguments = sys.argv[1:]
    ready = Path(ready)
    sys.argv = [sys.argv[0], *arguments]
    if kind == "piper":
        observe_piper(ready)
        from piper.__main__ import main as piper_main

        piper_main()
    elif kind == "whisper":
        observe_whisper(ready)
        from nexus.core.studio_speech_worker import main as whisper_main

        whisper_main()
    else:
        raise ValueError("Choose the explicit real-model interruption fixture")


if __name__ == "__main__":
    main()

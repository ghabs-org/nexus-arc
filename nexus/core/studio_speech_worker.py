"""Bounded local Whisper transcription worker; never downloads a model."""

import json
import sys


def main():
    try:
        import resource
    except ImportError:
        resource = None
    from pathlib import Path

    import whisper

    if resource:
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    model, source = map(Path, sys.argv[1:3])
    if not model.is_file() or not source.is_file():
        raise ValueError("Configure an installed local model and audio input")
    language = sys.argv[3] or None
    result = whisper.load_model(str(model), device="cpu").transcribe(
        str(source), language=language, fp16=False
    )
    print(json.dumps({"text": result["text"][:32000], "language": result.get("language")}))


if __name__ == "__main__":
    main()

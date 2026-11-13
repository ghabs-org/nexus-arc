"""Explicit test preparation only; product workers never import or download these models."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/lessac/medium/"
MODELS = {
    "tiny.en.pt": {
        "url": "https://openaipublic.azureedge.net/main/whisper/models/d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03/tiny.en.pt",
        "sha256": "d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03",
        "bytes": 75571315,
    },
    "en_US-lessac-medium.onnx": {
        "url": PIPER + "en_US-lessac-medium.onnx",
        "sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        "bytes": 63201294,
    },
    "en_US-lessac-medium.onnx.json": {
        "url": PIPER + "en_US-lessac-medium.onnx.json",
        "sha256": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        "bytes": 4885,
    },
    "PIPER_MODEL_CARD": {
        "url": PIPER + "MODEL_CARD",
        "sha256": "ce49eb457742208166d399a40cdec2c7fa9db77960930031564ab56f12882645",
        "bytes": 351,
    },
}


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, settings in MODELS.items():
        path = directory / name
        created = False
        try:
            if not path.exists():
                with path.open("xb") as output:
                    created = True
                    with urlopen(settings["url"], timeout=30) as response:
                        remaining = settings["bytes"]
                        while True:
                            block = response.read(min(1024 * 1024, remaining + 1))
                            if not block:
                                break
                            if len(block) > remaining:
                                raise ValueError("Test model exceeds its pinned size")
                            output.write(block)
                            remaining -= len(block)
            if path.is_symlink() or path.stat().st_size != settings["bytes"]:
                raise ValueError("Test model size/type differs from the approved snapshot")
            with path.open("rb") as content:
                digest = hashlib.file_digest(content, "sha256").hexdigest()
            if digest != settings["sha256"]:
                raise ValueError("Test model hash differs from the approved snapshot")
            results[name] = {"sha256": digest, "bytes": path.stat().st_size}
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory), sort_keys=True))

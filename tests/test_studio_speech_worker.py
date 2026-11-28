import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexus.core import studio_speech_worker


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_whisper_worker_preserves_cpu_only_model_and_platform_limits(
    tmp_path, monkeypatch, capsys, platform
):
    model, audio = tmp_path / "installed.pt", tmp_path / "selected.wav"
    model.write_bytes(b"fixture-model")
    audio.write_bytes(b"fixture-audio")
    transcribe = Mock(return_value={"text": "Local transcript", "language": "en"})
    load = Mock(return_value=SimpleNamespace(transcribe=transcribe))
    resource = SimpleNamespace(RLIMIT_CPU=1, RLIMIT_AS=2, setrlimit=Mock())
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=load))
    monkeypatch.setitem(sys.modules, "resource", None if platform == "win32" else resource)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "argv", ["worker", str(model), str(audio), "en"])
    studio_speech_worker.main()
    assert json.loads(capsys.readouterr().out) == {"text": "Local transcript", "language": "en"}
    load.assert_called_once_with(str(model), device="cpu")
    transcribe.assert_called_once_with(str(audio), language="en", fp16=False)
    if platform == "win32":
        resource.setrlimit.assert_not_called()
    elif platform == "darwin":
        resource.setrlimit.assert_called_once_with(resource.RLIMIT_CPU, (120, 120))
    else:
        assert resource.setrlimit.call_args_list == [
            ((resource.RLIMIT_CPU, (120, 120)),),
            ((resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3)),),
        ]


def test_missing_model_is_rejected_before_loading_whisper(tmp_path, monkeypatch):
    load = Mock(side_effect=AssertionError("Missing models must never trigger loading/download"))
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(load_model=load))
    monkeypatch.setitem(sys.modules, "resource", None)
    monkeypatch.setattr(
        sys, "argv", ["worker", str(tmp_path / "missing.pt"), str(tmp_path / "missing.wav"), ""]
    )
    with pytest.raises(ValueError, match="installed local model"):
        studio_speech_worker.main()
    load.assert_not_called()

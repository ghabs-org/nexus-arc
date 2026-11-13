"""Passive readiness must not mark a model usable without its runtime."""

from unittest.mock import patch

from nexus.core.studio import StudioService
from nexus.core.studio_speech import local_readiness


def test_model_alone_does_not_make_whisper_ready(tmp_path):
    model = tmp_path / "local.pt"
    model.write_bytes(b"installed-model-placeholder")
    with (
        patch("nexus.core.studio_speech.importlib.util.find_spec", return_value=None),
        patch("nexus.core.studio_speech.shutil.which", return_value=None),
    ):
        missing = local_readiness({"kind": "whisper", "model_path": str(model)})
    assert missing == ["Whisper Python runtime", "FFmpeg executable"]
    assert str(model) not in str(missing)


def test_piper_needs_voice_configuration_and_executable(tmp_path):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"model")
    with patch("nexus.core.studio_speech.shutil.which", return_value="/configured/piper"):
        assert local_readiness({"kind": "piper", "model_path": str(model)}) == [
            "Piper voice configuration (.onnx.json)"
        ]
        model.with_suffix(".onnx.json").write_text("{}")
        assert local_readiness({"kind": "piper", "model_path": str(model)}) == []
    with patch("nexus.core.studio_speech.shutil.which", return_value=None):
        assert local_readiness({"kind": "piper", "model_path": str(model)}) == ["Piper executable"]


def test_public_profiles_are_passive_and_keep_api_accounts_unverified(tmp_path):
    model = tmp_path / "private-model.pt"
    model.write_bytes(b"private-model-placeholder")
    studio = StudioService(
        tmp_path / "workspace",
        speech_options={
            "profiles": {
                "configured-local": {
                    "kind": "whisper",
                    "action": "transcribe",
                    "model_path": str(model),
                }
            }
        },
    )
    try:
        with (
            patch("nexus.core.studio_speech.importlib.util.find_spec", return_value=object()),
            patch("nexus.core.studio_speech.shutil.which", return_value="/configured/ffmpeg"),
            patch(
                "subprocess.run", side_effect=AssertionError("Readiness must not start software")
            ),
            patch.object(
                studio.speech,
                "_client",
                side_effect=AssertionError("Readiness must not resolve an API"),
            ),
        ):
            items = {p["id"]: p for p in studio.speech.available()["items"]}
        local = items["configured-local"]
        assert local["model_ready"] and local["missing_prerequisites"] == []
        assert local["verification_scope"] == "local prerequisites only"
        assert str(model) not in str(items)
        assert items["openai-transcribe"]["verification_scope"] == "account not checked"
        assert not items["local-whisper"]["model_ready"]
        assert not studio.records("runs") and not studio.records("speech-input-owners")
    finally:
        studio.close()

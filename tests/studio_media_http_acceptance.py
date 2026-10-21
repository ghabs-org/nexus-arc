"""Real media HTTP transport against an owned local Postman fixture, not an account."""

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_dynamic_embeds import upload
from tests.test_studio_media import png, profiles, wav


@pytest.fixture
def service(tmp_path, monkeypatch):
    base = os.environ["NEXUS_MEDIA_SIM_URL"].rstrip("/")
    assert base.startswith("http://127.0.0.1:")
    monkeypatch.setenv("MEDIA_TEST_KEY", "synthetic-media-key")
    configured = profiles()
    for profile in configured.values():
        profile["url"] = base
    app = StudioService(tmp_path, media_options={"profiles": configured})
    try:
        yield app
    finally:
        app.close()


@pytest.mark.parametrize(
    "action", ["image_generate", "image_edit", "image_describe", "audio_describe"]
)
def test_media_wire_operations_and_private_outputs(service, action):
    args = {"profile": action, "prompt": "Synthetic media"}
    if action != "image_generate":
        source = upload(
            service,
            "source.wav" if action == "audio_describe" else "source.png",
            wav() if action == "audio_describe" else png(),
        )
        args.update(artifact_id=source["id"], revision=1)
    if action == "image_edit":
        args.update(
            mask_artifact_id=upload(service, "mask.png", png((0, 0, 0, 0)))["id"], mask_revision=1
        )
    result = asyncio.run(service.media.execute(action, args))
    assert result["usage"]["total_tokens"] == 5
    if action in {"image_generate", "image_edit"}:
        artifact = service.catalog.get("artifacts", result["artifacts"][0]["id"])
        assert artifact["data"]["content_type"] == "image/png"
        assert artifact["revision"] == 1
    else:
        assert result["output"] and not result["artifacts"]


@pytest.mark.parametrize(
    "prompt",
    [
        "fixture:failure",
        "fixture:redirect",
        "fixture:timeout",
        "fixture:bad-json",
        "fixture:url-output",
    ],
)
def test_generation_failures_do_not_publish_or_prevent_explicit_recovery(service, prompt):
    if prompt == "fixture:timeout":
        service.media.profiles["image_generate"]["timeout"] = 1
    with pytest.raises(ValueError) as failure:
        asyncio.run(
            service.media.execute("image_generate", {"profile": "image_generate", "prompt": prompt})
        )
    assert "SECRET_PROVIDER_DETAIL" not in str(failure.value)
    assert not service.catalog.list("artifacts")
    result = asyncio.run(
        service.media.execute(
            "image_generate", {"profile": "image_generate", "prompt": "Explicit new request"}
        )
    )
    assert result["artifacts"][0]["revision"] == 1


@pytest.mark.parametrize("prompt", ["fixture:refusal", "fixture:truncated", "fixture:oversized"])
def test_description_results_are_validated(service, prompt):
    source = upload(service, "source.png", png())
    with pytest.raises(ValueError):
        asyncio.run(
            service.media.execute(
                "image_describe",
                {"profile": "image_describe", "prompt": prompt, "artifact_id": source["id"]},
            )
        )
    assert len(service.catalog.list("artifacts")) == 1


def test_transport_response_budget_is_enforced(service):
    profile = service.media.profiles["image_generate"]
    with pytest.raises(ValueError):
        service.research.request(
            "POST",
            profile["url"] + "/images/generations",
            {"Authorization": "Bearer synthetic-media-key"},
            {"model": "fixture", "prompt": "test", "n": 1, "output_format": "png"},
            private=True,
            response_limit=32,
        )


def test_full_example_workflow_uses_actual_http_and_pinned_artifacts(service):
    path = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_media.py"
    spec = importlib.util.spec_from_file_location("media_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.exercise(service)
    assert result["edited"]["input_artifacts"][0]["id"] == result["generated"]["artifacts"][0]["id"]

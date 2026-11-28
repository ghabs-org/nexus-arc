"""Configured media operations, private originals/results and no-fallback semantics."""

import asyncio
import base64
import io
import json
import threading
import wave

import pytest
from PIL import Image

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_connections import execution_identity
from nexus.core.studio_media import ACTIONS
from tests.test_studio_dynamic_embeds import settled, upload
from tests.test_studio_team import team as team


def png(color="red"):
    buffer = io.BytesIO()
    Image.new("RGBA", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return buffer.getvalue()


def profiles():
    return {
        action: {
            "action": action,
            "model": "synthetic-" + action,
            "enabled": True,
            "url": "http://127.0.0.1:49830/media/v1",
            "allow_private": True,
            "token_env": "MEDIA_TEST_KEY",
        }
        for action in ACTIONS
    }


def client(calls):
    def request(method, url, headers, body, text):
        calls.append((method, url, headers, body))
        assert headers == {"Authorization": "Bearer synthetic-media-key"}
        if "/images/" in url:
            assert body["n"] == 1 and body["output_format"] == "png"
            return {
                "data": [{"b64_json": base64.b64encode(png("blue")).decode()}],
                "usage": {"total_tokens": 5},
            }
        assert body["store"] is False
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "A synthetic media description."}}
            ],
            "usage": {"prompt_tokens": 3},
        }

    return request


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_TEST_KEY", "synthetic-media-key")
    calls = []
    service = StudioService(
        tmp_path,
        media_options={"profiles": profiles()},
        research_options={"request_client": client(calls)},
    )
    try:
        yield service, calls
    finally:
        service.close()


def test_media_requires_explicit_matching_enabled_profiles_and_no_catalog_calls(tmp_path):
    calls = []
    service = StudioService(tmp_path, research_options={"request_client": client(calls)})
    try:
        inventory = service.media.inventory()["items"]
        assert len(inventory) == 4 and not any(row["configured"] for row in inventory)
        assert all("subscriptions" in row["billing"] for row in inventory)
        for action in ACTIONS:
            with pytest.raises(ValueError, match="enabled"):
                asyncio.run(service.media.execute(action, {"profile": "missing", "prompt": "test"}))
        assert not calls
    finally:
        service.close()


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_each_media_operation_executes_and_pins_original_revisions(media, action):
    service, calls = media
    artifact = upload(
        service,
        "input.wav" if action == "audio_describe" else "input.png",
        wav() if action == "audio_describe" else png(),
    )
    args = {"profile": action, "prompt": "Describe or create this fixture"}
    if action != "image_generate":
        args.update(artifact_id=artifact["id"], revision=1)
    if action == "image_edit":
        mask = upload(service, "mask.png", png((0, 0, 0, 0)))
        args.update(mask_artifact_id=mask["id"], mask_revision=1)
    name = next(n for n in service.media.names if n.endswith(":" + action))
    run = settled(
        service,
        service.start_run(
            {"kind": "tool", "tool": name, "kwargs": args, "task": "Configured media"}
        ),
    )
    assert run["status"] == "completed", run
    result = json.loads(run["output"])
    assert len(calls) == 1 and result["model"] == "synthetic-" + action
    if action.startswith("image_") and action != "image_describe":
        ref = result["artifacts"][0]
        generated = service.catalog.get("artifacts", ref["id"])
        assert generated["generated_by_run"] == run["id"]
        assert service.artifacts.get(generated) == png("blue")
        assert run["structured_content"][0]["revision"] == 1
        assert result["usage"]["total_tokens"] == 5
    else:
        assert "description" in result["output"] and not result["artifacts"]
    if action != "image_generate":
        assert result["input_artifacts"][0]["revision"] == 1
    body = calls[0][3]
    if action == "image_edit":
        assert body["images"][0]["image_url"].endswith(base64.b64encode(png()).decode())
        assert body["mask"]["image_url"].startswith("data:image/png;base64,")
    if action == "audio_describe":
        assert (
            body["modalities"] == ["text"]
            and body["messages"][0]["content"][1]["type"] == "input_audio"
        )


@pytest.mark.parametrize(
    "variant",
    ["unknown_field", "wrong_action", "bad_image", "bad_audio", "bad_revision", "bad_mask"],
)
def test_media_preflight_rejects_invalid_inputs_before_charging(media, variant):
    service, calls = media
    image = upload(service, "input.png", png())
    args = {"profile": "image_edit", "prompt": "Edit fixture", "artifact_id": image["id"]}
    action = "image_edit"
    if variant == "unknown_field":
        args["url"] = "http://localhost/never"
    elif variant == "wrong_action":
        args["profile"] = "image_describe"
    elif variant in {"bad_image", "bad_audio"}:
        args["artifact_id"] = upload(service, "bad.bin", b"not valid media")["id"]
        if variant == "bad_audio":
            action = "audio_describe"
            args["profile"] = action
    elif variant == "bad_revision":
        args["revision"] = True
    else:
        args["mask_artifact_id"] = upload(service, "mask.png", png())["id"]
        service.artifacts.get = lambda record: b"invalid mask"
    with pytest.raises(ValueError):
        asyncio.run(service.media.execute(action, args))
    assert not calls


@pytest.mark.parametrize(
    "response",
    [
        {"data": [{"url": "https://example.invalid/never-fetch"}]},
        {"data": [{"b64_json": "invalid base64"}]},
        {"data": [{"b64_json": base64.b64encode(b"not PNG").decode()}]},
        {"data": [], "error": {"message": "SECRET_DO_NOT_LEAK"}},
        {"value": float("nan")},
    ],
)
def test_bad_provider_results_are_not_published_or_retried(media, response):
    service, calls = media

    def request(*args):
        calls.append(args)
        return response

    service.research.request_client = request
    before = len(service.catalog.list("artifacts"))
    with pytest.raises(ValueError) as failure:
        asyncio.run(
            service.media.execute("image_generate", {"profile": "image_generate", "prompt": "test"})
        )
    assert "SECRET_DO_NOT_LEAK" not in str(failure.value)
    assert len(calls) == 1 and len(service.catalog.list("artifacts")) == before


@pytest.mark.parametrize("variant", ["refusal", "truncated", "oversized"])
def test_description_refusal_or_incomplete_output_fails_without_artifacts(media, variant):
    service, calls = media
    image = upload(service, "input.png", png())
    response = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
    if variant == "refusal":
        response["choices"][0]["message"]["refusal"] = "not allowed"
    elif variant == "truncated":
        response["choices"][0]["finish_reason"] = "length"
    else:
        response["choices"][0]["message"]["content"] = "x" * 32769

    def request(*args):
        calls.append(args)
        return response

    service.research.request_client = request
    with pytest.raises(ValueError):
        asyncio.run(
            service.media.execute(
                "image_describe",
                {"profile": "image_describe", "prompt": "test", "artifact_id": image["id"]},
            )
        )
    assert len(calls) == 1


def test_missing_account_and_missing_image_dependency_fail_before_request(media, monkeypatch):
    service, calls = media
    monkeypatch.delenv("MEDIA_TEST_KEY")
    with pytest.raises(PermissionError):
        asyncio.run(
            service.media.execute("image_generate", {"profile": "image_generate", "prompt": "test"})
        )
    assert not calls
    monkeypatch.setattr("nexus.core.studio_media.importlib.util.find_spec", lambda _: None)
    with pytest.raises(ValueError, match="studio-images"):
        asyncio.run(
            service.media.execute("image_generate", {"profile": "image_generate", "prompt": "test"})
        )
    assert not calls


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_chat_tool_generated_artifact_is_preserved_in_saved_conversation(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("MEDIA_TEST_KEY", "synthetic-media-key")
    calls = []
    service = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            if "Untrusted tool result:" not in context.prompt:
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": next(
                                    n for n in service.media.names if n.endswith(":image_generate")
                                ),
                                "arguments": {
                                    "profile": "image_generate",
                                    "prompt": "Create fixture",
                                },
                            }
                        }
                    ),
                )
            return AgentResult(success=True, output="Your image is ready.")

    service = StudioService(
        tmp_path,
        provider_factory=lambda _: Provider(),
        state_options=None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "chat.sqlite"),
            "namespace": "media-chat",
            "workers": 1,
        },
        media_options={"profiles": profiles()},
        research_options={"request_client": client(calls)},
    )
    try:
        agent = service.save_definition(
            "agents",
            {
                "name": "Media assistant",
                "instructions": "Create one image",
                "tool_ids": [next(n for n in service.media.names if n.endswith(":image_generate"))],
            },
        )
        run = settled(
            service, service.start_run({"task": "Create fixture", "agent_id": agent["id"]})
        )
        assert run["status"] == "completed", run
        assert len(calls) == 1
        assert run["output"] == "Your image is ready."
        message = service.session(run["session_id"])["messages"][-1]
        assert message["structured_content"] == run["structured_content"]
        assert message["structured_content"][0]["type"] == "artifact"
    finally:
        service.close()


def test_shared_media_cannot_borrow_host_key_or_another_users_source(team, monkeypatch):
    from nexus.core.studio_media import StudioMedia

    service, policy, _, _, _ = team
    service.media.close()
    service.media = StudioMedia(service, profiles=profiles())
    monkeypatch.setenv("MEDIA_TEST_KEY", "HOST_KEY_MUST_NOT_BE_USED")
    artifact = upload(service, "private.png", png(), actor="alice")
    token = execution_identity.set("bob")
    calls = []
    service.research.request_client = client(calls)
    try:
        with pytest.raises(ValueError, match="unavailable"):
            asyncio.run(
                service.media.execute(
                    "image_describe",
                    {"profile": "image_describe", "prompt": "test", "artifact_id": artifact["id"]},
                )
            )
        with pytest.raises(PermissionError):
            asyncio.run(
                service.media.execute(
                    "image_generate", {"profile": "image_generate", "prompt": "test"}
                )
            )
        assert not calls
        policy.bindings["bob"] = "viewer"
        with pytest.raises(PermissionError):
            asyncio.run(
                service.media.execute(
                    "image_generate", {"profile": "image_generate", "prompt": "test"}
                )
            )
    finally:
        execution_identity.reset(token)


def test_cancelled_provider_request_cannot_publish_a_late_artifact(media):
    service, calls = media
    entered, release = threading.Event(), threading.Event()

    def request(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return {"data": [{"b64_json": base64.b64encode(png()).decode()}]}

    service.research.request_client = request
    try:
        name = next(n for n in service.media.names if n.endswith(":image_generate"))
        run = service.start_run(
            {
                "kind": "tool",
                "tool": name,
                "kwargs": {"profile": "image_generate", "prompt": "test"},
                "task": "Cancel fixture",
            }
        )
        assert entered.wait(5)
        service.cancel_run(run["id"])
        release.set()
        result = settled(service, run)
        assert result["status"] in {"cancelled", "interrupted"}
        assert not [
            r for r in service.catalog.list("artifacts") if r.get("generated_by_run") == run["id"]
        ]
        assert len(calls) == 1
    finally:
        release.set()


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_queued_media_pins_input_revision_and_ignores_forged_profile_guard(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("MEDIA_TEST_KEY", "synthetic-media-key")
    calls, pending = [], []
    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "media-pin",
            "workers": 1,
        }
    )
    service = StudioService(
        tmp_path / "workspace",
        state_options=options,
        media_options={"profiles": profiles()},
        research_options={"request_client": client(calls)},
    )
    try:
        monkeypatch.setattr(
            service.executor, "submit", lambda function, *args: pending.append((function, args))
        )
        image = upload(service, "original.png", png())
        tool = next(n for n in service.media.names if n.endswith(":image_describe"))
        run = service.start_run(
            {
                "kind": "tool",
                "tool": tool,
                "kwargs": {
                    "profile": "image_describe",
                    "prompt": "test",
                    "artifact_id": image["id"],
                },
                "_media_profile_revisions": {"image_describe": "forged"},
            }
        )
        payload = pending[0][1][1]
        assert payload["kwargs"]["revision"] == 1
        assert payload["_media_profile_revisions"] == service.media.revisions()
        assert "synthetic-media-key" not in json.dumps(payload)
        upload(service, "original.png", png("blue"), identity=image["id"])
        pending[0][0](*pending[0][1])
        completed = service.run(run["id"])
        assert completed["status"] == "completed", completed
        assert json.loads(completed["output"])["input_artifacts"][0]["revision"] == 1
        assert calls[0][3]["messages"][0]["content"][1]["image_url"]["url"].endswith(
            base64.b64encode(png()).decode()
        )
    finally:
        service.close()


def test_changed_profile_rejects_queued_work_before_request(media, monkeypatch):
    service, calls = media
    pending = []
    monkeypatch.setattr(
        service.executor, "submit", lambda function, *args: pending.append((function, args))
    )
    tool = next(n for n in service.media.names if n.endswith(":image_generate"))
    run = service.start_run(
        {"kind": "tool", "tool": tool, "kwargs": {"profile": "image_generate", "prompt": "test"}}
    )
    service.media.profiles["image_generate"]["model"] = "other-model"
    pending[0][0](*pending[0][1])
    assert service.run(run["id"])["status"] == "failed"
    assert not calls


def test_disabled_profile_during_request_cannot_publish(media):
    service, calls = media
    original = client(calls)

    def request(*args):
        result = original(*args)
        service.media.profiles["image_generate"]["enabled"] = False
        return result

    service.research.request_client = request
    with pytest.raises(ValueError, match="configuration changed"):
        asyncio.run(
            service.media.execute("image_generate", {"profile": "image_generate", "prompt": "test"})
        )
    assert len(calls) == 1 and not service.catalog.list("artifacts")


def test_shared_account_output_is_private_and_access_revocation_blocks_publish(team, monkeypatch):
    from nexus.core.studio_media import StudioMedia

    service, policy, _, request, _ = team
    service.media.close()
    service.media = StudioMedia(service, profiles=profiles())
    monkeypatch.setenv("MEDIA_TEST_KEY", "host-key-never-used")
    policy.environment_resolver = lambda identity: ({"MEDIA_TEST_KEY": "key-for-" + identity}, None)
    calls = []

    def provider(method, url, headers, body, text):
        calls.append(headers)
        assert headers == {"Authorization": "Bearer key-for-bob"}
        return {"data": [{"b64_json": base64.b64encode(png()).decode()}]}

    service.research.request_client = provider
    tool = next(n for n in service.media.names if n.endswith(":image_generate"))
    code, run = request(
        "/api/runs",
        {"kind": "tool", "tool": tool, "kwargs": {"profile": "image_generate", "prompt": "test"}},
        user="bob",
    )
    assert code == 202, run
    completed = settled(service, run)
    assert completed["status"] == "completed", completed
    ref = json.loads(completed["output"])["artifacts"][0]
    path = "/api/catalog/artifacts/" + ref["id"] + "/versions/1"
    assert request(path, user="bob") == (200, png())
    assert request(path, user="alice")[0] == 403
    assert request(path)[0] == 401
    original_count = len(service.catalog.list("artifacts"))

    def revoked(*args):
        value = provider(*args)
        policy.bindings["bob"] = "viewer"
        return value

    service.research.request_client = revoked
    code, second = request(
        "/api/runs",
        {"kind": "tool", "tool": tool, "kwargs": {"profile": "image_generate", "prompt": "test"}},
        user="bob",
    )
    assert code == 202
    assert settled(service, second)["status"] == "failed"
    assert len(service.catalog.list("artifacts")) == original_count and len(calls) == 2


def test_private_runtime_media_profiles_are_loaded_without_enabling_by_default(tmp_path):
    from nexus.core.studio_runtime import load_runtime_configuration

    root = tmp_path / "shared"
    root.mkdir()
    path = tmp_path / "private.json"
    path.write_text(
        json.dumps({"media": {"image": {"action": "image_generate", "model": "explicit-model"}}})
    )
    path.chmod(0o600)
    options = load_runtime_configuration(path, root)
    service = StudioService(root, **options)
    try:
        assert not service.media.profiles["image"]["enabled"]
        assert not any(row["configured"] for row in service.media.inventory()["items"])
    finally:
        service.close()

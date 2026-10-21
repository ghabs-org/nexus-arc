"""Quick Build tests use real persistence/validation/engine with an injected model."""

import asyncio
import copy
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from nexus.core.models import AgentResult
from nexus.core.quick_build import build_provider
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def proposal():
    return {
        "name": "Research team",
        "summary": "Research, then review the recommendation.",
        "assumptions": ["Provide source material in the task."],
        "agents": [
            {
                "id": "researcher",
                "name": "Researcher",
                "description": "Compare evidence",
                "instructions": "Use provided evidence. State uncertainty.",
                "provider": "claude-code",
            },
            {
                "id": "reviewer",
                "name": "Reviewer",
                "description": "Check the recommendation",
                "instructions": "Check claims. Explain unsupported conclusions.",
                "provider": "codex",
            },
        ],
        "workflow": {
            "id": "research",
            "name": "Research and review",
            "description": "Two specialists",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "agent_type": "researcher",
                    "prompt": "Research {task}",
                },
                {
                    "id": "review",
                    "name": "Review",
                    "agent_type": "reviewer",
                    "prompt": "Review the evidence for {task}",
                },
            ],
        },
    }


class Model:
    def __init__(self):
        self.output = json.dumps(proposal())
        self.success = True
        self.calls = []
        self.block = None

    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        self.calls.append(context)
        if self.block:
            self.block[0].set()
            await asyncio.to_thread(self.block[1].wait, 4)
        return AgentResult(
            success=self.success,
            output=self.output,
            error=None if self.success else "Provider quota exhausted",
        )


@pytest.fixture
def app(tmp_path):
    model = Model()
    execution = []

    class Runner:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            execution.append(context)
            return AgentResult(success=True, output=f"Result from {context.agent_name}")

    service = StudioService(
        tmp_path, provider_factory=lambda name: Runner(), builder_factory=lambda name: model
    )
    yield service, model, execution
    service.close()


def wait(service, run):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.run(run["id"])
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("Run did not finish")


def generate(service, **options):
    queued = service.start_run({"kind": "build", "task": "Create a research team", **options})
    result = wait(service, queued)
    assert result["status"] == "completed", result
    return service.builder.get(queued["build_id"])


def test_reopen_saved_build_updates_managed_resources_and_detects_manual_file_edits(app):
    service, model, _ = app
    draft = generate(service)
    saved = service.builder.apply(draft["id"], {"revision": draft["revision"]})
    editing = service.builder.reopen(saved["id"], {"revision": saved["revision"]})
    changed = copy.deepcopy(editing["proposal"])
    changed["agents"][0]["instructions"] = "New reviewed instructions"
    updated = service.builder.apply(
        editing["id"], {"revision": editing["revision"], "proposal": changed}
    )
    agent_id = changed["agents"][0]["id"]
    assert service.definition("agents", agent_id)["spec"]["purpose"] == "New reviewed instructions"
    editing = service.builder.reopen(updated["id"], {"revision": updated["revision"]})
    path = service.root / "agents" / f"{agent_id}.yaml"
    path.write_text(path.read_text() + "\n# Important manual context\n")
    with pytest.raises(ValueError, match="file changed"):
        service.builder.apply(editing["id"], {"revision": editing["revision"]})
    assert "Important manual context" in path.read_text()


def test_structured_build_can_reference_its_own_unsaved_agents(app):
    service, model, _ = app
    spec = proposal()
    spec["workflow"].pop("steps")
    spec["workflow"]["graph"] = {
        "version": 2,
        "nodes": [{"id": "draft", "kind": "agent", "agent_id": "researcher", "prompt": "Research"}],
        "edges": [{"source": "START", "target": "draft"}],
    }
    model.output = json.dumps(spec)
    draft = generate(service)
    assert not service.definitions("agents")
    assert draft["proposal"]["workflow"]["graph"]["version"] == 2


def test_draft_review_edit_save_restart_and_real_workflow_execution(app):
    service, model, execution = app
    draft = generate(service, provider="claude-code", model="opus")
    assert draft["revision"] == 1 and draft["status"] == "draft"
    assert service.definitions("agents") == service.definitions("workflows") == []
    assert model.calls[0].workspace != service.root
    assert not model.calls[0].workspace.exists()  # Temporary generation workspace cleaned.
    assert model.calls[0].model_override == "opus"
    assert draft["proposal"]["agents"][0]["model"] == "opus"
    assert draft["proposal"]["agents"][1]["model"] == ""
    edited = copy.deepcopy(draft["proposal"])
    edited["agents"][0]["instructions"] = "Only use sources the user supplied."
    saved = service.builder.apply(draft["id"], {"revision": 1, "proposal": edited})
    assert saved["status"] == "saved" and len(saved["files"]) == 3
    assert len(service.definitions("agents")) == 2
    assert (
        "Only use sources"
        in service.definition("agents", edited["agents"][0]["id"])["spec"]["purpose"]
    )
    assert service.validate_workflow(edited["workflow"]["id"])["valid"]
    # Save is idempotent and doesn't create duplicates.
    assert service.builder.apply(draft["id"], {"revision": 1}) == saved
    restored = StudioService(service.root)
    try:
        assert restored.builder.get(draft["id"]) == saved
    finally:
        restored.close()
    assert saved["lifecycle_status"] == "inactive"
    with pytest.raises(ValueError, match="inactive"):
        service.builder.test(draft["id"], {"task": "Compare two supplied notes"})
    activation = service.resource_lifecycle.plan_build(
        saved["id"], {"revision": 1, "action": "activate"}
    )
    assert not activation["errors"]
    service.resource_lifecycle.transition_build(
        saved["id"], {"plan_id": activation["id"], "digest": activation["digest"]}
    )
    result = wait(
        service, service.builder.test(draft["id"], {"task": "Compare two supplied notes"})
    )
    assert result["status"] == "completed", result
    assert len(execution) == 2
    assert execution[0].model_override == "opus"
    assert "Only use sources" in execution[0].prompt
    assert "Result from " in execution[1].prompt
    assert "Compare two supplied notes" in execution[1].prompt
    assert all(c.workspace == service.root for c in execution)


def test_refinement_preserves_review_edits_and_rejects_stale_apply(app):
    service, model, _ = app
    draft = generate(service)
    edited = copy.deepcopy(draft["proposal"])
    edited["agents"][0]["instructions"] = "A local edit to keep when refining"
    refined = generate(
        service,
        task="Focus on short recommendations",
        build_id=draft["id"],
        revision=1,
        proposal=edited,
    )
    assert refined["revision"] == 2 and len(refined["messages"]) == 4
    assert "A local edit to keep" in model.calls[-1].prompt
    for operation in [
        lambda: service.builder.apply(draft["id"], {"revision": 1}),
        lambda: generate(service, build_id=draft["id"], revision=1),
    ]:
        with pytest.raises(ValueError, match="latest revision"):
            operation()
    assert not service.definitions("agents")


@pytest.mark.parametrize(
    "bad",
    [
        {"agents": []},
        {"agents": [{**proposal()["agents"][0], "id": "../../escape"}]},
        {"agents": [{**proposal()["agents"][0], "provider": "unknown"}]},
        {
            "workflow": {
                **proposal()["workflow"],
                "steps": [
                    {"id": "one", "name": "One", "agent_type": "outside", "prompt": "Do {task}"}
                ],
            }
        },
        {"workflow": {**proposal()["workflow"], "parallel": True}},
        {
            "workflow": {
                **proposal()["workflow"],
                "steps": [
                    {
                        "id": "one",
                        "name": "One",
                        "agent_type": "researcher",
                        "prompt": "Do {missing}",
                    }
                ],
            }
        },
        {
            "workflow": {
                **proposal()["workflow"],
                "steps": [
                    {"id": "one", "name": "One", "agent_type": "researcher", "prompt": "Do {task"}
                ],
            }
        },
    ],
)
def test_invalid_generation_cannot_create_definitions(app, bad):
    service, model, _ = app
    model.output = json.dumps({**proposal(), **bad})
    run = service.start_run({"kind": "build", "task": "Generate a team"})
    failed = wait(service, run)
    assert failed["status"] == "failed" and "invalid proposal" in failed["error"]
    assert service.definitions("agents") == service.definitions("workflows") == []
    assert service.records("builds") == []


def test_failed_refinement_keeps_previous_draft_and_never_falls_back(app):
    service, model, _ = app
    draft = generate(service)
    model.success = False
    failed = wait(
        service,
        service.start_run(
            {"kind": "build", "task": "Change it", "build_id": draft["id"], "revision": 1}
        ),
    )
    assert failed["status"] == "failed" and "quota" in failed["error"]
    assert service.builder.get(draft["id"]) == draft
    assert len(model.calls) == 2


def test_rejects_non_json_output_and_supports_fenced_json(app):
    service, model, _ = app
    model.output = "Sure, I created all the files."
    failed = wait(service, service.start_run({"kind": "build", "task": "Generate team"}))
    assert failed["status"] == "failed"
    model.output = "```json\n" + json.dumps(proposal()) + "\n```"
    assert generate(service)["status"] == "draft"


def test_no_test_before_save_no_saved_build_refinement(app):
    service, _, _ = app
    draft = generate(service)
    with pytest.raises(ValueError, match="save"):
        service.builder.test(draft["id"], {"task": "Try"})
    service.builder.apply(draft["id"], {"revision": 1})
    with pytest.raises(ValueError, match="saved"):
        service.start_run(
            {"kind": "build", "task": "Change", "build_id": draft["id"], "revision": 1}
        )
    with pytest.raises(ValueError, match="Unknown provider"):
        service.start_run({"kind": "build", "task": "Change", "provider": "gemini"})


def test_collision_preflight_prevents_partial_save_and_symlink_escape(app, tmp_path):
    service, _, _ = app
    draft = generate(service)
    conflict = service.root / "workflows" / f"{draft['proposal']['workflow']['id']}.yaml"
    conflict.parent.mkdir()
    conflict.write_text("existing: unchanged")
    with pytest.raises(FileExistsError):
        service.builder.apply(draft["id"], {"revision": 1})
    assert service.definitions("agents") == []
    assert conflict.read_text() == "existing: unchanged"
    conflict.unlink()
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (service.root / "agents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        service.builder.apply(draft["id"], {"revision": 1})
    assert list(outside.iterdir()) == []


def test_io_failure_rolls_back_new_files_and_leaves_draft_retryable(app, monkeypatch):
    service, _, _ = app
    draft = generate(service)
    original = service._write

    def fail_state(path, value):
        if path.parent.name == "builds":
            raise OSError("simulated full disk")
        return original(path, value)

    with monkeypatch.context() as scope:
        scope.setattr(service, "_write", fail_state)
        with pytest.raises(OSError, match="full disk"):
            service.builder.apply(draft["id"], {"revision": 1})
    assert service.definitions("agents") == service.definitions("workflows") == []
    assert service.builder.get(draft["id"])["status"] == "draft"
    assert service.builder.apply(draft["id"], {"revision": 1})["status"] == "saved"


def test_parallel_refinements_and_save_while_generating_are_rejected(app):
    service, model, _ = app
    draft = generate(service)
    entered, release = threading.Event(), threading.Event()
    model.block = (entered, release)
    payload = {"kind": "build", "task": "Improve", "build_id": draft["id"], "revision": 1}
    run = service.start_run(payload)
    assert entered.wait(2)
    try:
        with pytest.raises(ValueError, match="already generating"):
            service.start_run(payload)
        with pytest.raises(ValueError, match="finish generating"):
            service.builder.apply(draft["id"], {"revision": 1})
    finally:
        release.set()
    assert wait(service, run)["status"] == "completed"


def test_generation_provider_flags_are_constrained_separately_from_agents():
    with patch("nexus.core.quick_build.AdapterRegistry") as registry:
        build_provider("codex")
        args = registry.return_value.create_ai.call_args.kwargs["extra_args"]
        assert "read-only" in args and "--ignore-user-config" in args
        assert "--dangerously-bypass-approvals-and-sandbox" not in args
        build_provider("claude-code")
        args = registry.return_value.create_ai.call_args.kwargs["extra_args"]
        assert args[args.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in args
    with pytest.raises(ValueError):
        build_provider("copilot")


def test_http_build_draft_apply_and_test_endpoints(app):
    service, _, _ = app
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "builder-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, authenticated=True):
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"X-Nexus-Studio-Token": "builder-token"} if authenticated else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        assert request("/api/builds", {"task": "Research team"}, False)[0] == 403
        status, run = request("/api/builds", {"task": "Research team", "provider": "codex"})
        assert status == 202 and wait(service, run)["status"] == "completed"
        path = f"/api/builds/{run['build_id']}"
        status, draft = request(path)
        assert status == 200 and draft["status"] == "draft"
        assert request("/api/builds")[1]["items"][0]["id"] == draft["id"]
        assert request(path + "/test", {"task": "Compare"})[0] == 400
        status, saved = request(path + "/apply", {"revision": 1})
        assert status == 201 and saved["status"] == "saved"
        assert request(path + "/apply", {"revision": 0})[0] == 400
        assert request(path + "/test", {"task": "Compare supplied notes"})[0] == 400
        feature = "/api/studio/builds/" + draft["id"]
        status, activation = request(feature + "/plan", {"revision": 1, "action": "activate"})
        assert status == 200 and not activation["errors"], activation
        status, activated = request(
            feature + "/transition", {"plan_id": activation["id"], "digest": activation["digest"]}
        )
        assert status == 200 and activated["status"] == "active", activated
        status, test = request(path + "/test", {"task": "Compare supplied notes"})
        assert status == 202 and wait(service, test)["status"] == "completed"
        assert request("/api/builds/missing")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_review_can_clear_builder_model_override_and_long_ids_round_trip(app):
    service, model, _ = app
    generated = proposal()
    long_id = "a" * 64
    generated["agents"][0]["id"] = long_id
    generated["workflow"]["steps"][0]["agent_type"] = long_id
    model.output = json.dumps(generated)
    draft = generate(service, provider="claude-code", model="opus")
    edited = copy.deepcopy(draft["proposal"])
    edited["agents"][0]["model"] = ""
    saved = service.builder.apply(draft["id"], {"revision": 1, "proposal": edited})
    document = service.definition("agents", saved["proposal"]["agents"][0]["id"])
    assert "model" not in document["spec"]


def test_expanded_builder_normalizes_graph_and_related_resources_without_side_effects(tmp_path):
    service = StudioService(tmp_path)
    try:
        data = proposal()
        data["workflow"] = {
            "id": "review-flow",
            "name": "Review flow",
            "description": "Typed graph",
            "graph": {
                "nodes": [
                    {
                        "id": "review",
                        "name": "Review",
                        "agent_id": "reviewer",
                        "prompt": "Review {task}",
                    },
                    {"id": "gate", "name": "Approve", "kind": "approval"},
                ],
                "edges": [
                    {"source": "START", "target": "review"},
                    {"source": "review", "target": "gate"},
                ],
            },
        }
        data["resources"] = [
            {
                "kind": "skills",
                "id": "accuracy",
                "name": "Accuracy",
                "data": {"content": "Use only supplied evidence"},
            },
            {
                "kind": "connectors",
                "id": "git",
                "name": "Git",
                "data": {"type": "github", "repo": "owner/project", "enabled": True},
            },
            {
                "kind": "entrypoints",
                "id": "incoming",
                "name": "Incoming",
                "data": {
                    "target_kind": "workflow",
                    "target_id": "review-flow",
                    "token_env": "INCOMING_SECRET",
                    "enabled": True,
                },
            },
        ]
        normalized = service.builder.normalize(data, "build12345678", "codex")
        assert service.definitions("agents") == [] and service.catalog.list("connectors") == []
        assert normalized["workflow"]["graph"]["nodes"][0]["agent_id"].startswith("qb-build123-")
        assert all(not r["data"].get("enabled", False) for r in normalized["resources"])
        service._write(
            service._path("builds", "build12345678"),
            {
                "id": "build12345678",
                "status": "draft",
                "revision": 1,
                "provider": "codex",
                "model": "",
                "proposal": normalized,
            },
        )
        saved = service.builder.apply("build12345678", {"revision": 1})
        assert saved["status"] == "saved"
        assert service.catalog.list("connectors")[0]["data"]["enabled"] is False
        assert (
            service.catalog.list("entrypoints")[0]["data"]["target_id"]
            == normalized["workflow"]["id"]
        )
        assert (
            service.definition("workflows", normalized["workflow"]["id"])["graph"]["nodes"][1][
                "kind"
            ]
            == "approval"
        )
    finally:
        service.close()


def test_builder_can_save_resource_only_plan_and_refuses_phantom_workflow_test(tmp_path):
    service = StudioService(tmp_path)
    try:
        normalized = service.builder.normalize(
            {
                "name": "Prompt library",
                "summary": "Save a reusable prompt",
                "agents": [],
                "workflow": None,
                "resources": [
                    {
                        "kind": "prompts",
                        "id": "summary",
                        "name": "Summary",
                        "data": {"content": "Summarize evidence"},
                    }
                ],
            },
            "resource1234",
            "codex",
        )
        service._write(
            service._path("builds", "resource1234"),
            {
                "id": "resource1234",
                "status": "draft",
                "revision": 1,
                "provider": "codex",
                "model": "",
                "proposal": normalized,
            },
        )
        service.builder.apply("resource1234", {"revision": 1})
        assert len(service.catalog.list("prompts")) == 1
        assert service.definitions("workflows") == []
        with pytest.raises(ValueError, match="no workflow"):
            service.builder.test("resource1234", {"task": "Test"})
    finally:
        service.close()


def test_agent_fields_nested_in_settings_are_lifted_not_rejected(app):
    service, _, _ = app
    data = proposal()
    data["agents"][0]["settings"] = {"timeout_seconds": 900, "max_model_calls": 4}
    agent = service.builder.normalize(data, "build12345678", "codex")["agents"][0]
    assert agent["timeout_seconds"] == 900 and agent["max_model_calls"] == 4
    assert "settings" not in agent

    data["agents"][0]["settings"] = {"timeout_seconds": 900, "install": "brag"}
    with pytest.raises(ValueError, match="Unsupported agent fields: install"):
        service.builder.normalize(data, "build12345678", "codex")
    data["agents"][0].update(timeout_seconds=60, settings={"timeout_seconds": 900})
    with pytest.raises(ValueError, match="both directly and in settings: timeout_seconds"):
        service.builder.normalize(data, "build12345678", "codex")
    data["agents"][0].pop("timeout_seconds")
    data["agents"][0]["settings"] = "fast"
    with pytest.raises(ValueError, match="Agent settings must be an object"):
        service.builder.normalize(data, "build12345678", "codex")


def test_quick_build_normalizes_zero_and_missing_tool_call_limits(app):
    service, _, _ = app
    data = proposal()
    data["agents"][0]["max_tool_calls"] = 0
    data["agents"][1]["max_tool_calls"] = "0"
    normalized = service.builder.normalize(data, "build12345678", "codex")
    assert normalized["agents"][0]["max_tool_calls"] == 0
    assert normalized["agents"][1]["max_tool_calls"] == 0

    data = proposal()
    data["agents"][0]["max_tool_calls"] = None
    data["agents"][0]["max_model_calls"] = None
    normalized = service.builder.normalize(data, "build12345678", "codex")
    assert "max_tool_calls" not in normalized["agents"][0]
    assert "max_model_calls" not in normalized["agents"][0]

    docs = service.builder.documents(normalized)
    spec = docs[0][2]["spec"]
    assert "max_tool_calls" not in spec

    data = proposal()
    data["agents"][0]["max_tool_calls"] = 150
    data["agents"][0]["max_model_calls"] = 200
    data["agents"][0]["timeout_seconds"] = 5000
    normalized = service.builder.normalize(data, "build12345678", "codex")
    assert normalized["agents"][0]["max_tool_calls"] == 100
    assert normalized["agents"][0]["max_model_calls"] == 101
    assert normalized["agents"][0]["timeout_seconds"] == 3600


def test_quick_build_get_missing_draft_raises_clean_not_found(app):
    service, _, _ = app
    with pytest.raises(FileNotFoundError, match="Build draft not found"):
        service.builder.get("nonexistent-build-id")


def test_parse_agy_stream_output_extracts_stdout_error():
    from nexus.adapters.ai.agy_provider import parse_agy_stream_output

    stdout = b'error: Individual quota reached. Resets in 140h.\nAGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED"}\n'
    result = parse_agy_stream_output(stdout, 3, b"", 0.5, None)
    assert not result.success
    assert "Individual quota reached" in result.error


def test_parse_agy_stream_output_extracts_deltas_and_rejects_empty():
    from nexus.adapters.ai.agy_provider import parse_agy_stream_output

    # 1. Step update text deltas when response in result event is empty
    stdout_deltas = (
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Agents are in "}}\n'
        b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"agents/ folder."}}\n'
        b'{"event":"result","result":{"status":"SUCCESS","response":""}}\n'
    )
    result = parse_agy_stream_output(stdout_deltas, 0, b"", 0.5, "gemini-3.8-flash-high")
    assert result.success
    assert result.output == "Agents are in agents/ folder."

    # 2. Completely empty response with status SUCCESS must be rejected as failure
    stdout_empty = b'{"event":"result","result":{"status":"SUCCESS","response":""}}\n'
    result_empty = parse_agy_stream_output(stdout_empty, 0, b"", 0.5, "gemini-3.8-flash-high")
    assert not result_empty.success
    assert "Antigravity returned no response" in result_empty.error

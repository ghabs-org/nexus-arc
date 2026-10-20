"""Optional Pillow capability acceptance: owned images, imports and real HTTP wire."""

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from nexus.adapters.decisions.errors import DecisionCallError
from nexus.core.studio import StudioService
from nexus.core.studio_decision_images import limits, pin, prepare
from nexus.core.studio_decisions import FORMAT
from tests.test_studio_expansion_controls import done
from tests.test_studio_team import team as team

QUESTION = {"visible": {"type": "boolean", "instructions": "Is damage visible?"}}


def config(**extra):
    return {
        "format": FORMAT,
        "backend": "openai",
        "model": "gpt-6-luna",
        "questions": QUESTION,
        **extra,
    }


def image_bytes(size=(24, 18), kind="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format=kind)
    return output.getvalue()


def artifact(studio, content=None, owner=None):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Evidence photo",
            "requester_nexus_id": owner,
            "data": {
                "filename": "evidence.png",
                "content_base64": base64.b64encode(
                    content if content is not None else image_bytes()
                ).decode(),
            },
        },
    )


def pinned(studio, owner=None):
    payload = {
        "kind": "workflow",
        "workflow_snapshot": {"graph": {"nodes": [{"images": ["evidence.png"]}]}},
        "requester_nexus_id": owner,
    }
    pin(studio, payload)
    return payload


def test_imported_image_workflow_calls_selected_client_with_owned_revision(tmp_path):
    calls = []

    def client(state, questions, **kwargs):
        calls.append((state, kwargs))
        return {"visible": {"type": "noul", "noul": 0.9}}

    studio = StudioService(tmp_path, workbench_options={"decision_client": client})
    try:
        photo = artifact(studio)
        source = {
            "agent_name": "vision",
            "agent_type": "workflow",
            "decision": {"model_alias": "vision", "max_images": 5, "max_image_bytes": 20971520},
            "nodes": [
                {
                    "id": "inspect",
                    "type": "decision",
                    "questions": QUESTION,
                    "images": ["{{workflow.input.photo}}"],
                    "input": {"question": "Inspect this selected photo"},
                }
            ],
            "output_mapping": {"answer": "{{inspect.output.answers.visible.value}}"},
        }
        converted = studio.configuration.convert_interop(
            {
                "documents": [source],
                "bindings": {"decisions": {"vision": {"backend": "openai", "model": "gpt-6-luna"}}},
            },
            None,
        )
        assert converted["valid"], converted
        plan = studio.configuration.plan(converted["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        run = studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": "vision",
                "task": "Inspect",
                "workflow_input": {"photo": photo["id"]},
            }
        )
        result = done(studio, run)
        assert result["status"] == "completed", result
        assert len(calls) == 1 and calls[0][1]["image_urls"][0].startswith("data:image/png;base64,")
        assert "base64" not in json.dumps(studio.records("decisions"))
        exported = studio.configuration.export_interop({"document": converted["document"]}, None)
        assert exported["valid"], exported
    finally:
        studio.close()


def test_queued_revision_survives_replacement_and_detects_content_type(tmp_path):
    studio = StudioService(tmp_path)
    try:
        photo = artifact(studio, image_bytes(kind="JPEG"))
        payload = pinned(studio)
        studio.catalog.save(
            "artifacts",
            {
                "id": photo["id"],
                "name": photo["name"],
                "revision": photo["revision"],
                "data": {
                    "filename": "evidence.png",
                    "content_base64": base64.b64encode(image_bytes()).decode(),
                },
            },
        )
        assert prepare(studio, ["artifact://evidence.png"], config(), payload, None)[0].startswith(
            "data:image/jpeg;"
        )
        assert prepare(studio, [None, "", []], config(), payload, None) == []
    finally:
        studio.close()


@pytest.mark.parametrize("content", [b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
def test_corrupt_artifacts_fail_without_provider_fallback(tmp_path, content):
    studio = StudioService(tmp_path)
    try:
        artifact(studio, content)
        with pytest.raises(ValueError, match="invalid"):
            prepare(
                studio, ["evidence.png"], config(on_provider_error="escalate"), pinned(studio), None
            )
    finally:
        studio.close()


def test_size_count_downscale_and_large_header_limits(tmp_path):
    studio = StudioService(tmp_path)
    try:
        artifact(studio, image_bytes((8100, 10)))
        payload = pinned(studio)
        urls = prepare(studio, ["evidence.png"], config(), payload, None)
        decoded = Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1])))
        assert max(decoded.size) == 8000
        for references, settings in [
            (["evidence.png"] * 6, {}),
            (["evidence.png"], {"max_image_bytes": 1}),
        ]:
            with pytest.raises(DecisionCallError) as exc:
                prepare(studio, references, config(**settings), payload, None)
            assert exc.value.category == "input_too_large"
        assert limits({"max_images": 0, "max_image_bytes": 0}) == (5, 20971520)
    finally:
        studio.close()


def test_team_cannot_use_other_owner_or_forge_queue_snapshot(team):
    studio, policy = team[:2]
    photo = artifact(studio, owner="alice")
    alice = {"nexus_id": "alice", "role": "builder"}
    bob = {"nexus_id": "bob", "role": "builder"}
    payload = pinned(studio, "bob")
    assert photo["id"] not in payload["_decision_image_revisions"]
    with pytest.raises(ValueError):
        prepare(studio, [photo["id"]], config(), payload, bob)
    forged = pinned(studio, "alice")
    with pytest.raises(PermissionError):
        prepare(studio, [photo["id"]], config(), forged, bob)
    assert prepare(studio, [photo["id"]], config(), forged, alice)


@pytest.mark.parametrize("backend", ["openai", "openrouter"])
def test_image_transport_uses_actual_http_protocol_without_text_regression(monkeypatch, backend):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            result = (
                {"answers": [{"name": "visible", "type": "predicate", "probability": 0.9}]}
                if backend == "openai"
                else {"answers": {"visible": {"type": "noul", "noul": 0.9}}}
            )
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/decisions"
    from nexus.adapters.decisions import jev, openai

    monkeypatch.setattr(jev, "DECISIONS_URL", endpoint)
    selected = openai if backend == "openai" else jev
    model = "gpt-6-luna" if backend == "openai" else "openai/gpt-6-luna-decisions"
    url = "data:image/png;base64," + base64.b64encode(image_bytes()).decode()
    try:
        selected.ask_strict(
            "Photo",
            {"visible": {"type": "noul", "instructions": "Visible damage?"}},
            model=model,
            api_key="synthetic",
            endpoint=endpoint,
            image_urls=[url],
        )
        if backend == "openai":
            assert seen[0]["input"][0]["content"][1] == {"type": "input_image", "image_url": url}
        else:
            assert seen[0]["state"][1] == {"type": "image_url", "image_url": {"url": url}}
        selected.ask_strict(
            "Text",
            {"visible": {"type": "noul", "instructions": "Visible?"}},
            model=model,
            api_key="synthetic",
            endpoint=endpoint,
        )
        assert seen[1]["input" if backend == "openai" else "state"] == "Text"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_generated_image_pins_and_single_tool_artifact_metadata(tmp_path):
    from nexus.core.studio_workflow_artifacts import tool_output

    studio = StudioService(tmp_path)
    try:
        payload = pinned(studio)
        photo = artifact(studio)
        photo["generated_by_run"] = "fixture-run"
        studio._write(studio.catalog._path("artifacts", photo["id"]), photo)
        result = tool_output(
            studio,
            {
                "data": "ready",
                "artifacts": [{"id": photo["id"], "revision": photo["revision"]}],
                "created_artifact": "untrusted",
            },
            "fixture-run",
            None,
        )
        assert result["created_artifact"] == photo["id"] + ":1"
        first = prepare(
            studio, [result["created_artifact"]], config(), payload, None, run_id="fixture-run"
        )
        assert first
        implicit = prepare(studio, [photo["id"]], config(), payload, None, run_id="fixture-run")
        studio.catalog.save(
            "artifacts",
            {
                "id": photo["id"],
                "name": photo["name"],
                "revision": photo["revision"],
                "data": {
                    "filename": "evidence.png",
                    "content_base64": base64.b64encode(image_bytes(kind="JPEG")).decode(),
                },
            },
        )
        assert (
            prepare(studio, [photo["id"]], config(), payload, None, run_id="fixture-run")
            == implicit
        )
        with pytest.raises(ValueError):
            prepare(studio, [photo["id"]], config(), payload, None, run_id="another-run")
        assert "created_artifact" not in tool_output(studio, {"artifacts": []}, "fixture-run", None)
        assert "created_artifact" not in tool_output(
            studio, {"artifacts": [{}, {}]}, "fixture-run", None
        )
    finally:
        studio.close()


def test_imported_tool_to_image_decision_uses_created_revision(tmp_path):
    from nexus.core.studio_data_tools import current_run_id
    from nexus.tools import tool_registry

    calls = []
    studio = StudioService(
        tmp_path,
        workbench_options={
            "decision_client": lambda state, questions, **kwargs: (
                calls.append(kwargs["image_urls"]) or {"visible": {"type": "noul", "noul": 0.9}}
            )
        },
    )

    async def make():
        photo = artifact(studio)
        photo["generated_by_run"] = current_run_id.get()
        studio._write(studio.catalog._path("artifacts", photo["id"]), photo)
        return {"artifacts": [{"id": photo["id"], "revision": photo["revision"]}]}

    tool_registry.register("acceptance:create-image", make)
    try:
        source = {
            "agent_name": "generated-vision",
            "agent_type": "workflow",
            "decision": {"model_alias": "vision"},
            "tools": [{"tool_type": "builtin", "tool_name": "create"}],
            "nodes": [
                {"id": "create", "type": "tool", "tool_name": "create", "input": {}},
                {
                    "id": "inspect",
                    "type": "decision",
                    "depends_on": ["create"],
                    "questions": QUESTION,
                    "images": ["{{create.output.created_artifact}}"],
                    "input": {"question": "Read generated evidence"},
                },
            ],
            "output_mapping": {"answer": "{{inspect.output.answers.visible.value}}"},
        }
        converted = studio.configuration.convert_interop(
            {
                "documents": [source],
                "bindings": {
                    "tools": {"create": "acceptance:create-image"},
                    "decisions": {"vision": {"backend": "openai", "model": "gpt-6-luna"}},
                },
            },
            None,
        )
        assert converted["valid"], converted
        plan = studio.configuration.plan(converted["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        run = studio.start_run(
            {"kind": "workflow", "workflow_id": "generated-vision", "task": "Create and inspect"}
        )
        result = done(studio, run)
        assert result["status"] == "completed", result
        assert len(calls) == 1
        assert len(studio.records("decision-image-pins")) == 1
    finally:
        tool_registry.unregister("acceptance:create-image", func=make)
        studio.close()

"""Exercise bounded values through actual Studio runs and scoped artifact storage."""

import asyncio
import base64
import json
import time
from datetime import UTC, datetime

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_connections import execution_identity
from nexus.core.studio_embed_values import calculation, template
from tests.test_studio_team import team as team


def upload(studio, filename, content, actor=None, identity=None):
    data = {
        "name": filename,
        "data": {"filename": filename, "content_base64": base64.b64encode(content).decode()},
    }
    if actor:
        data["requester_nexus_id"] = actor
    if identity:
        data["id"] = identity
        data["revision"] = studio.catalog.get("artifacts", identity)["revision"]
    return studio.catalog.save("artifacts", data)


def settled(studio, run):
    for _ in range(500):
        result = studio.run(run["id"])
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    pytest.fail("Embed task did not finish")


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    try:
        yield service
    finally:
        service.close()


def test_values_formatting_are_nonrecursive_and_use_explicit_state(studio):
    result = studio.dynamic_embeds.render(
        "Name «state:user.name»; amount «math:100 * 0.05 | .2f»; "
        "date «datetime:%Y-%m-%d»; count «state:items[0] | .1f»; «uuid:»",
        state={"user": {"name": "«math:1+2»"}, "items": [2]},
        now=datetime(2026, 10, 21, tzinfo=UTC),
    )
    assert result["text"].startswith("Name «math:1+2»; amount 5.00; date 2026-10-21; count 2.0; ")
    assert len(result["text"].split("; ")[-1]) == 36
    assert result["structured_content"] == []


@pytest.mark.parametrize(
    "source",
    [
        '__import__("os").environ',
        "().__class__",
        "sum([1, 2])",
        "True + 1",
        "2 ** 999",
        "99 ** 12",
        "1 / 0",
        "1e999",
        "1 << 9999",
        "[0] * 999999999",
        "9" * 900,
        "-(" * 20 + "1" + ")" * 20,
    ],
)
def test_calculations_reject_execution_and_excessive_work(source):
    with pytest.raises(ValueError):
        calculation(source)


def test_json_csv_and_text_chains_and_pinned_revisions(studio):
    data = upload(
        studio,
        "results.json",
        b'{"users":[{"name":"Ada","active":true},{"name":"Lin","active":false}]}',
    )
    old = studio.dynamic_embeds.render(
        "«artifact_content:results.json:1 >>> jsonpath:$.users[*].name >>> format:json»"
    )
    assert json.loads(old["text"]) == ["Ada", "Lin"]
    upload(studio, "results.json", b'{"users":[]}', identity=data["id"])
    assert (
        studio.dynamic_embeds.render(
            "«artifact_content:results.json:1 >>> jsonpath:$.users[0].name >>> format:text»"
        )["text"]
        == "Ada"
    )
    assert (
        studio.dynamic_embeds.render(
            "«artifact_content:results.json >>> jsonpath:$.users >>> format:json»"
        )["text"]
        == "[]"
    )
    upload(studio, "users.csv", b"name,status,note\nAda,active,a\nLin,inactive,b\n")
    result = studio.dynamic_embeds.render(
        "«artifact_content:users.csv >>> filter_rows_eq:status:active >>> select_cols:name >>> slice_rows:0:1 >>> format:csv»"
    )
    assert result["text"].splitlines() == ["name", "Ada"]
    upload(studio, "notes.txt", b"one\ntwo\nthree\nfour")
    text = studio.dynamic_embeds.render(
        "«artifact_content:notes.txt >>> slice_lines:1:4 >>> tail:2 >>> head:1 >>> grep:three >>> format:text»"
    )
    assert text["text"] == "three"
    assert text["artifact_refs"][0]["revision"] == 1


def test_safe_templates_escape_data_and_do_not_resolve_inserted_directives(studio):
    upload(studio, "data.json", b'[{"name":"<script>evil()</script>"},{"name":"Lin"}]')
    upload(
        studio,
        "view.mustache",
        b"{{! note }}{{#items}}<b>{{name}}</b>{{/items}}{{^items}}empty{{/items}}",
    )
    result = studio.dynamic_embeds.render(
        "«artifact_content:data.json >>> select_fields:name >>> apply_to_template:view.mustache >>> format:text»"
    )
    assert result["text"] == "<b>&lt;script&gt;evil()&lt;/script&gt;</b><b>Lin</b>"
    assert len(result["artifact_refs"]) == 2
    assert template("{{^items}}empty{{/items}}", []) == "empty"
    assert template("{{#items}}{{.}}{{/items}}", ["a", "b"]) == "ab"
    for source in ("{{{raw}}}", "{{>partial}}", "{{#a}}x{{/b}}", "{{#a}}bad {{ tag{{/a}}"):
        with pytest.raises(ValueError):
            template(source, {"a": True})


def test_attachments_metadata_errors_and_limits(studio):
    artifact = upload(studio, "image.png", b"\x89PNG\r\n\x1a\nsynthetic")
    result = studio.dynamic_embeds.render(
        "file «artifact_return:image.png» «artifact_content:image.png >>> format:datauri» «artifact_meta:image.png»"
    )
    assert len(result["structured_content"]) == 1
    assert result["structured_content"][0]["revision"] == 1
    metadata = json.loads(result["text"][5:].strip())
    assert metadata["artifact_id"] == artifact["id"]
    assert not set(metadata) & {"blob_key", "store_id", "owner_id", "content_base64"}
    bad = studio.dynamic_embeds.render(
        "Keep this. «artifact_content:image.png >>> format:text» «state:missing» «math:1+2»"
    )
    assert bad["text"].startswith("Keep this. [Embed unavailable:") and bad["text"].endswith(" 3")
    upload(studio, "large.txt", b"x" * 32769)
    assert (
        "unavailable"
        in studio.dynamic_embeds.render("«artifact_content:large.txt >>> format:text»")["text"]
    )
    assert "unavailable" in studio.dynamic_embeds.render("«math:2 | 999999f»")["text"]
    assert (
        "unavailable"
        in studio.dynamic_embeds.render(
            "«artifact_content:large.txt >>> grep:(a+)+$ >>> format:text»"
        )["text"]
    )
    result = studio.dynamic_embeds.render(" ".join(["«math:1»"] * 33))
    assert result["text"].count("1") == 32
    assert "limit" in result["text"]
    with pytest.raises(ValueError):
        studio.dynamic_embeds.render("x" * 262145)
    with pytest.raises(ValueError):
        studio.dynamic_embeds.render("«state:secret»", state={"secret": lambda: "never call"})


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_completed_chat_persists_rendered_output_and_pinned_attachments(tmp_path, backend):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output="Total «math:2*3». «artifact_return:report.txt» «status_update:Prepared report»",
            )

    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "embeds",
            "workers": 1,
        }
    )
    service = StudioService(
        tmp_path / "workspace", state_options=options, provider_factory=lambda _: Provider()
    )
    try:
        artifact = upload(service, "report.txt", b"first revision")
        run = settled(service, service.start_run({"task": "Compute and attach"}))
        assert run["status"] == "completed", run
        assert run["output"] == "Total 6.  "
        reply = service.session(run["session_id"])["messages"][-1]
        assert reply["content"] == run["output"]
        assert reply["structured_content"] == run["structured_content"]
        assert run["structured_content"][0]["revision"] == 1
        upload(service, "report.txt", b"next revision", identity=artifact["id"])
        assert service.session(run["session_id"])["messages"][-1] == reply
        assert run["embed_artifact_refs"][0]["revision"] == 1
        events = service.execution.snapshot(run["id"], None)["timeline"]
        assert any(e.get("message") == "Prepared report" for e in events)
    finally:
        service.close()


def test_scoped_tools_cannot_read_another_users_artifacts_or_environment(team, monkeypatch):
    service, policy, _, request, _ = team
    upload(service, "private.txt", b"ALICE_ONLY", actor="alice")
    monkeypatch.setenv("EMBED_TEST_SECRET", "NEVER_EXPOSE")
    token = execution_identity.set("bob")
    try:
        name = next(iter(service.dynamic_embeds.names))
        assert policy.tool_allowed(name, service)
        from nexus.tools import tool_registry

        result = asyncio.run(
            tool_registry.call(
                name,
                text="«artifact_content:private.txt >>> format:text» «state:EMBED_TEST_SECRET»",
            )
        )
        assert result.ok
        assert (
            "ALICE_ONLY" not in result.output["text"]
            and "NEVER_EXPOSE" not in result.output["text"]
        )
        assert result.output["artifact_refs"] == []
        policy.bindings["bob"] = "viewer"
        result = asyncio.run(tool_registry.call(name, text="«math:1»"))
        assert not result.ok
    finally:
        execution_identity.reset(token)
    code, inventory = request("/api/studio/tools", user="alice")
    assert code == 200 and any(row["id"].endswith(":render_embeds") for row in inventory["items"])


def test_expansion_bounds_duplicate_names_and_template_work(studio):
    text = "x" * 240000 + "«state:large»"
    result = studio.dynamic_embeds.render(text, state={"large": "y" * 30000})
    assert result["text"].startswith("x" * 240000)
    assert len(result["text"].encode()) <= 262144
    assert "output limit" in result["text"]
    result = studio.dynamic_embeds.render("«math:1»" * 10000)
    assert len(result["text"].encode()) <= 262144
    first = upload(studio, "same.txt", b"one")
    second = studio.catalog.save(
        "artifacts",
        {
            "id": "second",
            "name": "Other file",
            "data": {
                "filename": "same.txt",
                "content_base64": base64.b64encode(b"two").decode(),
            },
        },
    )
    assert (
        "unavailable"
        in studio.dynamic_embeds.render("«artifact_content:same.txt >>> format:text»")["text"]
    )
    assert (
        studio.dynamic_embeds.render("«artifact_content:" + first["id"] + " >>> format:text»")[
            "text"
        ]
        == "one"
    )
    assert (
        studio.dynamic_embeds.render("«artifact_content:" + second["id"] + " >>> format:text»")[
            "text"
        ]
        == "two"
    )
    assert "unavailable" in studio.dynamic_embeds.render("«artifact_return:same.txt:999»")["text"]
    with pytest.raises(ValueError, match="work limit"):
        template("{{#items}}" + "x{{.}}" * 20 + "{{/items}}", list(range(1000)))


def test_attachment_links_recheck_identity_and_preserve_old_bytes(team):
    service, _, _, request, _ = team
    artifact = upload(service, "download.txt", b"original", actor="alice")
    token = execution_identity.set("alice")
    try:
        result = service.dynamic_embeds.render(
            "«artifact_return:download.txt:1»", actor=service.dynamic_embeds.actor()
        )
    finally:
        execution_identity.reset(token)
    part = result["structured_content"][0]
    upload(service, "download.txt", b"new version", actor="alice", identity=artifact["id"])
    path = "/api/catalog/artifacts/" + part["artifact_id"] + "/versions/" + str(part["revision"])
    assert request(path, user="alice") == (200, b"original")
    assert request(path, user="bob")[0] == 403
    assert request(path)[0] == 401


@pytest.mark.parametrize("kind", ["tool", "workflow"])
def test_tool_and_workflow_outputs_keep_json_types_and_do_not_resolve_data_twice(studio, kind):
    name = next(iter(studio.dynamic_embeds.names))
    arguments = {"text": "«state:user»", "state": {"user": '«math:4+4» "quoted"'}}
    if kind == "tool":
        payload = {"kind": "tool", "task": "Explicit render", "tool": name, "kwargs": arguments}
    else:
        workflow = studio.save_definition(
            "workflows",
            {
                "name": "Render report",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "render", "kind": "tool", "tool_id": name, "kwargs": arguments}
                    ],
                    "output_mapping": {"ref": "nodes.render"},
                },
            },
        )
        payload = {"kind": "workflow", "task": "Explicit render", "workflow_id": workflow["id"]}
    run = settled(studio, studio.start_run(payload))
    assert run["status"] == "completed", run
    assert json.loads(run["output"])["text"] == '«math:4+4» "quoted"'


def test_embed_parts_share_existing_conversation_payload_limits(studio):
    result = AgentResult(
        success=True,
        output="ok",
        metadata={"structured_content": [{"type": "text", "text": "original"} for _ in range(40)]},
    )
    parts = studio.conversations.content(result, {}, additional=[{"type": "text", "text": "extra"}])
    assert len(parts) == 40 and all(part["text"] == "original" for part in parts)
    result.metadata["structured_content"] = "invalid provider metadata"
    assert studio.conversations.content(
        result, {}, additional=[{"type": "text", "text": "safe"}]
    ) == [{"type": "text", "text": "safe"}]

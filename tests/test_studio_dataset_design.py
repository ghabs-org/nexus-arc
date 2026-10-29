"""Dataset drafts reuse isolated designer jobs and never save or execute cases."""

import json

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def proposal(cases=None):
    return {"name": "Support cases", "data": {"cases": cases or [
        {"input": "Refund after 31 days", "contains": ["cannot refund"]},
        {"input": "Refund after 29 days", "contains": ["eligible"]},
    ]}}


def service(tmp_path, output):
    calls = []

    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            assert context.workspace != tmp_path and not list(context.workspace.iterdir())
            return AgentResult(success=True, output=output)

    return StudioService(tmp_path, builder_factory=lambda _: Model()), calls


def test_draft_has_no_workspace_metadata_or_automatic_resource_save(tmp_path):
    studio, calls = service(tmp_path, json.dumps(proposal()))
    try:
        studio.save_definition("agents", {"id": "private-agent", "name": "Private", "instructions": "Secret"})
        run = done(studio, studio.start_run({"kind": "design", "design_kind": "dataset", "task": "Refund rules", "case_count": 2, "provider": "codex", "model": "chosen-model"}))
        assert run["status"] == "completed", run
        assert len(run["design_proposal"]["data"]["cases"]) == 2
        assert not studio.catalog.list("datasets") and not studio.catalog.list("evaluations")
        assert len(calls) == 1 and calls[0].model_override == "chosen-model"
        assert calls[0].metadata == {"worker_tools": "none"}
        assert "private-agent" not in calls[0].prompt and "Secret" not in calls[0].prompt
        assert not calls[0].workspace.exists()
        # The generated proposal is usable by the ordinary reviewed save path.
        saved = studio.catalog.save("datasets", run["design_proposal"])
        assert saved["revision"] == 1 and len(studio.records("runs")) == 1
    finally:
        studio.close()


@pytest.mark.parametrize("output", [
    "not json", '{"name":"One","name":"Two","data":{}}',
    json.dumps(proposal([{"input": "x", "contains": ["ok"]}])),
    json.dumps(proposal([{"input": "x", "contains": ["ok"]}, {"input": " X ", "contains": ["ok"]}])),
    json.dumps(proposal([{"input": "x", "typo": "ignored"}, {"input": "y", "contains": ["ok"]}])),
    json.dumps({**proposal(), "activate": True}),
])
def test_invalid_generated_data_fails_without_saving(tmp_path, output):
    studio, calls = service(tmp_path, output)
    try:
        run = done(studio, studio.start_run({"kind": "design", "design_kind": "dataset", "task": "Test", "case_count": 2}))
        assert run["status"] == "failed", run
        assert "design_proposal" not in run
        assert not studio.catalog.list("datasets") and len(calls) == 1
    finally:
        studio.close()


@pytest.mark.parametrize("count", [0, 101, True, "2", 1.5])
def test_bad_counts_fail_before_queue_or_provider(tmp_path, count):
    studio, calls = service(tmp_path, json.dumps(proposal()))
    try:
        with pytest.raises(ValueError, match="1–100"):
            studio.start_run({"kind": "design", "design_kind": "dataset", "task": "Test", "case_count": count})
        assert not calls and not studio.records("runs")
    finally:
        studio.close()


def test_http_drafts_require_builder_csrf_and_owned_run(team, monkeypatch):
    studio, policy, _, request, _ = team
    calls = []

    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(success=True, output=json.dumps(proposal()))

    monkeypatch.setattr(policy, "provider", lambda *args, **kwargs: Model())
    payload = {"design_kind": "dataset", "task": "Refund rules", "case_count": 2, "provider": "codex"}
    path = "/api/studio/design"
    assert request(path, payload)[0] == 401
    for user in ("viewer", "bob"):
        assert request(path, payload, user=user)[0] == 403
    assert request(path, payload, user="alice", csrf="invalid")[0] == 403
    assert request(path, {**payload, "case_count": 101}, user="alice")[0] == 400
    # Direct /runs must not bypass the designer role check.
    assert request("/api/runs", {**payload, "kind": "design"}, user="bob")[0] == 403
    status, run = request(path, payload, user="alice")
    assert status == 202 and done(studio, run)["status"] == "completed"
    assert request("/api/runs/" + run["id"], user="bob")[0] == 403
    assert request("/api/runs/" + run["id"], user="alice")[1]["design_proposal"]["name"] == "Support cases"
    assert len(calls) == 1 and not studio.catalog.list("datasets")

"""Evaluation file roles are authorized, pinned and separated from target input."""

import base64
import copy
import hashlib
import json

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_datasets import parse_dataset, validate_dataset
from nexus.core.studio_evaluation_files import pin_files, validate_references
from tests.test_studio_resources import done
from tests.test_studio_team import team as team

JUDGE = {"provider": "codex", "rubric": "Compare with grading-reference files"}


def artifact(studio, identity, text, owner=None, **extra):
    return studio.catalog.save(
        "artifacts",
        {
            "id": identity,
            "name": identity,
            "requester_nexus_id": owner,
            "data": {
                "filename": identity + ".txt",
                "content_base64": base64.b64encode(text.encode()).decode(),
            },
            **extra,
        },
    )


def reference(item):
    return {"id": item["id"], "revision": item["revision"]}


def save_suite(studio, case):
    return studio.catalog.save(
        "evaluations",
        {
            "name": "File roles",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "cases": [case],
            },
        },
    )


def model(calls):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            if "Evaluate the answer using only the rubric" in context.prompt:
                return AgentResult(success=True, output='{"score":1,"reason":"Matches reference"}')
            return AgentResult(success=True, output="Approved")

    return Provider()


def test_queue_copies_exact_revisions_and_only_judge_gets_reference_after_archival(
    tmp_path, monkeypatch
):
    calls = []
    studio = StudioService(tmp_path, provider_factory=lambda _: model(calls))
    try:
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Use input"}
        )
        first = artifact(studio, "input", "ORIGINAL_INPUT")
        expected = artifact(studio, "reference", "SECRET_EXPECTED_ANSWER")
        # Explicitly select the old revision, not the newer current contents.
        artifact(studio, "input", "NEW_INPUT", revision=1)
        selected = save_suite(
            studio,
            {
                "input": "Review files",
                "input_files": [reference(first)],
                "reference_files": [reference(expected)],
                "judge": JUDGE,
            },
        )
        submissions = []
        submit = studio.executor.submit
        monkeypatch.setattr(studio.executor, "submit", lambda *args: submissions.append(args))
        queued = studio.start_run(
            {
                "kind": "evaluation",
                "evaluation_id": selected["id"],
                "_evaluation_files": [{"reference_files": [{"text": "FORGED"}]}],
            }
        )
        snapshot = submissions[0][2]["_evaluation_files"]
        assert snapshot[0]["input_files"][0]["text"] == "ORIGINAL_INPUT"
        artifact(studio, "reference", "REPLACED_EXPECTED", revision=1)
        studio.catalog.archive("artifacts", "input", {"revision": 2})
        studio.catalog.archive("artifacts", "reference", {"revision": 2})
        # Persisted payload remains sufficient even after copying/reloading JSON.
        args = list(submissions[0])
        args[2] = json.loads(json.dumps(args[2]))
        monkeypatch.setattr(studio.executor, "submit", submit)
        submit(*args)
        finished = done(studio, queued)
        assert finished["status"] == "completed", finished
        assert len(calls) == 2
        assert (
            "ORIGINAL_INPUT" in calls[0].prompt and "SECRET_EXPECTED_ANSWER" not in calls[0].prompt
        )
        assert "SECRET_EXPECTED_ANSWER" in calls[1].prompt
        assert all(
            "NEW_INPUT" not in context.prompt
            and "FORGED" not in context.prompt
            and "REPLACED_EXPECTED" not in context.prompt
            for context in calls
        )
        report = studio._read(studio._path("evaluation-reports", queued["id"]))
        assert report["passed"] == 1
        assert (
            report["results"][0]["file_sources"]["reference_files"][0]["sha256"]
            == hashlib.sha256(b"SECRET_EXPECTED_ANSWER").hexdigest()
        )
        assert "SECRET_EXPECTED_ANSWER" not in json.dumps(report)
    finally:
        studio.close()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "path",
        [{"id": "x"}],
        [{"id": "x", "revision": True}],
        [{"id": "x", "revision": 0}],
        [{"id": "x", "revision": 1, "path": "/tmp/secret"}],
        [{"id": "x", "revision": 1}] * 2,
        [{"id": str(i), "revision": 1} for i in range(6)],
    ],
)
def test_invalid_file_references_are_rejected(value):
    with pytest.raises(ValueError):
        validate_references(value)


def test_file_roles_survive_import_and_require_explicit_judging():
    case = {
        "input": "Task",
        "judge": JUDGE,
        "input_files": [{"id": "input", "revision": 2}],
        "reference_files": [{"id": "expected", "revision": 3}],
    }
    canonical = validate_dataset({"cases": [case]})
    assert parse_dataset({"format": "json", "content": json.dumps(canonical)}) == canonical
    assert parse_dataset({"format": "yaml", "content": json.dumps(canonical)}) == canonical
    bad = {**case, "judge": None, "contains": ["Approved"]}
    with pytest.raises(ValueError, match="judge"):
        validate_dataset({"cases": [bad]})
    with pytest.raises(ValueError, match="different"):
        validate_dataset({"cases": [{**case, "reference_files": case["input_files"]}]})


@pytest.mark.parametrize("text", ["", "x" * 16001])
def test_unreadable_or_oversize_text_rejects_before_any_run(tmp_path, text):
    studio = StudioService(tmp_path)
    try:
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Use input"}
        )
        item = artifact(studio, "input", text)
        saved = save_suite(
            studio, {"input": "Task", "contains": ["ok"], "input_files": [reference(item)]}
        )
        with pytest.raises(ValueError, match="nonempty extracted text"):
            studio.start_run({"kind": "evaluation", "evaluation_id": saved["id"]})
        assert not studio.records("runs")
    finally:
        studio.close()


def test_snapshot_total_limit_and_caller_copy_removed(tmp_path):
    studio = StudioService(tmp_path)
    try:
        item = artifact(studio, "input", "x" * 16000)
        payload = {"suite_snapshot": {"data": {"cases": [{"input_files": [reference(item)]}] * 60}}}
        with pytest.raises(ValueError, match="900 KiB"):
            pin_files(studio, payload)
        plain = {"suite_snapshot": {"data": {"cases": [{}]}}, "_evaluation_files": "FORGED"}
        pin_files(studio, plain)
        assert "_evaluation_files" not in plain
    finally:
        studio.close()


def test_http_private_current_and_historical_files_and_archival(team, monkeypatch):
    studio, policy, _, request, _ = team
    calls = []
    monkeypatch.setattr(policy, "provider", lambda *args, **kwargs: model(calls))
    alice = artifact(studio, "alice-file", "ALICE_PRIVATE", "alice")
    foreign = artifact(studio, "foreign", "BOB_PRIVATE", "bob")
    case = {"input": "Review", "contains": ["Approved"], "input_files": [reference(alice)]}
    saved = save_suite(studio, case)
    payload = {"kind": "evaluation", "evaluation_id": saved["id"]}
    assert request("/api/runs", payload, user="bob")[0] == 403
    # Current record visible to Alice, historical record owned by Bob.
    artifact(studio, "alice-file", "Current", "alice", revision=1)
    studio._write(
        studio._path("catalog-history/artifacts/alice-file", "1"), {**alice, "owner_id": "bob"}
    )
    assert request("/api/runs", payload, user="alice")[0] == 403
    # Replace the suite with a foreign file: sharing a suite never grants bytes.
    saved = studio.catalog.save(
        "evaluations",
        {
            **saved,
            "data": {**saved["data"], "cases": [{**case, "input_files": [reference(foreign)]}]},
        },
    )
    assert request("/api/runs", payload, user="alice")[0] == 403
    saved = studio.catalog.save(
        "evaluations",
        {
            **saved,
            "data": {
                **saved["data"],
                "cases": [{**case, "input_files": [{"id": alice["id"], "revision": 999}]}],
            },
        },
    )
    assert request("/api/runs", payload, user="alice")[0] == 404
    studio.catalog.archive("artifacts", "alice-file", {"revision": 2})
    assert request("/api/runs", payload, user="alice")[0] == 400
    assert calls == [] and studio.records("runs") == []


def test_workflow_target_payload_never_contains_reference_files(tmp_path, monkeypatch):
    calls, targets = [], []
    studio = StudioService(tmp_path, provider_factory=lambda _: model(calls))
    try:
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Use input"}
        )
        studio.save_definition(
            "workflows",
            {
                "id": "review",
                "name": "Review",
                "steps": [{"id": "read", "agent_type": "reviewer", "prompt": "{task}"}],
            },
        )
        source = artifact(studio, "input", "WORKFLOW_INPUT")
        expected = artifact(studio, "reference", "JUDGE_ONLY_SECRET")
        saved = studio.catalog.save(
            "evaluations",
            {
                "name": "Workflow files",
                "data": {
                    "target_kind": "workflow",
                    "target_id": "review",
                    "cases": [
                        {
                            "input": "Review files",
                            "input_files": [reference(source)],
                            "reference_files": [reference(expected)],
                            "judge": JUDGE,
                        }
                    ],
                },
            },
        )

        async def workflow(run_id, payload):
            targets.append(copy.deepcopy(payload))
            return "Approved"

        monkeypatch.setattr(studio, "_workflow", workflow)
        finished = done(
            studio, studio.start_run({"kind": "evaluation", "evaluation_id": saved["id"]})
        )
        assert finished["status"] == "completed", finished
        assert len(targets) == len(calls) == 1
        assert "WORKFLOW_INPUT" in targets[0]["task"]
        assert "JUDGE_ONLY_SECRET" not in json.dumps(targets[0])
        assert "_evaluation_files" not in targets[0]
        assert "JUDGE_ONLY_SECRET" in calls[0].prompt
    finally:
        studio.close()


def test_office_extraction_and_nontext_rejection_reuse_document_service(tmp_path):
    import io
    import zipfile

    studio = StudioService(tmp_path)
    try:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as document:
            document.writestr(
                "word/document.xml",
                '<w:document xmlns:w="word"><w:t>OFFICE_INPUT</w:t></w:document>',
            )
        item = studio.catalog.save(
            "artifacts",
            {
                "name": "Office",
                "data": {
                    "filename": "input.docx",
                    "content_base64": base64.b64encode(buffer.getvalue()).decode(),
                },
            },
        )
        payload = {"suite_snapshot": {"data": {"cases": [{"input_files": [reference(item)]}]}}}
        pin_files(studio, payload)
        assert payload["_evaluation_files"][0]["input_files"][0]["text"] == "OFFICE_INPUT"
        binary = studio.catalog.save(
            "artifacts",
            {
                "name": "Binary",
                "data": {
                    "filename": "image.png",
                    "content_base64": base64.b64encode(b"\x89PNG\x00\xff").decode(),
                },
            },
        )
        with pytest.raises(ValueError, match="text, PDF or Office"):
            pin_files(
                studio,
                {"suite_snapshot": {"data": {"cases": [{"input_files": [reference(binary)]}]}}},
            )
    finally:
        studio.close()

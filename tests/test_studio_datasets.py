"""Datasets validate without execution and remain independent of copied suite snapshots."""

import copy
import json

import pytest

from nexus.core.studio_datasets import DATASET_LIMIT, parse_dataset, validate_dataset
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done
from tests.test_studio_team import team as team

CASES = [{"input": "Review policy", "contains": ["Evidence"], "contexts": ["Reference passage."]}]


@pytest.mark.parametrize(
    "format,content",
    [
        ("json", json.dumps(CASES)),
        ("json", json.dumps({"kind": "dataset", "version": 1, "cases": CASES})),
        (
            "yaml",
            "kind: dataset\nversion: 1\ncases:\n- input: Review policy\n  contains: [Evidence]\n  contexts: [Reference passage.]\n",
        ),
        (
            "csv",
            '\ufeffinput,contains,contexts\nReview policy,"[""Evidence""]","[""Reference passage.""]"\n',
        ),
    ],
)
def test_import_formats_share_canonical_validation(format, content):
    result = parse_dataset({"format": format, "content": content})
    assert result == validate_dataset({"cases": CASES})
    assert parse_dataset({"format": "json", "content": json.dumps(result)}) == result


def test_csv_preserves_multiline_and_unicode_exact_reference_outputs():
    result = parse_dataset(
        {"format": "csv", "content": 'input,expected\n"First line\nSecond line","Approved ✓"\n'}
    )
    assert result["cases"][0]["input"] == "First line\nSecond line"
    assert result["cases"][0]["checks"] == [{"type": "exact", "expected": "Approved ✓"}]


@pytest.mark.parametrize(
    "format,content",
    [
        ("json", '{"cases":[],"cases":[]}'),
        ("json", '[{"input":"x","contains":["ok"],"unknown":true}]'),
        ("json", '[{"input":"x","checks":[{"type":"exact","expected":"ok","ignored":true}]}]'),
        ("json", '{"kind":"experiment","cases":[]}'),
        ("json", '{"version":true,"cases":[]}'),
        ("json", '{"version":2,"cases":[]}'),
        ("json", '[{"input":"x","checks":[{"type":"number_range","min":0,"max":NaN}]}]'),
        ("json", '[{"input":"x","checks":[{"type":"json_equals","expected":1e999}]}]'),
        ("yaml", "cases: []\ncases: []"),
        ("yaml", "cases: &a [*a]"),
        ("yaml", "!!python/object/apply:os.system ['echo forbidden']"),
        ("yaml", "cases:\n- {input: x, contains: [yes]}"),
        ("csv", "input,unknown\nx,ok"),
        ("csv", "input,expected,expected\nx,ok,ok"),
        ("csv", "input,expected\nx,ok,extra"),
        ("csv", "input,expected\nx"),
        ("csv", "input,expected\nx,"),
        ("csv", 'input,expected\n"unterminated,ok'),
        ("json", "[" * 2000),
    ],
)
def test_invalid_imports_never_return_partially_validated_cases(format, content):
    with pytest.raises(ValueError):
        parse_dataset({"format": format, "content": content})


def test_dataset_bounds_and_unknown_input_fields():
    with pytest.raises(ValueError, match="900 KiB"):
        parse_dataset({"format": "json", "content": " " * (DATASET_LIMIT + 1)})
    with pytest.raises(ValueError, match="1–100"):
        validate_dataset({"cases": CASES * 101})
    with pytest.raises(ValueError):
        parse_dataset({"format": "json", "content": json.dumps(CASES), "path": "/etc/passwd"})
    with pytest.raises(ValueError, match="format"):
        parse_dataset({"format": "pickle", "content": "x"})


def test_catalog_history_archive_configuration_and_exports(app):
    studio, calls = app
    first = studio.catalog.save("datasets", {"name": "Policy fixtures", "data": {"cases": CASES}})
    changed = copy.deepcopy(first["data"])
    changed["cases"][0]["contains"] = ["Clear"]
    second = studio.catalog.save("datasets", {**first, "data": changed})
    with pytest.raises(ValueError, match="changed"):
        studio.catalog.save("datasets", {**first, "data": changed})
    assert studio.catalog.history("datasets", first["id"])[0]["data"] == first["data"]
    archived = studio.catalog.archive("datasets", second["id"], {"revision": 2})
    with pytest.raises(ValueError, match="archived"):
        studio.catalog.get("datasets", second["id"])
    studio.catalog.archive(
        "datasets", second["id"], {"revision": archived["revision"], "archived": False}
    )
    assert studio.export_definitions()["catalog"]["datasets"][0]["revision"] == 4
    document = {
        "format": "nexus-studio-config/v1",
        "definitions": [],
        "resources": [
            {"kind": "datasets", "id": "imported", "name": "Imported", "data": first["data"]}
        ],
    }
    plan = studio.configuration.plan(document, None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    assert studio.catalog.get("datasets", "imported")["data"] == first["data"]
    assert calls == [] and studio.records("runs") == []


def test_copied_cases_and_queued_suite_snapshot_survive_dataset_and_suite_edits(app, monkeypatch):
    studio, calls = app
    dataset = studio.catalog.save("datasets", {"name": "Policy", "data": {"cases": CASES}})
    suite = studio.catalog.save(
        "evaluations",
        {
            "name": "Copied cases",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "cases": dataset["data"]["cases"],
            },
        },
    )
    queued = []
    original = studio.executor.submit
    monkeypatch.setattr(studio.executor, "submit", lambda *args: queued.append(args))
    run = studio.start_run(
        {"kind": "evaluation", "evaluation_id": suite["id"], "suite_snapshot": {"name": "FORGED"}}
    )
    assert queued[0][2]["suite_snapshot"]["data"]["cases"] == suite["data"]["cases"]
    changed = {"cases": [{"input": "Changed", "contains": ["NO_MATCH"]}]}
    studio.catalog.save("datasets", {**dataset, "data": changed})
    studio.catalog.archive("datasets", dataset["id"], {"revision": 2})
    studio.catalog.save("evaluations", {**suite, "data": {**suite["data"], **changed}})
    monkeypatch.setattr(studio.executor, "submit", original)
    original(*queued[0])
    finished = done(studio, run)
    assert finished["status"] == "completed"
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert report["suite_revision"] == 1 and report["passed"] == report["total"] == 1
    assert report["results"][0]["input"] == "Review policy" and len(calls) == 1


def test_http_dataset_permissions_shared_workspace_and_validation_are_explicit(team):
    studio, _, manager, request, calls = team
    parse = "/api/studio/datasets/parse"
    payload = {"format": "csv", "content": "input,expected\nReview,Approved"}
    assert request(parse, payload)[0] == 401
    for user in ("viewer", "bob"):
        assert request(parse, payload, user=user)[0] == 403
    status, data = request(parse, payload, user="alice")
    assert status == 200 and studio.catalog.list("datasets") == []
    assert request(parse, {**payload, "content": "input,typo\nx,y"}, user="alice")[0] == 400
    path = "/api/catalog/datasets"
    status, saved = request(path, {"name": "Workspace fixtures", "data": data}, user="alice")
    assert status == 201 and saved["revision"] == 1
    for user in ("alice", "bob", "viewer", "owner"):
        assert request(path, user=user)[1]["items"][0]["id"] == saved["id"]
    assert request(path, {"name": "Denied", "data": data}, user="bob")[0] == 403
    assert request(parse, payload, user="alice", csrf="invalid")[0] == 403
    manager.revoked.add("alice")
    assert request(path, user="alice")[0] == 401
    assert calls == [] and studio.records("runs") == []

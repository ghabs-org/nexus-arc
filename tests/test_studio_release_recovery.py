"""Abrupt process death tests the actual release journal on file and SQL state."""

import json
import subprocess
import sys

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_build_lifecycle import connector, proposal, seed

CHILD = r"""
import json, os, sys
from nexus.core.studio import StudioService
root, options, identity, plan_id, checksum, point = json.loads(sys.argv[1])
studio = StudioService(root, state_options=options)
write = studio._write
def crash(path, value):
    if point == "after_commit" and path.parent == studio.configuration.journal_dir and value.get("status") == "active":
        os._exit(73)
    write(path, value)
    if point == "release_marker" and path.parent.name == "configuration-releases":
        os._exit(73)
    if point == "after_resource" and value.get("kind") == "connectors" and value.get("data", {}).get("enabled"):
        os._exit(73)
    if point == "after_build" and path.parent.name == "builds" and value.get("lifecycle_status") == "active":
        os._exit(73)
studio._write = crash
studio.resource_lifecycle.transition_build(identity, {"plan_id": plan_id, "digest": checksum})
os._exit(99)
"""


@pytest.mark.parametrize("backend", ["files", "sql"])
@pytest.mark.parametrize(
    "point", ["after_resource", "after_build", "release_marker", "after_commit"]
)
def test_release_process_death_is_recovered_without_partial_activation(
    tmp_path, monkeypatch, backend, point
):
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-fixture-key")
    options = (
        {}
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "release.sqlite"),
            "namespace": "release-recovery",
            "workers": 0,
        }
    )
    root = tmp_path / "workspace"
    studio = StudioService(root, state_options=options)
    try:
        build = seed(studio, proposal(resources=[connector()]))
        identity = build["proposal"]["resources"][1]["id"]
        before = studio.catalog.get("connectors", identity)
        release_count = len(studio.records("configuration-releases", limit=None))
        review = studio.resource_lifecycle.plan_build(
            build["id"],
            {
                "revision": build["revision"],
                "action": "activate",
                "enable": ["connectors:" + identity],
            },
        )
        assert not review["errors"]
    finally:
        studio.close()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            CHILD,
            json.dumps(
                [
                    str(root),
                    options,
                    build["id"],
                    review["id"],
                    review["digest"],
                    point,
                ]
            ),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == 73, child.stderr
    recovered = StudioService(root, state_options=options)
    try:
        current = recovered.builder.get(build["id"])
        record = recovered.catalog.get("connectors", identity)
        if point == "after_commit" or (point == "release_marker" and backend == "files"):
            assert current["lifecycle_status"] == "active" and record["data"]["enabled"]
            assert len(recovered.records("configuration-releases", limit=None)) == release_count + 1
        else:
            assert current == build and record == before
            assert len(recovered.records("configuration-releases", limit=None)) == release_count
            assert "result" not in recovered._read(recovered._path("resource-plans", review["id"]))
            assert all(
                not item["active"] for item in recovered.records("resource-lifecycle", limit=None)
            )
        result = recovered.resource_lifecycle.transition_build(
            build["id"],
            {
                "plan_id": review["id"],
                "digest": review["digest"],
            },
        )
        assert result["status"] == "active"
        assert recovered.catalog.get("connectors", identity)["data"]["enabled"]
        assert len(recovered.records("configuration-releases", limit=None)) == release_count + 1
    finally:
        recovered.close()

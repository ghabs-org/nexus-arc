"""Check a reviewed OpenAPI service without enabling it or retaining its response."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_resource_health import StudioResourceHealth


def prepare(studio, url, path="/health"):
    resource = {
        "kind": "connectors",
        "id": "health-api",
        "name": "Service connectivity",
        "data": {
            "type": "openapi",
            "url": url,
            "allow_private": True,
            "auth": {"type": "bearer", "token_env": "NEXUS_HEALTH_FIXTURE_TOKEN"},
            "spec": {"openapi": "3.0.3", "paths": {path: {"get": {"operationId": "health"}}}},
            "operations": ["health"],
            "actions": ["list_operations", "read"],
            "health_operation": "health",
        },
    }
    proposal = {
        "name": "Service connectivity",
        "summary": "Explicit local service check",
        "assumptions": [],
        "agents": [],
        "workflow": None,
        "resources": [resource],
    }
    identity = "health-example"
    normalized = studio.builder.normalize(proposal, identity, "codex")
    studio._write(
        studio._path("builds", identity),
        {
            "id": identity,
            "owner_id": None,
            "status": "draft",
            "revision": 1,
            "provider": "codex",
            "model": "",
            "proposal": normalized,
            "messages": [],
        },
    )
    build = studio.builder.apply(identity, {"revision": 1})
    key = "connectors:" + build["proposal"]["resources"][0]["id"]
    return build, key


def exercise(studio, url, path="/health", expected="passed"):
    build, key = prepare(studio, url, path)
    health = StudioResourceHealth(studio.resource_lifecycle)
    result = health.check(
        build["id"],
        {
            "revision": build["revision"],
            "release_id": build["release_id"],
            "component": key,
        },
    )
    assert result["status"] == expected, result
    assert studio.resource_lifecycle.marker(key)["active"] is False
    assert not studio.catalog.get("connectors", key.split(":")[1])["data"]["enabled"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:49645/health-service")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--expect", choices=["passed", "failed"], default="passed")
    args = parser.parse_args()
    # Synthetic local simulation only; real accounts configure their own reference.
    os.environ.setdefault("NEXUS_HEALTH_FIXTURE_TOKEN", "synthetic-health-key")
    with tempfile.TemporaryDirectory(prefix="nexus-openapi-health-") as directory:
        studio = StudioService(Path(directory))
        try:
            print(json.dumps(exercise(studio, args.url, args.path, args.expect)))
        finally:
            studio.close()


if __name__ == "__main__":
    main()

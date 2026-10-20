"""Consume selected Teams and OpenAPI operations against local Postman peers.

Run studio-m56.sim.yaml on port 49730, then PYTHONPATH=. this script.
No provider account, installation hook or external message is used.
"""

import argparse
import asyncio
import base64
import io
import json
import os
import tempfile
import time
from unittest.mock import patch

import requests

from nexus.core.studio import StudioService
from nexus.core.studio_teams import GRAPH


def exercise(base="http://127.0.0.1:49730"):
    def graph_request(method, url, headers, body):
        if not url.startswith(GRAPH + "/teams/"):
            raise ValueError("Example only maps the known Graph peer")
        response = requests.request(
            method,
            url.replace("https://graph.microsoft.com", base + "/graph"),
            headers=headers,
            json=body,
            timeout=5,
            allow_redirects=False,
        )
        response.raise_for_status()
        return response.json()

    with (
        tempfile.TemporaryDirectory(prefix="nexus-m56-example-") as directory,
        patch.dict(
            os.environ,
            {
                "EXAMPLE_TEAMS_TOKEN": "synthetic-teams-token",
                "EXAMPLE_API_TOKEN": "synthetic-openapi-token",
            },
        ),
    ):
        studio = StudioService(
            directory, connection_options={"enterprise_options": {"request_client": graph_request}}
        )
        try:
            channel = studio.catalog.save(
                "connectors",
                {
                    "name": "Review channel",
                    "data": {
                        "type": "teams",
                        "team_id": "11111111-2222-3333-4444-555555555555",
                        "channel_id": "19:test@thread.tacv2",
                        "token_env": "EXAMPLE_TEAMS_TOKEN",
                        "actions": ["status", "query", "send", "update_message"],
                        "enabled": True,
                    },
                },
            )

            def call(action, arguments=None, confirm=False):
                return asyncio.run(
                    studio.connections.execute(
                        channel["id"],
                        {"action": action, "arguments": arguments or {}, "confirm": confirm},
                    )
                )

            assert call("status")["displayName"] == "Fixture channel"
            assert call("query")["value"]
            try:
                call("send", {"text": "Must not send without review"})
            except ValueError:
                pass
            else:
                raise AssertionError("Write bypassed review")
            sent = call("send", {"text": "Synthetic reviewed post"}, confirm=True)
            call("send", {"text": "Synthetic reply", "reply_to": sent["id"]}, confirm=True)
            call(
                "update_message", {"text": "Synthetic edit", "message_id": sent["id"]}, confirm=True
            )
            spec = {
                "openapi": "3.0.3",
                "security": [{"account": []}],
                "components": {
                    "securitySchemes": {
                        "account": {"type": "apiKey", "in": "header", "name": "X-Account"}
                    },
                    "parameters": {
                        "identifier": {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    },
                },
                "paths": {
                    "/items/{id}": {
                        "parameters": [{"$ref": "#/components/parameters/identifier"}],
                        "get": {"operationId": "read"},
                    }
                },
            }
            api = studio.catalog.save(
                "connectors",
                {
                    "name": "Selected API",
                    "data": {
                        "type": "openapi",
                        "url": base + "/graph",
                        "allow_private": True,
                        "auth": {
                            "type": "api-key",
                            "token_env": "EXAMPLE_API_TOKEN",
                            "header": "X-Account",
                        },
                        "spec": spec,
                        "operations": ["read"],
                        "actions": ["read"],
                        "enabled": True,
                    },
                },
            )
            result = asyncio.run(
                studio.connections.execute(
                    api["id"],
                    {
                        "action": "read",
                        "arguments": {"operation_id": "read", "parameters": {"id": "item"}},
                    },
                )
            )
            assert result["verified"] is True
            from nexus.core.studio_resource_health import StudioResourceHealth

            identity = "teams-health-example"
            proposal = studio.builder.normalize(
                {
                    "name": "Channel readiness",
                    "summary": "Read-only channel health",
                    "assumptions": [],
                    "agents": [],
                    "workflow": None,
                    "resources": [
                        {
                            "kind": "connectors",
                            "id": "channel",
                            "name": "Channel health",
                            "data": channel["data"],
                        }
                    ],
                },
                identity,
                "codex",
            )
            studio._write(
                studio._path("builds", identity),
                {
                    "id": identity,
                    "owner_id": None,
                    "status": "draft",
                    "revision": 1,
                    "provider": "codex",
                    "model": "",
                    "proposal": proposal,
                    "messages": [],
                },
            )
            build = studio.builder.apply(identity, {"revision": 1})
            component = "connectors:" + build["proposal"]["resources"][0]["id"]
            health = StudioResourceHealth(studio.resource_lifecycle).check(
                identity,
                {
                    "revision": build["revision"],
                    "release_id": build["release_id"],
                    "component": component,
                },
            )
            assert health["status"] == "passed"
            from PIL import Image

            from nexus.adapters.decisions.openai import ask_strict

            output = io.BytesIO()
            Image.new("RGB", (12, 12), "red").save(output, format="PNG")
            photo = studio.catalog.save(
                "artifacts",
                {
                    "name": "Selected photo",
                    "data": {
                        "filename": "photo.png",
                        "content_base64": base64.b64encode(output.getvalue()).decode(),
                    },
                },
            )
            studio.workbench.jobs.decision_client = lambda state, questions, **kwargs: ask_strict(
                state,
                questions,
                model="gpt-6-luna",
                api_key="synthetic-image-key",
                endpoint=base + "/graph/native",
                **kwargs,
            )
            workflow = studio.save_definition(
                "workflows",
                {
                    "name": "Photo review",
                    "graph": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "inspect",
                                "kind": "decision",
                                "decision": {
                                    "format": "nexus-typed-decision/v1",
                                    "backend": "openai",
                                    "model": "gpt-6-luna",
                                    "questions": {
                                        "visible": {
                                            "type": "boolean",
                                            "instructions": "Visible damage?",
                                        }
                                    },
                                },
                                "images": [photo["id"]],
                            }
                        ],
                        "output_mapping": {"ref": "nodes.inspect"},
                    },
                },
            )
            run = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Inspect explicitly selected photo",
                }
            )
            deadline = time.monotonic() + 10
            while studio.run(run["id"])["status"] in {"running", "queued"}:
                if time.monotonic() > deadline:
                    raise RuntimeError("Image peer did not finish")
                time.sleep(0.01)
            assert studio.run(run["id"])["status"] == "completed", studio.run(run["id"])
            collection = {
                "agent_name": "routed-items",
                "agent_type": "workflow",
                "tools": [{"tool_type": "builtin", "tool_name": "count"}],
                "nodes": [
                    {
                        "id": "each",
                        "type": "map",
                        "node": "select",
                        "items": ["short", "long"],
                        "concurrency_limit": 2,
                    },
                    {
                        "id": "select",
                        "type": "switch",
                        "cases": [{"condition": "'{{_map_item}}' == 'short'", "node": "short"}],
                        "default": "long",
                    },
                    {
                        "id": "short",
                        "type": "tool",
                        "tool_name": "count",
                        "depends_on": ["select"],
                        "input": {"text": "one"},
                    },
                    {
                        "id": "long",
                        "type": "tool",
                        "tool_name": "count",
                        "depends_on": ["select"],
                        "input": {"text": "one two three"},
                    },
                ],
                "output_mapping": {"results": "{{each.output}}"},
            }
            converted = studio.configuration.convert_interop(
                {"documents": [collection], "bindings": {"tools": {"count": "text:wordcount"}}},
                None,
            )
            assert converted["valid"], converted
            plan = studio.configuration.plan(converted["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            routed = studio.start_run(
                {"kind": "workflow", "workflow_id": "routed-items", "task": "Route synthetic items"}
            )
            deadline = time.monotonic() + 10
            while studio.run(routed["id"])["status"] in {"running", "queued"}:
                if time.monotonic() > deadline:
                    raise RuntimeError("Routed collection did not finish")
                time.sleep(0.01)
            finished = studio.run(routed["id"])
            assert finished["status"] == "completed", finished
            assert [
                item["selected_branch"] for item in json.loads(finished["output"])["results"]
            ] == ["short", "long"]
            return {
                "teams_read_and_reviewed_write": True,
                "openapi_local_reference_and_security": True,
                "read_only_health": True,
                "owned_image_decision": True,
                "routed_collection": True,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49730")
    print(json.dumps(exercise(parser.parse_args().base)))

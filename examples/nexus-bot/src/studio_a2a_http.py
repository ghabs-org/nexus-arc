"""Use REST and JSON-RPC against the same local A2A task, without a model account."""

import argparse
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler

TOKEN = "synthetic-a2a-example-local-only-token"


def prepare(studio):
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Protocol word count",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "words",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.words"},
            },
        },
    )
    return studio.catalog.save(
        "entrypoints",
        {
            "name": "Protocol example",
            "data": {
                "target_kind": "workflow",
                "target_id": workflow["id"],
                "token_env": "NEXUS_A2A_EXAMPLE_TOKEN",
                "enabled": True,
                "a2a_enabled": True,
            },
        },
    )


def exercise(base):
    def request(path, body=None, version="1.0"):
        payload = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path,
            data=payload,
            headers={
                "Authorization": "Bearer " + TOKEN,
                "A2A-Version": version,
                "Content-Type": "application/a2a+json",
            },
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    payload = {
        "message": {
            "role": "ROLE_USER",
            "messageId": "example-message",
            "parts": [{"text": "one two three"}],
        }
    }
    status, result = request("/rest/message:send", payload)
    assert status == 200, result
    task = result["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "3"
    _, retried = request(
        "", {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": payload}
    )
    assert retried["result"]["task"]["id"] == task["id"]
    _, old = request(
        "", {"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task["id"]}}, "0.3"
    )
    assert old["result"]["id"] == task["id"] and old["result"]["status"]["state"] == "completed"
    _, listed = request("/rest/tasks?pageSize=1")
    assert listed["totalSize"] == 1
    status, cancelled = request("/rest/tasks/" + task["id"] + ":cancel", {})
    assert status == 400 and cancelled["error"]["details"][0]["reason"] == "TASK_NOT_CANCELABLE"
    return {
        "task_id": task["id"],
        "output": "3",
        "tasks_created": 1,
        "completed_cancel": "rejected",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Keep disposable fixture available for local contract checks",
    )
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--sql", action="store_true", help="Use SQLite durable state")
    parser.add_argument(
        "--grpc", action="store_true", help="Also exercise optional generated gRPC binding"
    )
    args = parser.parse_args()
    os.environ["NEXUS_A2A_EXAMPLE_TOKEN"] = TOKEN
    with tempfile.TemporaryDirectory(prefix="nexus-a2a-http-") as directory:
        root = Path(directory)
        options = (
            {
                "url": "sqlite:///" + str(root / "state.sqlite"),
                "namespace": "protocol-example",
                "workers": 1,
            }
            if args.sql
            else None
        )
        studio = StudioService(root / "workspace", state_options=options)
        server = None
        try:
            entry = prepare(studio)
            server = ThreadingHTTPServer(
                ("127.0.0.1", args.port), make_studio_handler(studio, "local-protocol-csrf-token")
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}/api/a2a/{entry['id']}"
            result = {"base": base, **exercise(base)}
            if args.grpc:
                import grpc
                from a2a.types import a2a_pb2 as messages
                from a2a.types import a2a_pb2_grpc as service
                from google.protobuf.json_format import ParseDict

                from nexus.core.studio_a2a_grpc import StudioA2AGRPC

                listener = StudioA2AGRPC(studio, entry["id"])
                with grpc.insecure_channel(listener.address) as channel:
                    client = service.A2AServiceStub(channel)
                    payload = {
                        "message": {
                            "role": "ROLE_USER",
                            "messageId": "example-message",
                            "parts": [{"text": "one two three"}],
                        }
                    }
                    task = client.SendMessage(
                        ParseDict(payload, messages.SendMessageRequest()),
                        metadata=(("authorization", "Bearer " + TOKEN),),
                        timeout=10,
                    ).task
                    assert task.id == result["task_id"] and task.artifacts[0].parts[0].text == "3"
                result["grpc"] = listener.address
            print(json.dumps(result), flush=True)
            if args.serve:
                threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.shutdown()
                server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

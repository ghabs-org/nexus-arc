"""Disposable browser acceptance server; never pointed at an existing workspace.

Run explicitly with PYTHONPATH=. python tests/studio_conversation_acceptance.py.
All providers/actions are synthetic. The only mutation increments a local counter.
The handler deliberately drops run polling after that effect, never a POST.
"""

import asyncio
import base64
import importlib.util
import io
import json
import socket
import tempfile
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from nexus.studio import make_studio_handler
from nexus.tools import tool_registry


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def create_fixture(root):
    checkout = Path(__file__).resolve().parents[1]
    demo = module("conversation_demo", checkout / "examples/nexus-bot/src/studio_conversations.py")
    documents = module("document_fixtures", checkout / "tests/test_studio_document_navigation.py")
    studio, project, artifact = demo.create_demo(root, delay=1)
    counters = {"effects": 0, "dropped_polls": 0, "run_posts": 0, "blocked_requests": []}
    lock = threading.Lock()

    async def effect():
        with lock:
            counters["effects"] += 1
        await asyncio.sleep(2)
        return "Effect committed once"

    tool_registry.register("acceptance:effect", effect, "Synthetic local counter")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 800)
    fixtures = {
        "csv": ("table.csv", b"Source,Result\nPolicy,Verified"),
        "json": ("data.json", b'{"verified":true}'),
        "yaml": ("data.yaml", b"verified: true"),
        "markdown": ("notes.md", b"## Verified evidence\n\n**Local** source."),
        "mermaid": ("flow.mmd", b"flowchart LR\n A[Evidence] --> B[Verified]"),
        "text": ("notes.txt", b"Plain evidence"),
        "audio": ("sample.wav", audio.getvalue()),
        "image": (
            "pixel.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jCJcAAAAASUVORK5CYII="
            ),
        ),
        "pdf": ("pages.pdf", documents.pdf(3)),
        "office": ("sections.pptx", documents.office("Old first section", "Old second section")),
        "html": (
            "unsafe.html",
            b"""<h2>Safe evidence</h2><script>fetch('/acceptance/probe/script')</script><img src="/acceptance/probe/image" onerror="fetch('/acceptance/probe/event')"><link rel="stylesheet" href="/acceptance/probe/css"><style>@import '/acceptance/probe/import';</style><iframe src="/acceptance/probe/frame"></iframe><form action="/acceptance/probe/form"><input autofocus onfocus="fetch('/acceptance/probe/focus')"><button>Send</button></form><a href="https://user:password@127.0.0.1/acceptance/probe/credentials">Credential URL</a><svg><image href="/acceptance/probe/svg"/></svg>""",
        ),
    }
    for identity, (filename, content) in fixtures.items():
        studio.catalog.save(
            "artifacts",
            {
                "id": "acceptance-" + identity,
                "name": identity,
                "data": {
                    "filename": filename,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
    # Replacement preserves both pinned sections for browser comparison.
    studio.catalog.save(
        "artifacts",
        {
            "id": "acceptance-office",
            "revision": 1,
            "name": "office",
            "data": {
                "filename": "sections.pptx",
                "content_base64": base64.b64encode(
                    documents.office("Replacement section")
                ).decode(),
            },
        },
    )
    studio.save_definition(
        "workflows",
        {
            "name": "Acceptance handoff",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "route",
                        "kind": "switch",
                        "cases": [
                            {
                                "condition": {"left": 1, "op": "eq", "right": 1},
                                "value": {"choice": "review"},
                            }
                        ],
                        "default": {"choice": "skip"},
                    },
                    {
                        "id": "specialist",
                        "name": "Evidence specialist",
                        "kind": "agent",
                        "agent_id": "evidence-guide",
                        "prompt": "{task}",
                    },
                    {"id": "skipped", "kind": "delay", "delay_seconds": 0},
                    {
                        "id": "approval",
                        "name": "Approve local effect",
                        "kind": "approval",
                        "depends_on": ["specialist"],
                    },
                    {
                        "id": "effect",
                        "name": "Commit once",
                        "kind": "tool",
                        "tool_id": "acceptance:effect",
                        "depends_on": ["approval"],
                    },
                ],
                "edges": [
                    {"source": "route", "target": "specialist", "choice": "review"},
                    {"source": "route", "target": "skipped", "choice": "skip"},
                ],
                "output_mapping": {"ref": "nodes.effect"},
            },
        },
    )
    review = studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": "acceptance-handoff",
            "task": "Review synthetic evidence",
        }
    )
    assert demo.settled(studio, review["id"])["status"] == "waiting"
    return studio, project, artifact, review, counters, lock


def acceptance_handler(studio, counters, lock):
    base = make_studio_handler(studio, "acceptance-csrf")

    class Handler(base):
        def do_GET(self):  # noqa: N802 - standard HTTP handler callback
            route = urlparse(self.path).path
            if route.startswith("/acceptance/probe/"):
                with lock:
                    counters["blocked_requests"].append(route)
                return self.send_payload(200, {"unexpected_request": True})
            if route == "/acceptance/status":
                with lock:
                    report = dict(counters)
                return self.send_payload(200, report)
            if route.startswith("/api/runs/") and route.count("/") == 3:
                with lock:
                    drop = counters["effects"] > 0 and counters["dropped_polls"] < 2
                    if drop:
                        counters["dropped_polls"] += 1
                if drop:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
            return super().do_GET()

        def do_POST(self):  # noqa: N802 - standard HTTP handler callback
            if urlparse(self.path).path == "/api/runs":
                with lock:
                    counters["run_posts"] += 1
            return super().do_POST()

    return Handler


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-conversation-acceptance-") as root:
        studio, project, artifact, review, counters, lock = create_fixture(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), acceptance_handler(studio, counters, lock))
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "project": project["id"],
                    "artifact": artifact["id"],
                    "run": review["id"],
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()
            tool_registry.unregister("acceptance:effect")


if __name__ == "__main__":
    main()

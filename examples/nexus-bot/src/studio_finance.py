"""Consume quote and fundamentals tools through a native workflow and Studio HTTP.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_finance.py
Uses a deterministic info adapter, temporary files and loopback HTTP only.
Real SDK/HTTP fixtures: tests/studio_finance_sdk_acceptance.py.
"""

import json
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler

INFO = {
    "shortName": "Example Company",
    "regularMarketPrice": 125.5,
    "previousClose": 120,
    "currency": "USD",
    "marketCap": 1000000,
    "trailingPE": 20,
    "dividendYield": 0,
    "profitMargins": 0.25,
}


def exercise(studio):
    tool = next(name for name in studio.research.names if name.endswith(":finance"))
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Company snapshot",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "quote",
                        "kind": "tool",
                        "tool_id": tool,
                        "kwargs": {"ticker": "EXAMPLE", "operation": "price"},
                    },
                    {
                        "id": "company",
                        "kind": "tool",
                        "tool_id": tool,
                        "kwargs": {"ticker": "EXAMPLE", "operation": "fundamentals"},
                    },
                ],
                "output_mapping": {
                    "quote": {"ref": "nodes.quote"},
                    "company": {"ref": "nodes.company"},
                },
            },
        },
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_studio_handler(studio, "finance-example-csrf")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None):
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Studio-Token": "finance-example-csrf",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)

    try:
        run = request(
            "/api/runs",
            {
                "kind": "workflow",
                "workflow_id": workflow["id"],
                "task": "Retrieve example company data; no trading",
            },
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = request("/api/runs/" + run["id"])
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert run["status"] == "completed", run
        output = json.loads(run["output"])
        assert output["quote"]["change_percent"] == 4.58
        assert output["company"]["company_name"] == "Example Company"
        assert output["company"]["dividend_yield"] == 0
        assert output["company"]["revenue"] is None
        assert request("/api/runs/" + run["id"])["output"] == run["output"]
        assert len(studio.records("runs")) == 1
        return output
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-finance-example-") as root:
        studio = StudioService(
            root,
            research_options={
                "config": {"finance": True},
                "finance_info_client": lambda _: dict(INFO),
            },
        )
        try:
            print(json.dumps(exercise(studio)))
        finally:
            studio.close()


if __name__ == "__main__":
    main()

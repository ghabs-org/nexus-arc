"""Run the real OpenAPI health client against its local Postman contract fixture.

From repository root: PYTHONPATH=. python tests/studio_openapi_health_http_acceptance.py
Requires Postman CLI; uses only synthetic credentials and loopback HTTP.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "examples/nexus-bot/src")
from studio_openapi_health import exercise

from nexus.core.studio import StudioService

os.environ["NEXUS_HEALTH_FIXTURE_TOKEN"] = "synthetic-health-key"
log = Path("/private/tmp/nexus-m5-openapi-health-simulation.ndjson")
err = Path("/private/tmp/nexus-m5-openapi-health-simulation.err")
with log.open("w") as output, err.open("w") as errors:
    process = subprocess.Popen(
        [
            shutil.which("postman") or "postman",
            "simulation",
            "run",
            "postman/simulations/studio-openapi-health.sim.yaml",
            "--port",
            "49645",
            "--output",
            "ndjson",
        ],
        stdout=output,
        stderr=errors,
        start_new_session=True,
    )
    try:
        for _ in range(150):
            if "listening" in log.read_text():
                break
            if process.poll() is not None:
                raise RuntimeError(err.read_text())
            time.sleep(0.1)
        else:
            raise RuntimeError("Simulation did not start")
        for path in ["/health", "/failure", "/denied", "/redirect", "/oversized"]:
            with tempfile.TemporaryDirectory(prefix="nexus-health-simulation-") as directory:
                studio = StudioService(Path(directory))
                try:
                    result = exercise(
                        studio,
                        "http://127.0.0.1:49645/health-service",
                        path,
                        "passed" if path == "/health" else "failed",
                    )
                    assert "DO_NOT_PERSIST_HEALTH_DETAIL" not in json.dumps(
                        studio.records("resource-health")
                    )
                    print(path, result["status"], flush=True)
                finally:
                    studio.close()
    finally:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
requests = [e for e in events if e.get("event") == "request"]
print("Simulation request events:", len(requests))
assert len(requests) == 5, requests
assert not any("must-not-follow" in json.dumps(e) for e in requests)

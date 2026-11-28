"""Exercise the compiled plugin against the saved transport fault contract.

Postman is the default runner. --transport node explicitly runs the same fixture
without Postman when its local dataset engine is unavailable. The report names
the selected runner; a Postman failure never silently falls back or passes.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def read_events(log):
    """Ignore only an unfinished last line while the child is still writing."""
    lines = log.read_text().splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.strip() and line.endswith("\n")]


def wait_for_start(process, log, errors, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(event.get("event") == "listening" for event in read_events(log)):
            return
        code = process.poll()
        if code is not None:
            diagnostic = errors.read_text().strip() or log.read_text().strip()
            raise RuntimeError(f"Fault runner exited with status {code}: {diagnostic}")
        time.sleep(0.1)
    raise RuntimeError("Fault runner did not start within the deadline")


def stop_process(process):
    """Stop only our owned process group without masking a startup exception."""
    if process.poll() is not None:
        process.wait()
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
    raise RuntimeError("Owned fault runner did not stop")


def exercise(transport="postman"):
    if transport not in {"postman", "node"}:
        raise ValueError("Choose postman or node transport")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="nexus-openclaw-faults-") as directory:
        log = Path(directory) / "simulation.ndjson"
        errors = Path(directory) / "simulation.err"
        if transport == "postman":
            command = [
                "postman",
                "simulation",
                "run",
                "postman/simulations/studio-openclaw.sim.yaml",
                "--port",
                str(port),
                "--output",
                "ndjson",
                "--no-history",
            ]
            url = f"http://127.0.0.1:{port}/studio"
            environment = dict(os.environ)
        else:
            command = ["node", "postman/mocks/studio-openclaw/default.js"]
            url = f"http://127.0.0.1:{port}"
            environment = {**os.environ, "PORT": str(port), "NEXUS_OPENCLAW_FAULT_EVENTS": "1"}
        with log.open("w") as output, errors.open("w") as stderr:
            process = subprocess.Popen(
                command, env=environment, stdout=output, stderr=stderr, start_new_session=True
            )
            try:
                wait_for_start(process, log, errors)
                result = subprocess.run(
                    ["node", "tests/studio_openclaw_faults.mjs", url],
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                if result.returncode:
                    raise RuntimeError(f"Plugin transport checks failed: {result.stderr.strip()}")
                print(result.stdout.strip())
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    events = read_events(log)
                    if len([e for e in events if e.get("event") == "request"]) >= 8:
                        break
                    time.sleep(0.1)
            finally:
                stop_process(process)
        events = read_events(log)
        requests = [event for event in events if event.get("event") == "request"]
        assert len(requests) == 8, requests
        assert not any("must-not-follow" in json.dumps(event) for event in requests)
        report = {"transport": transport, "requests": len(requests), "automaticRetries": 0}
        print(json.dumps(report))
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("postman", "node"), default="postman")
    exercise(parser.parse_args().transport)

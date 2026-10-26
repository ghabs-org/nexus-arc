"""Independent newline JSON-RPC fixture with a double-forked resistant daemon."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen(
    [
        sys.executable,
        "-I",
        "-c",
        "import json,os,signal,sys,time; from pathlib import Path; "
        "pid=os.fork(); "
        "os._exit(0) if pid else None; "
        "os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(json.dumps({'daemon':os.getpid(),'daemon_group':os.getpgrp()})); "
        "time.sleep(120)",
        str(root / "daemon.json"),
    ],
    start_new_session=True,
)
deadline = time.monotonic() + 5
while not (root / "daemon.json").exists():
    if time.monotonic() > deadline:
        raise RuntimeError("Fixture daemon did not start")
    time.sleep(0.01)
details = json.loads((root / "daemon.json").read_text())
details.update(server=os.getpid(), group=os.getpgrp(), parent=os.getppid())
(root / "pids.json").write_text(json.dumps(details))
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "escaped-fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": "wait", "description": "Wait once", "inputSchema": {"type": "object"}}
            ]
        }
    elif method == "tools/call":
        with (root / "calls.log").open("a") as stream:
            stream.write("wait\n")
        (root / "called").touch()
        time.sleep(120)
        continue
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)

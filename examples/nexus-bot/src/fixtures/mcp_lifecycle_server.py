"""Independent local MCP fixture with an active, SIGTERM-resistant child."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(120)",
    ]
)
(root / "pids.json").write_text(
    json.dumps({"server": os.getpid(), "child": child.pid, "group": os.getpgrp()})
)
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": m["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "cleanup-fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "wait",
                    "description": "Wait in an owned fixture",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif method == "tools/call":
        with (root / "calls.log").open("a") as log:
            log.write("wait\n")
        (root / "called").write_text("once")
        time.sleep(120)
        continue
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": result}), flush=True)

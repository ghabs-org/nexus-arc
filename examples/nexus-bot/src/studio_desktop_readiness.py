"""Check authenticated native startup against a real Studio sidecar process.

Build apps/studio-desktop/src-tauri/examples/backend_probe.rs first, then pass
its executable path with --probe. The probe runs the shell's readiness code.
All state and servers are disposable; no webview or live provider is used.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_desktop import DesktopLaunchCredential, WorkspaceInUseError, WorkspaceLock
from nexus.studio import make_studio_handler


def probe(executable, port, workspace, credential):
    result = subprocess.run(
        [str(Path(executable).resolve()), str(port), str(workspace)],
        input=credential.line(),
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0 and json.loads(result.stdout) == {"ready": True}


def status(url, cookie=None):
    request = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def exercise(executable):
    credential = DesktopLaunchCredential.generate()
    with tempfile.TemporaryDirectory(prefix="nexus-native-readiness-") as directory:
        workspace = (Path(directory) / "workspace").resolve()
        workspace.mkdir()
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        # Launch the real sidecar command exactly as the native shell does.
        sidecar = subprocess.Popen(
            [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(workspace),
             "--port", str(port), "--host", "127.0.0.1", "--desktop-launch-stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        decoy = StudioService(Path(directory) / "decoy")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(decoy, "d" * 43))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            sidecar.stdin.write(credential.line())
            sidecar.stdin.flush()
            deadline = time.monotonic() + 60
            while not probe(executable, port, workspace, credential):
                assert sidecar.poll() is None and time.monotonic() < deadline, "sidecar not ready"
            base = f"http://127.0.0.1:{port}/api"
            anonymous = status(base + "/bootstrap")
            session = status(
                base + "/bootstrap", f"{DesktopLaunchCredential.COOKIE}={credential.session()}"
            )
            result = {
                "launched_sidecar_verified": True,
                "wrong_workspace_rejected": not probe(executable, port, workspace / "x", credential),
                "other_launch_credential_rejected": not probe(
                    executable, port, workspace, DesktopLaunchCredential.generate()
                ),
                "unauthenticated_studio_rejected": not probe(
                    executable, server.server_port, decoy.root, credential
                ),
                "anonymous_api_denied": anonymous[0] == 401 and "token" not in anonymous[1],
                "session_api_allowed": session[0] == 200 and bool(session[1].get("token")),
            }
            # A terminal launch on the same workspace is refused while the sidecar owns it.
            second = subprocess.run(
                [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(workspace), "--port", "0"],
                capture_output=True,
                timeout=60,
                check=False,
            )
            result["second_launch_rejected"] = second.returncode == WorkspaceInUseError.exit_code
            # The shell's graceful stop: one line on the private pipe, then a clean exit.
            sidecar.stdin.write(b"stop\n")
            sidecar.stdin.flush()
            result["stop_closes_and_releases_workspace"] = (
                sidecar.wait(15) == 0 and not WorkspaceLock(workspace).is_locked()
            )
            return {**result, "native_webview_tested": False, "standalone_distribution_ready": False}
        finally:
            sidecar.terminate()
            sidecar.wait(10)
            server.shutdown()
            server.server_close()
            decoy.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True)
    print(json.dumps(exercise(parser.parse_args().probe), indent=2))

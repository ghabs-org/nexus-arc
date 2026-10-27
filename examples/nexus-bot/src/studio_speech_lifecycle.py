"""Exercise Linux speech cancellation/shutdown with controlled child processes.

No speech models, APIs or microphone are used. The synthetic executable tests
ordinary speech jobs and deliberately detaches a SIGTERM-resistant child.
Run on Linux with PYTHONPATH=. python examples/nexus-bot/src/studio_speech_lifecycle.py.
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_speech import local_process

CHILD = """
import os,signal,time
from pathlib import Path
os.setsid()
signal.signal(signal.SIGTERM,signal.SIG_IGN)
Path(os.environ['NEXUS_SPEECH_CHILD_READY']).write_text(str(os.getpid()))
time.sleep(60)
"""
WORKER = (
    "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
    + repr(CHILD)
    + "]);time.sleep(60)"
)


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def ready(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Controlled speech child did not become ready")
        time.sleep(0.02)
    return int(path.read_text())


def gone(pid):
    deadline = time.monotonic() + 3
    while alive(pid):
        if time.monotonic() >= deadline:
            raise AssertionError("Detached speech child survived its owner")
        time.sleep(0.02)


def service(directory):
    model = directory / "fixture.onnx"
    model.touch()
    script = directory / "piper"
    script.write_text("#!" + sys.executable + "\n" + WORKER + "\n")
    script.chmod(0o700)
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    return StudioService(
        directory / "workspace",
        speech_options={
            "profiles": {
                "controlled-piper": {"kind": "piper", "action": "speak", "model_path": str(model)}
            }
        },
    )


def owner(directory):
    os.environ["NEXUS_SPEECH_CHILD_READY"] = str(directory / "ready")
    studio = service(directory)
    studio.speech.queue({"profile": "controlled-piper", "text": "Cancellation fixture"}, None)
    ready(directory / "ready")
    time.sleep(60)


def exercise():
    assert sys.platform == "linux", "This consumer requires actual Linux process ownership"
    results = {}
    with tempfile.TemporaryDirectory(prefix="nexus-speech-lifetime-") as temporary:
        root = Path(temporary)
        for case in ("cancel", "shutdown"):
            directory = root / case
            directory.mkdir()
            os.environ["NEXUS_SPEECH_CHILD_READY"] = str(directory / "ready")
            studio = service(directory)
            pid = None
            try:
                run = studio.speech.queue({"profile": "controlled-piper", "text": "Fixture"}, None)
                pid = ready(directory / "ready")
                started = time.monotonic()
                if case == "cancel":
                    studio.cancel_run(run["id"])
                else:
                    studio.close()
                gone(pid)
                results[case] = {
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "detached_child_removed": True,
                }
            finally:
                studio.close()
                if pid and alive(pid):
                    os.kill(pid, signal.SIGKILL)
            assert not studio.catalog.list("artifacts")
            assert not list(studio.speech.directory.iterdir())
            assert studio.records("speech-input-owners") == []
        directory = root / "timeout"
        directory.mkdir()
        os.environ["NEXUS_SPEECH_CHILD_READY"] = str(directory / "ready")

        async def timeout():
            try:
                await local_process(
                    [sys.executable, "-c", WORKER],
                    timeout=0.5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            except subprocess.TimeoutExpired:
                return
            raise AssertionError("Speech timeout did not retain its failure")

        asyncio.run(timeout())
        pid = ready(directory / "ready")
        gone(pid)
        results["timeout"] = {"detached_child_removed": True, "failure_retained": True}

        async def finite_input():
            payload = b"Bounded input\n" * 10000
            command = [
                sys.executable,
                "-c",
                "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write('diagnostic');sys.exit(7)",
            ]
            result = await local_process(
                command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
            )
            assert (
                result.returncode == 7
                and result.stdout == payload
                and result.stderr == b"diagnostic"
            )
            try:
                await local_process(
                    command,
                    input=b"",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    check=True,
                )
            except subprocess.CalledProcessError as error:
                assert error.returncode == 7 and error.stderr == b"diagnostic"
            else:
                raise AssertionError("Speech command failure was lost")

        asyncio.run(finite_input())
        results["finite_input"] = {"bytes_and_errors_preserved": True}
        directory = root / "owner-death"
        directory.mkdir()
        controller = subprocess.Popen(
            [sys.executable, __file__, "--owner", str(directory)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = None
        try:
            pid = ready(directory / "ready")
            controller.kill()
            controller.wait(timeout=3)
            gone(pid)
            results["owner_death"] = {"detached_child_removed": True}
        finally:
            if controller.poll() is None:
                controller.kill()
                controller.wait(timeout=3)
            if pid and alive(pid):
                os.kill(pid, signal.SIGKILL)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=Path)
    args = parser.parse_args()
    if args.owner:
        owner(args.owner)
    else:
        print(json.dumps(exercise(), sort_keys=True))

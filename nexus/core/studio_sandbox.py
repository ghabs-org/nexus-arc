"""Pinned, networkless Docker execution with bounded input and artifact output."""

import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

WRAPPER = r"""
import base64,json,os,pathlib,resource,subprocess,sys
request=json.loads(sys.stdin.buffer.read(48*1024*1024+1))
root=pathlib.Path('/work'); inputs=root/'inputs';outputs=root/'outputs'
inputs.mkdir();outputs.mkdir()
for item in request['files']:
    name=item['filename']
    if pathlib.Path(name).name!=name or name in {'.','..'}:raise ValueError('Invalid input filename')
    (inputs/name).write_bytes(base64.b64decode(item['content_base64'],validate=True))
(root/'job.py').write_text(request['code'])
def limits():
    resource.setrlimit(resource.RLIMIT_CPU,(request['timeout'],request['timeout']))
    resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,16*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
with (root/'stdout').open('wb') as out,(root/'stderr').open('wb') as err:
    result=subprocess.run([sys.executable,'-I',str(root/'job.py')],cwd=root,
        env={'PATH':os.defpath,'HOME':'/work','INPUT_DIR':str(inputs),'OUTPUT_DIR':str(outputs)},
        stdout=out,stderr=err,timeout=request['timeout'],preexec_fn=limits)
if result.returncode:raise ValueError('Sandbox program failed')
if (root/'stdout').stat().st_size>1024*1024:raise ValueError('Program output exceeds one MiB')
files=[];total=0
for path in sorted(outputs.rglob('*')):
    if path.is_symlink():raise ValueError('Output links are not allowed')
    if not path.is_file():continue
    if path.parent!=outputs:raise ValueError('Output files must be directly inside OUTPUT_DIR')
    total+=path.stat().st_size
    if total>16*1024*1024 or len(files)>=8:raise ValueError('Output artifacts exceed limit')
    files.append({'filename':path.name,'content_base64':base64.b64encode(path.read_bytes()).decode()})
print(json.dumps({'stdout':(root/'stdout').read_text(errors='replace'),'files':files}))
"""


class StudioSandbox:
    def __init__(self, profiles=None, run=None):
        self.profiles = profiles or {}
        if not isinstance(self.profiles, dict):
            raise ValueError("Analysis profiles must be a named mapping")
        self.run_command = run or subprocess.run
        for profile in self.profiles.values():
            if not isinstance(profile, dict) or set(profile) - {
                "image",
                "python",
                "timeout",
                "model_path",
                "memory_mb",
            }:
                raise ValueError(
                    "Worker profiles support image, python, timeout, model_path and memory_mb"
                )
            image = profile.get("image", "")
            if not isinstance(image, str) or not re.fullmatch(
                r"(?:[a-zA-Z0-9][\w./:-]*@)?sha256:[a-f0-9]{64}", image
            ):
                raise ValueError(
                    "Analysis workers require an immutable image@sha256 digest or local sha256 image ID"
                )
            timeout = profile.get("timeout", 30)
            if type(timeout) is not int or not 1 <= timeout <= 120:
                raise ValueError("Sandbox timeout must be 1–120 seconds")
            memory = profile.get("memory_mb", 768)
            if type(memory) is not int or not 128 <= memory <= 4096:
                raise ValueError("Worker memory must be 128–4096 MiB")
            if not isinstance(profile.get("python", "python3"), str) or not re.fullmatch(
                r"/?[A-Za-z0-9_./-]+", profile.get("python", "python3")
            ):
                raise ValueError("Choose an installed Python executable inside the worker image")
            model = profile.get("model_path")
            if model and (not Path(model).is_file() or Path(model).is_symlink()):
                raise ValueError("Worker model must be an installed ordinary file")

    def arguments(self, profile_id, name):
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(
                "Configure a pinned analysis worker profile in private runtime settings"
            )
        command = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cpus",
            "1",
            "--memory",
            f"{profile.get('memory_mb', 768)}m",
            "--memory-swap",
            f"{profile.get('memory_mb', 768)}m",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--log-driver",
            "none",
            "--tmpfs",
            "/work:rw,nosuid,nodev,noexec,size=268435456,mode=1777",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=1777",
        ]
        if profile.get("model_path"):
            model = str(Path(profile["model_path"]).resolve())
            if "," in model:
                raise ValueError("Model path cannot contain commas")
            command += [
                "--mount",
                f"type=bind,source={model},destination=/models/model.pt,readonly",
            ]
        return [
            *command,
            "--entrypoint",
            profile.get("python", "python3"),
            profile["image"],
            "-I",
            "-c",
            WRAPPER,
        ]

    def execute(self, code, files=None, profile="python"):
        if not isinstance(code, str) or not 1 <= len(code) <= 32000:
            raise ValueError("Analysis code must be text up to 32000 characters")
        files = files or []
        if (
            not isinstance(files, list)
            or len(files) > 5
            or len(json.dumps(files)) > 44 * 1024 * 1024
        ):
            raise ValueError("Analysis inputs exceed file limits")
        if shutil.which("docker") is None:
            raise ValueError("Docker is required for analysis tools; host execution is disabled")
        name = "nexus-analysis-" + uuid.uuid4().hex
        command = self.arguments(profile, name)
        configured = self.profiles[profile]
        check = self.run_command(
            ["docker", "image", "inspect", configured["image"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if check.returncode:
            raise ValueError(
                "Configured worker image is not installed; automatic image pulling is disabled"
            )
        payload = json.dumps(
            {"code": code, "files": files, "timeout": configured.get("timeout", 30)}
        ).encode()
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        expired = threading.Event()

        def stop():
            expired.set()
            try:
                self.run_command(
                    ["docker", "rm", "--force", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            finally:
                if process.poll() is None:
                    process.kill()

        def write():
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        diagnostic = bytearray()

        def read_errors():
            total = 0
            while True:
                chunk = process.stderr.read(65536)
                if not chunk:
                    return
                total += len(chunk)
                diagnostic.extend(chunk)
                del diagnostic[:-4096]
                if total > 1024 * 1024:
                    stop()
                    return

        writer = threading.Thread(target=write, daemon=True)
        errors = threading.Thread(target=read_errors, daemon=True)
        timer = threading.Timer(configured.get("timeout", 30) + 20, stop)
        writer.start()
        errors.start()
        timer.start()
        try:
            output = bytearray()
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 24 * 1024 * 1024:
                    stop()
                    raise ValueError("Sandbox response exceeds output limits")
            process.wait(timeout=5)
            if expired.is_set():
                raise ValueError("Analysis worker timed out")
            if process.returncode:
                raise ValueError(
                    "Analysis worker failed; verify its dependencies and input format: "
                    + diagnostic.decode(errors="replace")[-800:]
                )
            result = json.loads(output)
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("files"), list)
                or not isinstance(result.get("stdout"), str)
            ):
                raise ValueError("Invalid analysis worker response")
            return result
        except json.JSONDecodeError as exc:
            raise ValueError("Analysis worker returned an invalid response") from exc
        finally:
            timer.cancel()
            if process.poll() is None:
                stop()
            writer.join(timeout=2)
            errors.join(timeout=2)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()
            process.stderr.close()

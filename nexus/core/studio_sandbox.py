"""Pinned, networkless Docker execution with bounded input and artifact output."""

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from nexus.core.studio_container_recovery import (
    CONTAINER_ID,
    LABEL,
    launch_environment,
    route_fingerprint,
)
from nexus.core.studio_docker_route import pin_route

WRAPPER = r"""
import base64,json,os,pathlib,resource,subprocess,sys,threading
# A delayed remote start must not wait forever for an owner that already exited.
input_deadline=threading.Timer(15,lambda:os._exit(124));input_deadline.daemon=True;input_deadline.start()
frame=sys.stdin.buffer.readline(48*1024*1024+2)
if len(frame)>48*1024*1024+1 or not frame.endswith(b'\n'):raise ValueError('Invalid worker input frame')
request=json.loads(frame)
input_deadline.cancel();input_deadline.join()
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
    resource.setrlimit(resource.RLIMIT_NOFILE,(request['open_files'],request['open_files']))
with (root/'stdout').open('wb') as out,(root/'stderr').open('wb') as err:
    result=subprocess.Popen([sys.executable,'-I',str(root/'job.py')],cwd=root,
        env={'PATH':os.defpath,'HOME':'/work','INPUT_DIR':str(inputs),'OUTPUT_DIR':str(outputs)},
        stdin=subprocess.DEVNULL,stdout=out,stderr=err,preexec_fn=limits)
    def owner_lost():
        # Raw fd reads avoid a buffered-stdin lock at interpreter shutdown.
        os.read(0,1)
        os._exit(125)
    # Fork/exec and resource setup happen before starting this monitor thread.
    threading.Thread(target=owner_lost,daemon=True).start()
    try:result.wait(timeout=request['timeout'])
    except BaseException:
        result.kill();result.wait();raise
if result.returncode:
    with (root/'stderr').open('rb') as error:
        error.seek(max(0,(root/'stderr').stat().st_size-2000))
        detail=error.read(2000).decode(errors='replace')
    raise ValueError('Sandbox program failed: '+detail)
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


class _OwnedContainer:
    def __init__(self, process, command, name, run, report, env=None, route=None):
        self.process, self.command, self.name = process, command, name
        self.run, self.report = run, report
        self.env = env
        self.route = route
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.started = False
        self.creation_uncertain = False
        self.identity = None
        self.confirmed = False

    def stop(self):
        with self.lock:
            if self.started:
                return
            self.started = True
            if self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass
            threading.Thread(target=self._cleanup, daemon=True).start()

    def _cleanup(self):
        confirmed = False
        try:
            if self.route and route_fingerprint(self.command, self.env, self.run) != self.route:
                return
            result = self.run(
                [*self.command, "rm", "--force", self.identity or self.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                env=self.env,
            )
            if result.returncode == 0:
                confirmed = True
            else:
                result = self.run(
                    [
                        *self.command,
                        "ps",
                        "--all",
                        "--filter",
                        "name=^/" + self.name + "$",
                        "--format",
                        "{{.ID}}",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    env=self.env,
                )
                confirmed = result.returncode == 0 and not result.stdout.strip()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            # Absence cannot settle a create request whose acknowledgment was lost.
            confirmed = confirmed and not self.creation_uncertain
            self.confirmed = confirmed
            self.report(self.name, confirmed)
            self.done.set()


class StudioSandbox:
    def __init__(self, profiles=None, run=None, recovery=None):
        self.profiles = profiles or {}
        if not isinstance(self.profiles, dict):
            raise ValueError("Analysis profiles must be a named mapping")
        self.run_command = run or subprocess.run
        self.recovery = recovery
        self._lock = threading.Lock()
        self._closed = False
        self._active = {}
        self.cleanup_errors = set()
        for profile in self.profiles.values():
            if not isinstance(profile, dict) or set(profile) - {
                "image",
                "python",
                "timeout",
                "model_path",
                "memory_mb",
                "docker_context",
                "pids_limit",
                "open_files",
            }:
                raise ValueError(
                    "Worker profiles support image, python, timeout, model_path, memory_mb, docker_context, pids_limit and open_files"
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
            pids = profile.get("pids_limit", 64)
            if type(pids) is not int or not 16 <= pids <= 512:
                raise ValueError("Worker process limit must be 16–512")
            descriptors = profile.get("open_files", 64)
            if type(descriptors) is not int or not 64 <= descriptors <= 1024:
                raise ValueError("Worker open file limit must be 64–1024")
            if not isinstance(profile.get("python", "python3"), str) or not re.fullmatch(
                r"/?[A-Za-z0-9_./-]+", profile.get("python", "python3")
            ):
                raise ValueError("Choose an installed Python executable inside the worker image")
            model = profile.get("model_path")
            context = profile.get("docker_context")
            if context is not None and (
                not isinstance(context, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", context)
            ):
                raise ValueError("Choose a valid installed Docker context name")
            if model and (not Path(model).is_file() or Path(model).is_symlink()):
                raise ValueError("Worker model must be an installed ordinary file")

    @staticmethod
    def docker_command(profile):
        context = profile.get("docker_context")
        return ["docker", "--context", context] if context else ["docker"]

    def arguments(self, profile_id, name):
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError(
                "Configure a pinned analysis worker profile in private runtime settings"
            )
        command = [
            *self.docker_command(profile),
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
            str(profile.get("pids_limit", 64)),
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
            selected = Path(profile["model_path"])
            if not selected.is_file() or selected.is_symlink():
                raise ValueError("Worker model must be an installed ordinary file")
            model = str(selected.resolve())
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
        with self._lock:
            if self._closed:
                raise RuntimeError("Analysis worker is shutting down")
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
        docker = command[:3] if command[1:2] == ["--context"] else command[:1]
        configured = self.profiles[profile]
        # Explicit default context must not inherit an ambient host/TLS override.
        docker_env = launch_environment(configured.get("docker_context"))
        selected_docker, selected_environment = docker, docker_env
        with pin_route(docker, docker_env, self.run_command) as route:
            command = [*route.command, *command[len(docker) :]]
            docker, docker_env = route.command, route.environment
            check = self.run_command(
                [*docker, "image", "inspect", configured["image"]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                env=docker_env,
            )
            if check.returncode:
                raise ValueError(
                    "Configured worker image is not installed; automatic image pulling is disabled"
                )
            payload = (
                json.dumps(
                    {
                        "code": code,
                        "files": files,
                        "timeout": configured.get("timeout", 30),
                        "open_files": configured.get("open_files", 64),
                    }
                ).encode()
                + b"\n"
            )
            lease = (
                self.recovery.register(
                    name,
                    selected_docker,
                    selected_environment,
                    self.run_command,
                    route_sha256=route.fingerprint,
                )
                if self.recovery
                else None
            )
            create_command = list(command)
            create_command[len(docker)] = "create"
            create_command[len(docker) + 1 : len(docker) + 1] = ["--label", LABEL + "=" + name]
            try:
                with self._lock:
                    if self._closed:
                        raise RuntimeError("Analysis worker is shutting down")
                    process = subprocess.Popen(
                        create_command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=docker_env,
                    )
                    worker = _OwnedContainer(
                        process,
                        docker,
                        name,
                        self.run_command,
                        self._report_cleanup,
                        env=docker_env,
                    )
                    worker.creation_uncertain = True
                    self._active[name] = worker
            except BaseException:
                if lease:
                    # No create process was started, so there cannot be a late create.
                    lease.record["creation_uncertain"] = False
                    lease.finish(True)
                raise
            expired = threading.Event()
            writer = errors = timer = None

            def stop():
                expired.set()
                worker.stop()

            def write():
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
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

            try:
                created, _ = process.communicate(timeout=15)
                if process.returncode:
                    raise ValueError(
                        "Analysis container creation was not acknowledged; cleanup remains pending"
                    )
                identity = created.decode().strip()
                if not CONTAINER_ID.fullmatch(identity):
                    raise ValueError("Docker returned an invalid analysis container identity")
                worker.identity = identity
                worker.creation_uncertain = False
                if lease:
                    lease.created(identity)
                with self._lock, worker.lock:
                    if self._closed or worker.started:
                        raise ValueError("Analysis worker stopped during Studio shutdown")
                    process = subprocess.Popen(
                        [*docker, "start", "--attach", "--interactive", identity],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=docker_env,
                    )
                    worker.process = process
                writer = threading.Thread(target=write, daemon=True)
                errors = threading.Thread(target=read_errors, daemon=True)
                timer = threading.Timer(configured.get("timeout", 30) + 20, stop)
                writer.start()
                errors.start()
                timer.start()
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
                # stderr can reach EOF after stdout; collect it before reporting failure.
                errors.join(timeout=2)
                if worker.cancelled.is_set():
                    raise ValueError("Analysis worker stopped during Studio shutdown")
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
            except subprocess.TimeoutExpired as exc:
                raise ValueError(
                    "Analysis worker communication timed out; cleanup remains pending"
                ) from exc
            finally:
                if timer:
                    timer.cancel()
                worker.stop()
                if not worker.done.wait(8):
                    self._report_cleanup(name, False)
                with self._lock:
                    self._active.pop(name, None)
                # Reap a killed create/start client before releasing its ownership lock.
                try:
                    process.wait(timeout=2)
                except subprocess.SubprocessError:
                    pass
                if writer:
                    writer.join(timeout=2)
                if errors:
                    errors.join(timeout=2)
                if process.stdin and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                process.stdout.close()
                process.stderr.close()
                if lease:
                    lease.finish(worker.done.is_set() and worker.confirmed)

    def recover_cleanup(self, *, timeout=8):
        """Retry only journaled container removal; never retry generated programs."""
        if self.recovery is None:
            return {"items": [], "pending": len(self.cleanup_errors)}
        result = self.recovery.recover(self.run_command, timeout=timeout)
        for item in result["items"]:
            if item["status"] == "removed":
                self._report_cleanup(item["name"], True)
        return result

    def _report_cleanup(self, name, confirmed):
        with self._lock:
            if confirmed:
                self.cleanup_errors.discard(name)
            else:
                self.cleanup_errors.add(name)

    def close(self, *, wait=True):
        with self._lock:
            self._closed = True
            workers = list(self._active.values())
        for worker in workers:
            worker.cancelled.set()
            worker.stop()
        if wait:
            deadline = time.monotonic() + 8
            for worker in workers:
                if not worker.done.wait(max(0, deadline - time.monotonic())):
                    self._report_cleanup(worker.name, False)
        with self._lock:
            return not self.cleanup_errors and all(worker.done.is_set() for worker in workers)

"""Two real local PostgreSQL clusters; no host ports, VM or existing database.

Requires explicit installed PostgreSQL/SDK image IDs and a Docker context. The
trust-authenticated test servers exist only on this private internal network.
Missing prerequisites fail the separate opt-in gate, never skip regressions.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from nexus.core.studio_container_recovery import launch_environment


def test_native_postgres_restore_crashes_and_database_restart(request):
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    postgres = os.environ["NEXUS_ACCEPTANCE_POSTGRES_IMAGE"]
    app = os.environ["NEXUS_ACCEPTANCE_POSTGRES_APP_IMAGE"]
    assert all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in [postgres, app])
    environment = launch_environment(context)
    scope = "nexus-pg-restore-" + uuid.uuid4().hex[:12]
    network = scope + "-network"
    containers = []
    volumes = {kind: scope + "-" + kind for kind in ["source", "target", "app"]}
    database_user = "nexus_restore_fixture"
    parent = Path(__file__).resolve().parents[1]
    example = (parent / "examples/nexus-bot/src/studio_recovery.py").read_text()
    consumer = (parent / "tests/studio_postgres_restore_consumer.py").read_text()

    def docker(*args, timeout=25, check=True):
        result = subprocess.run(
            ["docker", "--context", context, *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check:
            assert result.returncode == 0, "Disposable PostgreSQL fixture operation failed"
        return result

    def ready(name, database):
        deadline = time.monotonic() + 40
        while docker(
            "exec",
            name,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            database_user,
            "-d",
            database,
            check=False,
        ).returncode:
            running = docker(
                "container", "inspect", "--format", "{{.State.Running}}", name
            ).stdout.strip()
            if running != "true":
                logs = docker("logs", "--tail", "15", name, check=False)
                raise AssertionError("Synthetic PostgreSQL fixture exited: " + logs.stderr)
            assert time.monotonic() < deadline, "Disposable PostgreSQL did not become ready"
            time.sleep(0.1)

    def start_database(role):
        name = scope + "-" + role
        containers.append(name)
        mount = "type=volume,source=" + volumes[role] + ",target=/var/lib/postgresql/data"
        # The setup helper receives only this empty test volume and the two
        # capabilities needed to set its ownership/mode, never a host directory.
        docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            name + "-setup",
            "--label",
            "nexus.studio.validation=" + scope,
            "--network",
            "none",
            "--read-only",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            mount,
            "--entrypoint",
            "/bin/sh",
            postgres,
            "-c",
            "chmod 0700 /var/lib/postgresql/data && chown postgres:postgres /var/lib/postgresql/data",
        )
        database = "nexus_" + role
        docker(
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "nexus.studio.validation=" + scope,
            "--network",
            network,
            "--network-alias",
            role,
            "--read-only",
            "--user",
            "postgres",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--pids-limit",
            "256",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
            "--tmpfs",
            "/var/run/postgresql:rw,nosuid,nodev,size=16m,mode=1777",
            "--mount",
            mount,
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env",
            "POSTGRES_USER=" + database_user,
            "--env",
            "POSTGRES_DB=" + database,
            postgres,
            "postgres",
            "-c",
            "statement_timeout=15000",
            "-c",
            "lock_timeout=5000",
        )
        ready(name, database)
        return name

    try:
        declared = json.loads(
            docker("image", "inspect", "--format", "{{json .Config.Volumes}}", postgres).stdout
        )
        assert set(declared) == {"/var/lib/postgresql/data"}, (
            "Select the documented PostgreSQL 17 fixture image"
        )
        docker(
            "network",
            "create",
            "--internal",
            "--label",
            "nexus.studio.validation=" + scope,
            network,
        )
        for volume in volumes.values():
            docker("volume", "create", "--label", "nexus.studio.validation=" + scope, volume)
        source_server, target_server = start_database("source"), start_database("target")
        controller = scope + "-app"
        containers.append(controller)
        docker(
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            controller,
            "--label",
            "nexus.studio.validation=" + scope,
            "--network",
            network,
            "--read-only",
            "--user",
            "1001:1001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "1024m",
            "--pids-limit",
            "512",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--mount",
            "type=volume,source=" + volumes["app"] + ",target=/validation",
            "--env",
            "NEXUS_ACCEPTANCE_DISPOSABLE_POSTGRES=1",
            "--env",
            "NEXUS_ACCEPTANCE_PG_SOURCE_URL=postgresql://" + database_user + "@source/nexus_source",
            "--env",
            "NEXUS_ACCEPTANCE_PG_TARGET_URL=postgresql://" + database_user + "@target/nexus_target",
            app,
            "-c",
            "import time;time.sleep(300)",
        )
        for name in [source_server, target_server, controller]:
            info = json.loads(
                docker(
                    "container",
                    "inspect",
                    "--format",
                    '{"ports":{{json .HostConfig.PortBindings}},"mounts":{{json .Mounts}}}',
                    name,
                ).stdout
            )
            assert not info["ports"]
            assert all(m["Type"] == "volume" for m in info["mounts"])
        program = (
            'import json\nexample={"__name__":"recovery_example"}\nexec('
            + repr(example)
            + ',example)\nconsumer={"__name__":"native_consumer"}\nexec('
            + repr(consumer)
            + ',consumer)\nprint(json.dumps(consumer["exercise"](example),sort_keys=True))\n'
        )
        result = subprocess.run(
            ["docker", "--context", context, "exec", "-i", controller, "python", "-"],
            input=program,
            capture_output=True,
            text=True,
            env=environment,
            timeout=150,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["native_source_target_restore"] and report["provider_calls"] == 0
        docker("restart", "--time", "20", target_server, timeout=30)
        ready(target_server, "nexus_target")
        program = (
            'import json\nconsumer={"__name__":"native_consumer"}\nexec('
            + repr(consumer)
            + ',consumer)\nprint(json.dumps(consumer["verify_after_database_restart"]()))\n'
        )
        repeated = subprocess.run(
            ["docker", "--context", context, "exec", "-i", controller, "python", "-"],
            input=program,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )
        assert repeated.returncode == 0, repeated.stderr
        report["after_database_restart"] = json.loads(repeated.stdout)
        request.node.user_properties.append(("result", json.dumps(report, sort_keys=True)))
    finally:
        # No operation targets any pre-existing database, network or volume.
        scoped = docker(
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            "label=nexus.studio.validation=" + scope,
            check=False,
        )
        for identity in scoped.stdout.splitlines():
            if re.fullmatch(r"[a-f0-9]{64}", identity):
                docker("rm", "--force", "--volumes", identity, check=False)
        for name in containers:
            docker("rm", "--force", "--volumes", name, check=False)
        for volume in volumes.values():
            docker("volume", "rm", volume, check=False)
        docker("network", "rm", network, check=False)
        assert not docker(
            "ps", "--all", "--quiet", "--filter", "label=nexus.studio.validation=" + scope
        ).stdout.strip()
        for volume in volumes.values():
            absent = docker("volume", "inspect", volume, check=False)
            assert absent.returncode != 0 and "no such volume" in absent.stderr.lower()
        absent = docker("network", "inspect", network, check=False)
        assert absent.returncode != 0 and "not found" in absent.stderr.lower()
        request.node.user_properties.append(("scope_resources_removed", "true"))

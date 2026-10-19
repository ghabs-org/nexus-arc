"""Explicit local infrastructure checks; excluded from default test discovery.

Run: pytest -o addopts='' tests/studio_live_deployment_checks.py -q
Requires Docker, openssl, nexus-arc[postgres,mqtt]. Uses only freshly created,
loopback-published containers and temporary data. Missing prerequisites fail,
rather than silently skipping a deployment check. No real accounts are used.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid

import pytest

from nexus.adapters.transport import MqttTransport
from nexus.core.studio import StudioService


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, **kwargs)


def wait_for(predicate, message, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    pytest.fail(message)


@pytest.fixture(scope="module")
def infrastructure(tmp_path_factory):
    root = tmp_path_factory.mktemp("studio-infrastructure")
    identifier = uuid.uuid4().hex[:12]
    containers = []
    certs = root / "certs"
    certs.mkdir(mode=0o755)
    config = certs / "tls.conf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n"
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(certs / "private.key"),
            "-out",
            str(certs / "public.crt"),
        ],
        check=True,
        capture_output=True,
    )
    # These are throwaway test certificates under a private pytest parent.
    (certs / "private.key").chmod(0o644)
    mqtt_image = os.getenv("NEXUS_STUDIO_CHECK_MQTT_IMAGE", "eclipse-mosquitto:2")
    pg_image = os.getenv("NEXUS_STUDIO_CHECK_POSTGRES_IMAGE", "postgres:17-alpine")
    (certs / "mosquitto.conf").write_text(
        "listener 8883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\ncertfile /mosquitto/config/public.crt\nkeyfile /mosquitto/config/private.key\npersistence false\nlog_dest stdout\n"
    )
    docker(
        "run",
        "--rm",
        "--pull",
        "never",
        "--user",
        "0:0",
        "--entrypoint",
        "mosquitto_passwd",
        "-v",
        f"{certs}:/config",
        mqtt_image,
        "-c",
        "-b",
        "/config/passwords",
        "studio-check",
        "isolated-test-only",
    )
    (certs / "passwords").chmod(0o644)

    def start(name, image, port, *args):
        full_name = "nexus-studio-check-" + identifier + "-" + name
        # An auto-published Docker port changes on restart. Reserve a random
        # loopback port explicitly so reconnect targets the same endpoint.
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            host_port = reserve.getsockname()[1]
        # Keep even failed starts in this list so partial setup is cleaned up.
        containers.append(full_name)
        docker(
            "run",
            "-d",
            "--rm",
            "--pull",
            "never",
            "--name",
            full_name,
            "--label",
            "nexus.studio.validation=" + identifier,
            "-p",
            f"127.0.0.1:{host_port}:{port}",
            *args,
            image,
        )
        mapping = json.loads(docker("inspect", full_name).stdout)[0]["NetworkSettings"]["Ports"][
            f"{port}/tcp"
        ][0]
        return full_name, int(mapping["HostPort"])

    try:
        pg_env = root / "postgres.env"
        pg_env.write_text(
            "POSTGRES_USER=studio_check\nPOSTGRES_DB=studio_check\nPOSTGRES_PASSWORD=isolated-test-only\n"
        )
        pg_env.chmod(0o600)
        pg, pg_port = start(
            "postgres",
            pg_image,
            5432,
            "--env-file",
            str(pg_env),
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=128m",
        )
        mqtt, mqtt_port = start("mqtt", mqtt_image, 8883, "-v", f"{certs}:/mosquitto/config:ro")

        def ready():
            result = subprocess.run(
                ["docker", "exec", pg, "pg_isready", "-U", "studio_check"], capture_output=True
            )
            if result.returncode:
                return False
            try:
                with socket.create_connection(("127.0.0.1", mqtt_port), timeout=1):
                    return True
            except OSError:
                return False

        wait_for(ready, "Local PostgreSQL/MQTT containers did not become ready")
        yield {
            "postgres": pg,
            "url": f"postgresql+psycopg2://studio_check:isolated-test-only@127.0.0.1:{pg_port}/studio_check",
            "mqtt": mqtt,
            "mqtt_options": {
                "host": "localhost",
                "port": mqtt_port,
                "tls": True,
                "ca_certs": str(certs / "public.crt"),
                "username": "studio-check",
                "password": "isolated-test-only",
            },
        }
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def test_real_mqtt_tls_delivers_metadata_and_rejects_untrusted_certificate(infrastructure):
    import ssl

    options = infrastructure["mqtt_options"]
    received, ready = [], threading.Event()
    subscriber = MqttTransport(**options, client_id="subscriber-" + uuid.uuid4().hex)
    publisher = MqttTransport(**options, client_id="publisher-" + uuid.uuid4().hex)
    try:
        subscriber.subscribe(
            "studio/check", lambda topic, value: (received.append(value), ready.set())
        )
        time.sleep(0.3)
        publisher.publish("studio/check", {"type": "completed", "run_id": "test-run"})
        assert ready.wait(5), "Broker did not deliver the execution metadata"
        assert received == [{"type": "completed", "run_id": "test-run"}]
        with pytest.raises(ssl.SSLError):
            MqttTransport(**{**options, "ca_certs": None}, client_id="untrusted-test")
    finally:
        subscriber.close()
        publisher.close()


def test_mqtt_rejects_wrong_account_before_reporting_ready(infrastructure):
    transport = None
    try:
        with pytest.raises((ValueError, ConnectionError, OSError)):
            transport = MqttTransport(
                **{**infrastructure["mqtt_options"], "password": "incorrect"},
                client_id="bad-password-test",
            )
    finally:
        if transport:
            transport.close()


def test_mqtt_restores_subscriptions_after_broker_restart(infrastructure):
    options = infrastructure["mqtt_options"]
    subscriber = MqttTransport(**options, client_id="reconnect-" + uuid.uuid4().hex)
    received, event = [], threading.Event()
    try:
        subscriber.subscribe(
            "studio/reconnect", lambda topic, value: (received.append(value), event.set())
        )
        docker("restart", infrastructure["mqtt"])
        mapping = json.loads(docker("inspect", infrastructure["mqtt"]).stdout)[0][
            "NetworkSettings"
        ]["Ports"]["8883/tcp"][0]
        assert int(mapping["HostPort"]) == options["port"], (
            "Docker changed the published broker port during restart"
        )
        wait_for(
            subscriber._client.is_connected, "Subscriber did not reconnect after broker restart"
        )
        publisher = MqttTransport(**options, client_id="after-restart-" + uuid.uuid4().hex)
        try:
            time.sleep(0.5)
            publisher.publish("studio/reconnect", {"after_restart": True})
            assert event.wait(6), "Reconnected transport did not restore its subscription"
            assert received == [{"after_restart": True}]
        finally:
            publisher.close()
    finally:
        subscriber.close()


@pytest.mark.parametrize(
    "scenario",
    [
        "test_queued_job_survives_controller_shutdown_and_another_process_consumes",
        "test_claim_is_exclusive_and_expired_attempt_is_never_replayed",
        "test_sql_checkpoint_approval_resumes_in_a_new_service",
        "test_project_lock_serializes_revision_updates",
        "test_sql_yaml_gate_and_runtime_survive_restart",
    ],
)
def test_postgres_durable_contract(infrastructure, tmp_path, monkeypatch, scenario):
    from tests import test_studio_durable as contract

    monkeypatch.setenv("NEXUS_STUDIO_TEST_DATABASE_URL", infrastructure["url"])
    getattr(contract, scenario)(tmp_path)


def test_postgres_and_private_files_restore_to_an_isolated_workspace(infrastructure, tmp_path):
    project, private = tmp_path / "project", tmp_path / "private"
    options = {"url": infrastructure["url"], "namespace": uuid.uuid4().hex, "workers": 1}
    app = StudioService(project, state_dir=private, state_options=options)
    try:
        app.save_definition(
            "workflows",
            {
                "name": "Restore review",
                "graph": {
                    "nodes": [
                        {"id": "gate", "name": "Approve", "kind": "approval"},
                        {
                            "id": "count",
                            "name": "Count",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": "{task}"},
                        },
                    ],
                    "edges": [
                        {"source": "START", "target": "gate"},
                        {"source": "gate", "target": "count"},
                    ],
                },
            },
        )
        artifact = app.catalog.save(
            "artifacts",
            {
                "name": "Recovery evidence",
                "data": {
                    "filename": "evidence.txt",
                    "content_base64": base64.b64encode(b"Local restore evidence").decode(),
                },
            },
        )
        run = app.start_run(
            {"kind": "workflow", "workflow_id": "restore-review", "task": "Three restored words"}
        )
        wait_for(
            lambda: app.run(run["id"])["status"] == "waiting", "Approval workflow did not pause"
        )
    finally:
        app.close()
    backup = tmp_path / "backup"
    backup.mkdir()
    shutil.copytree(project, backup / "project")
    shutil.copytree(private, backup / "private")
    dump = docker(
        "exec",
        infrastructure["postgres"],
        "pg_dump",
        "-U",
        "studio_check",
        "-d",
        "studio_check",
        "-Fc",
    ).stdout
    docker("exec", infrastructure["postgres"], "createdb", "-U", "studio_check", "studio_restored")
    docker(
        "exec",
        "-i",
        infrastructure["postgres"],
        "pg_restore",
        "-U",
        "studio_check",
        "-d",
        "studio_restored",
        input=dump,
    )
    restored = StudioService(
        backup / "project",
        state_dir=backup / "private",
        state_options={**options, "url": options["url"].rsplit("/", 1)[0] + "/studio_restored"},
    )
    try:
        assert (
            restored.artifacts.get(restored.catalog.get("artifacts", artifact["id"]))
            == b"Local restore evidence"
        )
        item = restored.workbench.inbox.items(None)[0]
        restored.workbench.inbox.review(
            item["id"], {"digest": item["digest"], "decision": "approve", "confirm": True}, None
        )
        wait_for(
            lambda: restored.run(run["id"])["status"] == "completed",
            "Restored approval could not continue",
        )
        assert restored.run(run["id"])["output"].endswith("3")
    finally:
        restored.close()
    original = StudioService(project, state_dir=private, state_options={**options, "workers": 0})
    try:
        assert original.run(run["id"])["status"] == "waiting"
    finally:
        original.close()

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# The proxy is a standalone deploy script, not part of the nexus package.
_SPEC = importlib.util.spec_from_file_location(
    "studio_docker_proxy", Path(__file__).resolve().parents[1] / "deploy" / "studio" / "docker_proxy.py"
)
proxy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = proxy
_SPEC.loader.exec_module(proxy)

IMAGE = "nexus-studio-worker@sha256:" + "c" * 64
ANALYSIS_IMAGE = "sha256:" + "a" * 64
NAME = "nexus-studio-" + "0" * 32
ANALYSIS_NAME = "nexus-analysis-" + "0" * 32


@pytest.fixture
def env(tmp_path):
    creds = tmp_path / "auth" / "codex" / "user-1"
    creds.mkdir(parents=True)
    scratch_root = tmp_path / "tmp"
    prompt = scratch_root / "nexus-team-prompt-abc"
    attachments = scratch_root / "nexus-attachments-def"
    prompt.mkdir(parents=True)
    attachments.mkdir()
    model = tmp_path / "model.pt"
    model.write_bytes(b"weights")
    policy = proxy.Policy(
        team_images={IMAGE},
        analysis_images={ANALYSIS_IMAGE},
        model_paths={str(model)},
        worker_user="1001:1001",
        credential_root=str(tmp_path / "auth"),
        studio_tmp=str(scratch_root),
    )
    return {"policy": policy, "creds": str(creds), "prompt": str(prompt), "attachments": str(attachments), "model": str(model), "tmp": tmp_path}


def team_body(env):
    # Shape captured from Docker CLI 29.8.2 for the studio_team.py command line.
    return {
        "Hostname": "", "Domainname": "", "User": "1001:1001",
        "AttachStdin": True, "AttachStdout": True, "AttachStderr": True, "Tty": False,
        "OpenStdin": True, "StdinOnce": True,
        "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/nexus", "CODEX_HOME=/run/credentials/codex"],
        "Cmd": ["codex", "exec", "-"], "Image": IMAGE, "Volumes": {}, "WorkingDir": "/workspace",
        "Entrypoint": None, "Labels": {},
        "HostConfig": {
            "Binds": None, "ContainerIDFile": "", "LogConfig": {"Type": "", "Config": {}},
            "NetworkMode": "default", "PortBindings": {}, "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "AutoRemove": True, "VolumeDriver": "", "VolumesFrom": None, "ConsoleSize": [0, 0],
            "CapAdd": None, "CapDrop": ["ALL"], "CgroupnsMode": "", "Dns": [], "ExtraHosts": None,
            "GroupAdd": None, "IpcMode": "", "Privileged": False, "PublishAllPorts": False,
            "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"}, "UTSMode": "", "UsernsMode": "", "ShmSize": 0,
            "Isolation": "", "Memory": 2147483648, "NanoCpus": 2000000000, "Devices": [],
            "DeviceRequests": None, "MemorySwap": 0, "MemorySwappiness": -1, "OomKillDisable": None,
            "PidsLimit": 256, "Ulimits": [], "MaskedPaths": None, "ReadonlyPaths": None,
            "Mounts": [
                {"Type": "bind", "Source": env["creds"], "Target": "/run/credentials/codex"},
                {"Type": "bind", "Source": env["prompt"], "Target": "/workspace", "ReadOnly": True},
            ],
        },
        "NetworkingConfig": {"EndpointsConfig": {"default": {"IPAMConfig": None, "Links": None, "NetworkID": "", "GwPriority": 0}}},
    }


def analysis_body(env):
    body = team_body(env)
    body.update(User="65534:65534", Image=ANALYSIS_IMAGE, Entrypoint=["python3"], Cmd=["-I", "-c", "x"])
    body["HostConfig"].update(
        NetworkMode="none", LogConfig={"Type": "none", "Config": {}}, Memory=805306368, MemorySwap=805306368,
        NanoCpus=1000000000, PidsLimit=64,
        Mounts=[{"Type": "bind", "Source": env["model"], "Target": "/models/model.pt", "ReadOnly": True}],
    )
    body["NetworkingConfig"] = {"EndpointsConfig": {}}
    return body


def create(env, body, name=NAME):
    return proxy.check_create({"name": [name]}, body, env["policy"])


def test_studio_cli_worker_and_analysis_requests_are_allowed(env):
    assert create(env, team_body(env)) == (NAME, [env["creds"], env["prompt"]])
    body = team_body(env)
    body["HostConfig"]["Mounts"].append({"Type": "bind", "Source": env["attachments"], "Target": "/workspace/attachments", "ReadOnly": True})
    assert create(env, body)[1][-1] == env["attachments"]
    assert create(env, analysis_body(env), ANALYSIS_NAME) == (ANALYSIS_NAME, [env["model"]])


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda b, e: b["HostConfig"].update(Privileged=True), "Privileged"),
        (lambda b, e: b["HostConfig"].update(NetworkMode="host"), "network"),
        (lambda b, e: b["HostConfig"].update(PidMode="host"), "PidMode"),
        (lambda b, e: b["HostConfig"].update(CapAdd=["SYS_ADMIN"]), "CapAdd"),
        (lambda b, e: b["HostConfig"].update(GroupAdd=["988"]), "GroupAdd"),
        (lambda b, e: b["HostConfig"].update(Devices=[{"PathOnHost": "/dev/sda"}]), "Devices"),
        (lambda b, e: b["HostConfig"].update(Binds=["/:/host"]), "Binds"),
        (lambda b, e: b["HostConfig"].update(SecurityOpt=["no-new-privileges", "apparmor=unconfined"]), "SecurityOpt"),
        (lambda b, e: b["HostConfig"].update(ReadonlyRootfs=False), "read-only"),
        (lambda b, e: b["HostConfig"].update(AutoRemove=False), "AutoRemove"),
        (lambda b, e: b["HostConfig"].update(CapDrop=[]), "CapDrop"),
        (lambda b, e: b["HostConfig"].update(Memory=0), "memory"),
        (lambda b, e: b["HostConfig"].update(Runtime="runsc"), "Runtime"),
        (lambda b, e: b["HostConfig"].update(UsernsMode="host"), "UsernsMode"),
        (lambda b, e: b.update(User="0:0"), "user"),
        (lambda b, e: b.update(User=""), "user"),
        (lambda b, e: b.update(Image="alpine"), "image"),
        (lambda b, e: b.update(Volumes={"/data": {}}), "volumes"),
        (lambda b, e: b.update(ExposedPorts={"80/tcp": {}}), "ExposedPorts"),
        (lambda b, e: b["NetworkingConfig"]["EndpointsConfig"].update(host={}), "network attachment"),
        (lambda b, e: b["HostConfig"]["Mounts"].append({"Type": "bind", "Source": "/", "Target": "/home/nexus"}), "outside"),
        (lambda b, e: b["HostConfig"]["Mounts"].append({"Type": "bind", "Source": e["creds"], "Target": "/var/run/docker.sock"}), "not allowed"),
        (lambda b, e: b["HostConfig"]["Mounts"].append({"Type": "volume", "Source": "data", "Target": "/data"}), "bind"),
        (lambda b, e: b["HostConfig"]["Mounts"][1].update(ReadOnly=False), "workspace"),
        (lambda b, e: b["HostConfig"]["Mounts"][0].update(BindOptions={"Propagation": "rshared"}), "propagation"),
        (lambda b, e: b["HostConfig"]["Mounts"][0].update(BindOptions={"CreateMountpoint": True}), "CreateMountpoint"),
        (lambda b, e: b["HostConfig"]["Mounts"][0].update(Source=e["creds"] + "/../../../.."), "real path"),
    ],
)
def test_escapes_are_denied(env, mutate, reason):
    body = team_body(env)
    mutate(body, env)
    with pytest.raises(proxy.Denied, match=reason):
        create(env, body)


def test_symlinked_mount_sources_are_denied(env):
    link = Path(env["tmp"]) / "auth" / "codex" / "user-2"
    link.symlink_to("/")
    body = team_body(env)
    body["HostConfig"]["Mounts"][0]["Source"] = str(link)
    with pytest.raises(proxy.Denied, match="without symlinks"):
        create(env, body)


def test_names_query_and_analysis_rules(env):
    with pytest.raises(proxy.Denied, match="worker name"):
        create(env, team_body(env), "n8n")
    with pytest.raises(proxy.Denied, match="query"):
        proxy.check_create({"name": [NAME], "x": ["1"]}, team_body(env), env["policy"])
    # Analysis workers get no network and only the configured model file.
    body = analysis_body(env)
    body["HostConfig"]["NetworkMode"] = "default"
    with pytest.raises(proxy.Denied, match="network"):
        create(env, body, ANALYSIS_NAME)
    body = analysis_body(env)
    body["HostConfig"]["Mounts"][0]["Source"] = env["prompt"]
    with pytest.raises(proxy.Denied, match="analysis"):
        create(env, body, ANALYSIS_NAME)
    # A team image is not an analysis image.
    body = analysis_body(env)
    body["Image"] = IMAGE
    with pytest.raises(proxy.Denied, match="image"):
        create(env, body, ANALYSIS_NAME)


def request(method, target, body=b""):
    raw = f"{method} {target} HTTP/1.1\r\nHost: api.moby.localhost\r\n\r\n".encode()
    parts = proxy.urlsplit(target)
    return proxy.Request(method, target, proxy.API_PREFIX_RE.sub("", parts.path), proxy.parse_qs(parts.query), {}, raw, body)


def test_routing_only_allows_studio_calls_on_tracked_containers(env):
    tracker = proxy.Tracker()
    policy = env["policy"]
    assert proxy.decide(request("HEAD", "/_ping"), policy, tracker)[0] == "pass"
    assert proxy.decide(request("GET", f"/v1.56/images/{IMAGE}/json"), policy, tracker)[0] == "pass"
    body = json.dumps(team_body(env)).encode()
    assert proxy.decide(request("POST", f"/v1.56/containers/create?name={NAME}", body), policy, tracker)[0] == "create"
    for denied in [
        ("GET", "/v1.56/containers/json"),
        ("GET", "/v1.56/images/alpine/json"),
        ("POST", "/v1.56/containers/abc123def456/exec"),
        ("POST", "/v1.56/containers/abc123def456/start"),
        ("DELETE", "/v1.56/containers/n8n"),
        ("POST", "/v1.56/images/create?fromImage=alpine"),
        ("POST", "/v1.56/build"),
        ("GET", "/v1.56/info"),
    ]:
        with pytest.raises(proxy.Denied):
            proxy.decide(request(*denied), policy, tracker)

    container_id = "f" * 64
    tracker.add(container_id, NAME, [env["creds"]])
    assert proxy.decide(request("POST", f"/v1.56/containers/{container_id}/attach?stream=1&stdin=1"), policy, tracker)[0] == "upgrade"
    assert proxy.decide(request("POST", f"/v1.56/containers/{container_id}/wait?condition=removed"), policy, tracker)[0] == "pass"
    assert proxy.decide(request("POST", f"/v1.56/containers/{container_id[:12]}/start"), policy, tracker)[0] == "pass"
    assert proxy.decide(request("DELETE", f"/v1.56/containers/{NAME}?force=1"), policy, tracker) == ("delete", container_id)
    with pytest.raises(proxy.Denied):
        proxy.decide(request("POST", f"/v1.56/containers/{container_id}/exec"), policy, tracker)


def test_start_rechecks_mount_sources(env):
    tracker = proxy.Tracker()
    container_id = "e" * 64
    tracker.add(container_id, NAME, [env["creds"]])
    Path(env["creds"]).rename(env["creds"] + "-moved")
    Path(env["creds"]).symlink_to("/")
    with pytest.raises(proxy.Denied, match="changed since create"):
        proxy.decide(request("POST", f"/containers/{container_id}/start"), env["policy"], tracker)


def test_policy_reads_studio_configuration(tmp_path, monkeypatch):
    team = tmp_path / "team.json"
    runtime = tmp_path / "runtime.json"
    team.write_text(json.dumps({"runner_image": IMAGE}))
    runtime.write_text(json.dumps({"analysis_workers": {"python": {"image": ANALYSIS_IMAGE, "model_path": "/m.pt"}}}))
    monkeypatch.setattr(proxy, "TEAM_CONFIG", team)
    monkeypatch.setattr(proxy, "RUNTIME_CONFIG", runtime)
    policy = proxy.load_policy()
    assert policy.team_images == {IMAGE} and policy.analysis_images == {ANALYSIS_IMAGE} and policy.model_paths == {"/m.pt"}
    runtime.write_text("not json")
    assert proxy.load_policy().analysis_images == set()


def test_forwarded_head_forces_connection_close():
    raw = request("GET", "/version").head.replace(b"\r\n\r\n", b"\r\nConnection: keep-alive\r\n\r\n")
    req = proxy.Request("GET", "/version", "/version", {}, {}, raw, b"")
    head = proxy.rewrite_head(req, upgrade=False)
    assert b"keep-alive" not in head and head.endswith(b"Connection: close\r\n\r\n")
    assert proxy.rewrite_head(copy.copy(req), upgrade=True) == raw


def test_cleanup_listing_is_limited_to_one_tracked_analysis_container(env):
    from urllib.parse import urlencode

    tracker = proxy.Tracker()
    identity = "b" * 64
    tracker.add(identity, ANALYSIS_NAME, [])
    query = urlencode({"all": "1", "filters": json.dumps({"name": [f"^/{ANALYSIS_NAME}$"]})})
    action, scoped = proxy.decide(request("GET", "/v1.56/containers/json?" + query), env["policy"], tracker)
    assert action == "list"
    assert json.loads(scoped["filters"][0]) == {
        "name": [f"^/{ANALYSIS_NAME}$"], "id": [identity],
    }


def test_retired_ownership_remains_for_absence_checks_but_evicts_before_live_owners(monkeypatch):
    monkeypatch.setattr(proxy, "MAX_TRACKED", 2)
    tracker = proxy.Tracker()
    live, retired, newer = "1" * 64, "2" * 64, "3" * 64
    tracker.add(live, "nexus-analysis-" + "1" * 32, [])
    tracker.add(retired, "nexus-analysis-" + "2" * 32, [])
    tracker.retire(retired)
    assert tracker.resolve(retired) == retired
    tracker.add(newer, "nexus-analysis-" + "3" * 32, [])
    assert tracker.resolve(live) == live and tracker.resolve(newer) == newer
    assert tracker.resolve(retired) is None
    assert len(tracker._by_id) == 2


def test_name_or_short_id_ambiguity_never_selects_an_arbitrary_owner():
    tracker = proxy.Tracker()
    one, two = "b" * 12 + "1" * 52, "b" * 12 + "2" * 52
    tracker.add(one, ANALYSIS_NAME, [])
    tracker.add(two, ANALYSIS_NAME, [])
    assert tracker.resolve(ANALYSIS_NAME) is None
    assert tracker.resolve("b" * 12) is None
    assert tracker.resolve(one) == one and tracker.resolve(two) == two


def test_authorized_name_is_rewritten_to_full_id_before_forwarding(env):
    identity = "a" * 64
    tracker = proxy.Tracker()
    tracker.add(identity, ANALYSIS_NAME, [])
    req = request("POST", "/v1.56/containers/" + ANALYSIS_NAME + "/start")
    action, selected = proxy.decide(req, env["policy"], tracker)
    assert action == "pass" and selected == identity
    assert proxy.rewrite_head(req, False, proxy.container_target(req.target, selected)).startswith(
        ("POST /v1.56/containers/" + identity + "/start HTTP/1.1\r\n").encode()
    )


def test_standalone_mount_layout_needs_no_installed_nexus_or_ai_sdks(tmp_path):
    import shutil

    root = Path(__file__).resolve().parents[1]
    shutil.copy2(root / "deploy/studio/docker_proxy.py", tmp_path / "docker_proxy.py")
    shutil.copy2(root / "nexus/core/studio_docker_proxy_policy.py", tmp_path / "studio_docker_proxy_policy.py")
    result = subprocess.run([
        sys.executable, "-I", "-c",
        "import runpy,sys; sys.modules['nexus']=None; ns=runpy.run_path(sys.argv[1]); assert ns['Tracker']().resolve('unknown') is None; print('standalone-ok')",
        str(tmp_path / "docker_proxy.py"),
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "standalone-ok"

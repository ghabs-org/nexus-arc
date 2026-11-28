"""Opt-in real Docker workloads. Missing prerequisites fail, never silently skip.

NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:... NEXUS_ACCEPTANCE_DOCKER_CONTEXT=desktop-linux PYTHONPATH=. python -m pytest tests/studio_data_worker_acceptance.py
"""

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import DATA_PROGRAM
from nexus.core.studio_sandbox import StudioSandbox


@pytest.fixture
def worker():
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    return StudioSandbox(
        {
            "data": {
                "image": image,
                "docker_context": context,
                "timeout": 60,
                "pids_limit": 256,
                "open_files": 256,
            }
        }
    )


def test_six_operation_workflow_uses_real_worker_and_private_artifacts(tmp_path):
    path = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_data_workers.py"
    spec = importlib.util.spec_from_file_location("data_worker_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    service = StudioService(
        tmp_path,
        data_tool_options={
            "profiles": example.profiles(
                os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
                os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
            )
        },
    )
    try:
        assert example.exercise(service)["artifacts"] == 6
    finally:
        service.close()


@pytest.mark.parametrize(
    "code,error",
    [
        (
            "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])\n(p/'link').symlink_to('/etc/passwd')",
            "links",
        ),
        (
            "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])/'nested'\np.mkdir()\n(p/'file').write_text('x')",
            "directly",
        ),
        (
            "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])\nfor i in range(9):(p/str(i)).write_text('x')",
            "limit",
        ),
        ("raise ValueError('worker-diagnostic-marker')", "worker-diagnostic-marker"),
    ],
)
def test_real_worker_rejects_unsafe_or_excessive_outputs(worker, code, error):
    with pytest.raises(ValueError, match=error):
        worker.execute(code, profile="data")


def test_worker_context_overrides_ambient_host_and_keeps_isolation(worker, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    result = worker.execute(
        """import errno,os,pathlib,resource,socket
assert os.getuid()==65534
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(256,256)
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='256'
assert not pathlib.Path('/proc/net/route').read_text().splitlines()[1:]
with socket.socket() as probe:
    probe.settimeout(1)
    assert probe.connect_ex(('192.0.2.1',443))==errno.ENETUNREACH
try:pathlib.Path('/host-write-must-fail').write_text('x')
except OSError:pass
else:raise AssertionError('Root filesystem writable')
assert not list(pathlib.Path(os.environ['INPUT_DIR']).iterdir())
print('isolated')
""",
        profile="data",
    )
    assert result == {"stdout": "isolated\n", "files": []}


@pytest.mark.parametrize(
    "action,content,parameters,error",
    [
        ("query_sql", b"value\n1\n", {"query": "DELETE FROM data"}, "not authorized"),
        ("create_database", b"value\n" + b"1\n" * 100001, {}, "Dataset exceeds limits"),
    ],
)
def test_data_worker_enforces_read_only_sql_and_row_limit(
    worker, action, content, parameters, error
):
    code = DATA_PROGRAM.replace(
        "PARAMETERS", repr(json.dumps({"action": action, "filename": "data.csv", **parameters}))
    )
    with pytest.raises(ValueError, match=error):
        worker.execute(
            code,
            [{"filename": "data.csv", "content_base64": base64.b64encode(content).decode()}],
            profile="data",
        )


def test_json_to_sqlite_and_sql_result_values(worker):
    code = DATA_PROGRAM.replace(
        "PARAMETERS",
        repr(
            json.dumps(
                {
                    "action": "query_sql",
                    "filename": "data.json",
                    "query": "SELECT sum(value) FROM data",
                }
            )
        ),
    )
    result = worker.execute(
        code,
        [
            {
                "filename": "data.json",
                "content_base64": base64.b64encode(b'[{"value":3},{"value":4}]').decode(),
            }
        ],
        profile="data",
    )
    assert json.loads(result["stdout"])["rows"] == [[7]]

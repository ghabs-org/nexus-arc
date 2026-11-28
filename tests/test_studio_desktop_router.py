"""Desktop runtime.json and the nexus-router bundled inside the app."""

import json
import os
import sys
from pathlib import Path

import pytest

from nexus.core import studio_desktop_bundle as bundle
from nexus.core import studio_desktop_router as desktop_router

ROUTER = Path(__file__).resolve().parents[2] / "nexus-router"
needs_router = pytest.mark.skipif(
    not (ROUTER / "src" / "server.py").is_file(), reason="sibling nexus-router checkout not present"
)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    paths = {"config": tmp_path / "config", "data": tmp_path / "data"}
    monkeypatch.setattr(desktop_router, "get_desktop_dirs", lambda: paths)
    monkeypatch.delenv("NEXUS_STUDIO_ROUTER_SOURCE", raising=False)
    return paths


def write_runtime(dirs, data, mode=0o600):
    dirs["config"].mkdir(parents=True, exist_ok=True)
    path = dirs["config"] / "runtime.json"
    path.write_text(json.dumps(data))
    path.chmod(mode)
    return path


def test_no_runtime_file_and_no_bundled_router_changes_nothing(dirs, tmp_path):
    assert desktop_router.desktop_runtime_config() is None
    assert desktop_router.desktop_options(tmp_path / "workspace") == {"workbench_options": {}}


def test_runtime_file_router_is_used_and_bundled_router_not_started(dirs, tmp_path, monkeypatch):
    write_runtime(dirs, {"router": {"url": "http://127.0.0.1:7771", "allow_private": True}})
    monkeypatch.setattr(desktop_router, "bundled_router_source", lambda: pytest.fail("not needed"))
    options = desktop_router.desktop_options(tmp_path / "workspace")
    client = options["workbench_options"]["router_client"]
    assert client.url == "http://127.0.0.1:7771" and not client.config.get("bundled")


def test_unsafe_runtime_file_is_ignored_with_a_warning(dirs, tmp_path, capsys):
    write_runtime(dirs, {"router": {"url": "http://127.0.0.1:7771", "allow_private": True}}, 0o644)
    assert desktop_router.desktop_options(tmp_path / "workspace") == {"workbench_options": {}}
    assert "mode 0600" in capsys.readouterr().err


def test_bundled_router_failure_never_blocks_startup(dirs, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(desktop_router, "bundled_router_source", lambda: tmp_path)
    monkeypatch.setattr(desktop_router, "start_bundled_router", lambda *_: 1 / 0)  # noqa: ARG005
    assert desktop_router.desktop_options(tmp_path / "workspace") == {"workbench_options": {}}
    assert "bundled model router unavailable" in capsys.readouterr().err


@needs_router
def test_bundled_router_serves_auto_routing_on_loopback(dirs, tmp_path, monkeypatch):
    # Restore the router's environment knobs after the in-process start.
    for key in (
        "NEXUS_ROUTER_STATE_ROOT",
        "NEXUS_ROUTER_POLICIES_ROOT",
        "NEXUS_ROUTER_REGISTRY_FILE",
    ):
        monkeypatch.setenv(key, "unset")
    monkeypatch.delenv("JEV_BACKEND", raising=False)
    monkeypatch.setenv("JEV_BACKEND", "off")
    monkeypatch.setenv("NEXUS_STUDIO_ROUTER_SOURCE", str(ROUTER))
    monkeypatch.delitem(sys.modules, desktop_router.PACKAGE, raising=False)
    options = desktop_router.desktop_options(tmp_path / "workspace")
    client = options["workbench_options"]["router_client"]
    client.studio = type("S", (), {"team": None})()
    try:
        assert client.config["bundled"] is True and client.url.startswith("http://127.0.0.1:")
        assert os.environ["NEXUS_ROUTER_STATE_ROOT"] == str(dirs["data"] / "router")
        decision = client.route(
            None, "Fix the failing parser unit test", ["openai-codex", "anthropic"]
        )
        assert decision["selected_model"].split("/")[0] in {"openai-codex", "anthropic"}
        assert client.diagnostics()["policies"]["routing.yaml"] == "ok"
        # The per-launch token is required: an untokened local caller is refused.
        import requests

        assert requests.get(client.url + "/diagnostics", timeout=5).status_code == 401
        assert requests.get(client.url + "/health", timeout=5).status_code == 200
        assert "NEXUS_ROUTER_TOKEN" not in os.environ
        assert (dirs["data"] / "router" / "data" / "router.sqlite").is_file()
    finally:
        for name in [n for n in sys.modules if n.startswith(desktop_router.PACKAGE)]:
            del sys.modules[name]


def test_build_ships_router_only_from_a_complete_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_STUDIO_ROUTER_SOURCE", raising=False)
    repo = tmp_path / "nexus-arc"
    repo.mkdir()
    assert bundle.router_bundle_source(repo) == (None, False)
    sibling = tmp_path / "nexus-router"
    for part in ("src", "policies", "generated"):
        (sibling / part).mkdir(parents=True)
    (sibling / "generated" / "models.json").write_text("{}")
    assert bundle.router_bundle_source(repo) == (sibling.resolve(), False)
    assert bundle.router_bundle_source(repo, sibling) == (sibling.resolve(), True)
    with pytest.raises(ValueError, match="lacks"):
        bundle.router_bundle_source(repo, tmp_path / "missing")

"""Authoring UI generates loadable agents/workflows and rejects bad input."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer


def _start_server(tmp_path):
    from nexus.init_gui import make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _post(server, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_gui_generates_loadable_agent_and_workflow(tmp_path):
    from nexus.adapters.ai.registry import AgentRegistry
    from nexus.core.yaml_loader import YamlWorkflowLoader

    server = _start_server(tmp_path)
    try:
        status, agent = _post(
            server,
            "/api/agent",
            {
                "name": "Triage",
                "description": "Starter",
                "agent_type": "triage",
                "provider": "opencode",
                "timeout_seconds": 300,
                "max_retries": 2,
                "purpose": "Classify.",
            },
        )
        assert status == 200, agent
        assert (tmp_path / "agents" / "triage.yaml").exists()

        registry = AgentRegistry(agents_dir=tmp_path / "agents")
        assert registry.get_provider_name("triage") == "opencode"

        status, workflow = _post(
            server,
            "/api/workflow",
            {
                "name": "Hello",
                "description": "Starter",
                "steps": [{"id": "triage", "name": "Triage", "agent_type": "triage"}],
            },
        )
        assert status == 200, workflow
        loaded = YamlWorkflowLoader.load(str(tmp_path / "workflows" / "hello.yaml"))
        assert len(loaded.steps) == 1
    finally:
        server.shutdown()


def test_gui_rejects_bad_input(tmp_path):
    server = _start_server(tmp_path)
    try:
        status, body = _post(server, "/api/agent", {"name": "", "agent_type": "triage"})
        assert status == 400
        status, body = _post(
            server, "/api/agent", {"name": "X", "agent_type": "triage", "provider": "nope"}
        )
        assert status == 400 and "provider" in body["error"]
        status, body = _post(server, "/api/workflow", {"name": "W", "steps": []})
        assert status == 400
        status, body = _post(
            server,
            "/api/agent",
            {"name": "Dup", "agent_type": "triage", "provider": "opencode"},
        )
        assert status == 200
        status, body = _post(
            server,
            "/api/agent",
            {"name": "Dup", "agent_type": "triage", "provider": "opencode"},
        )
        assert status == 409
    finally:
        server.shutdown()


def test_gui_lists_existing_files(tmp_path):
    import urllib.request as _url

    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "triage.yaml").write_text("kind: Agent")
    server = _start_server(tmp_path)
    try:
        with _url.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/list", timeout=10
        ) as resp:
            body = json.loads(resp.read())
        assert body == {"agents": ["triage"], "workflows": []}
    finally:
        server.shutdown()


def test_gui_page_uses_minimalist_tokens(tmp_path):
    import urllib.request as _url

    server = _start_server(tmp_path)
    try:
        with _url.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=10) as resp:
            page = resp.read().decode()
        for token in ("#F7F6F3", "#EAEAEA", "#111111", "#787774", "/api/list"):
            assert token in page
    finally:
        server.shutdown()


def test_providers_endpoint_structure(tmp_path):
    import urllib.request as _url

    server = _start_server(tmp_path)
    try:
        with _url.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/providers", timeout=30
        ) as resp:
            body = json.loads(resp.read())
        from nexus.init_project import PROVIDERS

        assert [p["name"] for p in body["providers"]] == list(PROVIDERS)
        assert all("available" in p and "models" in p for p in body["providers"])
    finally:
        server.shutdown()


def test_render_model_profiles_valid_and_invalid():
    import pytest as _pytest

    from nexus.init_gui import render_model_profiles

    snippet = render_model_profiles(
        {"fast": {"provider": "opencode", "model": "opencode/spark-free"}}
    )
    assert "opencode/spark-free" in snippet
    assert "profile_provider_priority" in snippet
    with _pytest.raises(ValueError, match="unsupported provider"):
        render_model_profiles({"fast": {"provider": "nope", "model": "x"}})
    with _pytest.raises(ValueError, match="model is required"):
        render_model_profiles({"fast": {"provider": "copilot", "model": ""}})


def test_profiles_endpoint_roundtrip(tmp_path):
    server = _start_server(tmp_path)
    try:
        status, body = _post(
            server,
            "/api/profiles",
            {"profiles": {"reasoning": {"provider": "codex", "model": "gpt-5"}}},
        )
        assert status == 200, body
        assert "gpt-5" in body["yaml"]
        status, body = _post(server, "/api/profiles", {"profiles": {}})
        assert status == 400
    finally:
        server.shutdown()


def test_tools_endpoints(tmp_path):
    server = _start_server(tmp_path)
    try:
        import urllib.request as _url

        with _url.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/tools", timeout=10
        ) as resp:
            body = json.loads(resp.read())
        names = [t["name"] for t in body["tools"]]
        assert "text:wordcount" in names
        status, result = _post(
            server, "/api/tools/call", {"name": "text:wordcount", "args": ["a b"]}
        )
        assert status == 200 and result == {"ok": True, "output": 2, "error": None}
        status, result = _post(server, "/api/tools/call", {"name": "nope:x"})
        assert status == 200 and result["ok"] is False
        status, body = _post(server, "/api/tools/call", {"name": "text:wordcount", "args": "x"})
        assert status == 400
    finally:
        server.shutdown()


def test_agent_model_override_roundtrip(tmp_path):
    server = _start_server(tmp_path)
    try:
        status, body = _post(
            server,
            "/api/agent",
            {"name": "Writer", "agent_type": "writer", "provider": "opencode",
             "model": "opencode/spark-free"},
        )
        assert status == 200, body
        assert 'model: "opencode/spark-free"' in body["yaml"]
        from nexus.adapters.ai.registry import AgentRegistry

        assert AgentRegistry(agents_dir=tmp_path / "agents").get_model("writer") == (
            "opencode/spark-free"
        )
    finally:
        server.shutdown()


def _sample_bot_config(tmp_path):
    import yaml as _yaml

    config = {
        "model_profiles": {"fast": {"gemini": "gemini-2.0-flash"}},
        "profile_provider_priority": {"fast": ["gemini"]},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    path = tmp_path / "project_config.yaml"
    path.write_text(_yaml.safe_dump(config))
    return path


def _start_server_with_config(tmp_path, config_path):
    from nexus.init_gui import make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, config_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_profiles_apply_roundtrip(tmp_path):
    import yaml as _yaml

    from nexus.init_gui import make_handler

    config_path = _sample_bot_config(tmp_path)
    server = _start_server_with_config(tmp_path, config_path)
    try:
        status, body = _post(
            server,
            "/api/profiles/apply",
            {"profiles": {"fast": {"provider": "opencode", "model": "opencode/free"}}},
        )
        assert status == 200, body
        updated = _yaml.safe_load(config_path.read_text())
        assert updated["model_profiles"]["fast"] == {"opencode": "opencode/free"}
        assert updated["profile_provider_priority"]["fast"] == ["opencode"]
        assert "backup" in body
    finally:
        server.shutdown()


def test_profiles_apply_without_config_rejected(tmp_path):
    server = _start_server(tmp_path)
    try:
        status, body = _post(
            server,
            "/api/profiles/apply",
            {"profiles": {"fast": {"provider": "opencode", "model": "x"}}},
        )
        assert status == 409
    finally:
        server.shutdown()


def test_profiles_apply_validates_before_writing(tmp_path):
    from nexus.init_gui import make_handler

    config_path = _sample_bot_config(tmp_path)
    before = config_path.read_text()
    server = _start_server_with_config(tmp_path, config_path)
    try:
        status, body = _post(
            server,
            "/api/profiles/apply",
            {"profiles": {"fast": {"provider": "nope", "model": "x"}}},
        )
        assert status == 400
        assert config_path.read_text() == before
        assert list(tmp_path.glob("project_config.bak-*")) == []
    finally:
        server.shutdown()


def test_field_defs_drive_forms(tmp_path):
    import urllib.request as _url

    server = _start_server(tmp_path)
    try:
        with _url.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/field-defs", timeout=10
        ) as resp:
            forms = json.loads(resp.read())["forms"]
        assert [f["id"] for f in forms["agent"]] == [
            "a-name", "a-type", "a-desc", "a-provider", "a-model",
            "a-timeout", "a-retries", "a-purpose",
        ]
        assert "opencode" in next(
            f["options"] for f in forms["agent"] if f["id"] == "a-provider"
        )
        assert [f["id"] for f in forms["step"]] == ["s-id", "s-name", "s-type"]
    finally:
        server.shutdown()


def test_profiles_accept_custom_names(tmp_path):
    """GUI 'Add profile' sends arbitrary names; backend must not restrict to fast/reasoning."""
    server = _start_server(tmp_path)
    try:
        status, body = _post(
            server,
            "/api/profiles",
            {"profiles": {"nightly": {"provider": "opencode", "model": "opencode/spark-free"}}},
        )
        assert status == 200, body
        assert "nightly" in body["yaml"]
    finally:
        server.shutdown()

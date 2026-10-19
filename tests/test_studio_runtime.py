"""Runtime configuration and scoped external query contracts without live accounts."""

import json

import pytest

from nexus.core.studio_runtime import StudioLokiQuery, StudioModelRouter, load_runtime_configuration


def test_private_runtime_configuration_requires_explicit_database_reference(tmp_path):
    config = tmp_path / "runtime.json"
    config.write_text(
        json.dumps(
            {
                "state": {"url_env": "STUDIO_DATABASE_URL", "namespace": "team", "workers": 0},
                "memory_sources": {
                    "legacy": {
                        "kind": "redis",
                        "url_env": "LEGACY_REDIS_URL",
                        "identities": {"alice": "123"},
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    result = load_runtime_configuration(config, tmp_path / "project")
    assert result["state_options"]["workers"] == 0
    assert result["workbench_options"]["memory_sources"]["legacy"]["identities"] == {"alice": "123"}
    config.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_runtime_configuration(config, tmp_path / "project")


def test_loki_query_is_project_scoped_and_uses_only_server_token(monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setenv("AUDIT_TOKEN", "scoped-token")
    remote = Mock(
        return_value={
            "status": "success",
            "data": {
                "result": [
                    {
                        "values": [
                            ["1", json.dumps({"event_type": "studio.run.completed", "run_id": "r"})]
                        ]
                    }
                ]
            },
        }
    )
    monkeypatch.setattr("nexus.core.studio_runtime.request_json", remote)
    query = StudioLokiQuery(
        {"url": "https://logs.example.test", "token_env": "AUDIT_TOKEN"}, "project-one"
    )
    assert query.get_events(24)[0]["run_id"] == "r"
    kwargs = remote.call_args.kwargs
    assert kwargs["params"]["query"] == '{app="nexus-studio",namespace="project-one"}'
    assert kwargs["headers"] == {"Authorization": "Bearer scoped-token"}


def test_model_router_never_uses_host_token_for_team_user(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    monkeypatch.setenv("ROUTER_TOKEN", "host-secret")
    remote = Mock(return_value={"model": "selected-model"})
    monkeypatch.setattr("nexus.core.studio_runtime.request_json", remote)
    router = StudioModelRouter({"url": "https://router.example.test", "token_env": "ROUTER_TOKEN"})
    router.studio = SimpleNamespace(
        team=SimpleNamespace(
            assert_execution=lambda _: None, environment_resolver=lambda _: ({}, None)
        )
    )
    with pytest.raises(PermissionError, match="account"):
        router("alice", "task", "reviewer")
    remote.assert_not_called()
    router.studio.team.environment_resolver = lambda _: ({"ROUTER_TOKEN": "alice-token"}, None)
    assert router("alice", "task", "reviewer") == "selected-model"
    assert remote.call_args.kwargs["headers"]["Authorization"] == "Bearer alice-token"

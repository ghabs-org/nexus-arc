"""Catalog setup is passive, complete and independent of account secrets."""

import json
from importlib.metadata import PackageNotFoundError

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_catalog_setup import describe_setup
from nexus.core.studio_connections import validate_connection
from nexus.plugins import PluginKind, PluginRegistry, PluginSetup
from tests.test_studio_team import team as team


def test_plugin_catalog_never_constructs_instances_and_keeps_legacy_specs():
    def forbidden(*args):
        raise AssertionError("Browsing constructed a plugin")

    registry = PluginRegistry()
    registry.register_factory(PluginKind.AI_PROVIDER, "legacy", "1", forbidden)
    setup = PluginSetup(
        "Selected account",
        ("Credential reference",),
        ("Review installation",),
        {"token_env": "TEST_TOKEN"},
    )
    registry.register_factory(PluginKind.AI_PROVIDER, "configured", "2", forbidden, setup=setup)
    items = registry.catalog()
    assert items[0]["setup"]["billing"] == "Selected account"
    assert items[1]["setup"] is None
    items[0]["setup"]["configuration_template"]["token_env"] = "CHANGED"
    assert registry.catalog()[0]["setup"]["configuration_template"]["token_env"] == "TEST_TOKEN"
    assert "factory" not in json.dumps(items)


def test_catalog_covers_every_supported_entry_without_resolving_accounts_or_running_commands(
    tmp_path, monkeypatch
):
    studio = StudioService(tmp_path)
    try:

        def forbidden(*args, **kwargs):
            raise AssertionError("Catalog must remain passive")

        monkeypatch.setattr("subprocess.run", forbidden)
        monkeypatch.setattr("subprocess.Popen", forbidden)
        monkeypatch.setattr(studio.research, "request", forbidden)
        monkeypatch.setattr("os.getenv", forbidden)
        result = studio.recipes.list()
        rows = result["items"] + result["official_items"]
        assert len(rows) == 27 and len(result["excluded"]) == 4
        for row in rows:
            setup = row["setup_metadata"]
            assert setup["billing"] and setup["requirements"] and setup["installation"]
            assert setup["configuration_template"] and setup["source_revision"] in setup["source"]
            assert setup["source"].startswith("https://github.com/ghabs-org/nexus-arc/")
            assert len(setup["source_revision"]) == 64
            assert setup["source"].endswith("#sha256=" + setup["source_revision"])
            assert row["readiness"]["live_verified"] is False
            assert row["live_verified"] is False
        sql = next(r for r in rows if r["id"] == "community-sql-database")
        assert sql["setup_metadata"]["alias_of"] == "community-sql-database-tool"
        assert all(row["catalog_commit"] == "" for row in result["official_items"])
    finally:
        studio.close()


def test_missing_dependency_does_not_become_ready_from_configuration(monkeypatch):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("nexus.core.studio_catalog_setup.version", missing)
    row = {"id": "finance", "mode": "tool", "action": "finance", "configured": True}
    state = describe_setup(row)
    assert state["readiness"]["state"] == "dependencies-missing"
    assert state["readiness"]["checks"][0]["installed_version"] is None
    assert state["readiness"]["checks"][0]["installation_pin"] == "0.2.65"


def test_installed_package_does_not_claim_account_or_worker_validation(monkeypatch):
    monkeypatch.setattr("nexus.core.studio_catalog_setup.version", lambda _: "9.9.9")
    row = {"id": "finance", "mode": "tool", "action": "finance", "configured": True}
    result = describe_setup(row)["readiness"]
    assert result["state"] == "configuration-present" and not result["live_verified"]
    assert result["checks"][0]["installed_version"] == "9.9.9"


@pytest.mark.parametrize(
    "identity",
    [
        "tavily",
        "bright-data",
        "zapier",
        "community-bedrock-agent",
        "community-mongodb",
        "community-sql-database-tool",
        "community-slack",
        "community-nuclia-tool",
        "community-powerbi",
    ],
)
def test_reviewed_connector_templates_reuse_existing_validators(tmp_path, identity):
    studio = StudioService(tmp_path)
    try:
        row = next(
            r
            for r in studio.recipes.list()["items"] + studio.recipes.list()["official_items"]
            if r["id"] == identity
        )
        data = row["setup_metadata"]["configuration_template"]["data"]
        # Selection placeholders intentionally cannot activate a resource. Fill
        # only those inputs, then use the same validator as normal saved records.
        replacements = {
            "YOUR_REGION": "us-east-1",
            "YOUR_RESOURCE_ID": "resource",
            "YOUR_ALIAS_ID": "alias",
            "YOUR_DATABASE": "database",
            "YOUR_COLLECTION": "collection",
            "SELECT_DIALECT": "postgresql",
            "YOUR_CHANNEL_ID": "channel",
            "https://YOUR_NUCLIA_ORIGIN": "https://example.org",
            "YOUR_KNOWLEDGE_BOX": "kb",
            "YOUR_DATASET_UUID": "11111111-1111-4111-8111-111111111111",
            "YOUR_WORKSPACE_UUID": "22222222-2222-4222-8222-222222222222",
        }

        def fill(value):
            if isinstance(value, dict):
                return {k: fill(v) for k, v in value.items()}
            if isinstance(value, list):
                return [fill(v) for v in value]
            return replacements.get(value, value) if isinstance(value, str) else value

        assert validate_connection(fill(data))["enabled"] is False
        assert "credential value" not in json.dumps(data)
    finally:
        studio.close()


def test_catalog_http_uses_existing_auth_and_never_resolves_credentials(team):
    service, policy, _, request, _ = team

    def forbidden(*args):
        raise AssertionError("Listing must not resolve user credentials")

    policy.environment_resolver = forbidden
    assert request("/api/studio/integrations")[0] in {401, 403}
    for identity in ["alice", "bob", "viewer"]:
        status, listing = request("/api/studio/integrations", user=identity)
        assert status == 200
        assert len(listing["items"]) + len(listing["official_items"]) == 27
        assert all(row["setup_metadata"]["billing"] for row in listing["items"])
        assert not any(row["readiness"]["live_verified"] for row in listing["official_items"])

"""Scaffold output must load through the real framework loaders."""

from unittest.mock import patch

import yaml


def test_init_scaffold_files_load(tmp_path):
    from nexus.adapters.ai.opencode_provider import OpenCodeProvider
    from nexus.adapters.ai.registry import AgentRegistry
    from nexus.adapters.registry import AdapterRegistry
    from nexus.core.yaml_loader import YamlWorkflowLoader
    from nexus.init_project import scaffold_project

    written = scaffold_project(
        tmp_path,
        {
            "project_name": "demo",
            "git_platform": "github",
            "git_repo": "demo/repo",
            "storage": "file",
            "ai_provider": "opencode",
        },
    )
    assert {p.name for p in written} == {"nexus.yaml", "triage-agent.yaml", "hello.yaml"}

    from nexus.adapters.git.github import GitHubPlatform

    with patch.object(GitHubPlatform, "_check_gh_cli", return_value=None):
        adapters = AdapterRegistry().from_config(
            yaml.safe_load((tmp_path / "nexus.yaml").read_text())
        )
    assert len(adapters.ai_providers) == 1
    assert isinstance(adapters.ai_providers[0], OpenCodeProvider)
    assert adapters.git is not None

    registry = AgentRegistry(agents_dir=tmp_path / "agents")
    assert registry.get_provider_name("triage") == "opencode"
    assert isinstance(registry.resolve("triage", [OpenCodeProvider()]), OpenCodeProvider)

    workflow = YamlWorkflowLoader.load(str(tmp_path / "workflows" / "hello.yaml"))
    assert len(workflow.steps) == 1
    assert workflow.steps[0].name == "triage"


def test_init_refuses_overwrite(tmp_path):
    import pytest

    from nexus.init_project import scaffold_project

    answers = {
        "project_name": "demo",
        "git_platform": "github",
        "git_repo": "demo/repo",
        "storage": "file",
        "ai_provider": "opencode",
    }
    scaffold_project(tmp_path, answers)
    with pytest.raises(FileExistsError):
        scaffold_project(tmp_path, answers)


def test_init_rejects_unknown_provider(tmp_path):
    import pytest

    from nexus.init_project import scaffold_project

    with pytest.raises(ValueError, match="Unsupported AI provider"):
        scaffold_project(
            tmp_path,
            {
                "project_name": "demo",
                "git_platform": "github",
                "git_repo": "demo/repo",
                "storage": "file",
                "ai_provider": "nope",
            },
        )

"""Skill import parses links/commands and reads GitHub through an injected fake."""

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_skill_import import parse_source

SHA = "a" * 40


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/latent-spaces/brag", ("latent-spaces", "brag", None, None, None)),
        ("github.com/owner/repo.git", ("owner", "repo", None, None, None)),
        ("owner/repo", ("owner", "repo", None, None, None)),
        (
            "https://github.com/o/r/tree/main/skills/brag",
            ("o", "r", "main", "skills/brag", None),
        ),
        (
            'npx skills add https://github.com/Leonxlnx/taste-skill --skill "design-taste-frontend"',
            ("Leonxlnx", "taste-skill", None, None, "design-taste-frontend"),
        ),
        ("npx -y skills add o/r -s brag-slim", ("o", "r", None, None, "brag-slim")),
    ],
)
def test_sources_parse_without_running_anything(text, expected):
    spec = parse_source(text)
    assert (spec["owner"], spec["repo"], spec["ref"], spec["path"], spec["skill"]) == expected


@pytest.mark.parametrize(
    "text",
    [
        "https://gitlab.com/o/r",
        "https://github.com.evil.test/o/r",
        "npx skills add http://10.0.0.1/o/r",
        "rm -rf /",
        "https://github.com/o/r/tree/main/../../x",
        "npx skills add o/r --skill '$(id)'",
    ],
)
def test_unsupported_or_unsafe_sources_are_rejected(text):
    with pytest.raises(ValueError):
        parse_source(text)


class FakeGitHub:
    files = {
        "README.md": "# repo",
        "skills/brag/SKILL.md": "---\nname: brag\ndescription: Make a launch video\n---\nSteps",
        "skills/brag/references/style.md": "Use bold type",
        "skills/brag/scripts/render.py": "print('render')",
        "skills/brag/assets/beat.ogg": "binary",
        "skills/slim/SKILL.md": "---\nname: brag-slim\ndescription: Smaller\n---\nShort",
    }
    reads = []

    def commit(self, owner, repo, ref):
        return ref or "main", SHA

    def tree(self, owner, repo, sha):
        return [{"path": path, "type": "blob", "size": len(text)} for path, text in self.files.items()]

    def text(self, owner, repo, sha, path):
        self.reads.append(path)
        return self.files[path]


@pytest.fixture
def app(tmp_path):
    service = StudioService(tmp_path)
    service.skill_import.source_factory = FakeGitHub
    yield service
    service.close()


def test_several_skills_offer_choices_then_import_the_picked_one(app):
    result = app.skill_import.import_skill({"source": "https://github.com/latent-spaces/brag"})
    assert [choice["skill"] for choice in result["choices"]] == ["brag", "brag-slim"]
    assert app.catalog.list("skills") == []

    result = app.skill_import.import_skill(
        {"source": "https://github.com/latent-spaces/brag", "skill": "brag"}
    )
    skill = result["skill"]
    assert skill["id"] == "brag" and skill["description"] == "Make a launch video"
    content = skill["data"]["content"]
    assert content.startswith("---\nname: brag") and "## Bundled file: references/style.md" in content
    assert "print('render')" not in content and "- scripts/render.py" in content
    assert result["skipped_files"] == ["assets/beat.ogg", "scripts/render.py"]
    assert skill["data"]["source"] == {
        "type": "github",
        "repository": "latent-spaces/brag",
        "ref": "main",
        "commit": SHA,
        "path": "skills/brag/SKILL.md",
    }


def test_command_matches_frontmatter_name_and_reimport_updates(app):
    command = "npx skills add https://github.com/o/r --skill brag-slim"
    first = app.skill_import.import_skill({"source": command})
    assert first["skill"]["id"] == "brag-slim" and not first["updated"]
    again = app.skill_import.import_skill({"source": command})
    assert again["updated"] and again["skill"]["revision"] == first["skill"]["revision"] + 1


def test_missing_skill_name_and_bad_source_metadata_fail(app):
    with pytest.raises(ValueError, match="No skill named nope"):
        app.skill_import.import_skill({"source": "o/r", "skill": "nope"})
    with pytest.raises(ValueError, match="Skill source"):
        app.catalog.save(
            "skills",
            {"name": "x", "data": {"content": "y", "source": {"repository": 1}}},
        )

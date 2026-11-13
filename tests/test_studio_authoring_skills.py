"""Offline authoring bundles, real CLI consumers and bounded replacement behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from nexus.cli import build_parser
from nexus.core.studio_authoring_skills import (
    MARKER,
    SKILLS,
    assistance_command,
    check_skills,
    configuration_example,
    install_skills,
    skill_bundle,
)


def test_bundle_is_deterministic_and_uses_installed_commands(monkeypatch):
    monkeypatch.setenv("PRIVATE_PROVIDER_KEY", "must-never-enter-the-bundle")
    one = skill_bundle()
    assert one == skill_bundle()
    assert set(one) == set(SKILLS)
    for name, files in one.items():
        header = files["SKILL.md"].split("---", 2)[1]
        metadata = yaml.safe_load(header)
        assert metadata["name"] == name
        assert len(metadata["description"]) > 30
        assert "convert-interop" in files["references/commands.md"]
        assert "nexus ai-assistance skill check" in files["references/commands.md"]
        assert "must-never-enter-the-bundle" not in json.dumps(files)
        assert json.loads(files[MARKER])["format"] == "nexus-authoring-skills/v1"


def test_generated_configuration_passes_native_preflight(tmp_path):
    from nexus.core.studio import StudioService

    studio = StudioService(tmp_path / "workspace")
    try:
        document = configuration_example()
        result = studio.configuration.normalize(document)
        assert result
        assert not studio.definitions("agents")
        assert not studio.records("configuration-plans")
    finally:
        studio.close()


def test_export_and_verify_detect_tampering_missing_and_extra_files(tmp_path):
    root = tmp_path / "export"
    result = install_skills(root)
    assert result["skills"] == list(SKILLS)
    assert check_skills(root)["valid"]
    path = root / SKILLS[0] / "SKILL.md"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("modified")
    assert not check_skills(root)["valid"]
    install_skills(root, force=True)
    path.unlink()
    assert not check_skills(root)["valid"]
    install_skills(root, force=True)
    (root / SKILLS[1] / "custom.md").write_text("keep")
    assert not check_skills(root)["valid"]
    with pytest.raises(ValueError, match="extra files"):
        install_skills(root, force=True)
    assert (root / SKILLS[1] / "custom.md").read_text() == "keep"


def test_existing_copy_preflight_never_leaves_half_installation(tmp_path):
    root = tmp_path / "skills"
    (root / SKILLS[1]).mkdir(parents=True)
    (root / SKILLS[1] / "SKILL.md").write_text("keep")
    with pytest.raises(ValueError, match="already exist"):
        install_skills(root)
    assert not (root / SKILLS[0]).exists()
    assert (root / SKILLS[1] / "SKILL.md").read_text() == "keep"
    assert not (root / ".nexus-authoring.lock").exists()


def test_replacement_failure_rolls_back_both_copies(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    install_skills(root)
    original = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    rename = Path.rename
    failed = False

    def fail_second(source, target):
        nonlocal failed
        if source.name == SKILLS[1] and source.parent != root and not failed:
            failed = True
            raise OSError("synthetic disk failure")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError, match="disk failure"):
        install_skills(root, force=True)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == original
    assert not (root / ".nexus-authoring.lock").exists()


def test_failed_rollback_preserves_recoverable_originals(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    install_skills(root)
    rename = Path.rename

    def fail_install_and_restore(source, target):
        if source.name == SKILLS[1] and source.parent != root:
            raise OSError("replacement rejected")
        if source.name.endswith(".previous"):
            raise OSError("rollback rejected")
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", fail_install_and_restore)
    with pytest.raises(OSError, match="preserve and restore originals"):
        install_skills(root, force=True)
    staging = list(root.glob(".nexus-authoring-*"))
    assert len(staging) == 1
    assert (staging[0] / (SKILLS[1] + ".previous") / "SKILL.md").is_file()
    assert not (root / ".nexus-authoring.lock").exists()


@pytest.mark.parametrize("location", ["root", "package", "file"])
def test_symlinks_never_write_outside_destination(tmp_path, location):
    root, outside = tmp_path / "skills", tmp_path / "outside"
    outside.mkdir()
    if location == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif location == "package":
        root.mkdir()
        (root / SKILLS[1]).symlink_to(outside, target_is_directory=True)
    else:
        install_skills(root)
        path = root / SKILLS[1] / "SKILL.md"
        path.unlink()
        path.symlink_to(outside / "do-not-write")
    with pytest.raises(ValueError, match="symbolic|regular"):
        install_skills(root, force=True)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "target,scope,folder",
    [
        ("agents", "project", ".agents"),
        ("codex", "project", ".agents"),
        ("claude", "project", ".claude"),
        ("claude", "user", ".claude"),
    ],
)
def test_targets_and_scopes(tmp_path, monkeypatch, capsys, target, scope, folder):
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, "home", lambda: home)
    args = build_parser().parse_args(
        [
            "ai-assistance",
            "skill",
            "install",
            "--target",
            target,
            "--scope",
            scope,
        ]
    )
    assistance_command(args)
    base = home if scope == "user" else project
    assert check_skills(base / folder / "skills")["valid"]
    assert json.loads(capsys.readouterr().out)["path"] == str(base / folder / "skills")


@pytest.mark.parametrize("flag,value", [("--target", "agents"), ("--scope", "project")])
def test_explicit_destination_excludes_target_scope(tmp_path, flag, value):
    args = build_parser().parse_args(
        [
            "ai-assistance",
            "skill",
            "install",
            "--to",
            str(tmp_path / "no-write"),
            flag,
            value,
        ]
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        assistance_command(args)
    assert not (tmp_path / "no-write").exists()


def test_default_check_finds_both_targets_and_reports_stale_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    args = build_parser().parse_args(["ai-assistance", "skill", "check"])
    with pytest.raises(SystemExit) as empty:
        assistance_command(args)
    assert empty.value.code == 1
    assert not json.loads(capsys.readouterr().out)["valid"]
    for folder in (".agents", ".claude"):
        install_skills(tmp_path / folder / "skills")
    assistance_command(args)
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] and len(result["copies"]) == 2
    marker = tmp_path / ".claude/skills" / SKILLS[0] / MARKER
    marker.write_text('{"version":"old"}')
    with pytest.raises(SystemExit):
        assistance_command(args)
    assert not json.loads(capsys.readouterr().out)["valid"]


def test_active_installation_lock_fails_without_touching_existing_files(tmp_path):
    (tmp_path / ".nexus-authoring.lock").mkdir()
    with pytest.raises(ValueError, match="active"):
        install_skills(tmp_path)
    assert list(tmp_path.iterdir()) == [tmp_path / ".nexus-authoring.lock"]


def test_real_cli_export_check_refusal_and_forced_refresh(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(checkout)}
    base = [sys.executable, "-m", "nexus.cli", "ai-assistance", "skill"]

    def run(*args):
        return subprocess.run([*base, *args], cwd=tmp_path, env=env, capture_output=True, text=True)

    result = run("export", "--to", str(tmp_path / "bundle"))
    assert result.returncode == 0, result.stderr
    assert run("check", "--path", str(tmp_path / "bundle")).returncode == 0
    assert run("export", "--to", str(tmp_path / "bundle")).returncode == 2
    assert run("export", "--to", str(tmp_path / "bundle"), "--force").returncode == 0
    assert run("install", "--target", "claude").returncode == 0
    assert (tmp_path / ".claude/skills/nexus-studio/SKILL.md").is_file()

"""Offline, versioned authoring skills for external coding assistants.

Only framework guidance is exported. Workspace content, credentials and catalog
skills are not read. Installation stages both skills before replacing any copy.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

FORMAT = "nexus-authoring-skills/v1"
MARKER = ".nexus-authoring.json"
SKILLS = ("nexus-studio", "nexus-studio-config")


def add_parser(subparsers):
    parser = subparsers.add_parser("ai-assistance", help="Export and install AI authoring skills")
    groups = parser.add_subparsers(dest="assistance_group", required=True)
    skill = groups.add_parser("skill", help="Manage Nexus authoring skills")
    operations = skill.add_subparsers(dest="assistance_operation", required=True)
    for name in ("install", "export", "check"):
        operation = operations.add_parser(name, help=f"{name.title()} offline authoring skills")
        if name == "export":
            operation.add_argument("--to", required=True, help="Destination skill bundle directory")
        elif name == "check":
            operation.add_argument(
                "--path", help="Skill root to check; otherwise check known roots"
            )
        else:
            operation.add_argument("--target", choices=("agents", "codex", "claude"))
            operation.add_argument("--scope", choices=("project", "user"))
            operation.add_argument("--to", help="Explicit skill root; excludes target/scope")
        if name != "check":
            operation.add_argument("--force", action="store_true", help="Replace generated copies")


def _json(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def cli_reference(parser):
    """Render the actual parser tree without invoking commands or reading settings."""
    lines = ["# Nexus command reference", "", "Generated from this installation's CLI parser.", ""]

    def visit(current, command):
        lines.extend([f"## `{command}`", ""])
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            flags = ", ".join(action.option_strings) or action.dest
            detail = action.help or ""
            choices = f" Choices: {', '.join(map(str, action.choices))}." if action.choices else ""
            lines.append(f"- `{flags}`: {detail}{choices}")
        lines.append("")
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, command + " " + name)

    visit(parser, "nexus")
    return "\n".join(lines)


def configuration_example():
    """Minimal portable native release; checked against the real validator in tests."""
    return {
        "format": "nexus-studio-config/v1",
        "definitions": [
            {
                "collection": "agents",
                "id": "researcher",
                "document": {
                    "apiVersion": "nexus-arc/v1",
                    "kind": "Agent",
                    "metadata": {"name": "Researcher", "description": "Review supplied evidence"},
                    "spec": {
                        "agent_type": "researcher",
                        "provider": "codex",
                        "purpose": "Review explicitly supplied evidence and cite sources.",
                        "timeout_seconds": 600,
                        "max_retries": 0,
                    },
                },
            }
        ],
        "resources": [],
    }


def skill_bundle():
    from nexus import __version__
    from nexus.cli import build_parser
    from nexus.core.studio import _PROVIDERS
    from nexus.core.studio_catalog import KINDS
    from nexus.core.studio_workflow_policy import workflow_limits

    commands = cli_reference(build_parser())
    configuration = (
        "# Native Studio configuration\n\n"
        "Release format: `nexus-studio-config/v1`. Top-level keys are `format`, "
        "`definitions`, `resources`. Definitions contain `collection`, `id`, `document`. "
        "Definition collections are `agents` and `workflows`. Catalog resources contain "
        "`kind`, `id`, `name`, optional `description`, and `data`.\n\n"
        "Installed catalog collections: " + ", ".join(f"`{kind}`" for kind in KINDS) + ".\n\n"
        "Binary artifacts are transferred separately, never in configuration releases. "
        "Provider IDs: " + ", ".join(f"`{item[0]}`" for item in _PROVIDERS) + ".\n\n"
        "Default structured workflow limits:\n\n```json\n" + _json(workflow_limits()) + "```\n\n"
        "The example JSON is a native release. The server's "
        "configuration preflight is authoritative for fields, resource dependencies, "
        "registered tools, graph schemas and administrator limits.\n\n"
        "## Review and apply\n\n"
        "Use an explicit running Studio URL. `validate` checks the whole release; "
        "`plan` returns an ID and digest for review. `apply` accepts that saved plan, "
        "not a raw release. Never apply before the user reviews the concrete changes.\n\n"
        "```sh\n"
        "nexus config validate configuration.json --url http://127.0.0.1:5003\n"
        "nexus config plan configuration.json --url http://127.0.0.1:5003 -o reviewed-plan.json\n"
        "nexus config apply reviewed-plan.json --url http://127.0.0.1:5003\n"
        "```\n\n"
        "Imported connector, entrypoint, external-agent and schedule resources stay disabled "
        "until local setup and explicit enabling. Store secrets in server credentials or "
        "environment references. Do not inline them in exported definitions.\n\n"
        "## External configurations\n\n"
        "Use `nexus config convert-interop` for supported external agent/workflow "
        "documents with explicit bindings. A conversion's diagnostics must be resolved "
        "before review. Unsupported fields must never be silently dropped. Other external "
        "resource kinds may require native configuration; check conversion diagnostics.\n"
    )
    overview = (
        "# Studio operation\n\n"
        "Launch `nexus studio --dir PATH`; the default URL is http://127.0.0.1:5003. "
        "A browser on another machine cannot use its file picker to read VM paths. "
        "Studio Workspace files exposes only configured server roots; private Library "
        "artifacts can be attached explicitly to chat or knowledge projects.\n\n"
        "Agent YAML lives in `<workspace>/agents/`; workflow YAML in `<workspace>/workflows/`. "
        "Private state defaults to `<workspace>/.nexus/studio`, or the configured state directory. "
        "Knowledge projects are document collections, not host directories.\n\n"
        "Codex and Claude Code providers use their configured server-side CLI account. "
        "API providers have separate billing. Never silently switch providers or send "
        "documents to an external embedding service.\n\n"
        "Use `nexus chat --url URL` for terminal chat. Inspect an existing run with "
        "`--run-id`; inspection does not resubmit it. After a lost connection or uncertain "
        "external effect, inspect the saved run and approval state instead of retrying a mutation.\n\n"
        "This authoring package contains no workspace data or live credentials. It does "
        "not grant filesystem access, authenticate an assistant, install dependencies, "
        "activate resources or bypass Nexus permissions.\n"
    )
    bodies = {
        "nexus-studio": (
            "Use when asked to operate Nexus Studio, find server workspace files, or explain "
            "providers, projects and saved runs.",
            "Read [Studio operation](references/studio.md) for storage, provider and recovery "
            "behavior. Read [commands](references/commands.md) for exact installed CLI flags. "
            "For configuration changes, use the nexus-studio-config skill.\n",
            {"references/studio.md": overview, "references/commands.md": commands},
        ),
        "nexus-studio-config": (
            "Use when asked to author, validate, review, import or export Nexus Studio agents, "
            "workflows and configuration releases.",
            "Read [configuration](references/configuration.md) before writing a release. "
            "Start with [the native example](references/configuration.json). "
            "Read [commands](references/commands.md) for installed command syntax. "
            "Run complete preflight and review the saved plan before applying. "
            "Unsupported mappings require diagnostics, never guessed substitutions.\n",
            {
                "references/configuration.md": configuration,
                "references/configuration.json": _json(configuration_example()),
                "references/commands.md": commands,
            },
        ),
    }
    packages = {}
    for name, (description, body, references) in bodies.items():
        files = {
            "SKILL.md": f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
            **references,
        }
        files[MARKER] = _json(
            {
                "format": FORMAT,
                "version": __version__,
                "name": name,
                "files": {
                    key: hashlib.sha256(text.encode()).hexdigest() for key, text in files.items()
                },
            }
        )
        packages[name] = files
    return packages


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Skill destinations must not traverse symbolic links")
    return path


def _files(path):
    if not path.is_dir() or path.is_symlink():
        raise ValueError("Expected a regular skill directory")
    files = set()
    for item in path.rglob("*"):
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise ValueError("Skill copies must contain regular files and directories only")
        if item.is_file():
            files.add(item.relative_to(path).as_posix())
    return files


def install_skills(destination, *, force=False):
    """Install both generated packages with preflight and rollback on write failure."""
    destination = _safe_path(destination)
    packages = skill_bundle()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = destination / ".nexus-authoring.lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError(
            "Another authoring installation is active; inspect its lock before retrying"
        ) from None
    staged = None
    changed = []
    try:
        for name, files in packages.items():
            path = destination / name
            if path.exists() or path.is_symlink():
                existing = _files(path)
                if not force:
                    raise ValueError(
                        "Authoring skills already exist; use --force to replace generated copies"
                    )
                if existing - set(files):
                    raise ValueError(
                        "Skill contains extra files; preserve custom content before replacement"
                    )
        staged = Path(tempfile.mkdtemp(prefix=".nexus-authoring-", dir=destination))
        for name, files in packages.items():
            path = staged / name
            for relative, text in files.items():
                target = path / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open("x", encoding="utf-8", newline="\n") as stream:
                    os.chmod(target, 0o600)
                    stream.write(text)
        for name in packages:
            target, backup = destination / name, staged / (name + ".previous")
            if target.exists():
                target.rename(backup)
            changed.append((target, backup))
            (staged / name).rename(target)
    except BaseException:
        try:
            for target, backup in reversed(changed):
                if target.exists():
                    shutil.rmtree(target)
                if backup.exists():
                    backup.rename(target)
        except BaseException:
            # Keep recoverable originals if the filesystem also rejects rollback.
            recovery = staged
            staged = None
            raise OSError(
                f"Skill rollback failed; preserve and restore originals from {recovery}"
            ) from None
        raise
    finally:
        if staged is not None:
            shutil.rmtree(staged)
        lock.rmdir()
    return {"format": FORMAT, "path": str(destination), "skills": list(packages)}


def check_skills(destination):
    """Compare every generated file and marker with this installed framework."""
    destination = _safe_path(destination)
    results = []
    for name, files in skill_bundle().items():
        path = destination / name
        try:
            actual = _files(path)
            errors = sorted(actual ^ set(files))
            for relative, text in files.items():
                target = path / relative
                if relative in actual and (
                    target.stat().st_size != len(text.encode())
                    or target.read_bytes() != text.encode()
                ):
                    errors.append(relative)
            results.append(
                {"name": name, "valid": not errors, "mismatched_files": sorted(set(errors))}
            )
        except (ValueError, OSError):
            results.append({"name": name, "valid": False, "error": "Missing or unsafe skill copy"})
    return {
        "format": FORMAT,
        "path": str(destination),
        "valid": all(r["valid"] for r in results),
        "skills": results,
    }


def assistance_command(args):
    operation = args.assistance_operation
    if operation == "check":
        if args.path:
            paths = [Path(args.path)]
        else:
            candidates = [
                base / folder / "skills"
                for base in (Path.cwd(), Path.home())
                for folder in (".agents", ".claude")
            ]
            paths = list(
                dict.fromkeys(
                    path for path in candidates if any((path / name).exists() for name in SKILLS)
                )
            )
        results = [check_skills(path) for path in paths]
        valid = bool(results) and all(result["valid"] for result in results)
        print(_json({"valid": valid, "copies": results}), end="")
        if not valid:
            raise SystemExit(1)
        return
    if operation == "export":
        destination = Path(args.to)
    elif args.to:
        if args.target is not None or args.scope is not None:
            raise ValueError("--to cannot be combined with --target or --scope")
        destination = Path(args.to)
    else:
        base = Path.home() if args.scope == "user" else Path.cwd()
        destination = base / (".claude" if args.target == "claude" else ".agents") / "skills"
    print(_json(install_skills(destination, force=args.force)), end="")

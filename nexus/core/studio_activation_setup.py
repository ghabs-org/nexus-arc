"""Local prerequisites for the existing reviewed build activation path."""

import copy
import json
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from nexus.core.studio_configuration import digest
from nexus.core.studio_data_tools import ACTION_PROFILES

CONNECTOR_PACKAGES = {
    "mcp": ["mcp"],
    "sql": ["SQLAlchemy"],
    "mongodb": ["pymongo"],
    "bedrock": ["boto3"],
    "bedrock-kb": ["boto3"],
    "bedrock-flow": ["boto3"],
    "openapi": ["jsonschema", "referencing"],
    "slack": ["slack-sdk"],
    "neptune": ["boto3"],
    "opensearch": ["boto3"],
    "dynamodb": ["boto3"],
}
SQL_PACKAGES = {
    "postgresql": "psycopg2-binary",
    "mysql": "PyMySQL",
    "mariadb": "PyMySQL",
    "mssql": "pymssql",
    "oracle": "oracledb",
}
WORKER_REQUIREMENTS = {
    "python": ([], []),
    "query_sql": ([], []),
    "create_database": ([], []),
    "transform_jq": (["PyYAML"], [["jq"]]),
    "chart": (["plotly", "kaleido"], [["chromium", "chromium-browser", "google-chrome"]]),
    "imagemagick": ([], [["magick", "convert"]]),
    "detect_objects": (["torch", "torchvision", "ultralytics"], []),
}
PROBE = """
import json,os,pathlib,shutil
from importlib.metadata import version,PackageNotFoundError
requirements=json.loads(PARAMETERS)
missing=[]
for name in requirements['packages']:
    try:version(name)
    except PackageNotFoundError:missing.append(name)
for choices in requirements['executables']:
    if not any(shutil.which(name) for name in choices):missing.append('/'.join(choices))
if requirements['model'] and not (pathlib.Path('/models/model.pt').is_file() and os.access('/models/model.pt',os.R_OK)):missing.append('selected model')
print(json.dumps({'missing':missing}))
"""


def referenced_tools(kind, value):
    data = value.get("spec", {}) if kind == "agents" else value.get("data", {})
    tools = set(data.get("tool_ids", []) + data.get("tools", []))
    if kind == "workflows":

        def walk(graph):
            if not isinstance(graph, dict):
                return
            for node in graph.get("nodes", []):
                if node.get("tool_id"):
                    tools.add(node["tool_id"])
                walk(node.get("body"))
            for name in ("on_success", "on_error"):
                walk(graph.get(name))
            for handler in graph.get("interop_exit_handlers", []):
                walk(handler.get("body"))

        walk(value.get("graph"))
    return sorted(tools)


def worker_search(studio, kind, value):
    """Whether an unconfigured search tool is covered by a shared CLI worker.

    Agents are covered when their worker searches by itself. Toolsets and skills
    defer to the agents that use them; workflow tool nodes run without a model.
    """
    if kind == "agents":
        spec = value.get("spec", {})
        provider = spec.get("provider") or "codex"
        if spec.get("model_id"):
            try:
                provider = studio.catalog.get("models", spec["model_id"])["data"]["provider"]
            except (FileNotFoundError, ValueError, KeyError):
                return False
        return studio.research.native_search(provider)
    if kind == "workflows":
        return False
    return bool(studio.team) and not studio.research.search


def local_prerequisites(studio, key, value, *, probe_workers=False, worker_cache=None):
    """Read metadata; only explicit activation probes start restricted workers.

    No remote service, credential resolver or installer runs here. Provider/account
    access remains governed by the existing lifecycle and runtime authorization.
    """
    kind = key.split(":", 1)[0]
    data = value.get("data", {})
    packages = list(CONNECTOR_PACKAGES.get(data.get("type"), [])) if kind == "connectors" else []
    if kind == "connectors":
        connector = data.get("type")
        if connector == "slack" and connector in studio.connections.factories:
            packages = []
        enterprise_key = "bedrock" if str(connector).startswith("bedrock") else connector
        if (
            connector in {"bedrock", "bedrock-kb", "bedrock-flow", "mongodb"}
            and enterprise_key in studio.connections.enterprise.factories
        ):
            packages = []
    if kind == "entrypoints" and data.get("mcp_enabled"):
        packages.append("mcp")
    if kind == "connectors" and data.get("type") == "sql":
        packages.append(SQL_PACKAGES[data["dialect"]])
    tools = referenced_tools(kind, value)
    if any(
        name in studio.media.names and name.rsplit(":", 1)[-1].startswith("image_")
        for name in tools
    ):
        packages.append("Pillow")
    if any(name in studio.research.names and name.endswith(":finance") for name in tools):
        if studio.research.finance_client is None:
            packages.append("yfinance")
    checks, issues, snapshot = [], [], {"packages": {}, "workers": {}, "tools": {}}
    if (
        kind == "connectors"
        and data.get("type") == "mcp"
        and data.get("transport", "stdio") == "stdio"
        and data.get("command")
    ):
        executable = shutil.which(data["command"])
        snapshot["executables"] = {"mcp": executable}
        checks.append(
            {"name": "MCP server executable", "status": "installed" if executable else "missing"}
        )
        if not executable:
            issues.append("mcp_executable_missing")
    for name in sorted(set(packages)):
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = None
        snapshot["packages"][name] = installed
        checks.append({"name": name, "status": "installed" if installed else "missing"})
        if installed is None:
            issues.append("optional_dependency_missing:" + name)
    inventories = {
        row["id"]: row
        for service in (studio.research, studio.media, studio.rulesets, studio.dynamic_embeds)
        for row in service.inventory()["items"]
    }
    builtin = worker_search(studio, kind, value)
    if kind == "agents":
        # Search tools reach agents through toolsets too; only the agent knows its worker.
        for identity in value.get("spec", {}).get("toolset_ids", []):
            try:
                listed = studio.catalog.get("toolsets", identity)["data"].get("tools", [])
            except (FileNotFoundError, ValueError):
                continue
            tools = sorted(
                set(tools) | {name for name in listed if name in studio.research.names}
            )
    for name in tools:
        if name not in studio.data_tools.names:
            row = inventories.get(name)
            if row is not None:
                configured = bool(row["configured"])
                search = name in studio.research.names and name.rsplit(":", 1)[-1] in {
                    "web_search",
                    "research",
                }
                if not configured and search and builtin:
                    snapshot["tools"][name] = "worker"
                    checks.append(
                        {"name": name.rsplit(":", 1)[-1], "status": "worker built-in search"}
                    )
                    continue
                snapshot["tools"][name] = configured
                checks.append(
                    {
                        "name": name.rsplit(":", 1)[-1],
                        "status": "configured" if configured else "missing",
                    }
                )
                if not configured:
                    issues.append("tool_setup_required:" + name.rsplit(":", 1)[-1])
            continue
        action = name.rsplit(":", 1)[-1]
        profile = ACTION_PROFILES[action]
        sandbox = studio.data_tools.sandbox
        selected = copy.deepcopy(sandbox.profiles.get(profile))
        state = {"profile": selected}
        snapshot["workers"][profile] = state
        if not selected:
            issues.append("worker_profile_missing:" + profile)
            checks.append({"name": profile + " worker", "status": "missing"})
            continue
        executable = shutil.which("docker")
        state["docker"] = executable
        if not executable:
            issues.append("docker_required")
        if action == "chart" and (
            selected.get("pids_limit", 64) < 256 or selected.get("open_files", 64) < 256
        ):
            issues.append("worker_chart_limits_required")
        if action == "detect_objects":
            model = Path(selected.get("model_path") or "")
            try:
                if not model.is_file() or model.is_symlink():
                    raise OSError
                stat = model.stat()
                state["model_revision"] = [
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]
            except OSError:
                issues.append("worker_model_missing:vision")
        checks.append({"name": profile + " worker", "status": "configured"})
        if (
            not probe_workers
            or not executable
            or (action == "detect_objects" and "model_revision" not in state)
        ):
            continue
        cache = worker_cache if worker_cache is not None else {}
        cache_key = (action, digest(state))
        if cache_key not in cache:
            required_packages, required_executables = WORKER_REQUIREMENTS[action]
            code = PROBE.replace(
                "PARAMETERS",
                repr(
                    json.dumps(
                        {
                            "packages": required_packages,
                            "executables": required_executables,
                            "model": action == "detect_objects",
                        }
                    )
                ),
            )
            try:
                result = sandbox.execute(code, profile=profile)
                outcome = json.loads(result["stdout"])
                if (
                    result["files"]
                    or not isinstance(outcome, dict)
                    or not isinstance(outcome.get("missing"), list)
                    or any(not isinstance(item, str) for item in outcome["missing"])
                ):
                    raise ValueError("Invalid worker prerequisite response")
                cache[cache_key] = not outcome["missing"]
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                cache[cache_key] = False
        if not cache[cache_key]:
            issues.append("worker_prerequisites_missing:" + action)
        elif selected != sandbox.profiles.get(profile):
            issues.append("worker_configuration_changed:" + profile)
        else:
            checks.append({"name": action + " worker software", "status": "checked"})
    return {"issues": sorted(set(issues)), "checks": checks, "fingerprint": digest(snapshot)}

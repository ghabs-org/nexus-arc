"""Model-router picks for ARC workflow agents, and the outcome of each pick.

Opt-in: ``NEXUS_ROUTER_ROUTE_AGENTS=true`` plus ``NEXUS_ROUTER_URL`` (and
``NEXUS_ROUTER_TOKEN`` when the router requires one). Explicit tool choices and
agent model pins always win; any router problem falls back to today's
provider order. Exactly one outcome is reported per routed launch.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from nexus.core.router_client import ARC_TOOL_FOR_ROUTER, ROUTER_FOR_ARC_TOOL, RouterClient
from nexus.core.router_feedback import TASK_LABELS

logger = logging.getLogger(__name__)

ENABLE_ENV = "NEXUS_ROUTER_ROUTE_AGENTS"
COST_PROFILES = {"cheap", "balanced", "premium"}
DECISION_KEYS = ("router_decision_id", "routed_model", "routed_tool", "routed_at")


def enabled() -> bool:
    return os.getenv(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _client() -> RouterClient | None:
    # Launches must not stall on a slow router.
    return RouterClient.from_env(timeout=5) if enabled() else None


def step_hint(raw: Any) -> dict | None:
    """A workflow step's ``routing: {task_type, cost_profile}``; invalid values are ignored."""
    if not isinstance(raw, dict):
        return None
    hint = {}
    if raw.get("task_type") in TASK_LABELS:
        hint["task_type"] = raw["task_type"]
    elif raw.get("task_type") is not None:
        logger.warning(
            "Ignoring workflow routing.task_type %r (use one of %s)", raw["task_type"], TASK_LABELS
        )
    if raw.get("cost_profile") in COST_PROFILES:
        hint["cost_profile"] = raw["cost_profile"]
    elif raw.get("cost_profile") is not None:
        logger.warning("Ignoring workflow routing.cost_profile %r", raw["cost_profile"])
    return hint or None


def model_pinned(orchestrator, agent_type: str, project_name: str | None) -> bool:
    resolver = getattr(orchestrator, "agent_spec_model_resolver", None)
    if not callable(resolver):
        return False
    try:
        return bool(str(resolver(agent_type, project_name or "nexus") or "").strip())
    except Exception:
        return False


def profile_catalog(orchestrator, project_name: str | None, ready: list[str]) -> list[str]:
    """Router ids of the models configured in the project's model profiles for ready tools."""
    resolver = getattr(orchestrator, "_resolved_model_profiles", None)
    if not callable(resolver):
        return []
    try:
        profiles = resolver(project_name) or {}
    except Exception:
        return []
    catalog = []
    for profile in profiles.values() if isinstance(profiles, dict) else ():
        if not isinstance(profile, dict):
            continue
        for tool, model in profile.items():
            model_id = (
                f"{ROUTER_FOR_ARC_TOOL[tool]}/{model}"
                if tool in ready and isinstance(model, str) and model
                else None
            )
            if model_id and model_id not in catalog:
                catalog.append(model_id)
    return catalog[:200]


def route_agent(
    orchestrator,
    *,
    prompt: str,
    agent_type: str,
    project_name: str | None,
    issue_num: str | None,
    workflow_name: str | None,
    exclude_tools=None,
    hint: dict | None = None,
    dry_run: bool = False,
) -> dict | None:
    """{decision_id, tool, model, routed_model} for the first routed model a ready tool can run."""
    client = _client()
    if client is None:
        return None
    from nexus.plugins.builtin.ai_runtime_plugin import AIProvider

    excluded = {str(getattr(tool, "value", tool)).lower() for tool in exclude_tools or []}
    ready = []
    for tool in ARC_TOOL_FOR_ROUTER.values():
        try:
            if tool not in excluded and orchestrator.check_tool_available(AIProvider(tool)):
                ready.append(tool)
        except Exception:
            continue
    if not ready:
        return None
    fields: dict[str, Any] = {
        "allowed_providers": sorted(ROUTER_FOR_ARC_TOOL[tool] for tool in ready),
        "nexus_context": {
            "nexus_project": project_name,
            "nexus_issue_id": str(issue_num) if issue_num else None,
            "nexus_workflow_id": workflow_name,
            "nexus_step_id": agent_type,
        },
        "source_type": "arc-workflow",
        "source_tag": f"{project_name or 'nexus'}#{issue_num or '?'}:{agent_type}",
        "dry_run": dry_run or None,
    }
    catalog = profile_catalog(orchestrator, project_name, ready)
    if catalog:
        # Route among the models this project's profiles actually run (newer than any registry).
        fields["available_models"] = catalog
    if hint and hint.get("task_type"):
        fields["classifier"] = {"task_type": hint["task_type"], "confidence": 0.9}
    if hint and hint.get("cost_profile"):
        fields["cost_profile"] = hint["cost_profile"]
    try:
        decision = client.route(None, str(prompt)[:8000], **fields)
    except Exception as exc:  # advisory: the agent still launches with the usual order
        logger.info("Model router unavailable for %s on #%s: %s", agent_type, issue_num, exc)
        return None
    for model_id in [decision.get("selected_model"), *(decision.get("fallbacks") or [])]:
        if not isinstance(model_id, str) or "/" not in model_id:
            continue
        provider, model = model_id.split("/", 1)
        tool = ARC_TOOL_FOR_ROUTER.get(provider)
        if tool in ready:
            return {
                "decision_id": decision.get("decision_id"),
                "tool": tool,
                "model": model,
                "routed_model": model_id,
                "explain": decision.get("explain"),
            }
    return None


def record_pick(entry: dict, pick: dict | None, launched_tool: str) -> dict:
    """Write the pick into a launched-agents entry, dropping any previous step's decision."""
    for key in DECISION_KEYS:
        entry.pop(key, None)
    if pick and pick.get("decision_id") and pick["tool"] == launched_tool:
        entry.update(
            router_decision_id=pick["decision_id"],
            routed_model=pick["routed_model"],
            routed_tool=pick["tool"],
            routed_at=time.time(),
        )
    return entry


def report_outcome(entry: dict, success: bool, *, rate_limited: bool = False) -> bool:
    """Post the outcome of the entry's routed launch once; clears the decision from *entry*."""
    decision_id = entry.get("router_decision_id")
    if not decision_id:
        return False
    started = float(entry.get("routed_at") or entry.get("timestamp") or 0)
    tool = entry.get("routed_tool") or entry.get("tool")
    for key in DECISION_KEYS:
        entry.pop(key, None)
    client = _client()
    if client is None:
        return False
    try:
        client.outcome(
            None,
            decision_id,
            success=success,
            latency_ms=int((time.time() - started) * 1000) if started else None,
        )
        if rate_limited and ROUTER_FOR_ARC_TOOL.get(tool):
            client.observe(None, ROUTER_FOR_ARC_TOOL[tool], 429, "rate_limit", quota_hint="low")
    except Exception as exc:
        logger.info("Router outcome not recorded for %s: %s", decision_id, exc)
        return False
    return True

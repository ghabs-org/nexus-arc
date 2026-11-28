"""
Nexus ARC (Agentic Runtime Core) - Production-grade AI workflow orchestration framework.

Copyright (c) 2026 Nexus Team
Licensed under Apache 2.0
"""

__version__ = "0.1.1"

from nexus.adapters.git import GitHubPlatform

# Adapter exports
from nexus.adapters.storage import FileStorage
from nexus.core.agent_mesh import (
    AgentMesh,
    AgentMeshEnvelope,
    AgentMeshError,
    AgentMeshTopic,
    MeshTimeoutError,
    NoCapablePeerError,
    RoutingStrategy,
)
from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    build_completion_comment,
    generate_completion_instructions,
    scan_for_completions,
)
from nexus.core.guards import LaunchGuard
from nexus.core.models import (
    Agent,
    AgentResult,
    Task,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.orchestrator import AIOrchestrator
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine


def __getattr__(name):
    """Load optional plugin exports only when requested.

    Importing the local CLI/Studio must not bootstrap bot authentication and
    require PROJECT_CONFIG_PATH before command-line arguments can be parsed.
    """
    if name in {"PluginKind", "PluginNotFoundError", "PluginRegistrationError",
                "PluginRegistry", "PluginSpec"}:
        from importlib import import_module

        value = getattr(import_module("nexus.plugins"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # Version
    "__version__",
    # Agent Mesh
    "AgentMesh",
    "AgentMeshEnvelope",
    "AgentMeshTopic",
    "RoutingStrategy",
    "AgentMeshError",
    "NoCapablePeerError",
    "MeshTimeoutError",
    # Core
    "WorkflowEngine",
    "WorkflowDefinition",
    "AIOrchestrator",
    # Completion Protocol
    "CompletionSummary",
    "DetectedCompletion",
    "build_completion_comment",
    "generate_completion_instructions",
    "scan_for_completions",
    # Guards
    "LaunchGuard",
    # Models
    "Workflow",
    "WorkflowStep",
    "WorkflowState",
    "Agent",
    "Task",
    "AgentResult",
    # Adapters
    "FileStorage",
    "GitHubPlatform",
    # Plugins
    "PluginKind",
    "PluginSpec",
    "PluginRegistry",
    "PluginRegistrationError",
    "PluginNotFoundError",
]

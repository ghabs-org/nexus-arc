"""Passive setup metadata shared by plugins and application catalogs."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PluginSetup:
    """Describe setup without a factory, executable hook or credential resolver.

    Templates contain credential references only. Installation instructions are
    display data: consumers must never execute them during catalog discovery.
    """

    billing: str
    requirements: tuple[str, ...] = ()
    installation: tuple[str, ...] = ()
    configuration_template: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    source_revision: str = ""
    alias_of: str | None = None

    def describe(self):
        """Return an independent JSON-compatible metadata snapshot."""
        return asdict(self)

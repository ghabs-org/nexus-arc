"""Peer discovery substrate for multi-agent topologies.

Presence without a control plane: peers announce themselves via
:meth:`heartbeat` (whatever transport carries the cards), entries expire
via :meth:`prune`, and ``allow/deny`` glob filters shape the visible
topology. ``on_added``/``on_removed`` callbacks let coordinators react to
membership changes.

Not ported from event-mesh designs: the counted-retrigger barrier for
parallel LLM function calls — ARC agents are launched processes, not
in-turn function loops, so partial-failure resumption lives in the
workflow engine instead.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PeerCard:
    """Advertised identity and capabilities of one peer agent."""

    name: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class PeerRegistry:
    """TTL peer directory with glob topology filters."""

    def __init__(
        self,
        ttl_seconds: float = 90.0,
        allow_list: list[str] | None = None,
        deny_list: list[str] | None = None,
    ):
        self._ttl = max(float(ttl_seconds), 1.0)
        self._allow = list(allow_list or ["*"])
        self._deny = list(deny_list or [])
        self._peers: dict[str, PeerCard] = {}
        self._lock = threading.Lock()
        self._on_added: list[Callable[[PeerCard], None]] = []
        self._on_removed: list[Callable[[PeerCard], None]] = []

    def on_added(self, callback: Callable[[PeerCard], None]) -> None:
        """Run *callback* whenever a newly visible peer registers."""
        self._on_added.append(callback)

    def on_removed(self, callback: Callable[[PeerCard], None]) -> None:
        """Run *callback* whenever a peer expires or is dropped."""
        self._on_removed.append(callback)

    def _visible(self, name: str) -> bool:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in self._deny):
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self._allow)

    def heartbeat(
        self,
        name: str,
        description: str = "",
        skills: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record a peer announcement; False when filtered out by topology."""
        name = str(name or "").strip()
        if not name or not self._visible(name):
            return False
        card = PeerCard(
            name=name,
            description=str(description or ""),
            skills=list(skills or []),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            is_new = name not in self._peers
            self._peers[name] = card
        if is_new:
            for callback in self._on_added:
                callback(card)
        return True

    def get(self, name: str) -> PeerCard | None:
        """Return the live card for *name*, pruning it first if stale."""
        with self._lock:
            card = self._peers.get(name)
            if card is None:
                return None
            if time.time() - card.last_seen > self._ttl:
                del self._peers[name]
            else:
                return card
        for callback in self._on_removed:
            callback(card)
        return None

    def all(self) -> list[PeerCard]:
        """All unexpired, visible peers."""
        with self._lock:
            live = {
                name: card
                for name, card in self._peers.items()
                if time.time() - card.last_seen <= self._ttl
            }
            expired = [c for n, c in self._peers.items() if n not in live]
            self._peers = live
        for card in expired:
            for callback in self._on_removed:
                callback(card)
        return list(live.values())

    def prune(self) -> int:
        """Drop expired peers; returns the drop count."""
        before = len(self._peers)
        self.all()
        return before - len(self._peers)

    def describe_for_prompt(self, limit_skills: int = 8) -> str:
        """One-line-per-peer delegation hint for coordinator prompts."""
        lines = []
        for card in self.all():
            skills = ", ".join(card.skills[:limit_skills])
            suffix = f" (skills: {skills})" if skills else ""
            lines.append(f"- {card.name}: {card.description}{suffix}")
        return "\n".join(lines)

    def specs(self) -> list[dict[str, str]]:
        """Live peers as ``agents/run``-style agent specs (name+description).

        Lets a discovered team execute through the shared provider today;
        live heartbeat transport is the follow-up.
        """
        return [
            {"name": card.name, "description": card.description or card.name}
            for card in self.all()
        ]

"""Agent Mesh Architecture for Nexus Core.

Provides a vendor-neutral, broker-agnostic event mesh fabric for multi-agent
coordination. Implements decoupled asynchronous pub/sub choreography,
hierarchical topic namespaces, dynamic capability-based routing, and
request-reply correlation over event streams without proprietary broker lock-in.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from nexus.agents.peer_registry import PeerCard, PeerRegistry
from nexus.core.events import EventBus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentMeshError(Exception):
    """Base exception for all Agent Mesh errors."""


class NoCapablePeerError(AgentMeshError):
    """Raised when no active peer provides the requested capability/skill."""


class MeshTimeoutError(AgentMeshError, TimeoutError):
    """Raised when a request-reply interaction exceeds its timeout."""


# ---------------------------------------------------------------------------
# Routing Strategy
# ---------------------------------------------------------------------------


class RoutingStrategy(str, Enum):
    """Load-balancing and dispatch strategies for capability-based routing."""

    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    LEAST_LOADED = "least_loaded"


# ---------------------------------------------------------------------------
# Message Envelope
# ---------------------------------------------------------------------------


@dataclass
class AgentMeshEnvelope:
    """Standardized message envelope for all Agent Mesh traffic."""

    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    topic: str = ""
    sender: str = ""
    recipient: str | None = None
    correlation_id: str | None = None
    reply_to: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert envelope to serializable dictionary."""
        return {
            "message_id": self.message_id,
            "topic": self.topic,
            "sender": self.sender,
            "recipient": self.recipient,
            "correlation_id": self.correlation_id,
            "reply_to": self.reply_to,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMeshEnvelope:
        """Create envelope instance from dictionary."""
        return cls(
            message_id=str(data.get("message_id") or uuid.uuid4().hex),
            topic=str(data.get("topic") or ""),
            sender=str(data.get("sender") or ""),
            recipient=data.get("recipient"),
            correlation_id=data.get("correlation_id"),
            reply_to=data.get("reply_to"),
            payload=dict(data.get("payload") or {}),
            timestamp=float(data.get("timestamp") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# Topic Hierarchy Schema
# ---------------------------------------------------------------------------


class AgentMeshTopic:
    """Topic namespace convention and matching logic for the Agent Mesh."""

    PREFIX_AGENTS = "agents"
    PREFIX_CAPABILITIES = "capabilities"
    PREFIX_MESH = "mesh"

    @classmethod
    def agent_inbox(cls, agent_name: str) -> str:
        """Topic for point-to-point delivery to an agent's inbox."""
        return f"{cls.PREFIX_AGENTS}.{agent_name}.inbox"

    @classmethod
    def agent_replies(cls, agent_name: str, correlation_id: str) -> str:
        """Topic for dedicated reply streams correlated to an earlier request."""
        return f"{cls.PREFIX_AGENTS}.{agent_name}.replies.{correlation_id}"

    @classmethod
    def capability_tasks(cls, skill: str) -> str:
        """Topic for task delegation addressed to a specific capability/skill."""
        return f"{cls.PREFIX_CAPABILITIES}.{skill}.tasks"

    @classmethod
    def broadcast(cls, category: str = "*") -> str:
        """Topic for mesh-wide broadcasts across all listening agents."""
        return f"{cls.PREFIX_MESH}.broadcast.{category}"

    @classmethod
    def event(cls, domain: str, event_type: str) -> str:
        """Topic for domain execution events emitted across the mesh."""
        return f"{cls.PREFIX_MESH}.events.{domain}.{event_type}"

    @classmethod
    def extract_agent_name(cls, topic: str) -> str | None:
        """Extract agent name from an agent inbox topic if applicable."""
        parts = topic.split(".")
        if len(parts) == 3 and parts[0] == cls.PREFIX_AGENTS and parts[2] == "inbox":
            return parts[1]
        return None

    @staticmethod
    def matches(pattern: str, topic: str) -> bool:
        """Check if topic matches pattern with hierarchical wildcards.

        Supports:
        - Exact string equality (e.g., 'agents.alpha.inbox')
        - Single-level wildcard '*' (e.g., 'agents.*.inbox' matches any agent inbox)
        - Multi-level wildcard '>' or '**' at end (e.g., 'mesh.events.>' matches all subtopics)
        - In-token globbing (e.g., 'agent-*') via fnmatch
        """
        if pattern == topic or pattern in ("*", ">", "**"):
            return True

        pattern_parts = pattern.split(".")
        topic_parts = topic.split(".")

        # Multilevel wildcard at end
        if pattern_parts[-1] in (">", "**"):
            prefix = pattern_parts[:-1]
            if len(topic_parts) < len(prefix):
                return False
            for p_seg, t_seg in zip(prefix, topic_parts[: len(prefix)]):
                if p_seg != "*" and p_seg != t_seg and not fnmatch.fnmatchcase(t_seg, p_seg):
                    return False
            return True

        if len(pattern_parts) != len(topic_parts):
            return False

        for p_seg, t_seg in zip(pattern_parts, topic_parts):
            if p_seg == "*":
                continue
            if p_seg != t_seg and not fnmatch.fnmatchcase(t_seg, p_seg):
                return False

        return True


# ---------------------------------------------------------------------------
# Subscription Record
# ---------------------------------------------------------------------------


@dataclass
class _MeshSubscription:
    sub_id: str
    pattern: str
    handler: Callable[[AgentMeshEnvelope], Any]


@dataclass
class _PendingRequest:
    future: asyncio.Future[AgentMeshEnvelope]
    reply_to: str


# ---------------------------------------------------------------------------
# Agent Mesh Fabric
# ---------------------------------------------------------------------------


class AgentMesh:
    """Decoupled event-driven agent mesh choreography fabric.

    Bridges peer discovery, hierarchical topic pub/sub, dynamic capability
    routing, and request-reply correlation in a single, broker-free native
    Python architecture.
    """

    def __init__(
        self,
        peer_registry: PeerRegistry | None = None,
        event_bus: EventBus | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self.peer_registry = peer_registry or PeerRegistry()
        self.event_bus = event_bus or EventBus()
        self.default_timeout = max(0.1, float(default_timeout))

        self._subscriptions: dict[str, _MeshSubscription] = {}
        self._correlations: dict[str, _PendingRequest] = {}
        self._rr_indexes: dict[str, int] = {}
        self._peer_task_counts: dict[str, int] = {}
        self._lock = threading.RLock()

        # Connect peer registry lifecycle to mesh broadcasts
        self.peer_registry.on_added(self._on_peer_added)
        self.peer_registry.on_removed(self._on_peer_removed)

    def _on_peer_added(self, card: PeerCard) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.publish(
                    AgentMeshEnvelope(
                        topic=AgentMeshTopic.broadcast("peer_lifecycle"),
                        sender="mesh",
                        payload={
                            "event": "peer_registered",
                            "name": card.name,
                            "skills": list(card.skills),
                            "description": card.description,
                        },
                    )
                )
            )
        except RuntimeError:
            pass

    def _on_peer_removed(self, card: PeerCard) -> None:
        with self._lock:
            self._peer_task_counts.pop(card.name, None)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.publish(
                    AgentMeshEnvelope(
                        topic=AgentMeshTopic.broadcast("peer_lifecycle"),
                        sender="mesh",
                        payload={"event": "peer_removed", "name": card.name},
                    )
                )
            )
        except RuntimeError:
            pass

    # -----------------------------------------------------------------------
    # Peer Management
    # -----------------------------------------------------------------------

    def register_peer(
        self,
        name: str,
        description: str = "",
        skills: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Register or announce an agent's presence and capabilities to the mesh."""
        return self.peer_registry.heartbeat(
            name=name,
            description=description,
            skills=skills,
            metadata=metadata,
        )

    def heartbeat(
        self,
        name: str,
        description: str = "",
        skills: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Refresh an agent's heartbeat on the mesh."""
        return self.register_peer(name, description, skills, metadata)

    def unregister_peer(self, name: str) -> bool:
        """Explicitly unregister a peer from the mesh."""
        with self._lock:
            self._peer_task_counts.pop(name, None)
            card = self.peer_registry._peers.pop(name, None)
        if card:
            self._on_peer_removed(card)
            return True
        return False

    def get_active_peers(self, skill: str | None = None) -> list[PeerCard]:
        """Return all live peers, optionally filtered by advertised skill."""
        peers = self.peer_registry.all()
        if not skill:
            return peers
        return [p for p in peers if skill in p.skills]

    # -----------------------------------------------------------------------
    # Pub/Sub Choreography
    # -----------------------------------------------------------------------

    def subscribe(
        self,
        pattern: str,
        handler: Callable[[AgentMeshEnvelope], Any],
    ) -> str:
        """Subscribe a callable to a topic pattern with hierarchical wildcards."""
        sub_id = uuid.uuid4().hex
        sub = _MeshSubscription(sub_id=sub_id, pattern=pattern, handler=handler)
        with self._lock:
            self._subscriptions[sub_id] = sub
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove a subscription by ID."""
        with self._lock:
            return self._subscriptions.pop(sub_id, None) is not None

    async def publish(
        self,
        envelope: AgentMeshEnvelope | dict[str, Any],
        topic: str | None = None,
    ) -> int:
        """Publish an envelope to the mesh, dispatching to all matching subscriptions."""
        if isinstance(envelope, dict):
            env = AgentMeshEnvelope.from_dict(envelope)
        else:
            env = envelope

        if topic:
            env.topic = topic

        if not env.topic:
            raise ValueError("Cannot publish envelope without a topic")

        # Resolve any correlated request waiter
        if env.correlation_id:
            with self._lock:
                pending = self._correlations.get(env.correlation_id)
            if pending and env.topic == pending.reply_to and not pending.future.done():
                pending.future.set_result(env)

        # Match subscriptions
        with self._lock:
            matched = [
                sub
                for sub in self._subscriptions.values()
                if AgentMeshTopic.matches(sub.pattern, env.topic)
            ]

        if not matched:
            return 0

        tasks = []
        for sub in matched:
            try:
                res = sub.handler(env)
                if inspect.isawaitable(res):
                    tasks.append(res)
            except Exception:
                logger.exception("Error executing sync mesh handler for sub %s", sub.sub_id)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error("Error executing async mesh handler: %s", res)

        return len(matched)

    # -----------------------------------------------------------------------
    # Point-to-Point and Broadcast Helpers
    # -----------------------------------------------------------------------

    async def send(
        self,
        agent_name: str,
        payload: dict[str, Any],
        sender: str = "mesh",
        correlation_id: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMeshEnvelope:
        """Send a message directly to a target agent's inbox."""
        topic = AgentMeshTopic.agent_inbox(agent_name)
        env = AgentMeshEnvelope(
            topic=topic,
            sender=sender,
            recipient=agent_name,
            correlation_id=correlation_id,
            reply_to=reply_to,
            payload=payload,
            metadata=metadata or {},
        )
        await self.publish(env)
        return env

    async def broadcast(
        self,
        category: str,
        payload: dict[str, Any],
        sender: str = "mesh",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Broadcast an event payload across all agents on the mesh."""
        topic = AgentMeshTopic.broadcast(category)
        env = AgentMeshEnvelope(
            topic=topic,
            sender=sender,
            payload=payload,
            metadata=metadata or {},
        )
        return await self.publish(env)

    # -----------------------------------------------------------------------
    # Dynamic Capability-Based Routing
    # -----------------------------------------------------------------------

    async def route_capability(
        self,
        skill: str,
        payload: dict[str, Any],
        sender: str = "mesh",
        reply_to: str | None = None,
        strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, AgentMeshEnvelope]:
        """Dynamically route a task to an available peer advertising the required skill.

        Returns a tuple of (chosen_peer_name, dispatched_envelope).
        Raises NoCapablePeerError if no live peer provides the skill.
        """
        candidates = self.get_active_peers(skill=skill)
        if not candidates:
            raise NoCapablePeerError(f"No active peer available for skill '{skill}'")

        with self._lock:
            if strategy == RoutingStrategy.ROUND_ROBIN:
                idx = self._rr_indexes.get(skill, 0)
                peer = candidates[idx % len(candidates)]
                self._rr_indexes[skill] = idx + 1
            elif strategy == RoutingStrategy.RANDOM:
                peer = random.choice(candidates)
            elif strategy == RoutingStrategy.LEAST_LOADED:
                peer = min(
                    candidates,
                    key=lambda c: self._peer_task_counts.get(c.name, 0),
                )
            else:
                peer = candidates[0]

            self._peer_task_counts[peer.name] = (
                self._peer_task_counts.get(peer.name, 0) + 1
            )

        envelope = AgentMeshEnvelope(
            topic=AgentMeshTopic.agent_inbox(peer.name),
            sender=sender,
            recipient=peer.name,
            correlation_id=correlation_id,
            reply_to=reply_to,
            payload=payload,
            metadata=dict(metadata or {}, routed_skill=skill, strategy=strategy.value),
        )
        await self.publish(envelope)
        return peer.name, envelope

    # -----------------------------------------------------------------------
    # Request-Reply Correlation Pattern
    # -----------------------------------------------------------------------

    async def request(
        self,
        target_topic: str,
        payload: dict[str, Any],
        sender: str = "mesh",
        recipient: str | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMeshEnvelope:
        """Send a request over the mesh and await a correlated reply.

        Generates a unique correlation ID and dedicated reply topic, then blocks
        asynchronously until the responder replies or the timeout expires.
        """
        correlation_id = uuid.uuid4().hex
        reply_to = AgentMeshTopic.agent_replies(sender, correlation_id)
        effective_timeout = timeout if timeout is not None else self.default_timeout
        target_recipient = recipient or AgentMeshTopic.extract_agent_name(target_topic)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentMeshEnvelope] = loop.create_future()
        pending = _PendingRequest(future=future, reply_to=reply_to)

        with self._lock:
            self._correlations[correlation_id] = pending

        request_env = AgentMeshEnvelope(
            topic=target_topic,
            sender=sender,
            recipient=target_recipient,
            correlation_id=correlation_id,
            reply_to=reply_to,
            payload=payload,
            metadata=metadata or {},
        )

        try:
            await self.publish(request_env)
            reply_env = await asyncio.wait_for(future, timeout=effective_timeout)
            return reply_env
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise MeshTimeoutError(
                f"Request to '{target_topic}' timed out after {effective_timeout}s "
                f"(correlation_id={correlation_id})"
            ) from exc
        finally:
            with self._lock:
                self._correlations.pop(correlation_id, None)

    async def reply(
        self,
        request_envelope: AgentMeshEnvelope,
        payload: dict[str, Any],
        sender: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMeshEnvelope:
        """Reply to an earlier request using its return topic and correlation ID."""
        if not request_envelope.reply_to:
            raise ValueError("Cannot reply: request envelope has no reply_to topic")
        if not request_envelope.correlation_id:
            raise ValueError("Cannot reply: request envelope has no correlation_id")

        reply_sender = sender or request_envelope.recipient or "mesh"
        reply_env = AgentMeshEnvelope(
            topic=request_envelope.reply_to,
            sender=reply_sender,
            recipient=request_envelope.sender,
            correlation_id=request_envelope.correlation_id,
            payload=payload,
            metadata=metadata or {},
        )
        await self.publish(reply_env)
        return reply_env

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """Close the mesh and cancel any pending request correlations."""
        with self._lock:
            for pending in self._correlations.values():
                if not pending.future.done():
                    pending.future.cancel()
            self._correlations.clear()
            self._subscriptions.clear()
            self._peer_task_counts.clear()

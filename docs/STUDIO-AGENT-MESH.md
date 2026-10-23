# Native Agent Mesh Architecture

Nexus provides a native, broker-free **Agent Mesh** fabric ([agent_mesh.py](file:///Users/gabrielebertini/git/ghabs/nexus-arc/nexus/core/agent_mesh.py)) for decoupled multi-agent choreography. It implements event-driven multi-agent architectural patterns without any external broker infrastructure, proprietary protocols, or third-party client dependencies.

---

## 1. Architectural Principles

| Pattern | Central Orchestration | Native Nexus Agent Mesh |
|---|---|---|
| **Coupling** | Direct synchronous RPCs, hardwired agent IDs | Decoupled event pub/sub over hierarchical topics |
| **Discovery** | Central controller maintains static mapping | Dynamic presence announcements and TTL heartbeats ([PeerRegistry](file:///Users/gabrielebertini/git/ghabs/nexus-arc/nexus/agents/peer_registry.py)) |
| **Routing** | Caller specifies exact destination worker | Dynamic capability routing by advertised skill (`route_capability`) |
| **Correlation** | Synchronous blocking HTTP/socket calls | Asynchronous request-reply with correlation IDs and reply topics |
| **Broker Infrastructure** | Proprietary broker clusters required | 100% native Python (`asyncio`), zero broker dependencies |

---

## 2. Topic Namespace Hierarchy

The mesh organizes communications into a dot-delimited hierarchy matching MQTT and event-mesh conventions:

- **Point-to-Point Agent Inbox**: `agents.{agent_name}.inbox`
  Direct delivery to a named agent instance.
- **Correlated Agent Replies**: `agents.{agent_name}.replies.{correlation_id}`
  Isolated return channels for asynchronous request-reply handshakes.
- **Capability Tasks**: `capabilities.{skill}.tasks`
  Skill-based task queues routed to any healthy peer advertising the skill.
- **Mesh Broadcasts**: `mesh.broadcast.{category}`
  Broadcast announcements such as peer lifecycle updates (`mesh.broadcast.peer_lifecycle`).
- **Domain Events**: `mesh.events.{domain}.{event_type}`
  Execution events emitted across the workflow lifecycle.

### Wildcard Support
- `*`: Single-level wildcard (e.g., `agents.*.inbox` matches any agent inbox).
- `>` or `**`: Multi-level wildcard at end of topic (e.g., `mesh.events.>` matches all domain events).

---

## 3. Standard Message Envelope

All traffic on the mesh travels in a standardized [AgentMeshEnvelope](file:///Users/gabrielebertini/git/ghabs/nexus-arc/nexus/core/agent_mesh.py):

```python
@dataclass
class AgentMeshEnvelope:
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    topic: str = ""
    sender: str = ""
    recipient: str | None = None
    correlation_id: str | None = None
    reply_to: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
```

---

## 4. Key Capabilities

### Dynamic Capability Routing
Senders can route tasks based on skill requirements rather than hardcoding agent identities:

```python
peer_name, env = await mesh.route_capability(
    skill="data_analysis",
    payload={"query": "SELECT * FROM metrics"},
    strategy=RoutingStrategy.ROUND_ROBIN,
)
```

Supported routing strategies:
- `RoutingStrategy.ROUND_ROBIN`: Cycles evenly through capable peers.
- `RoutingStrategy.RANDOM`: Random selection among capable peers.
- `RoutingStrategy.LEAST_LOADED`: Selects the peer with the lowest active task count.

### Request-Reply over Pub/Sub
Asynchronous request-reply with built-in correlation ID tracking and timeouts:

```python
# Requester awaits correlated response
reply = await mesh.request(
    target_topic=AgentMeshTopic.agent_inbox("reporter"),
    payload={"title": "Q3 Summary"},
    sender="coordinator",
    timeout=5.0,
)

# Responder publishes reply
await mesh.reply(request_env, payload={"status": "completed", "report": report_text})
```

---

## 5. Verification

- Test suite: [test_agent_mesh.py](file:///Users/gabrielebertini/git/ghabs/nexus-arc/tests/test_agent_mesh.py) (11 tests passed).
- Consumer example: [studio_agent_mesh.py](file:///Users/gabrielebertini/git/ghabs/nexus-arc/examples/nexus-bot/src/studio_agent_mesh.py).

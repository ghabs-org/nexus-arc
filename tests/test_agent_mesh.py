"""Tests for native Nexus Agent Mesh architecture."""

import asyncio
import pytest

from nexus.core.agent_mesh import (
    AgentMesh,
    AgentMeshEnvelope,
    AgentMeshTopic,
    MeshTimeoutError,
    NoCapablePeerError,
    RoutingStrategy,
)


def test_envelope_serialization():
    env = AgentMeshEnvelope(
        topic="agents.worker-1.inbox",
        sender="coordinator",
        recipient="worker-1",
        correlation_id="corr-123",
        reply_to="agents.coordinator.replies.corr-123",
        payload={"task": "process_data", "batch": 42},
        metadata={"priority": "high"},
    )
    raw = env.to_dict()
    assert raw["topic"] == "agents.worker-1.inbox"
    assert raw["sender"] == "coordinator"
    assert raw["payload"]["batch"] == 42
    assert raw["metadata"]["priority"] == "high"

    reconstructed = AgentMeshEnvelope.from_dict(raw)
    assert reconstructed.message_id == env.message_id
    assert reconstructed.topic == env.topic
    assert reconstructed.correlation_id == "corr-123"
    assert reconstructed.payload == env.payload


def test_topic_pattern_matching():
    matches = AgentMeshTopic.matches

    # Exact matches
    assert matches("agents.alpha.inbox", "agents.alpha.inbox")
    assert not matches("agents.alpha.inbox", "agents.beta.inbox")

    # Single-level wildcard
    assert matches("agents.*.inbox", "agents.alpha.inbox")
    assert matches("agents.*.inbox", "agents.beta.inbox")
    assert not matches("agents.*.inbox", "agents.alpha.outbox")
    assert not matches("agents.*.inbox", "agents.sub.alpha.inbox")

    # Multi-level wildcard
    assert matches("mesh.events.>", "mesh.events.workflow.started")
    assert matches("mesh.events.>", "mesh.events.agent.step.failed")
    assert not matches("mesh.events.>", "agents.alpha.inbox")
    assert matches("mesh.broadcast.>", "mesh.broadcast.peer_lifecycle")
    assert matches(">", "any.depth.topic.whatsoever")
    assert matches("*", "single")


@pytest.mark.asyncio
async def test_mesh_pub_sub_choreography():
    mesh = AgentMesh()
    received_inbox = []
    received_all_events = []

    def handle_inbox(env: AgentMeshEnvelope):
        received_inbox.append(env)

    async def handle_events(env: AgentMeshEnvelope):
        received_all_events.append(env)

    sub1 = mesh.subscribe("agents.*.inbox", handle_inbox)
    sub2 = mesh.subscribe("mesh.events.>", handle_events)

    # Publish to agent inbox
    env1 = AgentMeshEnvelope(topic="agents.alpha.inbox", payload={"cmd": "run"})
    count1 = await mesh.publish(env1)
    assert count1 == 1
    assert len(received_inbox) == 1
    assert len(received_all_events) == 0

    # Publish to mesh events
    env2 = AgentMeshEnvelope(topic="mesh.events.audit.log", payload={"action": "login"})
    count2 = await mesh.publish(env2)
    assert count2 == 1
    assert len(received_inbox) == 1
    assert len(received_all_events) == 1

    # Unsubscribe
    assert mesh.unsubscribe(sub1) is True
    await mesh.publish(env1)
    assert len(received_inbox) == 1  # No new messages delivered

    mesh.close()


@pytest.mark.asyncio
async def test_dynamic_capability_routing():
    mesh = AgentMesh()

    # Register peers with capabilities
    mesh.register_peer("coder-1", "Python dev", skills=["python", "fastapi"])
    mesh.register_peer("coder-2", "Python dev 2", skills=["python", "django"])
    mesh.register_peer("reviewer", "Code Reviewer", skills=["review", "audit"])

    # Verify peer discovery
    python_peers = mesh.get_active_peers(skill="python")
    assert len(python_peers) == 2
    assert {p.name for p in python_peers} == {"coder-1", "coder-2"}

    review_peers = mesh.get_active_peers(skill="review")
    assert len(review_peers) == 1
    assert review_peers[0].name == "reviewer"

    # Route with round robin
    inbox_coder1 = []
    inbox_coder2 = []
    mesh.subscribe("agents.coder-1.inbox", lambda env: inbox_coder1.append(env))
    mesh.subscribe("agents.coder-2.inbox", lambda env: inbox_coder2.append(env))

    peer_a, env_a = await mesh.route_capability(
        skill="python",
        payload={"task": "write_api"},
        strategy=RoutingStrategy.ROUND_ROBIN,
    )
    peer_b, env_b = await mesh.route_capability(
        skill="python",
        payload={"task": "write_db"},
        strategy=RoutingStrategy.ROUND_ROBIN,
    )

    assert {peer_a, peer_b} == {"coder-1", "coder-2"}
    assert len(inbox_coder1) == 1
    assert len(inbox_coder2) == 1

    # Route with missing capability raises NoCapablePeerError
    with pytest.raises(NoCapablePeerError, match="No active peer available"):
        await mesh.route_capability("quantum_computing", {"task": "simulate"})

    mesh.close()


@pytest.mark.asyncio
async def test_request_reply_correlation():
    mesh = AgentMesh()

    async def service_worker(env: AgentMeshEnvelope):
        # Service responder: compute reply and send back to envelope.reply_to
        req_val = env.payload.get("number", 0)
        await mesh.reply(env, payload={"result": req_val * 2})

    mesh.subscribe("agents.math-service.inbox", service_worker)

    # Issue request and await correlated reply
    response = await mesh.request(
        target_topic="agents.math-service.inbox",
        payload={"number": 21},
        sender="client-agent",
        timeout=2.0,
    )

    assert response is not None
    assert response.payload["result"] == 42
    assert response.recipient == "client-agent"
    assert response.correlation_id is not None

    mesh.close()


@pytest.mark.asyncio
async def test_request_reply_timeout():
    mesh = AgentMesh(default_timeout=0.2)

    # Target topic with no responder subscribed
    with pytest.raises(MeshTimeoutError, match="timed out"):
        await mesh.request(
            target_topic="agents.silent-service.inbox",
            payload={"ping": True},
            timeout=0.1,
        )

    mesh.close()


@pytest.mark.asyncio
async def test_direct_send_and_broadcast():
    mesh = AgentMesh()
    direct_msgs = []
    broadcast_msgs = []

    mesh.subscribe("agents.alpha.inbox", lambda env: direct_msgs.append(env))
    mesh.subscribe("mesh.broadcast.system", lambda env: broadcast_msgs.append(env))

    await mesh.send("alpha", {"type": "direct_ping"}, sender="beta")
    assert len(direct_msgs) == 1
    assert direct_msgs[0].sender == "beta"
    assert direct_msgs[0].payload["type"] == "direct_ping"

    await mesh.broadcast("system", {"notice": "restarting"})
    assert len(broadcast_msgs) == 1
    assert broadcast_msgs[0].payload["notice"] == "restarting"

    mesh.close()


@pytest.mark.asyncio
async def test_peer_lifecycle_and_unregistration():
    mesh = AgentMesh()
    lifecycle_events = []

    mesh.subscribe(
        AgentMeshTopic.broadcast("peer_lifecycle"),
        lambda env: lifecycle_events.append(env.payload),
    )

    mesh.register_peer("worker-node", "Worker Node", skills=["compute"])
    # Give asyncio event loop tick to deliver background broadcast
    await asyncio.sleep(0.01)

    assert any(
        e.get("event") == "peer_registered" and e.get("name") == "worker-node"
        for e in lifecycle_events
    )

    assert mesh.unregister_peer("worker-node") is True
    await asyncio.sleep(0.01)

    assert any(
        e.get("event") == "peer_removed" and e.get("name") == "worker-node"
        for e in lifecycle_events
    )

    assert mesh.get_active_peers(skill="compute") == []
    mesh.close()


@pytest.mark.asyncio
async def test_least_loaded_routing_strategy():
    mesh = AgentMesh()
    mesh.register_peer("worker-a", "Worker A", skills=["batch"])
    mesh.register_peer("worker-b", "Worker B", skills=["batch"])

    # First task to worker-a
    peer1, _ = await mesh.route_capability(
        skill="batch",
        payload={"job": 1},
        strategy=RoutingStrategy.LEAST_LOADED,
    )
    # Second task should go to worker-b because worker-a has 1 task
    peer2, _ = await mesh.route_capability(
        skill="batch",
        payload={"job": 2},
        strategy=RoutingStrategy.LEAST_LOADED,
    )
    assert {peer1, peer2} == {"worker-a", "worker-b"}

    mesh.close()


@pytest.mark.asyncio
async def test_reply_error_conditions():
    mesh = AgentMesh()

    # Envelope missing reply_to
    env_no_reply_to = AgentMeshEnvelope(topic="agents.test.inbox", correlation_id="c1")
    with pytest.raises(ValueError, match="no reply_to"):
        await mesh.reply(env_no_reply_to, {"msg": "hello"})

    # Envelope missing correlation_id
    env_no_corr = AgentMeshEnvelope(topic="agents.test.inbox", reply_to="reply.topic")
    with pytest.raises(ValueError, match="no correlation_id"):
        await mesh.reply(env_no_corr, {"msg": "hello"})

    mesh.close()


@pytest.mark.asyncio
async def test_publish_dict_and_missing_topic():
    mesh = AgentMesh()
    received = []
    mesh.subscribe("test.topic", lambda env: received.append(env))

    await mesh.publish({"topic": "test.topic", "payload": {"foo": "bar"}})
    assert len(received) == 1
    assert received[0].payload == {"foo": "bar"}

    with pytest.raises(ValueError, match="without a topic"):
        await mesh.publish(AgentMeshEnvelope(topic=""))

    mesh.close()


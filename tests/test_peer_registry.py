"""Peer discovery: heartbeat, TTL expiry, topology filters, specs."""


def _registry(**kwargs):
    from nexus.agents.peer_registry import PeerRegistry

    return PeerRegistry(ttl_seconds=kwargs.pop("ttl_seconds", 60.0), **kwargs)


def test_heartbeat_and_lookup():
    registry = _registry()
    assert registry.heartbeat("alpha", "First agent", skills=["triage"]) is True
    card = registry.get("alpha")
    assert card is not None and card.skills == ["triage"]
    assert registry.get("ghost") is None


def test_expiry_and_prune():
    import time

    from nexus.agents.peer_registry import PeerRegistry

    registry = PeerRegistry(ttl_seconds=1.0)
    registry.heartbeat("old")
    card = registry._peers["old"]
    card.last_seen = time.time() - 61.0
    assert registry.get("old") is None
    assert registry.prune() == 0


def test_allow_deny_topology():
    events = []
    registry = _registry(allow_list=["team-*"], deny_list=["team-secret"])
    registry.on_added(events.append)
    assert registry.heartbeat("team-a", "A") is True
    assert registry.heartbeat("team-secret", "S") is False
    assert registry.heartbeat("outsider", "O") is False
    assert [c.name for c in registry.all()] == ["team-a"]
    assert [c.name for c in events] == ["team-a"]


def test_removed_callback_on_expiry():
    import time

    from nexus.agents.peer_registry import PeerRegistry

    removed = []
    registry = PeerRegistry(ttl_seconds=1.0)
    registry.on_removed(removed.append)
    registry.heartbeat("temp")
    registry._peers["temp"].last_seen = time.time() - 61.0
    assert registry.all() == []
    assert [c.name for c in removed] == ["temp"]


def test_specs_and_prompt_lines():
    registry = _registry()
    registry.heartbeat("alpha", "First", skills=["triage", "design"])
    assert registry.specs() == [{"name": "alpha", "description": "First"}]
    assert "alpha" in registry.describe_for_prompt()

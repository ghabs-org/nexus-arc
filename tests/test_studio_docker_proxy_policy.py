"""Recovery lookups cannot enumerate or redirect authority to another container."""

import asyncio
import json
import signal

import pytest

from nexus.core.studio_docker_proxy_policy import (
    container_target,
    proxy_termination,
    recovery_listing,
)

NAME = "nexus-analysis-" + "a" * 32
IDENTITY = "b" * 64


@pytest.mark.parametrize("names", [["^/" + NAME + "$"], {"^/" + NAME + "$": True}])
def test_legacy_and_current_name_filters_become_full_id_scoped(names):
    selected = []

    def owned(name):
        selected.append(name)
        return IDENTITY

    result = recovery_listing({"all": ["1"], "filters": [json.dumps({"name": names})]}, owned)
    assert json.loads(result["filters"][0]) == {"name": ["^/" + NAME + "$"], "id": [IDENTITY]}
    assert selected == [NAME]


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"all": ["1"]},
        {"all": ["0"], "filters": ["{}"]},
        {"all": ["1", "0"], "filters": ["{}"]},
        {"all": ["1"], "filters": ["{}"], "size": ["1"]},
        {"all": ["1"], "filters": ["{}", "{}"]},
        {"all": ["1"], "filters": ["x" * 4097]},
        {"all": ["1"], "filters": ["not json"]},
        {"all": ["1"], "filters": ["[]"]},
    ],
)
def test_arbitrary_listing_is_rejected_before_ownership_lookup(query):
    def forbidden(name):
        pytest.fail("Invalid listing must not access the tracker")

    with pytest.raises(ValueError):
        recovery_listing(query, forbidden)


@pytest.mark.parametrize(
    "filters",
    [
        {"name": [NAME]},
        {"name": [".*"]},
        {"name": ["^/nexus-analysis-.*$"]},
        {"name": ["^/" + NAME + "$", "^/other$"]},
        {"name": {"^/" + NAME + "$": False}},
        {"name": []},
        {"name": [42]},
        {"name": ["^/" + NAME + "$"], "id": [IDENTITY]},
        {"label": ["anything"]},
    ],
)
def test_wildcards_extra_filters_and_unscoped_names_are_denied(filters):
    with pytest.raises(ValueError):
        recovery_listing({"all": ["1"], "filters": [json.dumps(filters)]}, lambda _: IDENTITY)


@pytest.mark.parametrize("owned", [None, "short-id", "c" * 63, "../other"])
def test_unknown_ownership_never_returns_a_successful_empty_listing(owned):
    with pytest.raises(ValueError, match="unconfirmed"):
        recovery_listing(
            {"all": ["1"], "filters": [json.dumps({"name": ["^/" + NAME + "$"]})]}, lambda _: owned
        )


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/containers/" + NAME + "?force=1", "/containers/" + IDENTITY + "?force=1"),
        ("/v1.56/containers/" + NAME + "/start", "/v1.56/containers/" + IDENTITY + "/start"),
        (
            "/v1.56/containers/" + IDENTITY[:12] + "/attach?stream=1&stdin=1",
            "/v1.56/containers/" + IDENTITY + "/attach?stream=1&stdin=1",
        ),
    ],
)
def test_name_and_short_id_requests_forward_the_owned_full_id(target, expected):
    assert container_target(target, IDENTITY) == expected


@pytest.mark.parametrize(
    "target",
    ["/info", "/containers/x/exec", "/containers/x/../other", "/v1.56/containers/x/archive"],
)
def test_unexpected_container_targets_are_not_forwarded(target):
    with pytest.raises(ValueError):
        container_target(target, IDENTITY)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_proxy_stop_is_cooperative_and_restores_previous_signal_policy(monkeypatch, failure):
    loop = asyncio.get_running_loop()
    callbacks, removed, restored = {}, [], []
    previous = object()
    monkeypatch.setattr(signal, "getsignal", lambda _: previous)
    monkeypatch.setattr(signal, "signal", lambda name, handler: restored.append((name, handler)))
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda name, callback: callbacks.update({name: callback})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda name: removed.append(name))

    async def serve():
        async with proxy_termination() as stop:
            assert not stop.is_set()
            callbacks[signal.SIGTERM]()
            callbacks[signal.SIGTERM]()
            assert stop.is_set()
            if failure:
                raise ValueError("startup failed")

    if failure:
        with pytest.raises(ValueError, match="startup failed"):
            await serve()
    else:
        await serve()
    assert removed == [signal.SIGTERM] and restored == [(signal.SIGTERM, previous)]

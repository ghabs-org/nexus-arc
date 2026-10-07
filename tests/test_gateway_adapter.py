"""Gateway adapter contract: normalize in, route replies out."""

import asyncio


def test_echo_roundtrip():
    from nexus.adapters.gateway import EchoGatewayAdapter

    adapter = EchoGatewayAdapter()
    task = adapter.to_task({"user_id": "7", "text": "hi"})
    assert (task.task_id, task.user_id, task.text) != ("", "", "")

    async def _handle(task):
        return f"echo:{task.text}"

    asyncio.run(adapter.run(task, _handle))
    assert len(adapter.sent) == 1
    assert adapter.sent[0].done is True
    assert adapter.sent[0].text == "echo:hi"


def test_error_routes_out():
    from nexus.adapters.gateway import EchoGatewayAdapter

    adapter = EchoGatewayAdapter()
    task = adapter.to_task({"user_id": "7", "text": "hi"})

    async def _boom(task):
        raise ValueError("kaput")

    asyncio.run(adapter.run(task, _boom))
    assert adapter.sent[0].error == "kaput"
    assert adapter.sent[0].done is True


def test_streaming_chunks():
    from nexus.adapters.gateway import EchoGatewayAdapter

    adapter = EchoGatewayAdapter()
    task = adapter.to_task({"user_id": "7", "text": "hi"})
    asyncio.run(adapter.on_text(task, "partial"))
    assert adapter.sent[0].done is False
    assert adapter.sent[0].text == "partial"

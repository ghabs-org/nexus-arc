"""Outbox: durable enqueue, at-least-once drain, dead-letter, reload."""


def _outbox(tmp_path, **kwargs):
    from nexus.outbox import FileOutbox

    return FileOutbox(tmp_path / "outbox.json", **kwargs)


def test_deliver_and_ack(tmp_path):
    outbox = _outbox(tmp_path)
    outbox.enqueue("workflow.done", {"id": "wf-1"})
    seen = []
    result = outbox.drain(lambda event: seen.append(event.payload["id"]))
    assert result == {"delivered": 1, "dead": 0}
    assert seen == ["wf-1"]
    assert outbox.pending() == []


def test_retry_then_dead_letter(tmp_path):
    outbox = _outbox(tmp_path, max_attempts=2)

    def _fail(event):
        raise RuntimeError("downstream down")

    outbox.enqueue("notify", {})
    assert outbox.drain(_fail) == {"delivered": 0, "dead": 0}
    assert len(outbox.pending()) == 1
    assert outbox.drain(_fail) == {"delivered": 0, "dead": 1}
    assert outbox.pending() == []
    assert len(outbox.dead()) == 1


def test_survives_reload(tmp_path):
    outbox = _outbox(tmp_path)
    outbox.enqueue("a", {"n": 1})
    del outbox
    from nexus.outbox import FileOutbox

    reopened = FileOutbox(tmp_path / "outbox.json")
    assert [(e.kind, e.payload) for e in reopened.pending()] == [("a", {"n": 1})]

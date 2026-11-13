import threading
from unittest.mock import Mock

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_team import team as team_fixture

ROUTE = "/api/workbench/readiness/cleanup"


@pytest.fixture(name="team")
def shared_team(tmp_path):
    yield from team_fixture.__wrapped__(tmp_path)


def test_cleanup_http_enforces_owner_csrf_origin_and_fixed_request(team, monkeypatch):
    service, _, _, request, _ = team
    recover = Mock(
        return_value={"items": [{"name": "saved-worker", "status": "unconfirmed"}], "pending": 1}
    )
    monkeypatch.setattr(service.data_tools.sandbox, "recover_cleanup", recover)
    assert request(ROUTE, {})[0] == 401
    for user in ("viewer", "bob", "alice"):
        assert (
            request(ROUTE, {"requester_nexus_id": "owner", "_role": "owner"}, user=user)[0] == 403
        )
    assert request(ROUTE, {}, user="owner", csrf="wrong")[0] == 403
    assert request(ROUTE, {}, user="owner", extra={"Origin": "https://other.invalid"})[0] == 403
    assert request(ROUTE, {"context": "other"}, user="owner")[0] == 400
    assert request(ROUTE + "/extra", {}, user="owner")[0] == 404
    recover.assert_not_called()
    status, report = request(ROUTE, {}, user="owner")
    assert status == 200 and report["pending"] == 1
    assert report["items"][0]["status"] == "unconfirmed" and report["checked_at"]
    recover.assert_called_once_with(timeout=8)


def test_readiness_get_never_starts_cleanup(team, monkeypatch):
    service, _, _, request, _ = team
    recover = Mock(side_effect=AssertionError("GET must not perform cleanup"))
    monkeypatch.setattr(service.data_tools.sandbox, "recover_cleanup", recover)
    status, report = request("/api/workbench/readiness", user="owner")
    assert status == 200 and report["analysis_cleanup"] == {"tracked": 0, "available": True}
    recover.assert_not_called()


def test_concurrent_cleanup_http_returns_busy_without_duplicate_dispatch(team, monkeypatch):
    service, _, _, request, _ = team
    entered, release = threading.Event(), threading.Event()
    responses = []

    def recover(**kwargs):
        assert kwargs == {"timeout": 8}
        entered.set()
        assert release.wait(3)
        return {"items": [], "pending": 0}

    dispatch = Mock(side_effect=recover)
    monkeypatch.setattr(service.data_tools.sandbox, "recover_cleanup", dispatch)
    worker = threading.Thread(target=lambda: responses.append(request(ROUTE, {}, user="owner")))
    worker.start()
    try:
        assert entered.wait(2)
        assert request(ROUTE, {}, user="owner")[0] == 409
        assert request(ROUTE, {}, user="bob")[0] == 403
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and responses[0][0] == 200
    dispatch.assert_called_once_with(timeout=8)


def test_closing_http_refuses_cleanup_without_dispatch(team, monkeypatch):
    service, _, _, request, _ = team
    dispatch = Mock(side_effect=AssertionError("Closing service cannot dispatch cleanup"))
    monkeypatch.setattr(service.data_tools.sandbox, "recover_cleanup", dispatch)
    service.closing = True
    try:
        assert request(ROUTE, {}, user="owner")[0] == 503
        assert (
            request("/api/workbench/readiness", user="owner")[1]["analysis_cleanup"]["available"]
            is False
        )
    finally:
        service.closing = False
    dispatch.assert_not_called()


def test_service_rechecks_current_owner_role_for_stale_principal(team, monkeypatch):
    service, policy, _, _, _ = team
    dispatch = Mock(side_effect=AssertionError("Revoked owner cannot dispatch cleanup"))
    monkeypatch.setattr(service.data_tools.sandbox, "recover_cleanup", dispatch)
    policy.bindings["owner"] = "operator"
    with pytest.raises(PermissionError, match="owner"):
        service.workbench.post(["readiness", "cleanup"], {}, {"nexus_id": "owner", "role": "owner"})
    dispatch.assert_not_called()


def test_local_cleanup_resolves_empty_journal_without_docker(tmp_path, monkeypatch):
    service = StudioService(tmp_path)
    monkeypatch.setattr(
        service.data_tools.sandbox,
        "run_command",
        Mock(side_effect=AssertionError("Empty cleanup needs no Docker command")),
    )
    try:
        status, report = service.workbench.post(["readiness", "cleanup"], {}, None)
        assert status == 200 and report["items"] == [] and report["pending"] == 0
    finally:
        service.close()

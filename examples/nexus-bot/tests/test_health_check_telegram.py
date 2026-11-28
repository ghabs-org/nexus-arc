"""Telegram status in health_check: Docker container first, systemd fallback."""

import subprocess
from unittest.mock import patch

import pytest

health_check = pytest.importorskip("src.health_check")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_running_container_is_reported_running():
    out = "running|2026-11-20T15:51:40.1Z|4242\n"
    with patch.object(health_check.subprocess, "run", return_value=_completed(stdout=out)):
        status = health_check.check_container_status("nexus-host-telegram-1")
    assert status["running"] is True
    assert status["found"] is True
    assert status["pid"] == 4242


def test_exited_container_is_not_running():
    with patch.object(health_check.subprocess, "run", return_value=_completed(stdout="exited||0\n")):
        status = health_check.check_container_status("nexus-host-telegram-1")
    assert status["found"] is True
    assert status["running"] is False


def test_telegram_uses_container_when_present(monkeypatch):
    monkeypatch.delenv("NEXUS_TELEGRAM_CONTAINER", raising=False)
    with patch.object(
        health_check, "check_container_status", return_value={"found": True, "running": True}
    ) as container, patch.object(health_check, "check_service_status") as unit:
        status = health_check.check_telegram_status()
    container.assert_called_once_with("nexus-host-telegram-1")
    unit.assert_not_called()
    assert status["running"] is True


def test_telegram_falls_back_to_systemd_when_container_missing(monkeypatch):
    monkeypatch.setenv("NEXUS_TELEGRAM_CONTAINER", "custom-telegram")
    with patch.object(
        health_check, "check_container_status", return_value={"found": False, "running": False}
    ), patch.object(
        health_check, "check_service_status", return_value={"running": True, "status": "active"}
    ) as unit:
        status = health_check.check_telegram_status()
    unit.assert_called_once_with("nexus-telegram.service")
    assert status["running"] is True

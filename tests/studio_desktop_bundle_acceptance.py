"""Actual relocated executable acceptance; requires an explicitly built sidecar."""

import os
import runpy
from pathlib import Path


def test_relocated_sidecar_serves_ui_and_runs_internal_pdf_worker_without_host_python():
    root = Path(__file__).resolve().parents[1]
    example = runpy.run_path(str(root / "examples/nexus-bot/src/studio_desktop_bundle.py"))
    result = example["exercise"](
        os.environ["NEXUS_ACCEPTANCE_SIDECAR"], os.environ.get("NEXUS_ACCEPTANCE_PROFILE", "core")
    )
    for name in [
        "relocated",
        "authenticated_startup",
        "workspace_write",
        "frontend_and_favicon",
        "frozen_pdf_worker",
        "frozen_pdf_errors",
        "frozen_pdf_text_and_page_bounds",
        "parent_eof_shutdown",
    ]:
        assert result[name]
    assert not result["host_python_on_path"] and not result["live_providers_used"]
    assert result["dependency_profile"]["passed"]
    assert len(result["pdf_preview_seconds"]) == result["pdf_preview_repetitions"]
    if os.environ.get("NEXUS_ACCEPTANCE_PROFILE") in {"connected", "full"}:
        from nexus.core.studio_desktop_profiles import profile_requirements

        assert set(result["dependency_profile"]["packages"]) == set(
            profile_requirements(os.environ["NEXUS_ACCEPTANCE_PROFILE"])
        )
    if os.environ.get("NEXUS_ACCEPTANCE_PROFILE") == "full":
        checks = result["dependency_profile"]["offline_checks"]
        assert set(checks) == {"speech_data", "cloud_models", "grpc_messages"}
        assert all(row["passed"] for row in checks.values())

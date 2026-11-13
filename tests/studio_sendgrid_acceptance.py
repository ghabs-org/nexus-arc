"""Explicit real-HTTP acceptance; start the repository SendGrid simulation first."""

import os
import runpy
from pathlib import Path


def test_catalog_sendgrid_reviewed_html_templates_and_failure_outcomes():
    consumer = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_sendgrid.py")
    )
    result = consumer["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_SENDGRID_URL", "http://127.0.0.1:49871/mail")
    )
    assert result == {
        "review_required": True,
        "html_accepted": True,
        "template_accepted": True,
        "failures_not_retried": True,
        "delivery_verified": False,
        "requests": 4,
    }

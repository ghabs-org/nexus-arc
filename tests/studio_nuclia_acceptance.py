"""Explicit local-HTTP Nuclia generation and private citation acceptance."""

import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "templates,sam_template,evidence,originals",
    [
        (False, False, False, False),
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ],
)
def test_nuclia_opt_in_generation_and_private_revision_citations(
    templates, sam_template, evidence, originals
):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia"),
        templates=templates,
        sam_template=sam_template,
        evidence=evidence,
        originals=originals,
    )
    assert result == {
        "requests": 10 if originals else 9,
        "failure_cases": 6,
        "private_revision_citations": True,
        "explicit_generation": True,
        "live_verified": False,
    }


@pytest.mark.parametrize("case", ["reject", "redirect", "partial", "oversized"])
def test_original_binary_transport_rejects_failed_partial_or_oversized_responses(tmp_path, case):
    from nexus.core.studio import StudioService

    studio = StudioService(tmp_path)
    try:
        base = os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia")
        with pytest.raises(ValueError) as error:
            studio.research.request(
                "GET",
                base + f"/api/v1/kb/selected/resource/resource/file/{case}/download/field",
                {"X-NUCLIA-SERVICEACCOUNT": "Bearer synthetic-nuclia-key"},
                private=True,
                binary=True,
                response_limit=64,
            )
        assert "PRIVATE_PROVIDER_DETAIL" not in str(error.value)
        assert not studio.catalog.list("artifacts")
    finally:
        studio.close()

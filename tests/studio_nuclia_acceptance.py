"""Explicit local-HTTP Nuclia generation and private citation acceptance."""

import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "templates,portable_template,evidence,originals,retrieved",
    [
        (False, False, False, False, False),
        (True, False, False, False, False),
        (False, True, False, False, False),
        (False, False, True, False, False),
        (False, False, False, True, False),
        (False, False, True, False, True),
    ],
)
def test_nuclia_opt_in_generation_and_private_revision_citations(
    templates, portable_template, evidence, originals, retrieved
):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia"),
        templates=templates,
        portable_template=portable_template,
        evidence=evidence,
        originals=originals,
        retrieved=retrieved,
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


@pytest.mark.parametrize("preserve_retrieval", [False, True])
def test_descriptor_binding_review_preserves_fixed_settings_over_http(preserve_retrieval):
    example = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "examples/nexus-bot/src/studio_nuclia_descriptors.py"
        )
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia"),
        preserve_retrieval=preserve_retrieval,
    )
    assert result == {
        "requests": 2,
        "reviewed_binding": True,
        "no_action_replay": True,
        "live_verified": False,
    }


def test_reviewed_audit_templates_search_answer_and_failure_over_http():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia_audit.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia")
    )
    assert result == {
        "provider_requests": 4,
        "search_and_answer": True,
        "preflight_rejections": 3,
        "no_action_replay": True,
        "live_verified": False,
    }


@pytest.mark.parametrize(
    "mode,artifact,output_filename_base",
    [
        (mode, artifact, name)
        for mode in ["default", "none", "llm_footnotes"]
        for artifact in [False, True]
        for name in ([None, "runtime-policy.MD"] if artifact else [None])
    ],
)
def test_reviewed_answer_presentation_over_http(mode, artifact, output_filename_base):
    example = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "examples/nexus-bot/src/studio_nuclia_presentation.py"
        )
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia"),
        mode=mode,
        artifact=artifact,
        output_filename_base=output_filename_base,
    )
    assert result == {
        "provider_requests": 2,
        "mode": mode,
        "answer_file": artifact,
        "plain_output_preserved": True,
        "no_action_replay": True,
        "live_verified": False,
    }


def test_agent_selected_answer_name_over_http():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia"),
        output_filename_base="agent-policy",
    )
    assert result["requests"] == 9
    assert result["private_revision_citations"] and not result["live_verified"]

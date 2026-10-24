"""Actual connector transport against the local Nuclia dependency simulation."""

import os
import runpy
from pathlib import Path

import pytest

from nexus.core.studio import StudioService


def test_conversation_files_are_selected_and_pinned_without_provider_links():
    example = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "examples/nexus-bot/src/studio_nuclia_attachments.py"
        )
    )
    assert example["exercise"](os.environ["NEXUS_ACCEPTANCE_NUCLIA_URL"]) == {
        "conversation_pages": 2,
        "saved_files": 3,
        "cached_repeat": True,
        "provider_requests": 6,
        "live_verified": False,
    }


@pytest.mark.parametrize("case", ["reject", "redirect", "partial", "oversized", "malformed"])
def test_conversation_metadata_failure_rejects_without_publishing_choices(tmp_path, case):
    from nexus.core.studio_nuclia_documents import download_original
    from tests.test_studio_nuclia_attachments import conversation_source

    studio = StudioService(tmp_path)
    record, selection = conversation_source(
        studio, url=os.environ["NEXUS_ACCEPTANCE_NUCLIA_URL"], allow_private=True
    )
    # Bind a synthetic cited field to the corresponding dependency failure shape.
    source = studio.run("source")
    source["citations"][0]["field_id"] = "c/" + case
    studio._write(studio._path("runs", "source"), source)
    studio.team = None
    from unittest.mock import patch

    try:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-nuclia-key"}),
            pytest.raises(ValueError),
        ):
            download_original(
                studio, record["data"], None, {**selection, "operation": "list"}, record["id"]
            )
        assert studio.records("source-attachment-selections", limit=None) == []
    finally:
        studio.close()

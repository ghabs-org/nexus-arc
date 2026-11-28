"""Approved model snapshots fail closed on truncated, altered or oversized content."""

import hashlib
import io
from unittest.mock import Mock

import pytest

from tests.fixtures import studio_voice_models as preparation


@pytest.mark.parametrize("content", [b"correct", b"tamper!", b"short", b"oversized!"])
def test_model_preparation_verifies_exact_bytes_and_removes_only_its_failed_file(
    tmp_path, monkeypatch, content
):
    approved = b"correct"
    monkeypatch.setattr(
        preparation,
        "MODELS",
        {
            "fixture.pt": {
                "url": "https://fixture.invalid/model",
                "bytes": len(approved),
                "sha256": hashlib.sha256(approved).hexdigest(),
            }
        },
    )
    fetch = Mock(return_value=io.BytesIO(content))
    monkeypatch.setattr(preparation, "urlopen", fetch)
    if content == approved:
        report = preparation.prepare(tmp_path)
        assert report["fixture.pt"]["bytes"] == 7
    else:
        with pytest.raises(ValueError):
            preparation.prepare(tmp_path)
        assert not (tmp_path / "fixture.pt").exists()
    fetch.assert_called_once_with("https://fixture.invalid/model", timeout=30)


def test_preexisting_altered_file_is_preserved_and_never_downloaded(tmp_path, monkeypatch):
    path = tmp_path / "fixture.pt"
    path.write_bytes(b"user model")
    monkeypatch.setattr(
        preparation,
        "MODELS",
        {
            "fixture.pt": {
                "url": "https://fixture.invalid/model",
                "bytes": 7,
                "sha256": "0" * 64,
            }
        },
    )
    fetch = Mock(side_effect=AssertionError("Must not overwrite an existing file"))
    monkeypatch.setattr(preparation, "urlopen", fetch)
    with pytest.raises(ValueError):
        preparation.prepare(tmp_path)
    assert path.read_bytes() == b"user model"
    fetch.assert_not_called()

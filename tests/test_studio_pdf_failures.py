"""PDF failures retain bounded execution and expose only safe recovery categories."""

import base64
import io
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

from nexus.core import studio_artifact_extract
from nexus.core.studio import StudioService
from nexus.core.studio_artifact_extract import (
    DEPENDENCY_MISSING,
    UNREADABLE_DOCUMENT,
    WORKER_FAILED,
)
from nexus.core.studio_artifacts import PDFExtractionError


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (subprocess.TimeoutExpired("worker", 15, output=b"private document"), "pdf_timeout"),
        (FileNotFoundError("/private/secret-path"), "pdf_worker_unavailable"),
        (subprocess.CalledProcessError(DEPENDENCY_MISSING, "worker"), "pdf_dependency_missing"),
        (subprocess.CalledProcessError(UNREADABLE_DOCUMENT, "worker"), "pdf_unreadable"),
        (subprocess.CalledProcessError(WORKER_FAILED, "worker"), "pdf_worker_failed"),
        (subprocess.CalledProcessError(-9, "worker"), "pdf_worker_terminated"),
        (subprocess.CalledProcessError(1, "worker", stderr=b"secret"), "pdf_worker_failed"),
    ],
)
def test_worker_failure_categories_survive_preview_without_leaking_values(
    tmp_path, monkeypatch, failure, code
):
    app = StudioService(tmp_path)
    try:
        record = app.catalog.save(
            "artifacts",
            {
                "name": "Private evidence",
                "data": {
                    "filename": "evidence.pdf",
                    "content_base64": base64.b64encode(b"%PDF- invalid").decode(),
                },
            },
        )

        def run(command, **kwargs):
            assert kwargs["timeout"] == 15 and kwargs["stderr"] == subprocess.DEVNULL
            raise failure

        monkeypatch.setattr(subprocess, "run", run)
        preview = app.artifacts.preview(record)
        assert preview["error_code"] == code and not preview["extraction_available"]
        assert preview["text"] == "" and "pages" not in preview
        assert not any(value in preview["message"] for value in ["secret", "/private", "OpenAI"])
        assert ("Install" in preview["message"]) == (code == "pdf_dependency_missing")
        assert app.artifacts.get(record) == b"%PDF- invalid"
    finally:
        app.close()


@pytest.mark.parametrize(
    "output", [b"not json", b"[]", b"{}", b'{"pages":true,"text":""}', b'{"pages":1}']
)
def test_malformed_worker_output_never_leaks_key_errors(tmp_path, monkeypatch, output):
    app = StudioService(tmp_path)
    try:
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output)
        )
        with pytest.raises(PDFExtractionError) as error:
            app.artifacts.pdf_text(b"%PDF- fixture")
        assert error.value.code == "pdf_invalid_response"
    finally:
        app.close()


def test_actual_worker_rejects_damaged_and_encrypted_documents(tmp_path):
    app = StudioService(tmp_path)
    try:
        encrypted = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt("not-a-real-credential")
        writer.write(encrypted)
        for content in [b"%PDF- broken source", encrypted.getvalue()]:
            with pytest.raises(PDFExtractionError) as error:
                app.artifacts.pdf_text(content)
            assert error.value.code == "pdf_unreadable"
    finally:
        app.close()


def test_missing_dependency_worker_exits_without_exception_details():
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(Path(studio_artifact_extract.__file__))],
        input=b"%PDF- fixture",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == DEPENDENCY_MISSING
    assert not result.stdout and not result.stderr

"""PDF failures retain bounded execution and expose only safe recovery categories."""

import base64
import io
import json
import os
import shutil
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
from nexus.core.studio_desktop_bundle import FROZEN_ENTRYPOINT
from nexus.core.studio_desktop_sidecar import worker_command


def blank_pdf():
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return buffer.getvalue()


def test_source_pdf_worker_does_not_import_an_unrelated_broken_provider(tmp_path):
    # Before isolated-file dispatch, importing Nexus reached this optional SDK
    # and failed before a valid PDF could even enter the bounded extractor.
    (tmp_path / "anthropic.py").write_text("raise RuntimeError('unrelated-provider-import')\n")
    result = subprocess.run(
        worker_command("nexus.core.studio_artifact_extract", "1"),
        input=blank_pdf(),
        capture_output=True,
        cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1]),
        },
        timeout=15,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {
        "text": "",
        "page_texts": [""],
        "pages": 1,
        "truncated": False,
    }


def test_frozen_entry_pdf_path_runs_without_loading_the_studio_package(tmp_path):
    package = tmp_path / "nexus"
    (package / "core").mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('Studio package must not load for PDF extraction')\n"
    )
    shutil.copyfile(
        studio_artifact_extract.__file__, package / "core" / "studio_artifact_extract.py"
    )
    entry = tmp_path / "entry.py"
    entry.write_text(FROZEN_ENTRYPOINT)
    result = subprocess.run(
        [sys.executable, str(entry), "--nexus-worker", "nexus.core.studio_artifact_extract", "1"],
        input=blank_pdf(),
        capture_output=True,
        cwd=tmp_path,
        env={"PATH": os.defpath},
        timeout=15,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["pages"] == 1


def test_frozen_entry_retains_existing_non_pdf_dispatch(tmp_path):
    package = tmp_path / "nexus" / "core"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "studio_desktop_sidecar.py").write_text(
        "import json,sys\ndef main(): print(json.dumps(sys.argv[1:]))\n"
    )
    entry = tmp_path / "entry.py"
    entry.write_text(FROZEN_ENTRYPOINT)
    result = subprocess.run(
        [sys.executable, str(entry), "--nexus-bundle-info"],
        capture_output=True,
        cwd=tmp_path,
        env={"PATH": os.defpath},
        timeout=15,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == ["--nexus-bundle-info"]


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


def test_concurrent_mixed_pdf_previews_preserve_pages_and_error_categories(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    app = StudioService(tmp_path)
    try:
        writer = PdfWriter()
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        for index in (1, 2):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 72 720 Td (Concurrent page {index}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        valid_buf = io.BytesIO()
        writer.write(valid_buf)
        encrypted_buf = io.BytesIO()
        writer.encrypt("example-only-secret")
        writer.write(encrypted_buf)

        valid_rec = app.catalog.save(
            "artifacts",
            {
                "name": "Valid multi-page",
                "data": {
                    "filename": "valid.pdf",
                    "content_base64": base64.b64encode(valid_buf.getvalue()).decode(),
                },
            },
        )
        damaged_rec = app.catalog.save(
            "artifacts",
            {
                "name": "Damaged",
                "data": {
                    "filename": "damaged.pdf",
                    "content_base64": base64.b64encode(b"%PDF- broken").decode(),
                },
            },
        )
        encrypted_rec = app.catalog.save(
            "artifacts",
            {
                "name": "Encrypted",
                "data": {
                    "filename": "encrypted.pdf",
                    "content_base64": base64.b64encode(encrypted_buf.getvalue()).decode(),
                },
            },
        )

        def worker(index):
            mode = index % 4
            if mode == 0:
                res = app.artifacts.preview(valid_rec, page=1)
                assert res["extraction_available"] and res["pages"] == 2
                assert res["text"] == "Concurrent page 1"
            elif mode == 1:
                res = app.artifacts.preview(valid_rec, page=2)
                assert res["extraction_available"] and res["pages"] == 2
                assert res["text"] == "Concurrent page 2"
            elif mode == 2:
                res = app.artifacts.preview(damaged_rec, page=1)
                assert not res["extraction_available"] and "pages" not in res
                assert res["error_code"] == "pdf_unreadable"
            else:
                res = app.artifacts.preview(encrypted_rec, page=1)
                assert not res["extraction_available"] and "pages" not in res
                assert res["error_code"] == "pdf_unreadable"
            return True

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(worker, range(16)))
        assert all(outcomes)
    finally:
        app.close()


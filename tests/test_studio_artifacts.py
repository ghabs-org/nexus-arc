"""Immutable binary versions, real PDF extraction and API multimodal payloads."""

import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nexus.adapters.ai.base import ExecutionContext
from nexus.core.studio import StudioService

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS9sAAAAASUVORK5CYII="
)


def save(app, content, name="evidence.txt", **kwargs):
    return app.catalog.save(
        "artifacts",
        {
            "name": name,
            **kwargs,
            "data": {"filename": name, "content_base64": base64.b64encode(content).decode()},
        },
    )


def pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Document evidence) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def test_artifact_bytes_are_versioned_outside_metadata_and_old_version_remains_readable(tmp_path):
    app = StudioService(tmp_path)
    try:
        first = save(app, b"First evidence")
        second = save(app, b"New evidence", id=first["id"], revision=1)
        assert second["revision"] == 2 and "content_base64" not in second["data"]
        assert app.artifacts.get(app.artifacts.revision(first["id"], 1)) == b"First evidence"
        assert app.catalog.artifact(first["id"])[1] == b"New evidence"
        first_path = app.state / "artifact-objects" / "studio" / first["data"]["blob_key"]
        first_path.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="integrity"):
            app.artifacts.get(first)
    finally:
        app.close()


def test_image_pdf_previews_and_provider_inputs_use_immutable_attachment_references(tmp_path):
    app = StudioService(tmp_path)
    try:
        image = save(app, PNG, "evidence.png")
        document = save(app, pdf(), "document.pdf")
        assert app.artifacts.preview(image)["kind"] == "image"
        preview = app.artifacts.preview(document)
        assert preview["pages"] == 1 and "Document evidence" in preview["text"]
        expanded = app.catalog.expand(
            {"task": "Review", "artifact_ids": [image["id"], document["id"]]}
        )
        assert expanded["artifact_refs"][0] == {"id": image["id"], "revision": 1}
        prompt, metadata = app.artifacts.provider_context(expanded, "openai", "Review")
        assert {item["media_type"] for item in metadata["attachments"]} == {
            "image/png",
            "application/pdf",
        }
        assert "content_base64" not in str(expanded)
        with pytest.raises(ValueError, match="vision-capable"):
            app.artifacts.provider_context(expanded, "claude-code", "Review")
        prompt, metadata = app.artifacts.provider_context(
            {"artifact_ids": [document["id"]]}, "codex", "Review"
        )
        assert "Document evidence" in prompt and metadata == {}
    finally:
        app.close()


@pytest.mark.asyncio
async def test_openai_adapter_emits_document_and_image_content_parts(tmp_path):
    from nexus.adapters.ai.openai_provider import OpenAIProvider

    create = AsyncMock(
        return_value=SimpleNamespace(
            model="vision-test",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Evidence reviewed"), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13),
        )
    )
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._model = "vision-test"
    provider._system_prompt = "Review supplied evidence"
    provider._timeout = 10
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    attachments = [
        {
            "filename": "image.png",
            "media_type": "image/png",
            "data": base64.b64encode(PNG).decode(),
        },
        {
            "filename": "document.pdf",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf()).decode(),
        },
    ]
    result = await provider.execute_agent(
        ExecutionContext("review", "Review", tmp_path, metadata={"attachments": attachments})
    )
    assert result.success, result.error
    content = create.await_args.kwargs["messages"][1]["content"]
    assert [item["type"] for item in content] == ["text", "image_url", "file"]
    assert content[2]["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_storage_profiles_require_secret_references_and_verified_activation(tmp_path):
    objects = {}

    class Store:
        def put(self, bucket, key, content, content_type=""):
            objects[(bucket, key)] = content

        def get(self, bucket, key):
            return objects.get((bucket, key))

        def delete(self, bucket, key):
            objects.pop((bucket, key), None)

    app = StudioService(tmp_path, artifact_options={"store_factory": lambda profile: Store()})
    try:
        with pytest.raises(ValueError, match="environment"):
            app.artifacts.save_profile(
                {
                    "name": "Bad",
                    "kind": "s3",
                    "bucket": "evidence",
                    "options": {"secret_key": "raw-secret"},
                }
            )
        profile = app.artifacts.save_profile(
            {
                "name": "Cloud bucket",
                "kind": "s3",
                "bucket": "evidence",
                "options": {"secret_key": {"env": "STORAGE_SECRET"}},
            }
        )
        with pytest.raises(ValueError, match="Test"):
            app.artifacts.activate(profile["id"])
        assert app.artifacts.test(profile["id"])["verified"] and objects == {}
        app.artifacts.activate(profile["id"])
        record = save(app, b"Bucket evidence")
        assert record["data"]["store_id"] == profile["id"]
        app.artifacts.activate("local")
        assert app.artifacts.get(record) == b"Bucket evidence"
    finally:
        app.close()

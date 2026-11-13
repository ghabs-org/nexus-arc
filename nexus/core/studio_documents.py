"""Bounded document extraction shared by knowledge indexing and previews."""

import io
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree


class DocumentExtractionError(ValueError):
    """A bounded extractor can safely explain its known document limitation."""


def extract_document(filename, content, pdf_extractor=None):
    suffix = PurePosixPath(filename).suffix.lower()
    if len(content) > 16 * 1024 * 1024:
        raise DocumentExtractionError("Document exceeds 16 MiB")
    if suffix == ".pdf":
        if pdf_extractor is None:
            raise ValueError("PDF extractor is not configured")
        result = pdf_extractor(content)
        if result.get("truncated"):
            raise DocumentExtractionError(
                "PDF exceeds 50 pages or reaches the 128000-character extraction limit; split it into smaller documents"
            )
        return [
            {"page": i + 1, "text": text}
            for i, text in enumerate(result.get("page_texts", [result["text"]]))
            if text.strip()
        ]
    if suffix in {".docx", ".pptx", ".xlsx"}:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
                raise DocumentExtractionError("Office document expands beyond extraction limits")
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError("Encrypted Office documents are not supported")
            names = sorted(
                entry.filename
                for entry in entries
                if (
                    entry.filename == "word/document.xml"
                    or entry.filename.startswith("ppt/slides/slide")
                    and entry.filename.endswith(".xml")
                    or entry.filename == "xl/sharedStrings.xml"
                    or entry.filename.startswith("xl/worksheets/sheet")
                    and entry.filename.endswith(".xml")
                )
            )
            pages, total = [], 0
            for name in names:
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                    raise ValueError("Office XML entities are not allowed")
                tree = ElementTree.fromstring(raw)
                text = " ".join(
                    node.text
                    for node in tree.iter()
                    if node.tag.rsplit("}", 1)[-1] in {"t", "v"} and node.text
                )
                total += len(text)
                if total > 256000:
                    raise DocumentExtractionError("Document text exceeds 256000 characters")
                pages.append({"page": len(pages) + 1, "text": text})
            return pages
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Choose a text, PDF or Office document") from exc
    if "\x00" in text or len(text) > 256000:
        raise DocumentExtractionError("Text must be UTF-8 and at most 256000 characters")
    return [{"page": 1, "text": text}]

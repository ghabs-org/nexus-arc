"""Bounded subprocess for optional PDF text extraction; reads stdin, writes JSON."""

import io
import json
import sys


def main():
    try:
        import resource
    except ImportError:
        resource = None
    if resource:
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    from pypdf import PdfReader

    content = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024:
        raise ValueError("PDF too large")
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF")
    selected = int(sys.argv[1]) if len(sys.argv) == 2 else None
    if selected is not None and selected < 1:
        raise ValueError("Page must be a positive integer")
    if selected is not None and selected > len(reader.pages):
        print(json.dumps({"pages": len(reader.pages), "page_unavailable": True}))
        return
    text = []
    length = 0
    pages = reader.pages[selected - 1 : selected] if selected else reader.pages[:50]
    for page in pages:
        value = (page.extract_text() or "")[: 128000 - length]
        text.append(value)
        length += len(value)
        if length >= 128000:
            break
    print(
        json.dumps(
            {
                "text": "\n\n".join(text),
                "page_texts": text,
                "pages": len(reader.pages),
                "truncated": (selected is None and len(reader.pages) > len(text))
                or length >= 128000,
            }
        )
    )


if __name__ == "__main__":
    main()

"""Verify the real embedding adapter against the local Postman compatibility service.

Start the checked-in simulation first, then run:
PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_vectors.py \
  --url http://127.0.0.1:49641/embeddings/v1
The fixture model names deliberately select synthetic responses, not real models.
"""

import argparse
import base64
import tempfile
import time

from nexus.core.studio import StudioService
from nexus.core.studio_embeddings import StudioEmbeddings
from nexus.core.studio_runtime import RuntimeServiceUnavailableError


def exercise(url):
    from urllib.parse import urlparse

    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This synthetic consumer only accepts a loopback simulation URL")
    config = {"kind": "local", "url": url, "allow_private": True, "model": "synthetic"}
    with tempfile.TemporaryDirectory(prefix="nexus-vector-example-") as root:
        studio = StudioService(root, knowledge_options={"embeddings": StudioEmbeddings(config)})
        try:
            project = studio.knowledge.save({"name": "Synthetic vector sources"}, None)
            for name, text in (
                ("Returns", "Refund policy allows returns."),
                ("Invoices", "Invoice payment policy."),
            ):
                artifact = studio.catalog.save(
                    "artifacts",
                    {
                        "name": name,
                        "data": {
                            "filename": name + ".md",
                            "content_base64": base64.b64encode(text.encode()).decode(),
                        },
                    },
                )
                document = studio.knowledge.attach(
                    project["id"], {"artifact_id": artifact["id"]}, None
                )
                for _ in range(500):
                    run = studio.run(document["run_id"])
                    if run["status"] not in {"queued", "running"}:
                        assert run["status"] == "completed", run
                        break
                    time.sleep(0.01)
                else:
                    raise TimeoutError("Local indexing did not finish")
            first = studio.knowledge.search(project["id"], "refund", None, "semantic")
            assert first["items"][0]["filename"] == "Returns.md"
            assert first["retrieval"]["document_cache_misses"] == 2
            second = studio.knowledge.search(project["id"], "invoice", None, "hybrid")
            assert second["items"][0]["filename"] == "Invoices.md"
            assert second["retrieval"]["document_cache_hits"] == 2
            for model in ("unavailable", "html-error"):
                studio.knowledge.embeddings = StudioEmbeddings({**config, "model": model})
                fallback = studio.knowledge.search(project["id"], "refund", None, "semantic")
                assert fallback["mode"] == "lexical" and "Local embedding" in fallback["notice"]
                studio.knowledge.embeddings = StudioEmbeddings(
                    {**config, "kind": "api", "model": model}
                )
                try:
                    studio.knowledge.search(project["id"], "refund", None, "semantic")
                except RuntimeServiceUnavailableError:
                    pass
                else:
                    raise AssertionError("API failure silently changed search method")
            for model in ("unauthorized", "malformed"):
                studio.knowledge.embeddings = StudioEmbeddings({**config, "model": model})
                try:
                    studio.knowledge.search(project["id"], "refund", None, "semantic")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Invalid provider response silently fell back")
            print(
                "Real HTTP adapter: cached document vectors, ranking, local fallback and API/auth/invalid-data errors verified."
            )
        finally:
            studio.close()


def exercise_unavailable(url, expect_timeout=False):
    from urllib.parse import urlparse

    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Unavailability checks only accept loopback endpoints")
    with tempfile.TemporaryDirectory(prefix="nexus-vector-failure-") as root:
        studio = StudioService(root)
        try:
            project = studio.knowledge.save({"name": "Failure fixture"}, None)
            for kind in ("local", "api"):
                studio.knowledge.embeddings = StudioEmbeddings(
                    {"url": url, "allow_private": True, "kind": kind, "model": "synthetic"}
                )
                start = time.monotonic()
                if kind == "local":
                    found = studio.knowledge.search(project["id"], "refund", None, "semantic")
                    assert found["mode"] == "lexical" and "Local embedding" in found["notice"]
                else:
                    try:
                        studio.knowledge.search(project["id"], "refund", None, "semantic")
                    except RuntimeServiceUnavailableError:
                        pass
                    else:
                        raise AssertionError("API availability failure silently fell back")
                elapsed = time.monotonic() - start
                if expect_timeout:
                    assert elapsed >= 9, "Endpoint failed before exercising its timeout"
                assert elapsed < 12, "Embedding request exceeded its bounded timeout"
            print(
                "Actual endpoint unavailability: bounded local fallback and explicit API failure verified."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--unavailable-url")
    parser.add_argument("--expect-timeout", action="store_true")
    args = parser.parse_args()
    exercise(args.url) if args.url else exercise_unavailable(
        args.unavailable_url, args.expect_timeout
    )

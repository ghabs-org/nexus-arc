"""Consume portable knowledge packages across two disposable Nexus workspaces.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge.py
No provider, credentials or cloud embeddings are used.
"""

import base64
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_knowledge_packages import StudioKnowledgePackages


def ready(studio, project):
    for _ in range(500):
        documents = studio.knowledge.documents(project["id"], None)
        if documents and all(doc["status"] == "ready" for doc in documents):
            return documents
        if any(doc["status"] == "failed" for doc in documents):
            raise ValueError("Document extraction failed")
        time.sleep(0.01)
    raise TimeoutError("Local indexing did not finish")


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-knowledge-example-") as directory:
        source = StudioService(Path(directory) / "source")
        target = StudioService(Path(directory) / "target")
        try:
            artifact = source.catalog.save(
                "artifacts",
                {
                    "name": "Policy",
                    "data": {
                        "filename": "policy.md",
                        "content_base64": base64.b64encode(
                            b"Refunds available within thirty days."
                        ).decode(),
                    },
                },
            )
            project = source.knowledge.save(
                {
                    "name": "Policies",
                    "instructions": "Cite the policy",
                    "retrieval_mode": "lexical",
                },
                None,
            )
            source.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
            ready(source, project)
            package = StudioKnowledgePackages(source).export(project["id"], None)
            imported = StudioKnowledgePackages(target).import_package(package, None)
            documents = ready(target, imported)
            assert documents[0]["artifact_id"] != artifact["id"]
            found = target.knowledge.search(imported["id"], "Refunds", None)
            assert found["items"][0]["artifact_revision"] == 1
            assert imported["instructions"] == "Cite the policy"
            print("Portable pinned document, fresh ownership/IDs and local retrieval verified.")
        finally:
            source.close()
            target.close()


if __name__ == "__main__":
    main()

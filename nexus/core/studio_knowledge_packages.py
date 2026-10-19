"""Bounded, explicit knowledge portability; bytes are data, never installation hooks."""

import base64
import hashlib
import json
import uuid

FORMAT = "nexus-knowledge-package/v1"
PACKAGE_BYTES = 16 * 1024 * 1024
ENCODED_BYTES = 24 * 1024 * 1024
SETTINGS = {"name", "instructions", "default_agent_id", "retrieval_mode"}


class StudioKnowledgePackages:
    def __init__(self, studio):
        self.studio = studio

    def export(self, identity, actor):
        studio = self.studio
        with studio.lock:
            project = studio.knowledge.get(identity, actor)
            documents = studio.knowledge.documents(identity, actor)
            if len(documents) > 100:
                raise ValueError("Export at most 100 documents per package")
            files, size = [], 0
            for document in documents:
                current = studio.catalog.get("artifacts", document["artifact_id"], archived=True)
                studio.collaboration.owned(current, actor)
                artifact = studio.artifacts.revision(current["id"], document["artifact_revision"])
                studio.collaboration.owned(artifact, actor)
                size += artifact["data"]["size"]
                if size > PACKAGE_BYTES:
                    raise ValueError("Knowledge package exceeds 16 MiB of document bytes")
                content = studio.artifacts.get(artifact)
                files.append(
                    {
                        "filename": document["filename"],
                        "content_base64": base64.b64encode(content).decode(),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "source": {"artifact_id": artifact["id"], "revision": artifact["revision"]},
                    }
                )
            return {
                "format": FORMAT,
                "project": {
                    key: project.get(key, "lexical" if key == "retrieval_mode" else None)
                    for key in sorted(SETTINGS)
                },
                "documents": files,
            }

    def preflight(self, package):
        """Validate the entire package before creating any target records."""
        if (
            not isinstance(package, dict)
            or set(package) != {"format", "project", "documents"}
            or package.get("format") != FORMAT
        ):
            raise ValueError("Choose a supported Nexus knowledge package")
        try:
            if (
                len(json.dumps(package, ensure_ascii=False, allow_nan=False).encode())
                > ENCODED_BYTES
            ):
                raise ValueError("Knowledge package exceeds 24 MiB encoded size")
        except (TypeError, OverflowError, RecursionError):
            raise ValueError("Knowledge package must contain JSON data") from None
        settings = package["project"]
        if not isinstance(settings, dict) or set(settings) - SETTINGS:
            raise ValueError("Unsupported knowledge project settings")
        settings = self.studio.knowledge.settings(settings)
        documents = package["documents"]
        if not isinstance(documents, list) or len(documents) > 100:
            raise ValueError("Import at most 100 documents per package")
        files, total = [], 0
        for document in documents:
            if not isinstance(document, dict) or set(document) != {
                "filename",
                "content_base64",
                "sha256",
                "source",
            }:
                raise ValueError("Each document requires filename, content, SHA-256 and source")
            source = document["source"]
            if (
                not isinstance(source, dict)
                or set(source) != {"artifact_id", "revision"}
                or not isinstance(source["artifact_id"], str)
                or not 1 <= len(source["artifact_id"]) <= 200
                or type(source["revision"]) is not int
                or source["revision"] < 1
            ):
                raise ValueError("Invalid document provenance")
            # Reuse artifact validation, including filename traversal, encoding
            # and individual size limits. Source IDs are never target IDs.
            value = self.studio.catalog.validate(
                "artifacts", {key: document[key] for key in ("filename", "content_base64")}
            )
            content = base64.b64decode(value["content_base64"])
            total += len(content)
            if total > PACKAGE_BYTES:
                raise ValueError("Knowledge package exceeds 16 MiB of document bytes")
            if hashlib.sha256(content).hexdigest() != document["sha256"]:
                raise ValueError("Document SHA-256 does not match its content")
            files.append(value)
        return settings, files

    def import_package(self, package, actor):
        studio = self.studio
        with studio.lock:
            settings, files = self.preflight(package)
            created = []
            try:
                # Complete all storage writes before publishing the project or
                # dispatching index jobs. Only newly generated IDs are touched.
                for value in files:
                    identity = uuid.uuid4().hex
                    created.append(identity)
                    studio.catalog.save(
                        "artifacts",
                        {
                            "id": identity,
                            "name": value["filename"],
                            "requester_nexus_id": actor["nexus_id"] if actor else None,
                            "data": value,
                        },
                    )
                project = studio.knowledge.save(settings, actor)
            except Exception:
                for identity in created:
                    studio.catalog._path("artifacts", identity).unlink(missing_ok=True)
                raise
            # The existing durable indexing service owns extraction/recovery.
            # Runtime dispatch errors are surfaced, never retried as a new import.
            for identity in created:
                studio.knowledge.attach(project["id"], {"artifact_id": identity}, actor)
            return project

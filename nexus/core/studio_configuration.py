"""Reviewable configuration releases with preflight and recoverable file journals."""

import copy
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import yaml

from nexus.core.studio_catalog import KINDS, StudioCatalog
from nexus.core.studio_graph import StudioGraph
from nexus.core.studio_knowledge import owner
from nexus.core.yaml_loader import YamlWorkflowLoader


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ConfigurationPreflightError(ValueError):
    """All failing graph paths, reusable by configuration import diagnostics."""

    def __init__(self, failures):
        super().__init__(str(failures[0][1]))
        self.paths = tuple(path for path, _ in failures)


class StudioConfiguration:
    def __init__(self, studio):
        self.studio = studio
        self.journal_dir = studio.state / "configuration-journals"
        self.journal_dir.mkdir(mode=0o700, exist_ok=True)
        self.recover()

    def authorize(self, actor):
        if self.studio.team and (not actor or actor.get("role") != "owner"):
            raise PermissionError("Workspace configuration requires owner role")

    def export(self, actor):
        self.authorize(actor)
        definitions = [
            {"collection": kind, "id": item["id"], "document": item["document"]}
            for kind in ("agents", "workflows")
            for item in self.studio.definitions(kind)
            if item.get("document")
        ]
        resources = [
            {
                "kind": kind,
                "id": item["id"],
                "name": item["name"],
                "description": item["description"],
                "data": item["data"],
            }
            for kind in KINDS
            if kind != "artifacts"
            for item in self.studio.catalog.list(kind)
        ]
        return {
            "format": "nexus-studio-config/v1",
            "definitions": definitions,
            "resources": resources,
        }

    def convert_experiment(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_experiment_import import convert_experiment

        return convert_experiment(self.studio, data)

    def export_experiment(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_experiment_export import export_experiment

        return export_experiment(self.studio, data)

    def convert_experiment_handoff(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_experiment_export import convert_handoff

        return convert_handoff(self.studio, data)

    def convert_evaluations(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_evaluation_import import convert_package

        report = convert_package(data)
        report["document"] = self.normalize(report["document"])
        return report

    def export_evaluations(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_evaluation_export import export_package

        return export_package(self.studio, data)

    def convert_evaluation_handoff(self, data, actor):
        self.authorize(actor)
        from nexus.core.studio_evaluation_export import convert_handoff

        report = convert_handoff(data)
        report["document"] = self.normalize(report["document"])
        return report

    def convert_interop(self, data, actor):
        from nexus.core.studio_interop_configuration import StudioPortableConfiguration

        return StudioPortableConfiguration(self).convert(data, actor)

    def export_interop(self, data, actor):
        from nexus.core.studio_interop_configuration import StudioPortableConfiguration

        return StudioPortableConfiguration(self).export(data, actor)

    def convert_manifest(self, data, actor):
        from nexus.core.studio_manifest import StudioManifestTranslator

        return StudioManifestTranslator(self).convert(data, actor)

    def export_manifest(self, data, actor):
        from nexus.core.studio_manifest import StudioManifestTranslator

        return StudioManifestTranslator(self).export(data, actor)

    def normalize(self, document):
        if (
            not isinstance(document, dict)
            or set(document) - {"format", "definitions", "resources"}
            or document.get("format") != "nexus-studio-config/v1"
        ):
            raise ValueError("Choose a nexus-studio-config/v1 document")
        definitions, resources = document.get("definitions", []), document.get("resources", [])
        if (
            not isinstance(definitions, list)
            or not isinstance(resources, list)
            or len(definitions) + len(resources) > 100
        ):
            raise ValueError("A configuration release supports at most 100 resources")
        from nexus.core.studio import _identity

        definitions = copy.deepcopy(definitions)
        ids, overlay, catalog_overlay = set(), {}, {}
        for item in definitions:
            if (
                not isinstance(item, dict)
                or set(item) != {"collection", "id", "document"}
                or item["collection"] not in {"agents", "workflows"}
            ):
                raise ValueError("Definitions require collection, id and document")
            identity = _identity(item["id"])
            key = (item["collection"], identity)
            if key in ids:
                raise ValueError("Duplicate configuration definition")
            ids.add(key)
            doc = item["document"]
            if not isinstance(doc, dict) or set(doc) - {
                "apiVersion",
                "kind",
                "metadata",
                "spec",
                "graph",
                "steps",
            }:
                raise ValueError("Unsupported definition fields")
            overlay[key] = doc
        targets = {(kind, identity) for kind, identity in ids} | {
            (kind, item["id"])
            for kind in ("agents", "workflows")
            for item in self.studio.definitions(kind)
        }
        normalized = []
        for item in resources:
            if not isinstance(item, dict) or set(item) - {
                "kind",
                "id",
                "name",
                "description",
                "data",
            }:
                raise ValueError("Unsupported configuration resource fields")
            kind, identity = item.get("kind"), _identity(item.get("id"))
            if kind not in KINDS or kind == "artifacts" or (kind, identity) in ids:
                raise ValueError(
                    "Duplicate or unsupported resource collection; binary artifacts are exported separately"
                )
            ids.add((kind, identity))
            if not isinstance(item.get("name"), str) or not 1 <= len(item["name"]) <= 200:
                raise ValueError("Configuration resources require a name up to 200 characters")
            data = self.studio.catalog.validate(kind, item.get("data"), targets or None)
            if kind in {"connectors", "entrypoints", "externalagents", "schedules"}:
                # Imported external dependencies always need an explicit local
                # setup check and enable action after the reviewed release.
                data["enabled"] = False
            record = {**item, "data": data}
            normalized.append(record)
            catalog_overlay[(kind, identity)] = record
        # Validators see prospective definitions and instruction resources, but
        # preflight never writes state, registers tools or invokes providers.
        shadow = copy.copy(self.studio)
        shadow.definition = lambda kind, identity: (
            overlay[(kind, identity)]
            if (kind, identity) in overlay
            else self.studio.definition(kind, identity)
        )
        shadow.catalog = StudioCatalog(shadow)
        shadow.catalog.get = lambda kind, identity, **kwargs: (
            catalog_overlay[(kind, identity)]
            if (kind, identity) in catalog_overlay
            else self.studio.catalog.get(kind, identity, **kwargs)
        )
        shadow.graph = StudioGraph(shadow)
        for item in definitions:
            doc = item["document"]
            if item["collection"] == "agents":
                if doc.get("kind") != "Agent" or not isinstance(doc.get("spec"), dict):
                    raise ValueError("Agent definitions require kind Agent and a spec")
                spec = doc["spec"]
                allowed = {
                    "agent_type",
                    "purpose",
                    "instructions",
                    "provider",
                    "model",
                    "effort",
                    "timeout_seconds",
                    "max_retries",
                    "tool_ids",
                    "skill_ids",
                    "guardrails",
                }
                from nexus.core.studio_agent_settings import FIELDS

                allowed |= FIELDS
                if set(spec) - allowed or spec.get("max_retries", 0) != 0:
                    raise ValueError(
                        "Unsupported agent settings; import cannot discard fields or enable implicit retries"
                    )
                timeout = spec.get("timeout_seconds", 600)
                if type(timeout) is not int or not 1 <= timeout <= 3600:
                    raise ValueError("Agent timeout must be between one and 3600 seconds")
                shadow._definition_document(
                    "agents",
                    {
                        **spec,
                        "id": item["id"],
                        "name": doc.get("metadata", {}).get("name"),
                        "instructions": spec.get("purpose") or spec.get("instructions"),
                    },
                )
            elif "graph" in doc:
                if doc.get("kind") != "Workflow":
                    raise ValueError("Workflow definitions require kind Workflow")
                doc["graph"] = shadow.graph.validate(doc["graph"])
            else:
                YamlWorkflowLoader.load_from_dict(doc)
        # Validate complete named-workflow semantics before publication, including
        # references to new children in the same release. Preparation is read-only.
        workflow_snapshots = {
            i["id"]: i["document"]
            for i in self.studio.definitions("workflows")
            if i.get("document")
        }
        workflow_snapshots.update(
            {identity: doc for (kind, identity), doc in overlay.items() if kind == "workflows"}
        )
        agent_snapshots = self.studio.versions.snapshots()
        agent_snapshots.update(
            {identity: doc["spec"] for (kind, identity), doc in overlay.items() if kind == "agents"}
        )
        failures = []
        for index, item in enumerate(definitions):
            graph = item["document"].get("graph", {})
            if item["collection"] == "workflows" and graph.get("version") == 2:
                try:
                    shadow.graph.structured.prepare(
                        graph,
                        {
                            "workflow_id": item["id"],
                            "task": "",
                            "agent_snapshots": agent_snapshots,
                            "workflow_snapshots": workflow_snapshots,
                        },
                        validate_input=False,
                    )
                except (ValueError, FileNotFoundError) as exc:
                    failures.append((f"/definitions/{index}/graph", exc))
        if failures:
            raise ConfigurationPreflightError(failures)
        return {
            "format": "nexus-studio-config/v1",
            "definitions": definitions,
            "resources": normalized,
        }

    def current(self, document):
        result = {}
        for item in document["definitions"]:
            path = self.studio.root / item["collection"] / (item["id"] + ".yaml")
            if path.is_symlink() or not path.resolve().is_relative_to(self.studio.root):
                raise ValueError("Configuration definitions must be ordinary workspace files")
            result[f"{item['collection']}:{item['id']}"] = (
                path.read_text() if path.exists() else None
            )
        for item in document["resources"]:
            path = self.studio.catalog._path(item["kind"], item["id"])
            result[f"{item['kind']}:{item['id']}"] = (
                self.studio._read(path) if path.exists() else None
            )
        return result

    def validate(self, document, actor):
        self.authorize(actor)
        normalized = self.normalize(document)
        with self.studio.lock:
            current = self.current(normalized)
        return {
            "valid": True,
            "changes": [
                {"resource": key, "action": "create" if value is None else "update"}
                for key, value in current.items()
            ],
            "document": normalized,
        }

    def plan(self, document, actor):
        self.authorize(actor)
        return self._plan(document, actor)

    def _plan(self, document, actor, *, records=None, metadata=None, enabled=()):
        """Trusted framework consumers share the same preflight/journal machinery."""
        normalized = self.normalize(document)
        for item in normalized["resources"]:
            if item["kind"] + ":" + item["id"] in enabled:
                if "enabled" not in item["data"]:
                    raise ValueError("Only external resources support explicit activation")
                item["data"]["enabled"] = True
        with self.studio.lock:
            current = self.current(normalized)
            plan = {
                "id": uuid.uuid4().hex,
                "owner_id": owner(actor),
                "document": normalized,
                "base": current,
                "digest": digest([normalized, current]),
            }
            if records:
                plan["record_updates"] = records
                plan["record_base"] = {
                    key: self.studio._read(self.studio._path(*key.split(":", 1)))
                    if self.studio._path(*key.split(":", 1)).exists()
                    else None
                    for key in records
                }
            if metadata:
                plan["metadata"] = metadata
            self.studio._write(self.studio._path("configuration-plans", plan["id"]), plan)
        return {
            "id": plan["id"],
            "digest": plan["digest"],
            "changes": [
                {"resource": key, "action": "create" if value is None else "update"}
                for key, value in current.items()
            ],
            "document": normalized,
            "external_resources_enabled": False,
        }

    def restore(self, journal):
        # Restore file definitions first. SQL resource changes either committed
        # with the active release marker or rolled back with its transaction.
        for item in journal["document"]["definitions"]:
            key = f"{item['collection']}:{item['id']}"
            path = self.studio.root / item["collection"] / (item["id"] + ".yaml")
            before = journal["base"][key]
            new = yaml.safe_dump(item["document"], sort_keys=False, allow_unicode=True)
            if path.is_symlink() or not path.resolve().is_relative_to(self.studio.root):
                raise ValueError("Release recovery found a linked definition; review its journal")
            if path.exists() and path.read_text() not in {before, new}:
                raise ValueError(
                    "Definition changed outside the interrupted release; review its journal"
                )
            if before is None:
                path.unlink(missing_ok=True)
            else:
                self.replace_file(path, before)
        if not self.studio.sql_state:
            for item in journal["document"]["resources"]:
                path = self.studio.catalog._path(item["kind"], item["id"])
                previous = journal["base"][f"{item['kind']}:{item['id']}"]
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    self.studio._write(path, previous)
            for key, previous in journal.get("record_base", {}).items():
                path = self.studio._path(*key.split(":", 1))
                current = self.studio._read(path) if path.exists() else None
                if current == previous:
                    continue
                if path.exists() and self.studio._read(path) not in [
                    previous,
                    journal["record_updates"][key],
                ]:
                    raise ValueError("Managed state changed outside interrupted release")
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    self.studio._write(path, previous)

    def recover(self):
        with self.studio.lock:
            for path in self.journal_dir.glob("*.json"):
                journal = json.loads(path.read_text())
                if journal.get("status") != "applying":
                    continue
                release_path = self.studio._path("configuration-releases", journal["id"])
                committed = (
                    release_path.exists()
                    and self.studio._read(release_path).get("status") == "active"
                )
                if not committed:
                    self.restore(journal)
                journal["status"] = "active" if committed else "recovered"
                self.studio._write(path, journal)
            self.studio.operations.reconcile_connectors()

    @staticmethod
    def replace_file(path, text):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(text)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def apply(self, identity, checksum, actor):
        self.authorize(actor)
        return self._apply(identity, checksum, actor)

    def _apply(self, identity, checksum, actor):
        journal = None
        committed = False
        try:
            with self.studio.lock:
                plan = self.studio._read(self.studio._path("configuration-plans", identity))
                if plan["owner_id"] != owner(actor) or plan["digest"] != checksum:
                    raise PermissionError(
                        "Review this account's configuration plan before applying"
                    )
                if digest([plan["document"], self.current(plan["document"])]) != checksum:
                    raise ValueError("Workspace changed; generate and review a new plan")
                for key, expected in plan.get("record_base", {}).items():
                    path = self.studio._path(*key.split(":", 1))
                    current = self.studio._read(path) if path.exists() else None
                    if current != expected:
                        raise ValueError("Managed state changed; generate and review a new plan")
                self.normalize(plan["document"])
                journal = {**plan, "status": "applying"}
                self.studio._write(self.journal_dir / (identity + ".json"), journal)
                for item in plan["document"]["resources"]:
                    if item["kind"] in {"schedules", "entrypoints", "evaluations"}:
                        continue
                    self._save_resource(item, plan, actor)
                for item in plan["document"]["definitions"]:
                    path = self.studio.root / item["collection"] / (item["id"] + ".yaml")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.replace_file(
                        path, yaml.safe_dump(item["document"], sort_keys=False, allow_unicode=True)
                    )
                for item in plan["document"]["resources"]:
                    if item["kind"] in {"schedules", "entrypoints", "evaluations"}:
                        self._save_resource(item, plan, actor)
                for key, value in plan.get("record_updates", {}).items():
                    self.studio._write(self.studio._path(*key.split(":", 1)), value)
                release = {
                    "id": identity,
                    "owner_id": owner(actor),
                    "digest": checksum,
                    "status": "active",
                    "format": "nexus-resource-release/v1",
                    "document": plan["document"],
                    "base": plan["base"],
                    "metadata": plan.get("metadata", {}),
                }
                from nexus.core.studio import _now

                release["created_at"] = _now()
                self.studio._write(self.studio._path("configuration-releases", identity), release)
                if self.studio.sql_state:

                    def finalize():
                        journal["status"] = "active"
                        try:
                            self.studio._write(self.journal_dir / (identity + ".json"), journal)
                        except OSError:
                            release["recovery_journal_pending"] = True

                    def rollback_files():
                        try:
                            self.restore(journal)
                            journal["status"] = "rolled-back"
                            self.studio._write(self.journal_dir / (identity + ".json"), journal)
                            self.studio.operations.reconcile_connectors()
                        except (OSError, ValueError):
                            # Keep an applying journal for guarded recovery after restart.
                            release["recovery_journal_pending"] = True

                    self.studio.lock.on_transaction_end(
                        committed=finalize, rolled_back=rollback_files
                    )
            committed = True
            if self.studio.sql_state:
                return release
            journal["status"] = "active"
            try:
                self.studio._write(self.journal_dir / (identity + ".json"), journal)
            except OSError:
                return {**release, "recovery_journal_pending": True}
            return release
        except BaseException:
            if journal and not committed:
                with self.studio.lock:
                    self.restore(journal)
                    journal["status"] = "rolled-back"
                    self.studio._write(self.journal_dir / (identity + ".json"), journal)
                    self.studio.operations.reconcile_connectors()
            raise

    def _save_resource(self, item, plan, actor):
        previous = plan["base"][f"{item['kind']}:{item['id']}"]
        self.studio.catalog.save(
            item["kind"],
            {
                **item,
                "revision": previous["revision"] if previous else 0,
                "requester_nexus_id": (previous.get("owner_id") if previous else None)
                or (actor["nexus_id"] if actor else None),
            },
        )

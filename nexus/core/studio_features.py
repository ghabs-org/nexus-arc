"""Additional Studio surfaces; identity always comes from the host policy."""

from nexus.core.studio_knowledge import owner


class StudioFeatures:
    def __init__(self, studio):
        self.studio = studio

    def get(self, parts, query, actor):
        studio = self.studio
        if parts == ["overview"]:
            from nexus.core.studio_overview import StudioOverview

            return StudioOverview(studio).summary(actor)
        if parts == ["assistant"]:
            question = query.get("question", [""])[0]
            return studio.assistant.answer(question, actor=actor)
        if parts in (["files"], ["files", "preview"]):
            root, path = query.get("root", ["files"])[0], query.get("path", [""])[0]
            method = (
                studio.workspace_files.preview
                if len(parts) == 2
                else studio.workspace_files.listing
            )
            return method(root, path, actor)
        if len(parts) == 3 and parts[0] == "external-runs" and parts[2] == "callbacks":
            return studio.external.callbacks.status(parts[1], actor)
        if len(parts) == 3 and parts[0] == "entrypoints" and parts[2] == "a2a-deliveries":
            return studio.a2a.push.health(
                parts[1],
                actor,
                limit=int(query.get("limit", ["50"])[0]),
                after=query.get("after", [None])[0],
            )
        if len(parts) == 3 and parts[0] == "connectors" and parts[2] == "oauth":
            return studio.oauth.status(parts[1], actor)
        if len(parts) == 3 and parts[0] == "builds" and parts[2] == "lifecycle":
            return studio.resource_lifecycle.inspect_build(parts[1], actor)
        if parts == ["workflow-policy"]:
            from nexus.core.studio_workflow_policy import DEFAULT_LIMITS, HARD_LIMITS

            return {
                "version": 1,
                "effective": dict(studio.workflow_limits),
                "defaults": DEFAULT_LIMITS,
                "ceilings": HARD_LIMITS,
                "configuration": "Administrator server configuration; queued runs pin their limits",
            }
        if len(parts) == 3 and parts[0] == "prompts" and parts[2] == "compare":
            from nexus.core.studio_prompts import StudioPromptHistory

            return StudioPromptHistory(studio).compare(
                parts[1], int(query.get("revision", ["0"])[0])
            )
        if len(parts) == 2 and parts[0] == "schedules":
            from nexus.core.studio_schedules import StudioSchedules

            return StudioSchedules(studio).details(parts[1])
        if parts == ["projects"]:
            return {
                "items": studio.knowledge.list(actor),
                "retrieval_options": studio.knowledge.retrieval_options(),
            }
        if len(parts) == 2 and parts[0] == "projects":
            return studio.knowledge.get(parts[1], actor, True)
        if len(parts) == 3 and parts[0] == "projects":
            if parts[2] == "documents":
                return {"items": studio.knowledge.documents(parts[1], actor)}
            if parts[2] == "folders":
                return studio.knowledge.folders.status(parts[1], actor)
            if parts[2] == "search":
                return studio.knowledge.search(
                    parts[1], query.get("q", [""])[0], actor, query.get("mode", ["lexical"])[0]
                )
            if parts[2] == "sessions":
                studio.knowledge.get(parts[1], actor)
                return {
                    "items": [
                        r
                        for r in studio.records("sessions", limit=None)
                        if r.get("knowledge_project_id") == parts[1]
                        and (not studio.team or studio.team.visible(r, actor))
                    ]
                }
            if parts[2] == "export":
                project = studio.knowledge.get(parts[1], actor)
                return {
                    "format": "nexus-knowledge-project/v1",
                    "project": {
                        **{k: project[k] for k in ("name", "instructions", "default_agent_id")},
                        "retrieval_mode": project.get("retrieval_mode", "lexical"),
                    },
                    "documents": [
                        {k: d[k] for k in ("artifact_id", "artifact_revision", "filename")}
                        for d in studio.knowledge.documents(parts[1], actor)
                    ],
                }
            if parts[2] == "package":
                from nexus.core.studio_knowledge_packages import StudioKnowledgePackages

                return StudioKnowledgePackages(studio).export(parts[1], actor)
        if parts == ["feedback"]:
            return {"items": studio.collaboration.list_feedback(actor)}
        if parts == ["sharing"]:
            return {
                "public_enabled": studio.collaboration.sharing_enabled(),
                "items": [
                    {k: v for k, v in r.items() if k not in {"token_hash", "messages"}}
                    for r in studio.records("conversation-shares", limit=None)
                    if r["owner_id"] == owner(actor)
                ],
            }
        if len(parts) == 2 and parts[0] == "shares":
            return studio.collaboration.get_share(parts[1], actor)
        if len(parts) == 4 and parts[0] == "shares" and parts[2] == "artifacts":
            return studio.collaboration.shared_artifact(parts[1], parts[3], actor)
        if parts == ["schema"]:
            return studio.schema
        if parts == ["speech"]:
            return studio.speech.available()
        if parts == ["tools"]:
            return {
                "items": studio.data_tools.inventory()["items"]
                + studio.research.inventory()["items"]
                + studio.media.inventory()["items"]
                + studio.dynamic_embeds.inventory()["items"]
                + studio.rulesets.inventory()["items"]
                + studio.assistant.inventory()["items"]
            }
        if parts == ["integrations"]:
            return studio.recipes.list()
        if parts == ["configuration", "export"]:
            return studio.configuration.export(actor)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "retry":
            return studio.retry.describe(parts[1], actor)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "rerun-step":
            return studio.step_rerun.describe(parts[1], actor)
        if parts == ["workspace-proposals"]:
            return studio.workspace_proposals.list(query.get("run_id", [""])[0], actor)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "execution":
            return studio.execution.snapshot(
                parts[1],
                actor,
                limit=int(query.get("limit", ["500"])[0]),
                before=query.get("before", [None])[0],
            )
        raise FileNotFoundError("Studio feature route not found")

    def post(self, parts, data, actor):
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "retry":
            return 202, self.studio.retry.queue(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "rerun-step":
            return 202, self.studio.step_rerun.queue(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "workspace-proposals" and parts[2] in {"apply", "reject"}:
            return 200, self.studio.workspace_proposals.decide(parts[1], parts[2], actor)
        if len(parts) == 4 and parts[0] == "external-runs" and parts[2:] == ["callbacks", "revoke"]:
            return 200, self.studio.external.callbacks.revoke(parts[1], actor)
        if len(parts) == 4 and parts[0] == "connectors" and parts[2:] == ["oauth", "disconnect"]:
            return 200, self.studio.oauth.disconnect(parts[1], actor)
        studio = self.studio
        if parts == ["assistant", "answer"]:
            return 200, studio.assistant.answer(data.get("question", ""), actor)
        if parts == ["datasets", "parse"]:
            from nexus.core.studio_datasets import parse_dataset

            if studio.team:
                studio.team.authorize(actor, "/api/catalog/datasets")
            return 200, parse_dataset(
                {key: value for key, value in data.items() if key not in {"requester_nexus_id", "_role"}}
            )
        if len(parts) == 2 and parts[0] == "files" and parts[1] in {"upload", "folder", "snapshot"}:
            return 201, getattr(studio.workspace_files, parts[1])(data, actor)
        if parts == ["original-document"]:
            from nexus.core.studio_nuclia_documents import queue_original

            selection = {
                key: value
                for key, value in data.items()
                if key not in {"requester_nexus_id", "_role"}
            }
            return 202, queue_original(studio, selection, actor)
        if len(parts) == 3 and parts[0] == "builds" and parts[2] == "review":
            return 200, studio.builder.review(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "builds" and parts[2] in {"refresh", "delete"}:
            return 200, getattr(studio.builder.maintenance, parts[2])(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "builds" and parts[2] == "health":
            from nexus.core.studio_resource_health import StudioResourceHealth

            return 200, StudioResourceHealth(studio.resource_lifecycle).check(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "builds" and parts[2] in {"plan", "transition"}:
            action = (
                studio.resource_lifecycle.plan_build
                if parts[2] == "plan"
                else studio.resource_lifecycle.transition_build
            )
            return 200, action(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "integrations" and parts[2] == "configure":
            return 201, studio.recipes.configure(parts[1], data, actor)
        if parts == ["configuration", "interop", "convert"]:
            request = {
                key: value
                for key, value in data.items()
                if key not in {"requester_nexus_id", "_role"}
            }
            return 200, studio.configuration.convert_interop(request, actor)
        if parts == ["configuration", "interop", "export"]:
            request = {
                key: value
                for key, value in data.items()
                if key not in {"requester_nexus_id", "_role"}
            }
            return 200, studio.configuration.export_interop(request, actor)
        if parts == ["configuration", "manifest", "convert"]:
            request = {
                key: value
                for key, value in data.items()
                if key not in {"requester_nexus_id", "_role"}
            }
            return 200, studio.configuration.convert_manifest(request, actor)
        if parts == ["configuration", "manifest", "export"]:
            request = {
                key: value
                for key, value in data.items()
                if key not in {"requester_nexus_id", "_role"}
            }
            return 200, studio.configuration.export_manifest(request, actor)
        if parts == ["configuration", "validate"]:
            return 200, studio.configuration.validate(data.get("document"), actor)
        if parts == ["configuration", "plan"]:
            return 200, studio.configuration.plan(data.get("document"), actor)
        if parts == ["configuration", "apply"]:
            return 201, studio.configuration.apply(data.get("plan_id"), data.get("digest"), actor)
        if parts == ["speech"]:
            return 202, studio.speech.queue(data, actor)
        if len(parts) == 3 and parts[0] == "runs" and parts[2] in {"cancel", "input"}:
            studio.collaboration.owned(studio.run(parts[1]), actor)
            if parts[2] == "cancel":
                return 202, studio.cancel_run(parts[1], actor)
            return 202, studio.graph.structured.resume_input(
                parts[1], data.get("values"), checkpoint=data.get("evaluation_checkpoint")
            )
        if parts == ["projects"]:
            return 201, studio.knowledge.save(data, actor)
        if parts == ["projects", "import"]:
            if data.get("format") == "nexus-knowledge-package/v1":
                from nexus.core.studio_knowledge_packages import StudioKnowledgePackages

                return 201, StudioKnowledgePackages(studio).import_package(data, actor)
            if data.get("format") != "nexus-knowledge-project/v1":
                raise ValueError("Choose a Nexus knowledge project export")
            project = data.get("project")
            if not isinstance(project, dict):
                raise ValueError("Project configuration is required")
            documents = data.get("documents", [])
            if not isinstance(documents, list) or len(documents) > 100:
                raise ValueError("Import at most 100 document references")
            # Resolve all references before saving any project state.
            for doc in documents:
                if not isinstance(doc, dict):
                    raise ValueError("Document references must be objects")
                artifact = studio.catalog.get("artifacts", doc.get("artifact_id"))
                studio.collaboration.owned(artifact, actor)
                pinned = studio.artifacts.revision(artifact["id"], doc.get("artifact_revision"))
                studio.collaboration.owned(pinned, actor)
            saved = studio.knowledge.save(
                {
                    **{k: project.get(k) for k in ("name", "instructions", "default_agent_id")},
                    "retrieval_mode": project.get("retrieval_mode", "lexical"),
                },
                actor,
            )
            for doc in documents:
                studio.knowledge.attach(saved["id"], doc, actor)
            return 201, saved
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "documents":
            return 202, studio.knowledge.attach(parts[1], data, actor)
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "folders":
            return 201, studio.knowledge.folders.link(parts[1], data, actor)
        if len(parts) == 5 and parts[0] == "projects" and parts[2:5:2] == ["folders", "unlink"]:
            return 200, studio.knowledge.folders.unlink(parts[1], parts[3], actor)
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "sync":
            return 202, studio.knowledge.folders.sync(parts[1], actor)
        if (
            len(parts) == 5
            and parts[0] == "projects"
            and parts[2] == "documents"
            and parts[4] == "reindex"
        ):
            return 202, studio.knowledge.retry(parts[1], parts[3], actor)
        if parts == ["feedback"]:
            return 201, studio.collaboration.feedback(data, actor)
        if parts == ["feedback", "promote"]:
            if studio.team:
                studio.team.authorize(actor, "/api/catalog/evaluations")
            return 201, studio.collaboration.promote(data, actor)
        if parts == ["sharing"]:
            return 200, studio.collaboration.configure(data, actor)
        if parts == ["shares"]:
            return 201, studio.collaboration.share(data, actor)
        if len(parts) == 3 and parts[0] == "shares" and parts[2] == "revoke":
            return 200, studio.collaboration.revoke(parts[1], actor)
        if parts == ["design"]:
            if studio.team:
                studio.team.authorize(actor, "/api/catalog/prompts")
            if data.get("design_kind") not in {"prompt", "schedule"}:
                raise ValueError("Choose prompt or schedule design")
            if (
                not isinstance(data.get("conversation", ""), str)
                or len(data.get("conversation", "")) > 16000
            ):
                raise ValueError("Design conversation must be text up to 16000 characters")
            return 202, studio.start_run(
                {
                    **data,
                    "kind": "design",
                    "requester_nexus_id": actor["nexus_id"] if actor else None,
                }
            )
        if parts == ["schedules", "preview"]:
            from datetime import UTC, datetime

            from nexus.core.studio_calendar import next_occurrence

            after = datetime.now(UTC)
            items = []
            for _ in range(5):
                after = next_occurrence(data.get("cron"), data.get("timezone", "UTC"), after)
                items.append(after.isoformat())
            return 200, {"items": items}
        if len(parts) == 3 and parts[0] == "prompts" and parts[2] == "restore":
            if studio.team:
                studio.team.authorize(actor, "/api/catalog/prompts")
            from nexus.core.studio_prompts import StudioPromptHistory

            return 200, StudioPromptHistory(studio).restore(parts[1], data)
        if len(parts) == 3 and parts[0] == "schedules" and parts[2] == "enabled":
            if studio.team:
                studio.team.authorize(actor, "/api/catalog/schedules")
            from nexus.core.studio_schedules import StudioSchedules

            return 200, StudioSchedules(studio).enabled(parts[1], data)
        raise FileNotFoundError("Studio feature route not found")

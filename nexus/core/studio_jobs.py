"""Background delegation, audio transcription and typed-decision jobs for Studio."""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import math
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path

from nexus.adapters.transcription.base import TranscriptionInput
from nexus.core.studio_catalog import _text

AUDIO_LIMIT = 4 * 1024 * 1024
AUDIO_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}


class StudioJobs:
    def __init__(self, workbench, transcription_factory=None, decision_client=None):
        self.workbench = workbench
        self.studio = workbench.studio
        self.transcription_factory = transcription_factory
        self.decision_client = decision_client
        if not self.studio.sql_state:
            self.cleanup_audio()

    def cleanup_audio(self):
        """Remove unfinished upload bytes after shutdown/restart; never replay audio jobs."""
        for record in self.studio.records("audio-inputs", limit=None):
            if record.get("format") not in AUDIO_FORMATS:
                continue
            path = self.studio._path("audio-bytes", record["id"], record["format"])
            if path.exists():
                path.unlink()
                record.update(claimed=True, interrupted=True)
                self.studio._write(self.studio._path("audio-inputs", record["id"]), record)

    def prepare(self, payload):
        kind = payload.get("kind")
        actor = self.workbench.actor(payload)
        if kind == "delegation":
            identities = payload.get("agent_ids")
            if (
                not isinstance(identities, list)
                or not 1 <= len(identities) <= 4
                or any(not isinstance(identity, str) for identity in identities)
                or len(set(identities)) != len(identities)
            ):
                raise ValueError("Choose 1–4 distinct saved agents")
            for identity in identities:
                self.studio.definition("agents", identity)
            if payload.get("strategy") not in {"sequential", "parallel", "automatic"}:
                raise ValueError("Choose sequential or parallel delegation")
            if payload.get("strategy") == "automatic":
                if payload.get("default_agent_id") not in identities:
                    raise ValueError("Choose an explicit fallback from the selected specialists")
                from nexus.core.studio import _PROVIDERS

                if payload.get("coordinator_provider", "codex") not in {
                    item[0] for item in _PROVIDERS
                }:
                    raise ValueError("Choose a configured coordinator provider")
            _text(payload.get("task"), "Delegation task", 16000)
        elif kind == "external":
            profile = self.studio.external.profile(payload.get("external_agent_id"))
            _text(payload.get("task"), "External agent task", 16000)
            if payload.get("external_resume_id"):
                previous = self.workbench.visible(
                    self.studio._read(
                        self.studio._path("external-runs", payload["external_resume_id"])
                    ),
                    actor,
                    own=True,
                )
                self.studio.external.assert_binding(profile, previous)
        elif kind == "decision":
            _text(payload.get("task"), "Decision input", 16000)
            _text(payload.get("instructions"), "Decision instructions", 4000)
            options = payload.get("options")
            if not isinstance(options, dict) or not 2 <= len(options) <= 12:
                raise ValueError("Provide 2–12 named decision options")
            for name, description in options.items():
                if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
                    raise ValueError("Use short alphanumeric option names")
                _text(description, "Option description", 1000)
            threshold = payload.get("threshold", 0.6)
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold)
                or not 0 <= threshold <= 1
            ):
                raise ValueError("Confidence threshold must be between 0 and 1")
            if payload.get("default") not in options:
                raise ValueError("Choose a default from the listed options")
            if payload.get("backend") not in {"openrouter", "opencode", "openai", "typesafe"}:
                raise ValueError("Choose OpenRouter, OpenCode, OpenAI or TypeSafe for typed decisions")
            if payload.get("model") and (
                not isinstance(payload["model"], str) or len(payload["model"]) > 200
            ):
                raise ValueError("Model ID must be at most 200 characters")
        elif kind == "transcription":
            record = self.workbench.visible(
                self.studio._read(self.studio._path("audio-inputs", payload.get("input_id"))),
                actor,
                own=True,
            )
            if record.get("claimed"):
                raise ValueError("This audio upload was already queued")
            payload["task"] = "Transcribe " + record["filename"]
            language = payload.get("language")
            if language and (
                not isinstance(language, str) or not re.fullmatch(r"[a-zA-Z-]{2,20}", language)
            ):
                raise ValueError("Use a short language code, or leave it empty")
        elif kind == "campaign":
            self.workbench.campaigns.prepare(payload)

    def upload_transcription(self, payload, actor):
        filename = _text(payload.get("filename"), "Audio filename", 200)
        if (
            Path(filename).name != filename
            or "\\" in filename
            or any(ord(char) < 32 for char in filename)
        ):
            raise ValueError("Use an audio filename without directories")
        extension = Path(filename).suffix.lstrip(".").lower()
        if extension not in AUDIO_FORMATS:
            raise ValueError("Unsupported audio format")
        encoded = payload.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > (AUDIO_LIMIT + 2) // 3 * 4:
            raise ValueError("Audio upload must be no larger than 4 MiB")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid audio encoding") from exc
        if not content or len(content) > AUDIO_LIMIT:
            raise ValueError("Choose a nonempty audio file no larger than 4 MiB")
        identity = uuid.uuid4().hex
        path = self.studio._path("audio-bytes", identity, extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        record = {
            "id": identity,
            "owner_id": self.workbench.owner(actor),
            "filename": filename,
            "format": extension,
            "size": len(content),
            "claimed": False,
        }
        self.studio._write(self.studio._path("audio-inputs", identity), record)
        try:
            return self.studio.start_run(
                {
                    "kind": "transcription",
                    "input_id": identity,
                    "language": payload.get("language"),
                    "requester_nexus_id": self.workbench.owner(actor),
                }
            )
        except BaseException:
            path.unlink(missing_ok=True)
            self.studio._path("audio-inputs", identity).unlink(missing_ok=True)
            raise

    async def transcription(self, run_id, payload):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.core.studio_workbench import now

        actor = self.workbench.actor(payload)
        with self.studio.lock:
            record = self.workbench.visible(
                self.studio._read(self.studio._path("audio-inputs", payload["input_id"])),
                actor,
                own=True,
            )
            if record.get("claimed"):
                raise ValueError("This audio upload was already consumed")
            record["claimed"] = True
            self.studio._write(self.studio._path("audio-inputs", record["id"]), record)
        path = self.studio._path("audio-bytes", record["id"], record["format"])
        try:
            if self.transcription_factory:
                provider = self.transcription_factory(self.workbench.owner(actor))
                if inspect.isawaitable(provider):
                    provider = await provider
            else:
                key = (
                    self.studio.team.environment(self.workbench.owner(actor), "openai")[
                        "OPENAI_API_KEY"
                    ]
                    if self.studio.team
                    else os.getenv("OPENAI_API_KEY")
                )
                if not key:
                    raise ValueError(
                        "Whisper transcription requires an OpenAI API key; CLI subscription login is not used"
                    )
                provider = AdapterRegistry().create_transcription("whisper", api_key=key)
            if not await provider.check_availability():
                raise ValueError("Transcription provider is not ready. Check its API setup")
            self.studio._update_run(run_id, provider="whisper-api")
            result = await asyncio.wait_for(
                provider.transcribe(
                    TranscriptionInput(
                        source=path.read_bytes(),
                        format=record["format"],
                        language=payload.get("language") or None,
                    )
                ),
                timeout=300,
            )
            data = asdict(result)
            data.update(
                id=run_id,
                owner_id=self.workbench.owner(actor),
                filename=record["filename"],
                updated_at=now(),
            )
            self.studio._write(self.studio._path("transcriptions", run_id), data)
            return result.text
        except Exception:
            raise RuntimeError(
                "Audio transcription failed. Check your own OpenAI API setup and audio format; CLI subscriptions do not cover this API"
            ) from None
        finally:
            path.unlink(missing_ok=True)

    async def delegate(self, run_id, payload):
        from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
        from nexus.agents.parallel import ParallelAgent
        from nexus.agents.sequential import SequentialAgent

        studio = self.studio
        failures = []

        def configured_agent(identity):
            pinned = payload.get("agent_snapshots", {}).get(identity)
            return pinned if pinned is not None else studio.definition("agents", identity)["spec"]

        class Specialist(BaseAgent):
            async def run(self, context):
                task = context.task
                if context.prior_outputs:
                    task += "\nPrior specialist outputs:\n" + context.prior_summary()
                studio.execution.record(run_id, "delegation", agent_id=self.name, status="running")
                status = "failed"
                try:
                    request = {**payload, "agent_id": self.name}
                    configured = configured_agent(self.name)
                    if (
                        context.metadata.get("studio_router_model")
                        and not configured.get("model")
                        and not payload.get("model")
                    ):
                        request["model"] = context.metadata["studio_router_model"]
                    result = await studio._invoke(request, task, run_id)
                    status = "completed"
                except asyncio.CancelledError:
                    status = "interrupted"
                    failures.append(self.name)
                    raise
                finally:
                    studio.execution.record(run_id, "delegation", agent_id=self.name, status=status)
                return AgentOutput(content=result.output)

        from nexus.core.studio_agent_settings import routing_description

        children = [
            Specialist(
                name=identity,
                description=routing_description(configured_agent(identity)),
            )
            for identity in payload["agent_ids"]
        ]
        cls = ParallelAgent if payload["strategy"] == "parallel" else SequentialAgent
        if payload["strategy"] == "automatic":
            from nexus.core.studio_coordination import StudioCoordinator

            group = StudioCoordinator(studio, children, payload, run_id)
        else:
            group = cls(name="studio-delegation", sub_agents=children, timeout=600)
        result = await group.run(
            AgentContext(
                task=payload["task"],
                metadata={
                    "requester": {
                        "source": "studio",
                        "user_id": str(payload.get("requester_nexus_id") or "desktop"),
                    }
                },
            )
        )
        if failures or result.metadata.get("timeout"):
            raise RuntimeError("A delegated specialist timed out")
        self.studio._update_run(
            run_id,
            delegated_agents=[result.metadata["coordinator_selected_agent"]]
            if payload["strategy"] == "automatic"
            else payload["agent_ids"],
            candidate_agents=payload["agent_ids"],
            delegation_strategy=payload["strategy"],
        )
        outputs = result.metadata.get("sequential_outputs")
        return (
            "\n\n".join(
                f"{name}\n{output}"
                for name, output in zip(payload["agent_ids"], outputs, strict=True)
            )
            if outputs
            else result.content
        )

    def decision_credentials(self, payload):
        backend = payload["backend"]
        if self.studio.team:
            self.studio.team.assert_execution(payload.get("requester_nexus_id"))
            config = self.studio.team.decision_keys.get(payload["requester_nexus_id"], {})
            if config.get("backend") != backend:
                raise ValueError("This decision backend is not configured for your Nexus user")
            key = os.getenv(config["key_env"])
            model = payload.get("model") or config.get("model")
        else:
            key = os.getenv(
                {
                    "openrouter": "OPENROUTER_API_KEY",
                    "opencode": "OPENCODE_API_KEY",
                    "openai": "OPENAI_API_KEY",
                    "typesafe": "TYPESAFE_API_KEY",
                }[backend]
            )
            if not key and backend == "opencode":
                try:
                    from nexus.adapters.ai.opencode_provider import get_opencode_auth

                    auth = get_opencode_auth()
                    if auth:
                        key = auth[1]
                except Exception:
                    pass
            model = payload.get("model")
        if not key:
            raise ValueError("Selected decision service needs its configured API key")
        from nexus.adapters.decisions.jev import (
            DECISIONS_URL,
            DEFAULT_FREE_MODEL,
            DEFAULT_MODEL,
            SYSTEMONE_URL,
        )

        if backend == "openai":
            from nexus.adapters.decisions.openai import (
                DECISIONS_URL as NATIVE_URL,
            )
            from nexus.adapters.decisions.openai import (
                DEFAULT_MODEL as NATIVE_MODEL,
            )

            return {"api_key": key, "model": model or NATIVE_MODEL, "endpoint": NATIVE_URL}

        if backend == "typesafe":
            from nexus.adapters.decisions.typesafe import (
                DEFAULT_MODEL as TYPESAFE_MODEL,
                SYSTEMONE_URL as TYPESAFE_URL,
            )

            return {"api_key": key, "model": model or TYPESAFE_MODEL, "endpoint": TYPESAFE_URL}

        return {
            "api_key": key,
            "model": model or (DEFAULT_MODEL if backend == "openrouter" else DEFAULT_FREE_MODEL),
            "endpoint": DECISIONS_URL if backend == "openrouter" else SYSTEMONE_URL,
        }

    async def decision(self, run_id, payload, record_id=None):
        from nexus.adapters.decisions.jev import ask, choice_question, parse_choice
        from nexus.core.studio_workbench import now

        actor = self.workbench.actor(payload)
        kwargs = self.decision_credentials(payload) if not self.decision_client else {}
        if payload.get("backend") == "typesafe":
            from nexus.adapters.decisions.typesafe import ask as typesafe_ask

            default_client = typesafe_ask
        else:
            default_client = ask
        client = self.decision_client or default_client
        self.studio._update_run(run_id, provider="decision-" + payload["backend"])
        questions = {"route": choice_question(payload["instructions"], payload["options"])}
        answers = await asyncio.to_thread(client, payload["task"], questions, **kwargs)
        if inspect.isawaitable(answers):
            answers = await answers
        reason = "Provider choice accepted"
        try:
            result = parse_choice(answers.get("route"), default=payload["default"])
            valid = (
                isinstance(answers.get("route"), dict)
                and answers["route"].get("choice") in payload["options"]
                and result.choice in payload["options"]
                and math.isfinite(result.confidence)
                and 0 <= result.confidence <= 1
            )
        except (ValueError, TypeError, AttributeError):
            valid, result = False, None
        confident = valid and result.confidence >= payload.get("threshold", 0.6)
        if not confident:
            reason = "No valid decision or confidence below threshold; explicit default used"
        choice = result.choice if confident else payload["default"]
        data = {
            "id": record_id or run_id,
            "owner_id": self.workbench.owner(actor),
            "updated_at": now(),
            "input": payload["task"],
            "options": payload["options"],
            "choice": choice,
            "confidence": result.confidence if valid else 0,
            "accepted": bool(confident),
            "threshold": payload.get("threshold", 0.6),
            "reason": reason,
            "backend": payload["backend"],
        }
        self.studio._write(self.studio._path("decisions", record_id or run_id), data)
        return json.dumps(data, indent=2)

    async def execute(self, run_id, payload):
        kind = payload["kind"]
        if kind == "external":
            return await self.studio.external.execute(run_id, payload)
        if kind == "delegation":
            return await self.delegate(run_id, payload)
        if kind == "transcription":
            return await self.transcription(run_id, payload)
        if kind == "decision":
            return await self.decision(run_id, payload)
        return await self.workbench.campaigns.execute(run_id, payload)

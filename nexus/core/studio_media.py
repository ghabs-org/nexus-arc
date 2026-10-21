"""Explicit image/audio API tools over Nexus identity, artifacts and tool receipts."""

import asyncio
import base64
import contextvars
import hashlib
import importlib.util
import io
import json
import re
import uuid
import warnings
import wave
from copy import deepcopy

from nexus.core.studio_connections import execution_identity
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_remote import remote_headers, validate_destination, validate_remote

media_profile_revisions = contextvars.ContextVar("studio_media_profile_revisions", default=None)

ACTIONS = {"image_generate", "image_edit", "image_describe", "audio_describe"}
IMAGE_LIMIT = 16 * 1024 * 1024
BILLING = "Separate media API account or selected local endpoint; coding subscriptions do not cover API requests"


def image_info(content):
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("Install nexus-arc[studio-images] for validated image tools") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if (
                    image.format not in {"PNG", "JPEG", "WEBP"}
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError
                width, height = image.size
                if not 0 < width <= 8000 or not 0 < height <= 8000 or width * height > 40_000_000:
                    raise ValueError
                kind = image.format
                image.verify()
                with Image.open(io.BytesIO(content)) as decoded:
                    decoded.load()
                return {
                    "mime": {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[kind],
                    "width": width,
                    "height": height,
                }
    except Exception:
        raise ValueError("Image is invalid, unsupported or exceeds pixel limits") from None


def validate_audio(content):
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            frames, rate, channels, width = (
                audio.getnframes(),
                audio.getframerate(),
                audio.getnchannels(),
                audio.getsampwidth(),
            )
            if (
                not 1 <= channels <= 2
                or not 8000 <= rate <= 192000
                or not 1 <= width <= 4
                or not 0 < frames <= rate * 120
                or audio.getcomptype() != "NONE"
            ):
                raise ValueError
            if len(audio.readframes(frames)) != frames * channels * width:
                raise ValueError
    except Exception:
        raise ValueError("Audio must be complete PCM WAV within duration/channel limits") from None


class StudioMedia:
    def __init__(self, studio, profiles=None):
        from nexus.tools import tool_registry

        self.studio, self.profiles, self.names = studio, {}, {}
        if profiles is not None and (not isinstance(profiles, dict) or len(profiles) > 32):
            raise ValueError("Media profiles must be a mapping of at most 32 entries")
        for name, raw in (profiles or {}).items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                or not isinstance(raw, dict)
            ):
                raise ValueError("Media profile needs a short ID and object settings")
            if set(raw) - {
                "action",
                "model",
                "enabled",
                "url",
                "allow_private",
                "auth",
                "token_env",
                "timeout",
            }:
                raise ValueError("Unsupported media profile settings")
            if raw.get("action") not in ACTIONS or type(raw.get("enabled", False)) is not bool:
                raise ValueError("Media profiles require an action and explicit boolean enabling")
            model, timeout = raw.get("model"), raw.get("timeout", 120)
            if not isinstance(model, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", model
            ):
                raise ValueError("Select an explicit media model")
            if type(timeout) not in (int, float) or not 1 <= timeout <= 180:
                raise ValueError("Media timeout must be from 1 to 180 seconds")
            auth = raw.get(
                "auth", {"type": "bearer", "token_env": raw.get("token_env", "OPENAI_API_KEY")}
            )
            if not isinstance(auth, dict) or auth.get("type", "none") not in {
                "none",
                "bearer",
                "basic",
                "api-key",
            }:
                raise ValueError("Media profiles use explicit header authentication references")
            remote = validate_remote(
                {
                    "url": raw.get("url", "https://api.openai.com/v1"),
                    "allow_private": raw.get("allow_private", False),
                    "auth": auth,
                }
            )
            self.profiles[name] = {
                **remote,
                "action": raw["action"],
                "model": model,
                "enabled": raw.get("enabled", False),
                "timeout": timeout,
            }
        namespace = hashlib.sha256(str(studio.state).encode()).hexdigest()[:12]
        for action in sorted(ACTIONS):

            def bind(selected):
                async def invoke(**arguments):
                    return await self.execute(selected, arguments)

                return invoke

            function = bind(action)
            name = f"studio:{namespace}:{action}"
            tool_registry.register(
                name,
                function,
                category="Data and media",
                description=(
                    f"{action}: explicitly select profile and prompt; edit/describe also require artifact_id, optional revision. "
                    "Returns private artifacts or text. Separate API billing, no retry/fallback. Generated artifacts include id/revision for attachment references."
                ),
            )
            self.names[name] = function

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)

    def inventory(self):
        images_available = importlib.util.find_spec("PIL") is not None
        return {
            "items": [
                {
                    "id": name,
                    "configured": any(
                        p["enabled"] and (images_available or action == "audio_describe")
                        for p in self.profiles.values()
                        if p["action"] == action
                    ),
                    "billing": BILLING,
                    "setup": "Configure an explicit media profile/model and account, then enable it. Images require studio-images; audio input is PCM WAV.",
                    "profiles": [
                        {"id": identity, "model": p["model"], "enabled": p["enabled"]}
                        for identity, p in self.profiles.items()
                        if p["action"] == action
                    ],
                }
                for name in self.names
                for action in [name.rsplit(":", 1)[1]]
            ]
        }

    def revisions(self):
        return {
            name: hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
            for name, profile in self.profiles.items()
        }

    def pin_tool(self, payload):
        if payload.get("kind") != "tool" or payload.get("tool") not in self.names:
            return
        args = dict(payload.get("kwargs", {}))
        actor = (
            {
                "nexus_id": payload.get("requester_nexus_id"),
                "role": self.studio.team.bindings.get(payload.get("requester_nexus_id")),
            }
            if self.studio.team
            else None
        )
        for identity_key, revision_key in (
            ("artifact_id", "revision"),
            ("mask_artifact_id", "mask_revision"),
        ):
            if args.get(identity_key) and revision_key not in args:
                try:
                    record = self.studio.collaboration.owned(
                        self.studio.catalog.get("artifacts", args[identity_key]), actor
                    )
                except (ValueError, PermissionError, FileNotFoundError):
                    raise ValueError("Media input is unavailable") from None
                args[revision_key] = record["revision"]
        payload["kwargs"] = args

    def assert_profile(self, identity, profile):
        revisions = media_profile_revisions.get()
        if (
            self.profiles.get(identity) != profile
            or not profile["enabled"]
            or (revisions is not None and revisions.get(identity) != self.revisions().get(identity))
        ):
            raise ValueError("Media configuration changed; review and submit a new request")

    def actor(self):
        if not self.studio.team:
            return None
        identity = execution_identity.get()
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    def fence(self, actor):
        if self.studio.team:
            self.studio.team.assert_execution(actor["nexus_id"])
        run_id = current_run_id.get()
        if run_id:
            run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
            if run.get("cancel_requested") or run.get("status") not in {"running", "queued"}:
                raise asyncio.CancelledError

    def source(self, identity, revision, actor, audio=False):
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("Media revision must be a positive integer")
        try:
            current = self.studio.collaboration.owned(
                self.studio.catalog.get("artifacts", identity), actor
            )
            record = self.studio.collaboration.owned(
                self.studio.artifacts.revision(current["id"], revision), actor
            )
        except (FileNotFoundError, PermissionError, ValueError):
            raise ValueError("Media input is unavailable") from None
        maximum = 4 * 1024 * 1024 if audio else IMAGE_LIMIT
        if record["data"].get("size", maximum + 1) > maximum:
            raise ValueError("Media input exceeds size limit")
        content = self.studio.artifacts.get(record)
        if len(content) > maximum:
            raise ValueError("Media input exceeds size limit")
        info = None
        if audio:
            validate_audio(content)
        else:
            info = image_info(content)
        return (
            content,
            info,
            {
                "id": record["id"],
                "revision": record["revision"],
                "sha256": record["data"]["sha256"],
            },
        )

    async def execute(self, action, args):
        actor = self.actor()
        if action not in ACTIONS or not isinstance(args, dict):
            raise ValueError("Unknown media operation")
        allowed = {"profile", "prompt"}
        if action != "image_generate":
            allowed |= {"artifact_id", "revision"}
        if action in {"image_generate", "image_edit"}:
            allowed |= {"size", "background"}
        if action == "image_edit":
            allowed |= {"mask_artifact_id", "mask_revision"}
        if set(args) - allowed:
            raise ValueError("Unsupported media arguments")
        profile = deepcopy(self.profiles.get(args.get("profile")))
        if not profile or profile["action"] != action or not profile["enabled"]:
            raise ValueError("Select an enabled media profile for this operation")
        if action != "audio_describe" and importlib.util.find_spec("PIL") is None:
            raise ValueError("Install nexus-arc[studio-images] before invoking image tools")
        self.assert_profile(args.get("profile"), profile)
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
            raise ValueError("Supply a media prompt from 1 to 4000 characters")
        body = {"model": profile["model"]}
        sources, artifact = [], None
        if action in {"image_generate", "image_edit"}:
            size, background = args.get("size", "auto"), args.get("background", "auto")
            if size not in {"auto", "1024x1024", "1024x1536", "1536x1024"} or background not in {
                "auto",
                "opaque",
                "transparent",
            }:
                raise ValueError("Unsupported media size or background")
            body.update(prompt=prompt, n=1, size=size, background=background, output_format="png")
        if action != "image_generate":
            content, info, ref = self.source(
                args.get("artifact_id"),
                args.get("revision"),
                actor,
                audio=action == "audio_describe",
            )
            sources.append(ref)
            encoded = base64.b64encode(content).decode()
            if action == "image_edit":
                body["images"] = [{"image_url": f"data:{info['mime']};base64,{encoded}"}]
                if args.get("mask_artifact_id"):
                    mask, mask_info, mask_ref = self.source(
                        args["mask_artifact_id"], args.get("mask_revision"), actor
                    )
                    if mask_info["mime"] != "image/png" or (
                        mask_info["width"],
                        mask_info["height"],
                    ) != (info["width"], info["height"]):
                        raise ValueError("Editing mask must be PNG with input image dimensions")
                    body["mask"] = {
                        "image_url": "data:image/png;base64," + base64.b64encode(mask).decode()
                    }
                    sources.append(mask_ref)
                elif "mask_revision" in args:
                    raise ValueError("Mask revision requires a mask artifact")
            else:
                part = (
                    {"type": "input_audio", "input_audio": {"data": encoded, "format": "wav"}}
                    if action == "audio_describe"
                    else {
                        "type": "image_url",
                        "image_url": {"url": f"data:{info['mime']};base64,{encoded}"},
                    }
                )
                body.update(
                    messages=[
                        {"role": "user", "content": [{"type": "text", "text": prompt}, part]}
                    ],
                    max_completion_tokens=2048,
                    store=False,
                )
                if action == "audio_describe":
                    body["modalities"] = ["text"]
        path = (
            "/images/generations"
            if action == "image_generate"
            else "/images/edits"
            if action == "image_edit"
            else "/chat/completions"
        )
        url = profile["url"] + path
        self.fence(actor)
        validate_destination(url, profile["allow_private"])
        headers = remote_headers(self.studio, profile, actor["nexus_id"] if actor else None)
        run_id = current_run_id.get()
        if run_id:
            self.studio.execution.record(
                run_id,
                "media-request",
                status="dispatched",
                model=profile["model"],
                provider=args["profile"],
            )
        try:
            response = await asyncio.to_thread(
                self.studio.research.request,
                "POST",
                url,
                headers,
                body,
                private=profile["allow_private"],
                response_limit=24 * 1024 * 1024,
                timeout=profile["timeout"],
            )
            if (
                not isinstance(response, dict)
                or len(json.dumps(response, allow_nan=False)) > 24 * 1024 * 1024
            ):
                raise ValueError
        except Exception:
            raise ValueError(
                "Media provider request failed; no automatic retry or provider fallback. Provider usage may have occurred."
            ) from None
        self.fence(actor)
        self.assert_profile(args["profile"], profile)
        if action in {"image_generate", "image_edit"}:
            data = response.get("data")
            if (
                not isinstance(data, list)
                or len(data) != 1
                or not isinstance(data[0], dict)
                or not isinstance(data[0].get("b64_json"), str)
            ):
                raise ValueError(
                    "Media provider did not return one inline image; result URLs are never fetched"
                )
            try:
                content = base64.b64decode(data[0]["b64_json"], validate=True)
            except ValueError:
                raise ValueError("Media provider returned invalid image data") from None
            if len(content) > IMAGE_LIMIT or image_info(content)["mime"] != "image/png":
                raise ValueError("Generated media exceeds supported PNG limits")
            identity = uuid.uuid4().hex
            with self.studio.lock:
                self.fence(actor)
                self.assert_profile(args["profile"], profile)
                artifact = self.studio.catalog.save(
                    "artifacts",
                    {
                        "id": identity,
                        "name": "Generated image",
                        "requester_nexus_id": actor["nexus_id"] if actor else None,
                        "data": {
                            "filename": "generated-" + identity[:12] + ".png",
                            "content_base64": base64.b64encode(content).decode(),
                        },
                    },
                )
                if run_id:
                    artifact["generated_by_run"] = run_id
                    self.studio._write(self.studio.catalog._path("artifacts", identity), artifact)
                    run = self.studio.run(run_id)
                    parts = list(run.get("structured_content", []))
                    part = {
                        "type": "artifact",
                        "artifact_id": identity,
                        "revision": artifact["revision"],
                        "filename": artifact["data"]["filename"],
                    }
                    if len(parts) < 40:
                        parts.append(part)
                    self.studio._update_run(run_id, content_version=1, structured_content=parts)
                    self.studio.execution.record(
                        run_id,
                        "artifact",
                        status="generated",
                        artifact_id=identity,
                        artifact_revision=artifact["revision"],
                    )
            output = "Image saved as a private artifact"
        else:
            try:
                choices = response["choices"]
                if (
                    not isinstance(choices, list)
                    or len(choices) != 1
                    or choices[0].get("finish_reason") != "stop"
                    or choices[0]["message"].get("refusal")
                ):
                    raise ValueError
                output = choices[0]["message"]["content"]
                if (
                    not isinstance(output, str)
                    or not output.strip()
                    or len(output.encode()) > 32768
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ValueError(
                    "Media provider refused, truncated or returned invalid description text"
                ) from None
        usage = response.get("usage", {})
        usage = (
            {
                key: value
                for key, value in usage.items()
                if key
                in {
                    "prompt_tokens",
                    "completion_tokens",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                }
                and type(value) is int
                and 0 <= value <= 10**9
            }
            if isinstance(usage, dict)
            else {}
        )
        return {
            "output": output,
            "artifacts": (
                [
                    {
                        "id": artifact["id"],
                        "revision": artifact["revision"],
                        "filename": artifact["data"]["filename"],
                    }
                ]
                if artifact
                else []
            ),
            "input_artifacts": sources,
            "profile": args["profile"],
            "model": profile["model"],
            "usage": usage,
            "billing": BILLING,
            "untrusted": True,
        }

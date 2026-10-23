"""Explicit local/API voice profiles, temporary audio and private generated speech."""

import asyncio
import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from copy import deepcopy
from pathlib import Path

from nexus.core.studio_catalog import _text

PROFILE_FORMAT = "nexus-speech-profile/v1"
TEMPORARY_AUDIO = re.compile(r"[0-9a-f]{32}\.(?:webm|wav|mp3|mp4|m4a|ogg)")


def profile_digest(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


async def local_process(*args, **kwargs):
    """Keep temporary files until the bounded child process has actually stopped."""
    worker = asyncio.create_task(asyncio.to_thread(subprocess.run, *args, **kwargs))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break  # Retrieve the child failure below without masking cancellation.
    if cancelled:
        worker.exception()
        raise asyncio.CancelledError
    return worker.result()


DEFAULT_PROFILES = {
    "local-whisper": {"kind": "whisper", "action": "transcribe", "model_path": ""},
    "local-piper": {"kind": "piper", "action": "speak", "model_path": ""},
    "openai-transcribe": {
        "kind": "openai",
        "action": "transcribe",
        "token_env": "OPENAI_API_KEY",
        "model": "whisper-1",
    },
    "openai-speech": {
        "kind": "openai",
        "action": "speak",
        "token_env": "OPENAI_API_KEY",
        "model": "tts-1",
        "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
    },
}


class StudioSpeech:
    def __init__(self, studio, profiles=None, client_factory=None):
        self.studio = studio
        self.profiles = deepcopy({**DEFAULT_PROFILES, **(profiles or {})})
        self.client_factory = client_factory
        self.directory = studio.state / "speech-inputs"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        for name, profile in self.profiles.items():
            if profile.get("kind") not in {"whisper", "piper", "openai"} or profile.get(
                "action"
            ) not in {"transcribe", "speak"}:
                raise ValueError(f"Unsupported speech profile: {name}")

    def available(self):
        result = []
        for name, p in self.profiles.items():
            local = p["kind"] in {"whisper", "piper"}
            ready = Path(p.get("model_path") or "/nonexistent-model").is_file() if local else True
            if p["kind"] == "piper":
                ready = ready and bool(shutil.which("piper"))
            result.append(
                {
                    "id": name,
                    "action": p["action"],
                    "local": local,
                    "model_ready": bool(ready),
                    "voices": p.get("voices", []),
                    "billing": "Local model" if local else "Separate API account and billing",
                    "setup": "Configure an installed model in private runtime speech profiles"
                    if local
                    else "Configure the speech provider API credential in your Nexus account environment",
                }
            )
        return {"items": result}

    def prepare(self, payload):
        """Replace any client snapshot with bounded, credential-reference-only settings."""
        name = payload.get("speech_profile")
        profile = self.profiles.get(name) if isinstance(name, str) else None
        if not isinstance(profile, dict) or set(profile) - {
            "kind",
            "action",
            "model_path",
            "model",
            "token_env",
            "voices",
        }:
            raise ValueError("Choose a configured speech profile with supported settings")
        settings = deepcopy(profile)
        if settings.get("kind") in {"whisper", "piper"}:
            path = settings.get("model_path", "")
            if not isinstance(path, str) or len(path) > 4096 or "\0" in path:
                raise ValueError("Speech model path must be bounded text")
            settings["model_path"] = str(Path(path).expanduser().resolve()) if path else ""
        else:
            settings.setdefault("token_env", "OPENAI_API_KEY")
        self.validate_settings(settings)
        voice = payload.get("speech_voice")
        if settings["action"] == "speak":
            voices = settings.get("voices", [])
            voice = voice or (voices[0] if voices else None)
            if voices and voice not in voices:
                raise ValueError("Select a configured speech voice")
            if settings["kind"] == "openai" and (
                not isinstance(voice, str) or not 1 <= len(voice) <= 80
            ):
                raise ValueError("Select a configured speech voice")
        language = payload.get("speech_language", "")
        if not isinstance(language, str) or len(language) > 20:
            raise ValueError("Speech language must be text up to 20 characters")
        payload["speech_voice"], payload["speech_language"] = voice, language
        snapshot = {"format": PROFILE_FORMAT, "profile_id": name, "settings": settings}
        payload["_speech_profile_snapshot"] = {**snapshot, "digest": profile_digest(snapshot)}

    @staticmethod
    def validate_settings(settings):
        kind, action = settings.get("kind"), settings.get("action")
        if (
            kind not in {"whisper", "piper", "openai"}
            or action not in {"speak", "transcribe"}
            or (kind == "whisper" and action != "transcribe")
            or (kind == "piper" and action != "speak")
        ):
            raise ValueError("Unsupported speech profile kind or action")
        voices = settings.get("voices", [])
        if (
            not isinstance(voices, list)
            or len(voices) > 40
            or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in voices)
        ):
            raise ValueError("Speech voices must be a bounded list of names")
        if kind == "openai":
            model, token = settings.get("model"), settings.get("token_env")
            if (
                not isinstance(model, str)
                or not 1 <= len(model) <= 200
                or not isinstance(token, str)
                or len(token) > 128
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)
                or set(settings) - {"kind", "action", "model", "token_env", "voices"}
            ):
                raise ValueError("API speech requires a model and credential environment reference")
        elif (
            set(settings) - {"kind", "action", "model_path", "voices"}
            or not isinstance(settings.get("model_path"), str)
            or len(settings["model_path"]) > 4096
            or "\0" in settings["model_path"]
        ):
            raise ValueError("Local speech requires a bounded model path")

    def pinned_profile(self, payload):
        snapshot = payload.get("_speech_profile_snapshot")
        if snapshot is None:
            raise ValueError("Voice job predates pinned settings; review and submit a new request")
        try:
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != {"format", "profile_id", "settings", "digest"}
                or snapshot["format"] != PROFILE_FORMAT
                or snapshot["profile_id"] != payload.get("speech_profile")
                or not isinstance(snapshot["settings"], dict)
            ):
                raise ValueError
            body = {key: value for key, value in snapshot.items() if key != "digest"}
            if snapshot["digest"] != profile_digest(body):
                raise ValueError
            self.validate_settings(snapshot["settings"])
            return deepcopy(snapshot["settings"])
        except (ValueError, TypeError, RecursionError):
            raise ValueError(
                "Pinned speech settings are invalid; review and submit a new request"
            ) from None

    def queue(self, data, actor):
        # Registration and dispatch share the existing workspace transaction so a
        # second process cannot collect a recording while it is being accepted.
        with self.studio.lock:
            return self._queue(data, actor)

    def _queue(self, data, actor):
        name = data.get("profile")
        profile = self.profiles.get(name) if isinstance(name, str) else None
        if profile is None:
            raise ValueError("Choose a configured speech profile")
        action = profile["action"]
        request = {
            "kind": "speech",
            "speech_profile": data["profile"],
            "task": "Transcribe recorded audio"
            if action == "transcribe"
            else _text(data.get("text"), "Speech text", 3000),
            "requester_nexus_id": actor["nexus_id"] if actor else None,
            "speech_language": str(data.get("language") or "")[:20],
            "speech_voice": data.get("voice"),
        }
        path = None
        if action == "transcribe":
            encoded = data.get("audio_base64")
            if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
                raise ValueError("Choose up to 4 MiB of audio")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Invalid audio encoding") from exc
            if not 1 <= len(content) <= 4 * 1024 * 1024:
                raise ValueError("Choose up to 4 MiB of audio")
            suffix = data.get("format", "webm")
            if suffix not in {"webm", "wav", "mp3", "mp4", "m4a", "ogg"}:
                raise ValueError("Unsupported audio recording format")
            path = self.directory / (uuid.uuid4().hex + "." + suffix)
        else:
            voices = profile.get("voices", [])
            voice = data.get("voice") or (voices[0] if voices else None)
            if voices and voice not in voices:
                raise ValueError("Select a configured speech voice")
            request["speech_voice"] = voice
        created = False
        try:
            if path:
                with path.open("xb") as file:
                    created = True
                    os.chmod(path, 0o600)
                    file.write(content)
                request["speech_input"] = path.name
                self.studio._write(
                    self.studio._path("speech-input-owners", path.stem),
                    {"owner_id": request["requester_nexus_id"] or "desktop", "filename": path.name},
                )
            return self.studio.start_run(request)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
                self.studio._path("speech-input-owners", path.stem).unlink(missing_ok=True)
            raise

    def bind_input(self, run_id, payload):
        """Bind owned input before dispatch while the workspace lock is held."""
        name = payload.get("speech_input")
        if not name and payload["_speech_profile_snapshot"]["settings"]["action"] != "transcribe":
            return
        if not isinstance(name, str) or not TEMPORARY_AUDIO.fullmatch(name):
            raise ValueError("Invalid temporary audio reference")
        path = self.studio._path("speech-input-owners", Path(name).stem)
        record = self.studio._read(path)
        if (
            record.get("owner_id") != (payload.get("requester_nexus_id") or "desktop")
            or record.get("filename") != name
        ):
            raise PermissionError("Audio input belongs to another account")
        if record.get("run_id"):
            raise ValueError("Audio input already belongs to an accepted job")
        self.studio._write(path, {**record, "run_id": run_id})

    def cleanup(self):
        """Collect managed audio only when no accepted or leased job needs it."""
        removed = {"files": 0, "records": 0}
        with self.studio.lock:
            live = {
                run["id"]
                for run in self.studio.records("runs", limit=None)
                if run["status"] in {"queued", "running"}
            } | set(self.studio.active_tasks)
            protected = set()
            store = self.studio.sql_state
            if store:
                with store.transaction() as connection:
                    jobs = connection.execute(
                        store.sa.select(store.jobs).where(
                            store.jobs.c.namespace == store.namespace,
                            store.jobs.c.status.in_(["queued", "running"]),
                        )
                    ).mappings()
                    for job in jobs:
                        if job["status"] == "running" or job["run_id"] in live:
                            live.add(job["run_id"])
                            protected.add(job["payload"].get("speech_input"))
            owners = self.studio.records("speech-input-owners", limit=None)
            for record in owners:
                if record.get("run_id") in live:
                    protected.add(record.get("filename"))
            for path in self.directory.iterdir():
                if (
                    TEMPORARY_AUDIO.fullmatch(path.name)
                    and path.name not in protected
                    and (path.is_symlink() or path.is_file())
                ):
                    path.unlink(missing_ok=True)
                    removed["files"] += 1
            for record in owners:
                name = record.get("filename", "")
                if (
                    isinstance(name, str)
                    and TEMPORARY_AUDIO.fullmatch(name)
                    and name not in protected
                    and not (self.directory / name).exists()
                ):
                    self.studio._path("speech-input-owners", Path(name).stem).unlink(
                        missing_ok=True
                    )
                    removed["records"] += 1
        return removed

    def _client(self, profile, payload):
        if self.client_factory:
            return self.client_factory(profile, payload)
        values = os.environ
        if self.studio.team:
            values, error = self.studio.team.environment_resolver(payload["requester_nexus_id"])
            if error:
                raise PermissionError("Speech account credentials unavailable")
        token = values.get(profile.get("token_env", "OPENAI_API_KEY"))
        if not token:
            raise PermissionError("Configure separate speech API credentials")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ValueError("Install nexus-arc[openai] for API speech") from exc
        return OpenAI(api_key=token, timeout=60, max_retries=0)

    async def execute(self, run_id, payload):
        source = self.directory / payload.get("speech_input", "unused")
        if source.parent != self.directory or source.name != payload.get("speech_input", "unused"):
            raise ValueError("Invalid temporary audio reference")
        try:
            profile = self.pinned_profile(payload)
            if profile["action"] == "transcribe":
                record = self.studio._read(self.studio._path("speech-input-owners", source.stem))
                if (
                    record["owner_id"] != (payload.get("requester_nexus_id") or "desktop")
                    or record["filename"] != source.name
                ):
                    raise PermissionError("Audio input belongs to another account")
                if not source.is_file() or source.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("Audio input expired; record or upload it again")
                if profile["kind"] == "whisper":
                    model = Path(profile.get("model_path") or "")
                    if not model.is_file():
                        raise ValueError(
                            "Configure an installed local Whisper model; automatic model download is disabled"
                        )
                    result = await local_process(
                        [
                            sys.executable,
                            "-m",
                            "nexus.core.studio_speech_worker",
                            str(model.resolve()),
                            str(source),
                            payload["speech_language"],
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=130,
                        check=True,
                    )
                    return json.loads(result.stdout)["text"]
                client = self._client(profile, payload)
                try:
                    content = io.BytesIO(source.read_bytes())
                    content.name = source.name
                    kwargs = {"model": profile["model"], "file": content}
                    if payload["speech_language"]:
                        kwargs["language"] = payload["speech_language"]
                    result = await asyncio.to_thread(client.audio.transcriptions.create, **kwargs)
                    return str(result.text)[:32000]
                finally:
                    client.close()
            if profile["kind"] == "piper":
                model = Path(profile.get("model_path") or "")
                executable = shutil.which("piper")
                if not executable or not model.is_file():
                    raise ValueError(
                        "Configure Piper and an installed voice model for local speech"
                    )
                output = self.directory / (uuid.uuid4().hex + ".wav")
                created = False
                try:
                    with self.studio.lock:
                        with output.open("xb"):
                            created = True
                            os.chmod(output, 0o600)
                        self.studio._write(
                            self.studio._path("speech-input-owners", output.stem),
                            {
                                "filename": output.name,
                                "owner_id": payload.get("requester_nexus_id") or "desktop",
                                "run_id": run_id,
                            },
                        )
                    await local_process(
                        [executable, "--model", str(model.resolve()), "--output_file", str(output)],
                        input=payload["task"].encode(),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=90,
                        check=True,
                    )
                    if output.stat().st_size > 16 * 1024 * 1024:
                        raise ValueError("Generated audio exceeds 16 MiB")
                    content, suffix = output.read_bytes(), "wav"
                finally:
                    if created:
                        output.unlink(missing_ok=True)
                        self.studio._path("speech-input-owners", output.stem).unlink(
                            missing_ok=True
                        )
            else:
                client = self._client(profile, payload)
                try:
                    result = await asyncio.to_thread(
                        client.audio.speech.create,
                        model=profile["model"],
                        voice=payload["speech_voice"],
                        input=payload["task"],
                        response_format="mp3",
                    )
                    content, suffix = result.content, "mp3"
                finally:
                    client.close()
            if len(content) > 16 * 1024 * 1024:
                raise ValueError("Generated audio exceeds 16 MiB")
            artifact = self.studio.catalog.save(
                "artifacts",
                {
                    "id": "speech-" + run_id,
                    "name": "Spoken response",
                    "requester_nexus_id": payload.get("requester_nexus_id"),
                    "data": {
                        "filename": "response." + suffix,
                        "content_base64": base64.b64encode(content).decode(),
                    },
                },
            )
            self.studio._update_run(run_id, speech_artifact_id=artifact["id"])
            return "Speech ready"
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Local speech processing failed; verify installed models and audio format"
            ) from exc
        except (ValueError, PermissionError):
            raise
        except Exception as exc:
            raise ValueError(
                "Speech provider failed; verify account access, model and audio format"
            ) from exc
        finally:
            if source.name != "unused":
                record_path = self.studio._path("speech-input-owners", source.stem)
                if record_path.exists() and self.studio._read(record_path)["owner_id"] == (
                    payload.get("requester_nexus_id") or "desktop"
                ):
                    source.unlink(missing_ok=True)
                    record_path.unlink(missing_ok=True)

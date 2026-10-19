"""Explicit local/API voice profiles, temporary audio and private generated speech."""

import asyncio
import base64
import binascii
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from nexus.core.studio_catalog import _text

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
        self.profiles = {**DEFAULT_PROFILES, **(profiles or {})}
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

    def queue(self, data, actor):
        profile = self.profiles.get(data.get("profile"))
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
            with path.open("xb") as file:
                os.chmod(path, 0o600)
                file.write(content)
            request["speech_input"] = path.name
            self.studio._write(
                self.studio._path("speech-input-owners", path.stem),
                {"owner_id": request["requester_nexus_id"] or "desktop", "filename": path.name},
            )
        else:
            voices = profile.get("voices", [])
            voice = data.get("voice") or (voices[0] if voices else None)
            if voices and voice not in voices:
                raise ValueError("Select a configured speech voice")
            request["speech_voice"] = voice
        try:
            return self.studio.start_run(request)
        except BaseException:
            if path:
                path.unlink(missing_ok=True)
                self.studio._path("speech-input-owners", path.stem).unlink(missing_ok=True)
            raise

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
        profile = self.profiles[payload["speech_profile"]]
        source = self.directory / payload.get("speech_input", "unused")
        if source.parent != self.directory or source.name != payload.get("speech_input", "unused"):
            raise ValueError("Invalid temporary audio reference")
        try:
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
                    result = await asyncio.to_thread(
                        subprocess.run,
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
                try:
                    await asyncio.to_thread(
                        subprocess.run,
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
                    output.unlink(missing_ok=True)
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

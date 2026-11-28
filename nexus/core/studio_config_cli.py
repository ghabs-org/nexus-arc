"""Configuration CLI talks to the running Studio, preserving its locks and auth."""

import json
from pathlib import Path

import yaml

from nexus.core.studio_http_client import StudioHTTPClient


def configuration_command(args):
    client = StudioHTTPClient(args.url, args.session_env)
    try:
        return _execute(args, client.request)
    finally:
        client.close()


def _execute(args, request):
    import os

    invalid_conversion = False
    if args.operation == "convert-interop":
        path = Path(args.file)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Configuration file exceeds one MiB")
        payload = {"text": path.read_text()}
        if getattr(args, "bindings", None):
            binding_path = Path(args.bindings)
            if binding_path.stat().st_size > 65536:
                raise ValueError("Bindings file exceeds 64 KiB")
            payload["bindings"] = json.loads(binding_path.read_text())
        result = request("/api/studio/configuration/interop/convert", payload)
        invalid_conversion = not result["valid"]
    elif args.operation == "export-interop":
        result = request("/api/studio/configuration/interop/export", {})
        invalid_conversion = not result["valid"]
        if result["valid"]:
            result = result["bundle"]
    elif args.operation == "convert-manifest":
        path = Path(args.file)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Manifest file exceeds one MiB")
        payload = {"text": path.read_text()}
        if getattr(args, "env_file", None):
            env_path = Path(args.env_file)
            if env_path.stat().st_size > 65536:
                raise ValueError("Environment file exceeds 64 KiB")
            payload["variables"] = yaml.safe_load(env_path.read_text()) or {}
        if getattr(args, "target", None):
            payload["environment"] = args.target
        result = request("/api/studio/configuration/manifest/convert", payload)
        invalid_conversion = not result["valid"]
    elif args.operation == "export-manifest":
        payload = {}
        if getattr(args, "target", None):
            payload["environment"] = args.target
        if getattr(args, "file", None):
            path = Path(args.file)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Configuration document exceeds one MiB")
            payload["document"] = yaml.safe_load(path.read_text())
        result = request("/api/studio/configuration/manifest/export", payload)
        invalid_conversion = not result.get("valid", True)
    elif args.operation == "export":
        result = request("/api/studio/configuration/export")
    else:
        path = Path(args.file)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Configuration file exceeds one MiB")
        document = yaml.safe_load(path.read_text())
        if isinstance(document, dict) and document.get("format") == "nexus-interop-conversion/v1":
            if document.get("valid") is not True or not isinstance(document.get("document"), dict):
                raise ValueError("Resolve external conversion diagnostics before native review")
            document = document["document"]
        if args.operation in {"validate", "plan"}:
            result = request("/api/studio/configuration/" + args.operation, {"document": document})
            if args.operation == "validate":
                result = {"valid": True, "changes": result["changes"]}
        else:
            if (
                not isinstance(document, dict)
                or not document.get("id")
                or not document.get("digest")
            ):
                raise ValueError("Apply requires a saved, reviewed configuration plan")
            result = request(
                "/api/studio/configuration/apply",
                {"plan_id": document["id"], "digest": document["digest"]},
            )
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as file:
            os.chmod(args.output, 0o600)
            file.write(text)
    else:
        print(text, end="")

    if invalid_conversion:
        raise SystemExit(2)

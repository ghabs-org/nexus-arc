"""Outbound Slack artifact delivery with size and destination verification."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from nexus.adapters.notifications.slack import validate_timestamp

logger = logging.getLogger(__name__)

MAX_ARTIFACT_SIZE = 16 * 1024 * 1024  # 16 MiB per artifact
MAX_TOTAL_SIZE = 32 * 1024 * 1024     # 32 MiB total


def sanitize_filename(name: str) -> str:
    """Sanitize filename to prevent directory traversal or invalid characters."""
    clean = os.path.basename(name).strip()
    if not clean or clean.startswith(".") or not re.match(r"^[A-Za-z0-9_.\-]+$", clean):
        return "artifact.bin"
    return clean[:128]


class SlackArtifacts:
    """Manages outbound delivery of Nexus artifacts to Slack channels and threads."""

    def __init__(self, studio: Any) -> None:
        self.studio = studio

    def get_run_artifacts(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect verified artifact metadata for a completed or active run."""
        artifacts: list[dict[str, Any]] = []
        raw_artifacts = run.get("artifacts") or []
        for item in raw_artifacts:
            art_id = item if isinstance(item, str) else item.get("id")
            if not art_id:
                continue
            path = self.studio._path("artifacts", art_id)
            if not path.exists():
                continue
            meta = {}
            if hasattr(self.studio, "artifacts") and hasattr(self.studio.artifacts, "metadata"):
                try:
                    meta = self.studio.artifacts.metadata(art_id) or {}
                except Exception:
                    pass
            size = path.stat().st_size
            filename = sanitize_filename(meta.get("filename") or meta.get("name") or f"{art_id}.bin")
            artifacts.append({
                "id": art_id,
                "name": filename,
                "size": size,
                "mime_type": meta.get("mime_type", "application/octet-stream"),
                "path": str(path),
            })
        return artifacts

    def upload_artifact(
        self,
        connector_id: str,
        channel: str,
        artifact_id: str,
        thread_ts: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Deliver one outbound artifact to the verified Slack channel/thread."""
        conn_record = self.studio.catalog.get("connectors", connector_id)
        conn_data = conn_record.get("data", {})
        if conn_data.get("type") != "slack" or not conn_data.get("enabled"):
            raise PermissionError("Slack connection is not enabled")
        if conn_data.get("channel") != channel:
            raise PermissionError("Artifact destination channel must match connection")

        if thread_ts:
            validate_timestamp(thread_ts)

        path = self.studio._path("artifacts", artifact_id)
        if not path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id} not found")

        size = path.stat().st_size
        if size > MAX_ARTIFACT_SIZE:
            raise ValueError(f"Artifact exceeds maximum allowed size ({MAX_ARTIFACT_SIZE} bytes)")

        with open(path, "rb") as f:
            content = f.read()

        filename = sanitize_filename(f"{artifact_id}.bin")
        effective_title = title or filename

        # Upload via Slack WebClient or adapter
        token_env = conn_data.get("token_env")
        token = os.environ.get(token_env, "") if token_env else conn_data.get("token", "")
        if not token:
            raise ValueError("Slack token is not configured")

        from slack_sdk import WebClient
        client = WebClient(token=token, retry_handlers=[])

        upload_kwargs: dict[str, Any] = {
            "channel": channel,
            "file": content,
            "filename": filename,
            "title": effective_title,
        }
        if thread_ts:
            upload_kwargs["thread_ts"] = thread_ts

        try:
            if hasattr(client, "files_upload_v2"):
                res = client.files_upload_v2(**upload_kwargs)
            else:
                res = client.files_upload(**upload_kwargs)
            return {
                "ok": True,
                "file_id": res.get("file", {}).get("id") or "uploaded",
                "filename": filename,
                "size": size,
            }
        except Exception as exc:
            logger.error("Slack file upload failed: %s", exc)
            raise ValueError(f"Failed to upload artifact to Slack: {exc}") from exc

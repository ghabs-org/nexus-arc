"""Opt-in private Slack file capture over existing artifacts and channel receipts."""

import base64
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit

import requests

from nexus.core.studio_artifacts import ARTIFACT_LIMIT
from nexus.core.studio_slack_threads import assert_current

API = "https://slack.com/api/files.info"
FORMAT = "nexus-slack-files/v1"
TOTAL_LIMIT = 32 * 1024 * 1024


def download_url(value):
    """Never forward a Slack credential to event URLs, redirects or other hosts."""
    if not isinstance(value, str) or len(value) > 4000 or any(ord(c) < 33 for c in value):
        raise ValueError("Slack file has no supported private download URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "files.slack.com"
        or not parsed.path.startswith("/files-pri/")
        or parsed.fragment
        or "\\" in value
    ):
        raise ValueError("Slack private downloads must use files.slack.com")
    return value


def read(url, token, limit, params=None):
    """Bound both declared and actual bytes; no redirects, proxies or retries."""
    with requests.Session() as client:
        client.trust_env = False
        deadline = time.monotonic() + 30
        with client.get(
            url,
            params=params,
            headers={"Authorization": "Bearer " + token},
            timeout=(5, 15),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise ValueError("Slack file request failed")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise ValueError("Slack file response exceeds its byte limit")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > limit or time.monotonic() > deadline:
                    raise ValueError("Slack file response exceeds its byte limit")
            return bytes(content)


class SlackFiles:
    def __init__(self, studio):
        self.studio = studio

    def capture(self, record, body, event):
        """Keep only signed file IDs and a reviewed connection revision in receipts."""
        files = body["event"].get("files", [])
        if not isinstance(files, list) or not 1 <= len(files) <= 5:
            raise ValueError("Slack file messages must contain one to five files")
        identities = []
        for item in files:
            identity = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identity, str) or not re.fullmatch(r"F[A-Z0-9]{1,95}", identity):
                raise ValueError("Slack attachments require bounded file IDs")
            if identity in identities:
                raise ValueError("Slack file IDs must be distinct")
            identities.append(identity)
        connection = self.connection(record, event)
        return {
            "format": FORMAT,
            "ids": identities,
            "connector_id": connection["id"],
            "connector_revision": connection["revision"],
        }

    def connection(self, record, event):
        assert_current(self.studio, event, record)
        if not record["data"].get("capture_slack_files"):
            raise PermissionError("Slack file capture is disabled")
        connection = self.studio.catalog.get(
            "connectors", record["data"]["notification_connector_id"]
        )
        data = connection["data"]
        if (
            data.get("type") != "slack"
            or not data.get("enabled")
            or data.get("channel") != event["channel"]
        ):
            raise PermissionError(
                "Slack files require an enabled connection in the selected channel"
            )
        return connection

    def authorize(self, event):
        record = self.studio.catalog.get("entrypoints", event["entrypoint_id"])
        connection = self.connection(record, event)
        manifest = event["slack_files"]
        if (
            manifest.get("format") != FORMAT
            or manifest.get("connector_id") != connection["id"]
            or type(manifest.get("connector_revision")) is not int
            or manifest["connector_revision"] != connection["revision"]
        ):
            raise PermissionError("Slack attachment connection changed")
        return connection

    def context(self, event):
        self.authorize(event)
        refs = event.get("slack_artifact_refs", [])
        if len(refs) != len(event["slack_files"]["ids"]):
            raise ValueError("Slack files have not finished private capture")
        return {"artifact_ids": [ref["id"] for ref in refs], "expected_artifact_refs": refs}

    def claim_current(self, event):
        current = self.studio._read(self.studio._path("channel-events", event["id"]))
        if (
            current["status"] != "slack_files_fetching"
            or current.get("slack_file_claim") != event["slack_file_claim"]
        ):
            raise ValueError("Slack capture claim changed; inspect Activity")
        self.authorize(event)

    def prepare(self, identity):
        """Claim once under the native lock, then fetch outside it on the event worker.

        A crash during capture is uncertain, never automatically relaunched.
        Partial private artifacts remain inspectable; they never start a run.
        """
        path = self.studio._path("channel-events", identity)
        with self.studio.lock:
            event = self.studio._read(path)
            if event["status"] != "slack_files_pending":
                return
            try:
                connection = self.authorize(event)
                event["status"] = "slack_files_fetching"
                event["slack_file_claim"] = uuid.uuid4().hex
                self.studio._write(path, event)
            except Exception:
                event.update(
                    status="failed", error="Slack file configuration changed before capture"
                )
                self.studio._write(path, event)
                return
        try:
            token = self.studio.connections.credentials(
                connection["data"], event["requester_nexus_id"]
            )
            captured, total = [], 0
            for file_id in event["slack_files"]["ids"]:
                with self.studio.lock:
                    self.claim_current(event)
                response = json.loads(read(API, token, 256 * 1024, {"file": file_id}))
                info = response.get("file", {})
                if not response.get("ok") or info.get("id") != file_id:
                    raise ValueError("Slack file metadata request failed")
                shares = info.get("shares", {})
                shared = any(
                    event["channel"] in shares.get(kind, {}) for kind in ("public", "private")
                )
                size = info.get("size")
                name = info.get("name")
                if (
                    not shared
                    or info.get("mode") != "hosted"
                    or info.get("is_external") is not False
                    or type(size) is not int
                    or not 0 <= size <= ARTIFACT_LIMIT
                    or not isinstance(name, str)
                    or not 1 <= len(name) <= 200
                    or name in {".", ".."}
                    or any(ord(c) < 32 or c in "/\\" for c in name)
                ):
                    raise ValueError(
                        "Slack file is unavailable or outside supported private capture"
                    )
                total += size
                if total > TOTAL_LIMIT:
                    raise ValueError("Combined Slack files exceed 32 MiB")
                content = read(download_url(info.get("url_private_download")), token, size)
                if len(content) != size:
                    raise ValueError("Slack file download size changed")
                captured.append((file_id, name, content))
            with self.studio.lock:
                self.claim_current(event)
                for file_id, name, content in captured:
                    artifact_id = (
                        "slack-file-" + hashlib.sha256(f"{identity}:{file_id}".encode()).hexdigest()
                    )
                    artifact = self.studio.catalog.save(
                        "artifacts",
                        {
                            "id": artifact_id,
                            "name": name,
                            "requester_nexus_id": event["requester_nexus_id"],
                            "description": "Private file captured from a verified Slack message",
                            "data": {
                                "filename": name,
                                "content_base64": base64.b64encode(content).decode(),
                            },
                        },
                    )
                    event.setdefault("slack_artifact_refs", []).append(
                        {"id": artifact["id"], "revision": artifact["revision"]}
                    )
                    self.studio._write(path, event)
                event["status"] = "slack_files_ready"
                self.studio._write(path, event)
        except Exception:
            with self.studio.lock:
                current = self.studio._read(path)
                if (
                    current["status"] == "slack_files_fetching"
                    and current.get("slack_file_claim") == event["slack_file_claim"]
                ):
                    event.update(
                        status="failed",
                        error="Slack file capture failed; inspect files and account setup before sending a new message",
                    )
                    self.studio._write(path, event)

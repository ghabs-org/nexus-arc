"""Original file transfer through Studio's existing authorized artifact service."""

import base64
import os
import stat
import uuid
from pathlib import Path
from urllib.parse import quote

from nexus.core.studio_artifacts import ARTIFACT_LIMIT


class TerminalFiles:
    def __init__(self, client):
        self.client = client

    def upload(self, source):
        path = Path(source).expanduser()
        # Reject pipes/devices without blocking on open. A selected regular-file
        # symlink is allowed; only the chosen file's bounded bytes are uploaded.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Upload a regular file")
            if info.st_size > ARTIFACT_LIMIT:
                raise ValueError("Upload files must be at most 16 MiB")
            content = file.read(ARTIFACT_LIMIT + 1)
        if len(content) > ARTIFACT_LIMIT:
            raise ValueError("Upload files must be at most 16 MiB")
        saved = self.client.request(
            "/api/catalog/artifacts",
            {
                "id": uuid.uuid4().hex,
                "name": path.name,
                "data": {
                    "filename": path.name,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
        print(f"Uploaded {path.name} · {saved['id']} · revision {saved['revision']}")
        return saved

    def artifacts(self, session):
        """Resolve outputs from this conversation, excluding unrelated saved files."""
        run_ids = list(
            dict.fromkeys(
                message["run_id"]
                for message in session.get("messages", [])
                if message.get("run_id")
            )
        )
        references = {}
        for identity in run_ids:
            run = self.client.request("/api/runs/" + quote(identity, safe=""))
            if run.get("session_id") != session["id"]:
                raise ValueError("Artifact run does not belong to this conversation")
            for part in run.get("structured_content", []):
                if part.get("type") == "artifact":
                    references[(part["artifact_id"], part["revision"])] = None
            if run.get("artifact_id"):
                references[(run["artifact_id"], run.get("artifact_revision"))] = None
            if run.get("speech_artifact_id"):
                references[(run["speech_artifact_id"], None)] = None
        items = []
        for identity, revision in references:
            prefix = "/api/catalog/artifacts/" + quote(identity, safe="")
            record = self.client.request(prefix)
            if revision is not None and record["revision"] != revision:
                history = self.client.request(prefix + "/history")["items"]
                record = next((item for item in history if item["revision"] == revision), None)
                if record is None:
                    raise ValueError("Recorded artifact revision is unavailable")
            items.append(record)
        return items

    def download(self, arguments, session):
        if len(arguments) > 2:
            raise ValueError("Usage: /download [id:revision or filename] [new path]")
        items = self.artifacts(session)
        if not items:
            raise ValueError("This conversation has no generated artifacts")
        if not arguments:
            self.display(items)
            try:
                selected = input(
                    "Download artifact numbers (comma separated), or Enter to cancel: "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print("Download selection cancelled.")
                return
            if not selected:
                return
            indexes = list(dict.fromkeys(int(value.strip()) - 1 for value in selected.split(",")))
            if any(index < 0 or index >= len(items) for index in indexes):
                raise ValueError("Select artifact numbers from the list")
            chosen = [items[index] for index in indexes]
        else:
            value = arguments[0]
            exact = [item for item in items if f"{item['id']}:{item['revision']}" == value]
            chosen = exact or [item for item in items if item["id"] == value]
            chosen = chosen or [item for item in items if item["data"]["filename"] == value]
            if len(chosen) != 1:
                raise ValueError("Choose one artifact from this conversation using its id:revision")
        targets = []
        for item in chosen:
            name = item["data"]["filename"]
            if name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Artifact has an unsafe filename")
            path = Path(arguments[1]).expanduser() if len(arguments) == 2 else Path(name)
            if path.exists() or path.is_symlink() or path in targets:
                raise ValueError("Download destinations must be distinct new files")
            targets.append(path)
        for item, path in zip(chosen, targets, strict=True):
            self.client.download(
                "/api/catalog/artifacts/"
                + quote(item["id"], safe="")
                + "/versions/"
                + str(item["revision"]),
                path,
                sha256=item["data"].get("sha256"),
            )
            print(f"Downloaded {item['id']}:{item['revision']} to {path}")

    @staticmethod
    def display(items):
        for index, item in enumerate(items, 1):
            print(f"{index}. {item['id']}:{item['revision']} · {item['data']['filename']}")
        print(f"{len(items)} generated artifacts in this conversation.")

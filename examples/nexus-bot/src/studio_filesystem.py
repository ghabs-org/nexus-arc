"""Exercise an explicitly installed filesystem MCP server in disposable folders.

Install @modelcontextprotocol/server-filesystem@2.0.0 separately, then run:
PYTHONPATH=. python examples/nexus-bot/src/studio_filesystem.py \
    --runtime /absolute/path/to/node --script /installed/server-filesystem/dist/index.js

No package installation, account lookup or existing workspace is used here.
"""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


async def exercise(runtime, script, *, persistent=False):
    with tempfile.TemporaryDirectory(prefix="nexus-filesystem-") as directory:
        parent = Path(directory).resolve()
        root = parent / "workspace"
        root.mkdir()
        sibling = parent / "workspace-other"
        sibling.mkdir()
        secret = sibling / "private.txt"
        secret.write_text("outside-private-text", encoding="utf-8")
        source = root / "notes.txt"
        source.write_text("Workspace evidence.", encoding="utf-8")
        destination = root / "reviewed.txt"
        studio = StudioService(root)
        try:
            record = studio.recipes.configure(
                "filesystem", {"command": str(runtime), "server_script": str(script)}, None
            )
            assert not record["data"]["enabled"]
            assert record["data"]["env"] == {}

            async def call(tool, arguments, **options):
                return await studio.connections.execute(
                    record["id"],
                    {"action": "call", "tool": tool, "arguments": arguments, **options},
                )

            async def rejected(operation, error, message):
                try:
                    await operation
                except error as failure:
                    assert message in str(failure)
                else:
                    raise AssertionError("An unpermitted filesystem action succeeded")

            await rejected(call("read_text_file", {"path": str(source)}), ValueError, "disabled")
            discovered = await studio.connections.execute(
                record["id"], {"action": "discover"}, probe=True
            )
            tools = {row["name"]: row for row in discovered}
            selected = ["list_allowed_directories", "read_text_file", "write_file"]
            assert set(selected) <= tools.keys()
            assert "path" in tools["read_text_file"]["inputSchema"]["required"]
            record = studio.catalog.save(
                "connectors",
                {
                    **record,
                    "data": {
                        **record["data"],
                        "enabled": True,
                        "actions": ["discover", "call"],
                        "tools": selected,
                        "read_only_tools": selected[:2],
                        "persistent": persistent,
                    },
                },
            )
            allowed = await call("list_allowed_directories", {})
            assert str(root) in allowed and str(sibling) not in allowed
            text = await studio.connections.tool(record["id"])(
                action="call", tool="read_text_file", arguments={"path": str(source)}
            )
            assert text == "Workspace evidence."
            await rejected(
                call("list_directory", {"path": str(root)}, confirm=True),
                PermissionError,
                "not enabled",
            )
            write = {"path": str(destination), "content": "Reviewed once."}
            await rejected(call("write_file", write), ValueError, "confirm")
            await rejected(
                studio.connections.tool(record["id"])(
                    action="call", tool="write_file", arguments=write, confirm=True
                ),
                PermissionError,
                "approved integration workflow",
            )
            assert not destination.exists()
            result = await call("write_file", write, confirm=True)
            assert "Successfully wrote" in result
            assert destination.read_text(encoding="utf-8") == "Reviewed once."
            assert await call("read_text_file", {"path": str(destination)}) == "Reviewed once."

            blocked = [secret, root / ".." / "workspace-other" / "private.txt"]
            # Symlink creation needs extra privileges on some Windows hosts.
            # Its acceptance remains explicit rather than silently skipping it.
            (root / "outside-link").symlink_to(sibling, target_is_directory=True)
            blocked.append(root / "outside-link" / "private.txt")
            for path in blocked:
                await rejected(
                    call("read_text_file", {"path": str(path)}),
                    ValueError,
                    "Integration action failed",
                )
            outside_write = sibling / "must-not-exist.txt"
            await rejected(
                call("write_file", {"path": str(outside_write), "content": "No"}, confirm=True),
                ValueError,
                "Integration action failed",
            )
            assert not outside_write.exists()
            assert secret.read_text(encoding="utf-8") == "outside-private-text"
            # A remote rejection must leave a persistent session usable, too.
            assert await call("read_text_file", {"path": str(source)}) == text
            record = studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "enabled": False}}
            )
            await rejected(call("read_text_file", {"path": str(source)}), ValueError, "disabled")
            return {
                "discovered_tools": len(tools),
                "selected_tools": selected,
                "persistent": persistent,
                "reviewed_write": True,
                "model_self_approval_blocked": True,
                "outside_paths_and_symlinks_blocked": True,
                "post_failure_read": True,
                "package_installation_performed": False,
            }
        finally:
            studio.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--persistent", action="store_true")
    options = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(exercise(options.runtime, options.script, persistent=options.persistent)),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

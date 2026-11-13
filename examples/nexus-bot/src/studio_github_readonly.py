"""Explicit live GitHub read acceptance through Studio; never modifies remote state.

Pass a repository and an existing token environment reference. Credentials stay in
memory; the temporary Studio workspace stores references only and is removed.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from nexus.adapters.git.github import GitHubPlatform
from nexus.core.studio import StudioService
from nexus.core.studio_connections import validate_connection


async def exercise(repo, token_env):
    validate_connection({"type": "github", "repo": repo, "token_env": token_env})
    token = os.environ.get(token_env)
    if not token:
        raise ValueError("Provide an explicit configured GitHub credential environment reference")
    calls = []
    original = GitHubPlatform._sync_request

    def read_only(adapter, method, path, payload=None):
        if (
            method != "GET"
            or payload is not None
            or not (path == "user" or path == f"repos/{repo}" or path.startswith(f"repos/{repo}/"))
        ):
            raise AssertionError("Live acceptance permits only selected GitHub GET requests")
        try:
            result = original(adapter, method, path, payload)
        except Exception:
            # Adapter list operations can normalize some transport failures to
            # empty results. A failed live request must never become a pass.
            raise AssertionError(
                "Live GitHub GET failed; no successful acceptance recorded"
            ) from None
        calls.append({"method": method, "path": path})
        return result

    with tempfile.TemporaryDirectory(prefix="nexus-github-read-") as temporary:
        studio = StudioService(Path(temporary))
        try:
            connector = studio.catalog.save(
                "connectors",
                {
                    "name": "Live GitHub reads",
                    "data": {
                        "type": "github",
                        "repo": repo,
                        "token_env": token_env,
                        "enabled": True,
                        "actions": ["list_issues", "get_issue", "list_prs", "get_pr"],
                    },
                },
            )
            identity = connector["id"]
            data = connector["data"]
            with patch.object(GitHubPlatform, "_sync_request", read_only):
                adapter = await studio.connections.adapter(data, None)
                user = await adapter._get("user")
                assert isinstance(user.get("id"), int) and user["id"] > 0
                assert isinstance(user.get("login"), str) and user["login"]
                repository = await adapter._get(f"repos/{repo}")
                assert repository["full_name"].lower() == repo.lower()
                assert isinstance(repository["private"], bool)
                issues = await studio.connections.execute(
                    identity, {"action": "list_issues", "arguments": {"limit": 5}}
                )
                pulls = await studio.connections.execute(
                    identity, {"action": "list_prs", "arguments": {"state": "closed", "limit": 5}}
                )
                for rows in (issues, pulls):
                    assert isinstance(rows, list) and len(rows) <= 5
                    assert all(
                        isinstance(row["number"], int)
                        and row["number"] > 0
                        and row["url"].startswith(f"https://github.com/{repo}/")
                        for row in rows
                    )
                detail = {}
                if issues:
                    found = await studio.connections.execute(
                        identity,
                        {
                            "action": "get_issue",
                            "arguments": {"issue_id": str(issues[0]["number"])},
                        },
                    )
                    assert found["number"] == issues[0]["number"]
                    detail["get_issue"] = True
                if pulls:
                    found = await studio.connections.execute(
                        identity,
                        {"action": "get_pr", "arguments": {"pr_id": str(pulls[0]["number"])}},
                    )
                    assert found["number"] == pulls[0]["number"]
                    detail["get_pr"] = True
                before = len(calls)
                try:
                    await studio.connections.execute(
                        identity, {"action": "comment", "confirm": True, "arguments": {}}
                    )
                except PermissionError:
                    pass
                else:
                    raise AssertionError("Unselected write action must be rejected")
                assert len(calls) == before
            for path in Path(temporary).rglob("*"):
                if path.is_file():
                    assert token.encode() not in path.read_bytes(), (
                        "Credential persisted unexpectedly"
                    )
            return {
                "repository": repo,
                "authenticated_account_verified": True,
                "studio_native_rest_adapter": True,
                "issues_returned": len(issues),
                "pull_requests_returned": len(pulls),
                "detail_checks": detail,
                "read_only_requests": calls,
                "remote_mutations": 0,
                "unselected_write_blocked_before_transport": True,
                "credential_persisted": False,
                "pending": [action for action in ("get_issue", "get_pr") if action not in detail],
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token-env", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(exercise(args.repo, args.token_env)), sort_keys=True))

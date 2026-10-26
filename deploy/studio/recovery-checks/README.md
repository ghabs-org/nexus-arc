# Isolated Linux server recovery checks

This explicit acceptance image runs the existing source runtime on Linux with
the same pinned Python 3.13 base as `Dockerfile.studio`. It is a test image,
not a replacement production image or a desktop installer. Building downloads
Python test dependencies; execution requires no external account or network.

From the repository root:

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile -t nexus-studio-recovery:local .
docker run --rm --init --pull never --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 768m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=384m \
  -e PYTEST_ADDOPTS=-pno:cacheprovider nexus-studio-recovery:local
```

No host folders, credentials, Docker socket, published ports or persistent
volumes are mounted. Tests run as uid/gid 65534. Disposable loopback listeners
are reachable only inside the test container. The build prepares the test
bootstrap configuration before switching to the non-root user. Store console
output outside the container if required; temporary files vanish on exit.

The gate checks file and SQLite state through four real server launches each:
normal SIGTERM, SIGKILL and restart, then an inactive restored copy. Every live
launch rejects a second writer. Definitions, paused approval, old/current
artifact bytes, pinned citation revisions and run identity survive. Separate
controller/worker drills instrument the real `StudioService.close` method to
verify it finishes even after a second SIGTERM during cleanup. Existing backup,
SQL restore, workspace lock and parent-lifetime regressions run in the same gate.

For source checks without Docker, install the package, pytest, pytest-asyncio,
SQLAlchemy and pypdf, then run the test paths listed in the Dockerfile. The thin
consumer also runs directly on POSIX:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_server_recovery.py
PYTHONPATH=. python examples/nexus-bot/src/studio_server_recovery.py --sql
```

No persisted format or migration changes are needed. Upgrade the installed
package and restart the chosen service normally to pick up cooperative SIGTERM.
Keep the service manager's stop timeout; unresponsive external dependencies and
SIGKILL cannot be turned into guaranteed graceful completion.

This local Linux/arm64 container proof does not certify a personal HTTPS VM,
systemd deployment, Nexus account login, PostgreSQL host, device or native app.
Those acceptance gates remain separate. See
[the checkpoint](../../../docs/STUDIO-SERVER-RECOVERY-CHECKPOINT.json).

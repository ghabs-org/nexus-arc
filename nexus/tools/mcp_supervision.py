"""Select per-session POSIX ownership without changing MCP configuration."""

import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def available():
    if sys.platform == "darwin":
        try:
            from nexus.core.studio_mcp_supervisor import _configure

            return os.name == "posix" and callable(_configure())
        except OSError:
            return False
    if (
        sys.platform != "linux"
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
    ):
        return False
    try:
        if not Path(f"/proc/self/task/{os.getpid()}/children").is_file():
            return False
        descriptor = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(descriptor, 0)
        finally:
            os.close(descriptor)
        return True
    except OSError:
        return False


def supervised_command(command, args):
    if not available():
        if sys.platform == "linux":
            logger.warning(
                "Linux MCP detached-process supervision unavailable; using SDK process-group cleanup only."
            )
        elif sys.platform == "darwin":
            logger.warning(
                "macOS MCP owner supervision unavailable; using SDK process-group cleanup only."
            )
        return command, list(args or [])
    from nexus.core.studio_desktop_sidecar import worker_command

    launch = worker_command("nexus.core.studio_mcp_supervisor", "--", command, *list(args or []))
    return launch[0], launch[1:]

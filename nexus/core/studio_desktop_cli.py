"""CLI command handlers for `nexus desktop`.

Provides subcommands for desktop lifecycle, instance status, workspace import,
and system preflight audits.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser

from nexus.core.studio_desktop import (
    DesktopBackendRunner,
    WorkspaceLock,
    check_desktop_prerequisites,
    get_desktop_dirs,
    import_workspace,
    is_process_alive,
)


def desktop_command(args: argparse.Namespace) -> int:
    """CLI driver for `nexus desktop`."""
    subcommand = getattr(args, "desktop_subcommand", "status")
    workspace_dir = Path(getattr(args, "dir", ".")).resolve()

    if subcommand == "preflight":
        audit = check_desktop_prerequisites()
        if getattr(args, "json_output", False):
            print(json.dumps(audit, indent=2))
        else:
            print(f"Nexus Studio Desktop Preflight ({audit['platform']} {audit['architecture']}):")
            for c in audit["checks"]:
                req = "[REQUIRED]" if c.get("required") else "[OPTIONAL]"
                ver = f" (v{c['version']})" if c.get("version") else ""
                print(f"  - {c['name']}{ver}: {c['status'].upper()} {req}")
                if c.get("notice"):
                    print(f"    Notice: {c['notice']}")
        return 0

    elif subcommand == "status":
        lock = WorkspaceLock(workspace_dir)
        info = lock.read()
        locked = lock.is_locked()
        result = {
            "workspace": str(workspace_dir),
            "locked": locked,
            "info": info if locked else None,
        }
        if getattr(args, "json_output", False):
            print(json.dumps(result, indent=2))
        else:
            if locked and info:
                print(f"Workspace '{workspace_dir.name}' is RUNNING (PID {info.get('pid')}, port {info.get('port')})")
            else:
                print(f"Workspace '{workspace_dir.name}' is STOPPED (not locked)")
        return 0

    elif subcommand == "stop":
        lock = WorkspaceLock(workspace_dir)
        info = lock.read()
        if not lock.is_locked() or not info:
            print(f"Workspace '{workspace_dir.name}' is not running.")
            return 0
        pid = info.get("pid")
        if pid and is_process_alive(pid):
            try:
                import signal
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped Nexus Studio process (PID {pid}).")
            except OSError as exc:
                print(f"Failed to stop process {pid}: {exc}", file=sys.stderr)
                return 1
        lock.release()
        return 0

    elif subcommand == "import":
        source = getattr(args, "from_dir", None)
        if not source:
            print("Error: --from argument is required for workspace import", file=sys.stderr)
            return 1
        try:
            res = import_workspace(source, workspace_dir, backup_first=not getattr(args, "no_backup", False))
            if getattr(args, "json_output", False):
                print(json.dumps(res, indent=2))
            else:
                print(f"Imported workspace from {res['source']} to {res['target']} ({res['copied_items']} items)")
                if res.get("backup"):
                    print(f"Created pre-import backup at {res['backup']}")
            return 0
        except Exception as exc:
            if getattr(args, "json_output", False):
                print(json.dumps({"success": False, "error": str(exc)}))
            else:
                print(f"Import failed: {exc}", file=sys.stderr)
            return 1

    elif subcommand == "launch":
        port = getattr(args, "port", 0)
        runner = DesktopBackendRunner(workspace_dir, port=port)
        res = runner.start()
        if not res["success"]:
            if res.get("already_running"):
                print(f"Studio already running on workspace (PID {res.get('pid')}, port {res.get('port')})")
                if not getattr(args, "no_browser", False):
                    webbrowser.open(f"http://127.0.0.1:{res.get('port')}")
                return 0
            print(f"Launch failed: {res.get('error')}", file=sys.stderr)
            return 1

        print(f"Nexus Studio Desktop backend launched:")
        print(f"  URL: {res['url']}")
        print(f"  PID: {res['pid']}")
        print(f"  Workspace: {res['workspace']}")

        if not getattr(args, "no_browser", False):
            webbrowser.open(res["url"])

        # If running interactively, wait for KeyboardInterrupt
        try:
            while runner.process and runner.process.poll() is None:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down desktop backend...")
            runner.stop()
        return 0

    print(f"Unknown desktop subcommand: {subcommand}", file=sys.stderr)
    return 1


def add_parser(subparsers: Any) -> Any:
    """Register `desktop` subcommand parser on `nexus` CLI."""
    parser = subparsers.add_parser("desktop", help="Manage Nexus Studio standalone desktop application")
    desktop_subs = parser.add_subparsers(dest="desktop_subcommand")

    # preflight
    pre_p = desktop_subs.add_parser("preflight", help="Check system prerequisites for desktop execution")
    pre_p.add_argument("--json", action="store_true", dest="json_output", help="Output JSON result")

    # status
    stat_p = desktop_subs.add_parser("status", help="Inspect workspace instance lock and status")
    stat_p.add_argument("--dir", default=".", help="Workspace directory")
    stat_p.add_argument("--json", action="store_true", dest="json_output", help="Output JSON result")

    # stop
    stop_p = desktop_subs.add_parser("stop", help="Stop running desktop backend for workspace")
    stop_p.add_argument("--dir", default=".", help="Workspace directory")

    # import
    imp_p = desktop_subs.add_parser("import", help="Import existing workspace with backup")
    imp_p.add_argument("--from", required=True, dest="from_dir", help="Source workspace directory")
    imp_p.add_argument("--dir", default=".", help="Target workspace directory")
    imp_p.add_argument("--no-backup", action="store_true", help="Skip pre-import backup")
    imp_p.add_argument("--json", action="store_true", dest="json_output", help="Output JSON result")

    # launch
    launch_p = desktop_subs.add_parser("launch", help="Launch desktop backend and local webview/browser")
    launch_p.add_argument("--dir", default=".", help="Workspace directory")
    launch_p.add_argument("--port", type=int, default=0, help="Explicit port (default: auto)")
    launch_p.add_argument("--no-browser", action="store_true", help="Do not open local browser automatically")

    return parser

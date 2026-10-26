"""Portable native backend entrypoint and bounded internal worker dispatch."""

import importlib
import importlib.metadata
import importlib.util
import json
import multiprocessing
import platform
import sys
from pathlib import Path

WORKERS = {
    "nexus.core.studio_artifact_extract": "isolated-file",
    "nexus.core.studio_speech_worker": "module",
    "nexus.core.studio_finance_worker": "isolated-file",
    "nexus.core.studio_mcp_supervisor": "isolated-file",
}


def bundle_info():
    """Report package presence only; never probe credentials, providers or models."""
    packages = [
        "openai",
        "anthropic",
        "slack_sdk",
        "mcp",
        "sqlalchemy",
        "pypdf",
        "whisper",
        "boto3",
        "pymongo",
    ]
    static = Path(__file__).resolve().parents[1] / "studio_static"
    return {
        "format": "nexus-sidecar-info/v1",
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "nexus": importlib.metadata.version("nexus-arc"),
        "frontend_present": all(
            (static / name).is_file()
            for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]
        ),
        "optional_sdk_presence": {
            name: importlib.util.find_spec(name) is not None for name in packages
        },
        "verification_scope": "Package presence only; not model/account/service validation",
    }


def worker_command(module, *arguments, executable=None):
    """Preserve source commands; use the bundled executable for frozen workers."""
    if module not in WORKERS:
        raise ValueError("Unsupported internal desktop worker")
    executable = executable or sys.executable
    if getattr(sys, "frozen", False):
        return [executable, "--nexus-worker", module, *arguments]
    if WORKERS[module] == "isolated-file":
        return [
            executable,
            "-I",
            str(Path(__file__).with_name(module.rsplit(".", 1)[1] + ".py")),
            *arguments,
        ]
    return [executable, "-m", module, *arguments]


def main():
    multiprocessing.freeze_support()
    arguments = sys.argv[1:]
    if arguments == ["--nexus-bundle-info"]:
        print(json.dumps(bundle_info()))
        return
    if arguments[:1] == ["--nexus-bundle-check"]:
        from nexus.core.studio_desktop_profiles import check_profile

        if len(arguments) != 2:
            raise SystemExit("Specify one desktop dependency profile")
        try:
            result = check_profile(arguments[1])
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result))
        if not result["passed"]:
            raise SystemExit(1)
        return
    if arguments[:1] == ["--nexus-desktop"]:
        # Native shell access to the existing `nexus desktop` commands (e.g. import).
        from nexus.cli import main as cli_main

        sys.argv = [sys.argv[0], "desktop", *arguments[1:]]
        cli_main()
        return
    if arguments[:1] == ["--nexus-worker"]:
        if len(arguments) < 2 or arguments[1] not in WORKERS:
            raise SystemExit("Unsupported internal desktop worker")
        module = arguments[1]
        sys.argv = [module, *arguments[2:]]
        importlib.import_module(module).main()
        return
    from nexus.cli import main as cli_main

    # Frozen Python does not use PYTHONUNBUFFERED. Keep the owned process's
    # startup/log pipe responsive without requiring host interpreter settings.
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    sys.argv = [sys.argv[0], "studio", *arguments]
    cli_main()


if __name__ == "__main__":
    main()

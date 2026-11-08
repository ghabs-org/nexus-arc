"""Desktop runtime file, and the nexus-router that ships inside the app.

Native desktop launches read ``runtime.json`` from the desktop config folder.
When that file names no router, the app runs the bundled nexus-router inside
this process on a loopback-only thread, so "Auto (model router)" works out of
the box. Nothing here ever stops Studio from starting.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.studio_desktop import get_desktop_dirs

RUNTIME_FILE = "runtime.json"
PACKAGE = "nexus_router_bundled"


def desktop_runtime_config() -> Path | None:
    """The desktop runtime file if the user created one (mode 0600)."""
    path = get_desktop_dirs()["config"] / RUNTIME_FILE
    return path if path.is_file() else None


def bundled_router_source() -> Path | None:
    """Router source shipped in the frozen app; NEXUS_STUDIO_ROUTER_SOURCE in development."""
    if getattr(sys, "frozen", False):
        source = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "nexus_router"
    else:
        source = Path(os.environ.get("NEXUS_STUDIO_ROUTER_SOURCE") or "/nonexistent")
    return source if (source / "src" / "server.py").is_file() else None


def start_bundled_router(source: Path, state_root: Path) -> ThreadingHTTPServer:
    """Serve the bundled router on 127.0.0.1:<free port> from a daemon thread."""
    # The router reads its paths from the environment at import time.
    os.environ.update(
        NEXUS_ROUTER_STATE_ROOT=str(state_root),
        NEXUS_ROUTER_POLICIES_ROOT=str(source / "policies"),
        NEXUS_ROUTER_REGISTRY_FILE=str(source / "generated" / "models.json"),
    )
    # Task text never goes to a hosted classifier unless the user opted in.
    os.environ.setdefault("JEV_BACKEND", "off")
    if PACKAGE not in sys.modules:
        # Its own package name: the router's checkout calls itself `src`.
        spec = importlib.util.spec_from_file_location(
            PACKAGE,
            source / "src" / "__init__.py",
            submodule_search_locations=[str(source / "src")],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = package
        spec.loader.exec_module(package)
    server = importlib.import_module(PACKAGE + ".server")
    server.ensure_schema()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.RouterHandler)
    threading.Thread(target=httpd.serve_forever, name="nexus-router", daemon=True).start()
    return httpd


def desktop_options(root) -> dict:
    """Runtime options for a native desktop launch; problems are reported, never fatal."""
    from nexus.core.studio_runtime import StudioModelRouter, load_runtime_configuration

    options: dict = {}
    path = desktop_runtime_config()
    if path:
        try:
            options = load_runtime_configuration(path, root)
        except (ValueError, OSError) as exc:
            print(f"Nexus Studio: ignoring desktop runtime file {path}: {exc}", file=sys.stderr)
    workbench = options.setdefault("workbench_options", {})
    if workbench.get("router_client"):  # the user's own router wins
        return options
    source = bundled_router_source()
    if source is None:
        return options
    try:
        httpd = start_bundled_router(source, get_desktop_dirs()["data"] / "router")
    except Exception as exc:  # optional: Studio works without Auto
        print(f"Nexus Studio: bundled model router unavailable: {exc}", file=sys.stderr)
        return options
    workbench["router_client"] = StudioModelRouter(
        {"url": f"http://127.0.0.1:{httpd.server_port}", "allow_private": True, "bundled": True}
    )
    return options

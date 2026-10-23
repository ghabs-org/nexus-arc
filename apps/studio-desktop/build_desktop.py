"""Build and packaging script for Nexus Studio desktop application.

Prepares the sidecar runner, verifies frontend assets, and stages the Tauri 2 build.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys


def stage_sidecar(target_dir: Path) -> Path:
    """Stage a development wrapper using this interpreter, not a standalone sidecar."""
    bin_dir = target_dir / "src-tauri" / "binaries"
    bin_dir.mkdir(parents=True, exist_ok=True)

    sidecar_path = bin_dir / "nexus-sidecar"
    script = f"""#!/bin/sh
# Nexus Studio Desktop sidecar launcher
exec "{sys.executable}" -m nexus.cli studio "$@"
"""
    sidecar_path.write_text(script, encoding="utf-8")
    # Make executable
    sidecar_path.chmod(sidecar_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sidecar_path


def verify_frontend_assets(repo_root: Path) -> bool:
    """Verify studio static files exist and are populated."""
    static_dir = repo_root / "nexus" / "studio_static"
    if not static_dir.is_dir():
        print(f"Error: static directory not found at {static_dir}", file=sys.stderr)
        return False
    required = ["index.html", "studio.js", "studio.css"]
    missing = [f for f in required if not (static_dir / f).is_file()]
    if missing:
        print(f"Error: missing static assets: {', '.join(missing)}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Package Nexus Studio desktop shell")
    parser.add_argument("--sidecar-only", action="store_true", help="Stage only the sidecar runner")
    args = parser.parse_args()

    desktop_dir = Path(__file__).parent.resolve()
    repo_root = desktop_dir.parent.parent.resolve()

    if not verify_frontend_assets(repo_root):
        return 1

    sidecar = stage_sidecar(desktop_dir)
    print(f"Staged desktop sidecar runner at: {sidecar}")

    if args.sidecar_only:
        return 0

    print("Desktop staging complete. To build Tauri app bundle, run: npm run build")
    return 0


if __name__ == "__main__":
    sys.exit(main())

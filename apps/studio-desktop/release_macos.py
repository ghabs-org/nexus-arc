"""Explicit native release consumer; Apple credentials remain in Keychain."""

import argparse
import json
import subprocess

from nexus.core.studio_desktop_release import release_macos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--identity", required=True, help="Developer ID Application identity or digest"
    )
    parser.add_argument("--keychain-profile", required=True)
    parser.add_argument(
        "--updater-key", required=True, help="Private local key path; never its content"
    )
    parser.add_argument("--tauri-cli", required=True, help="Official Tauri CLI tauri.js path")
    parser.add_argument("--notes", help="Release notes shown in the update dialog")
    args = parser.parse_args()
    try:
        result = release_macos(
            args.application,
            args.output,
            args.identity,
            args.keychain_profile,
            args.updater_key,
            args.tauri_cli,
            args.notes,
        )
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Release stopped. Inspect local prerequisites; no artifacts were published.")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

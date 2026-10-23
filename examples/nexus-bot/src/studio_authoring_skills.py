"""Export and verify authoring skills without touching real assistant folders.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_authoring_skills.py
"""

import tempfile
from pathlib import Path

from nexus.core.studio_authoring_skills import check_skills, install_skills


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-authoring-example-") as temporary:
        destination = Path(temporary).resolve() / "authoring-skills"
        result = install_skills(destination)
        assert check_skills(destination)["valid"]
        assert (destination / "nexus-studio-config/references/configuration.json").is_file()
        print("Verified offline authoring skills:", ", ".join(result["skills"]))


if __name__ == "__main__":
    main()

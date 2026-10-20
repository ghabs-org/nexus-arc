"""Small consumer of the framework skill import API: GitHub skill into a workspace."""

import argparse

from nexus.core.studio import StudioService


def main():
    parser = argparse.ArgumentParser(description="Import an Agent Skill from GitHub")
    parser.add_argument("source", help="GitHub link, owner/repo or `npx skills add ...` command")
    parser.add_argument("--skill", default=None, help="Skill name when the source holds several")
    parser.add_argument("--dir", default=".", help="Workspace that receives the skill")
    args = parser.parse_args()
    service = StudioService(args.dir)
    try:
        result = service.skill_import.import_skill({"source": args.source, "skill": args.skill})
        if "choices" in result:
            for choice in result["choices"]:
                print(f"{choice['skill']}\t{choice['path']}")
            parser.exit(2, "Several skills found; choose one with --skill\n")
        skill = result["skill"]
        print(f"{'Updated' if result['updated'] else 'Imported'} {skill['id']} (revision {skill['revision']})")
        for path in result["skipped_files"]:
            print(f"not imported: {path}")
    finally:
        service.close()


if __name__ == "__main__":
    main()

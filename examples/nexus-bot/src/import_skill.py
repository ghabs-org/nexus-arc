"""Small consumer of the framework skill import API: GitHub skill into a workspace."""

import argparse

from nexus.core.studio import StudioService


def main():
    parser = argparse.ArgumentParser(description="Import an Agent Skill from GitHub")
    parser.add_argument("source", help="GitHub link, owner/repo or `npx skills add ...` command")
    parser.add_argument("--skill", default=None, help="Skill name when the source holds several")
    parser.add_argument(
        "--skills",
        nargs="*",
        default=None,
        help="One or more skill names when the source holds several",
    )
    parser.add_argument("--dir", default=".", help="Workspace that receives the skill")
    args = parser.parse_args()
    service = StudioService(args.dir)
    try:
        payload = {"source": args.source}
        if args.skills:
            payload["skills"] = args.skills
        elif args.skill:
            payload["skill"] = args.skill
        result = service.skill_import.import_skill(payload)
        if "choices" in result:
            for choice in result["choices"]:
                print(f"{choice['skill']}\t{choice['path']}")
            parser.exit(2, "Several skills found; choose with --skills or --skill\n")
        skills = result.get("skills", [result["skill"]] if result.get("skill") else [])
        for skill in skills:
            print(
                f"{'Updated' if result['updated'] else 'Imported'} {skill['id']} (revision {skill['revision']})"
            )
        for path in result["skipped_files"]:
            print(f"not imported: {path}")
    finally:
        service.close()


if __name__ == "__main__":
    main()

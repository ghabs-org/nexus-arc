"""Install a reviewed-image cleanup workflow; never start a provider or run.

PYTHONPATH=. python examples/nexus-bot/src/studio_cleanup_images.py \
    --dir /path/to/workspace --artifact owned-artifact-id

The explicitly selected OpenAI API model uses API billing, not a CLI subscription.
After installation, review the workflow in Studio and run it explicitly. The image
must already belong to the current local workspace. Queuing pins its revision.
"""

import argparse
import json

from nexus.core.studio import StudioService


def document(artifact_id):
    """Reuse the framework importer and its scoped acknowledgement rules."""
    return {
        "documents": [
            {
                "agent_name": "cleanup-image-review",
                "agent_type": "workflow",
                "decision": {"model_alias": "vision"},
                "tools": [
                    {"tool_type": "builtin", "tool_name": "count"},
                    {"tool_type": "builtin", "tool_name": "extract"},
                ],
                "nodes": [
                    {
                        "id": "work",
                        "type": "tool",
                        "tool_name": "count",
                        "input": {"text": "Local work finished"},
                    },
                    {
                        "id": "evidence",
                        "type": "tool",
                        "tool_name": "extract",
                        "input": {"data": {"evidence": {"photo": artifact_id}}, "path": "evidence"},
                    },
                    {
                        "id": "inspect",
                        "type": "decision",
                        "questions": {
                            "visible": {
                                "type": "boolean",
                                "instructions": "Is damage visible in the selected image?",
                            }
                        },
                        "images": ["{{evidence.output.photo}}"],
                    },
                ],
                "on_exit": {"always": "evidence", "on_success": "inspect"},
                "output_mapping": {"review": "{{inspect.output}}"},
            }
        ],
        "bindings": {
            "tools": {"count": "text:wordcount", "extract": "data:query"},
            "decisions": {"vision": {"backend": "openai", "model": "gpt-6-luna"}},
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    studio = StudioService(args.dir)
    try:
        studio.catalog.get("artifacts", args.artifact)
        report = studio.configuration.convert_interop(document(args.artifact), None)
        if not report["valid"]:
            raise ValueError(json.dumps(report["diagnostics"]))
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        print(
            "Installed cleanup-image-review. No run started. Review API billing and start explicitly in Studio."
        )
    finally:
        studio.close()


if __name__ == "__main__":
    main()

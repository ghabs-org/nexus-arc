"""Convert explicit skill references through native reviewed configuration.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_interop_skill_refs.py
No network access, provider invocation or activation occurs.
"""

import tempfile

from nexus.core.studio import StudioService


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-skill-refs-") as root:
        studio = StudioService(root)
        try:
            studio.catalog.save("skills", {"id": "evidence", "name": "Evidence", "data": {"content": "Use supplied evidence and identify uncertainty."}})
            source = {"kind": "agent", "name": "reviewer", "spec": {
                "systemPrompt": "Review the supplied evidence.", "modelProvider": ["local-account"],
                "skills": [{"id": "review", "name": "Review", "description": "Review supplied material"}],
                "skillRefs": ["review-guidance"],
            }}
            report = studio.configuration.convert_interop({"documents": [source], "bindings": {
                "models": {"local-account": {"provider": "codex"}},
                "skills": {"review-guidance": "evidence"},
            }}, None)
            assert report["valid"], report
            assert report["document"]["definitions"][0]["document"]["spec"]["skill_ids"] == ["evidence"]
            exported = studio.configuration.export_interop({"document": report["document"]}, None)
            assert exported["valid"] and exported["bundle"]["documents"][0]["spec"] == source["spec"]
            assert not studio.definitions("agents") and not studio.records("runs")
            print("Converted and roundtripped explicit skill bindings. Review a native plan before applying.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

"""Validated prompt variables and bounded, non-executable template expansion."""

import re

VARIABLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9 _-]{0,63})\s*\}\}")


class StudioPromptHistory:
    """Compare immutable prompt versions and restore through catalog validation."""

    def __init__(self, studio):
        self.studio = studio

    def compare(self, identity, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("Choose a positive prompt revision")
        with self.studio.lock:
            current = self.studio.catalog.get("prompts", identity, archived=True)
            versions = [current, *self.studio.catalog.history("prompts", identity)]
            selected = next((v for v in versions if v["revision"] == revision), None)
            if selected is None:
                raise FileNotFoundError("Prompt revision not found")

            def fields(record):
                return {
                    "name": record["name"],
                    "description": record["description"],
                    "content": record["data"]["content"],
                    "variables": record["data"].get("variables", {}),
                    "tags": record["data"].get("tags", []),
                    "shortcut": record["data"].get("shortcut", ""),
                }

            before, after = fields(selected), fields(current)
            return {
                "version": 1,
                "id": identity,
                "selected_revision": revision,
                "current_revision": current["revision"],
                "archived": current["archived"],
                "selected": before,
                "current": after,
                "changed_fields": [key for key in before if before[key] != after[key]],
                "revisions": sorted({v["revision"] for v in versions}, reverse=True),
            }

    def restore(self, identity, payload):
        with self.studio.lock:
            compared = self.compare(identity, payload.get("restore_revision"))
            if (
                type(payload.get("revision")) is not int
                or payload["revision"] != compared["current_revision"]
            ):
                raise ValueError("Prompt changed; reopen its current revision")
            if compared["archived"]:
                raise ValueError("Restore this archived prompt before changing its content")
            selected = compared["selected"]
            return self.studio.catalog.save(
                "prompts",
                {
                    "id": identity,
                    "revision": compared["current_revision"],
                    "name": selected["name"],
                    "description": selected["description"],
                    "data": {
                        key: selected[key] for key in ("content", "variables", "tags", "shortcut")
                    },
                },
            )


def validate_prompt(data):
    from nexus.core.studio_catalog import _text

    content = _text(data.get("content"), "Content")
    names = list(dict.fromkeys(name.strip() for name in VARIABLE.findall(content)))
    variables = data.get("variables", {})
    if not isinstance(variables, dict) or set(variables) - set(names):
        raise ValueError("Variable defaults must reference variables in prompt content")
    if len(names) > 30 or any(not isinstance(v, str) or len(v) > 4000 for v in variables.values()):
        raise ValueError("Use up to 30 variables with text defaults up to 4000 characters")
    tags = data.get("tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 10
        or any(not isinstance(tag, str) or not 1 <= len(tag) <= 40 for tag in tags)
    ):
        raise ValueError("Use up to ten tags of 1–40 characters")
    shortcut = data.get("shortcut", "")
    if (
        not isinstance(shortcut, str)
        or shortcut
        and not re.fullmatch(r"[a-zA-Z][\w-]{0,63}", shortcut)
    ):
        raise ValueError("Prompt shortcuts must be identifiers up to 64 characters")
    result = {"content": content}
    if names:
        result["variables"] = {name: variables.get(name, "") for name in names}
    if tags:
        result["tags"] = list(dict.fromkeys(tags))
    if shortcut:
        result["shortcut"] = shortcut
    return result


def render_prompt(data, values=None):
    values = values or {}
    if not isinstance(values, dict):
        raise ValueError("Prompt values must be an object")
    defaults = data.get("variables", {})
    names = {name.strip() for name in VARIABLE.findall(data["content"])}
    if set(values) - names:
        raise ValueError("Unknown prompt variable")

    def replace(match):
        name = match[1].strip()
        value = values.get(name, defaults.get(name))
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError(f"Fill prompt variable: {name}")
        return value

    return VARIABLE.sub(replace, data["content"])

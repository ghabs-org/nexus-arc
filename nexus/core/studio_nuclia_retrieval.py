"""Bounded native context-building strategies for Nuclia answer requests."""

EXPANDED_CONTEXT = (
    {"name": "neighbouring_paragraphs", "before": 2, "after": 2},
    {"name": "hierarchy", "count": 0},
)


def validate_rag_strategies(value):
    """Normalize supported provider defaults without widening retrieval implicitly."""
    if not isinstance(value, list) or len(value) > 2:
        raise ValueError("Nuclia context strategies require a list of at most two entries")
    normalized, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("Nuclia context strategies require named objects")
        name = item["name"]
        if name not in {"neighbouring_paragraphs", "hierarchy"} or name in seen:
            raise ValueError("Choose each supported Nuclia context strategy at most once")
        seen.add(name)
        defaults = {"before": 2, "after": 2} if name == "neighbouring_paragraphs" else {"count": 0}
        maximum = 10 if name == "neighbouring_paragraphs" else 1024
        if set(item) - {"name", *defaults} or any(
            type(item.get(key, default)) is not int or not 0 <= item.get(key, default) <= maximum
            for key, default in defaults.items()
        ):
            raise ValueError("Nuclia context strategy fields/counts exceed supported bounds")
        normalized.append({"name": name, **defaults, **item})
    return normalized

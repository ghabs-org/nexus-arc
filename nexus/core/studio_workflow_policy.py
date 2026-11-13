"""Administrator-owned workflow bounds, pinned into queued execution."""

DEFAULT_LIMITS = {
    "map_items": 100,
    "map_concurrency": 4,
    "loop_iterations": 100,
    "nesting_depth": 10,
    "node_instances": 10000,
}
HARD_LIMITS = {
    "map_items": 10000,
    "map_concurrency": 32,
    "loop_iterations": 10000,
    "nesting_depth": 32,
    "node_instances": 100000,
}


def workflow_limits(config=None):
    if config is None:
        return dict(DEFAULT_LIMITS)
    if not isinstance(config, dict) or set(config) - set(DEFAULT_LIMITS):
        raise ValueError(
            "Unknown workflow limit; use map_items, map_concurrency, loop_iterations, nesting_depth and node_instances"
        )
    values = {**DEFAULT_LIMITS, **config}
    for key, value in values.items():
        if type(value) is not int or not 1 <= value <= HARD_LIMITS[key]:
            raise ValueError(f"Workflow limit {key} must be between 1 and {HARD_LIMITS[key]}")
    return values

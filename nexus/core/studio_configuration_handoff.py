"""Shared bounded handoff parsing; checksums grant no execution authority."""

import json
import math

from nexus.core.studio_configuration import digest

LIMIT = 2 * 1024 * 1024


def read_handoff(data, expected_format):
    if (
        not isinstance(data, dict)
        or set(data) != {"handoff"}
        or not isinstance(data["handoff"], str)
    ):
        raise ValueError("Choose a configuration handoff JSON file up to two MiB")
    try:
        if not 0 < len(data["handoff"].encode()) <= LIMIT:
            raise ValueError("Choose a configuration handoff JSON file up to two MiB")
    except UnicodeError:
        raise ValueError("Configuration handoff must be valid UTF-8 text") from None

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    def invalid(value):
        raise ValueError

    try:
        handoff = json.loads(
            data["handoff"], object_pairs_hook=unique, parse_float=finite, parse_constant=invalid
        )
    except (ValueError, RecursionError):
        raise ValueError(
            "Handoff must be bounded JSON with unique keys and finite numbers"
        ) from None
    if (
        not isinstance(handoff, dict)
        or set(handoff) != {"format", "source", "request", "digest"}
        or handoff["format"] != expected_format
    ):
        raise ValueError("Use the supported handoff format and version")
    try:
        valid_digest = isinstance(handoff["digest"], str) and handoff["digest"] == digest(
            {key: value for key, value in handoff.items() if key != "digest"}
        )
    except (ValueError, TypeError, RecursionError):
        valid_digest = False
    if not valid_digest:
        raise ValueError("Configuration handoff digest does not match; export a fresh copy")
    return handoff

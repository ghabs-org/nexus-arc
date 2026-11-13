"""Bounded inline decision image wire parts; never fetch remote URLs."""

import base64

from nexus.adapters.decisions.errors import DecisionCallError

MAX_PAYLOAD = 90 * 1024 * 1024


def validate_images(images):
    if images is None:
        return []
    if not isinstance(images, list) or len(images) > 128:
        raise DecisionCallError("config")
    total = 0
    for value in images:
        if (
            not isinstance(value, str)
            or len(value) > MAX_PAYLOAD
            or not value.startswith(
                ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
            )
        ):
            raise DecisionCallError("config")
        try:
            total += len(base64.b64decode(value.split(",", 1)[1], validate=True))
        except ValueError:
            raise DecisionCallError("config") from None
        if total > 64 * 1024 * 1024:
            raise DecisionCallError("input_too_large")
    return images

"""Bounded decision HTTP transport; no redirects, account fallback or response detail leaks."""

import json
import time

from nexus.adapters.decisions.errors import DecisionCallError


def unique_keys(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate provider response key")
        result[name] = value
    return result


def request_json(endpoint, key, payload, timeout, *, max_bytes=65536):
    import requests

    if not isinstance(key, str) or not key or any(c in key for c in "\r\n"):
        raise DecisionCallError("config")
    try:
        encoded = json.dumps(payload, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise DecisionCallError("config") from None
    if len(encoded) > max_bytes:
        raise DecisionCallError("input_too_large")
    response = None
    deadline = time.monotonic() + timeout
    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "User-Agent": "nexus-arc/1.0 (python-requests)",
            },
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        )
        status = response.status_code
        if status in (401, 403):
            raise DecisionCallError("unauthorized")
        if status == 402 or 300 <= status < 400:
            raise DecisionCallError("config")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            content.extend(chunk)
            if time.monotonic() >= deadline:
                raise DecisionCallError("timeout")
            if len(content) > 1024 * 1024:
                raise DecisionCallError("config" if 400 <= status < 500 else "invalid_response")
        try:
            body = json.loads(content, object_pairs_hook=unique_keys)
        except (ValueError, UnicodeError):
            body = None
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("type", error.get("code")) if isinstance(error, dict) else None
        if status == 413 or code in ("max_tokens_exceeded", "context_length_exceeded"):
            raise DecisionCallError("input_too_large")
        if code in ("insufficient_quota", "insufficient_credits"):
            raise DecisionCallError("config")
        if status == 429:
            raise DecisionCallError("rate_limited" if isinstance(body, dict) else "config")
        if status >= 500:
            raise DecisionCallError("unreachable")
        if not 200 <= status < 300:
            raise DecisionCallError("config")
        if not isinstance(body, dict):
            raise DecisionCallError("invalid_response")
        return body
    except requests.exceptions.Timeout:
        raise DecisionCallError("timeout") from None
    except requests.exceptions.RequestException:
        raise DecisionCallError("unreachable") from None
    finally:
        if response is not None:
            response.close()


def metadata(result, body):
    if isinstance(body.get("model"), str) and 0 < len(body["model"]) <= 200:
        result.metadata["model"] = body["model"]
    usage = body.get("usage", {})
    if isinstance(usage, dict):
        for name in ("input_tokens", "output_tokens"):
            if type(usage.get(name)) is int and 0 <= usage[name] <= 10**9:
                result.metadata.setdefault("usage", {})[name] = usage[name]
    return result

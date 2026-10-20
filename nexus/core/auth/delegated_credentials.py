"""Scope-bound encrypted records using Nexus credential crypto and host persistence.

The host supplies its existing state backend and lock; no second account database
or authentication scheme is created. Scope is authenticated inside the envelope.
"""

import hashlib
import json

from nexus.core.auth.credential_crypto import decrypt_secret, encrypt_secret


def scope_key(*parts):
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def seal(scope, value):
    return encrypt_secret(json.dumps({"scope": scope, "value": value}, separators=(",", ":")))


def unseal(scope, envelope):
    try:
        decoded = json.loads(decrypt_secret(envelope))
        if decoded["scope"] != scope:
            raise ValueError("Scope mismatch")
        return decoded["value"]
    except Exception:
        raise PermissionError("Delegated credentials unavailable; reconnect this account") from None

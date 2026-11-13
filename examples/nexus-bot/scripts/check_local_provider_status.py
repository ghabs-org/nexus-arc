"""Print sanitized local CLI status; this is not a shared-user account check."""

import json

from nexus.core.provider_account_commands import resolve_provider_cli
from nexus.core.provider_account_status import provider_account_status

if __name__ == "__main__":
    print(json.dumps([
        {"provider": provider, **provider_account_status(provider, resolve_provider_cli(binary))}
        for provider, binary in (("codex", "codex"), ("claude-code", "claude"), ("agy", "agy"))
    ], indent=2))

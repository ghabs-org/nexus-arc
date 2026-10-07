"""Chat operator for the command bridge: ask about the system itself.

POST /api/v1/chat {"task": "how do I add a tool?"} answers from a live
system inventory (providers, registered tools, capabilities) plus a short
maintainer-written playbook — no docs folder needed at runtime.

Set ``"context": "none"`` to skip the system prelude for plain chat.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# In-memory conversation store: session_id -> [{role, text}]. Disk-backed
# (one JSON file per session) so history survives bridge restarts; memory
# stays the fast path. The workflow StorageBackend is workflow-shaped, so
# sessions get their own tiny file store instead of a forced fit.
_SESSIONS: dict[str, list[dict[str, str]]] = {}
_SESSIONS_LOCK = threading.Lock()
_MAX_SESSIONS = 100
_MAX_TURNS = 20
_HISTORY_BUDGET_CHARS = 6000


def _memory_dir() -> Path:
    return Path(
        os.getenv("NEXUS_CHAT_MEMORY_DIR", "./data/chat-memory")
    )


def _valid_session_id(session_id: str) -> str | None:
    candidate = re.sub(r"[^a-zA-Z0-9_-]", "", str(session_id or ""))[:64]
    return candidate or None


def _session_path(session_id: str) -> Path | None:
    valid = _valid_session_id(session_id)
    if valid is None:
        return None
    return _memory_dir() / f"{valid}.json"


def _load_history(session_id: str) -> list[dict[str, str]]:
    """History from memory, falling back to disk (then cached)."""
    with _SESSIONS_LOCK:
        if session_id in _SESSIONS:
            return list(_SESSIONS[session_id])
    path = _session_path(session_id)
    if path is not None and path.is_file():
        try:
            import json as _json

            stored = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, list):
                turns = [
                    {"role": str(t.get("role")), "text": str(t.get("text", ""))}
                    for t in stored
                    if isinstance(t, dict) and t.get("role") in {"user", "assistant"}
                ]
                with _SESSIONS_LOCK:
                    _SESSIONS[session_id] = turns
                return list(turns)
        except (ValueError, OSError) as exc:
            logger.warning("Chat memory read failed for %s: %s", session_id, exc)
    return []


def _prune_disk() -> None:
    """Keep at most _MAX_SESSIONS session files, oldest mtime first."""
    try:
        files = sorted(
            _memory_dir().glob("*.json"), key=lambda p: p.stat().st_mtime
        )
    except OSError:
        return
    for stale in files[: max(0, len(files) - _MAX_SESSIONS)]:
        try:
            stale.unlink()
        except OSError:
            pass


def _remember(session_id: str, user_text: str, reply_text: str) -> None:
    """Append one exchange to memory and disk, trimming old turns/sessions."""
    import json as _json

    with _SESSIONS_LOCK:
        history = _SESSIONS.setdefault(session_id, [])
        history.append({"role": "user", "text": user_text})
        history.append({"role": "assistant", "text": reply_text})
        del history[: max(0, len(history) - 2 * _MAX_TURNS)]
        while len(_SESSIONS) > _MAX_SESSIONS:
            _SESSIONS.pop(next(iter(_SESSIONS)))
        snapshot = list(history)
    path = _session_path(session_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(snapshot), encoding="utf-8")
    except OSError as exc:
        logger.warning("Chat memory write failed for %s: %s", session_id, exc)
        return
    _prune_disk()


def _render_history(session_id: str) -> str:
    """Prior turns as transcript, bounded by the prompt budget."""
    from nexus.core.prompt_budget import apply_prompt_budget

    history = _load_history(session_id)
    if not history:
        return ""
    transcript = "\n".join(
        f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['text']}"
        for turn in history
    )
    budgeted = apply_prompt_budget(
        transcript, max_chars=_HISTORY_BUDGET_CHARS, summary_max_chars=1200
    )
    return str(budgeted["text"])


def build_system_prompt(capabilities: dict[str, Any] | None = None) -> str:
    """Render the system prelude from live framework inventory."""
    from nexus.init_project import PROVIDERS
    from nexus.tools import tool_registry

    specs = [tool_registry.get(name) for name in tool_registry.names()]
    tool_lines = "\n".join(
        f"- {spec.name}: {spec.description or 'no description'}"
        for spec in specs
        if spec is not None
    ) or "- (none registered)"
    caps = ""
    if capabilities:
        names = sorted(str(k) for k in capabilities.keys())
        caps = f"\nBridge commands available: {', '.join(names)}\n" if names else ""
    return (
        "You are the Nexus ARC chat operator: a concise assistant for the system itself.\n"
        "Answer questions about how Nexus ARC works and how to build with it.\n"
        "Ground answers in this live inventory; say when something is not covered by it.\n"
        f"\nAI providers: {', '.join(PROVIDERS)} (opencode needs no API key)\n"
        f"\nRegistered workflow tools:\n{tool_lines}\n"
        f"{caps}\n"
        "How to build things:\n"
        "- New workflow tool: @tool_registry.tool(name='ns:verb') in nexus/tools/ "
        "or a new module importing it; call via ToolNode('ns:verb') in graphs.\n"
        "- New agent: agents/<name>.yaml with spec.agent_type + spec.provider; "
        "resolve with AgentRegistry.\n"
        "- New workflow: workflows/<name>.yaml with steps[{id, agent_type}]; "
        "validate with YamlWorkflowLoader.\n"
        "- New integration: channel/plugin class + one branch in "
        "nexus/adapters/registry.py + row in docs/INTEGRATIONS.md.\n"
        "- New bot command: core handler + telegram table entry + TELEGRAM_COMMANDS "
        "+ wrapper in examples/nexus-bot.\n"
        "- Scaffold starters: `nexus init [--yes]`; browser authoring: `nexus init --gui`.\n"
        "When prior turns appear under 'Conversation so far', use them to resolve "
        "follow-ups (pronouns, 'my X', 'it', 'that').\n"
    )


CHAT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus ARC &mdash; Operator chat</title>
<style>
:root{--canvas:#F7F6F3;--surface:#FFFFFF;--ink:#111111;--muted:#787774;--line:#EAEAEA}
*{box-sizing:border-box}
body{background:var(--canvas);color:var(--ink);margin:0;
font-family:"SF Pro Display","Helvetica Neue",Helvetica,Arial,sans-serif;line-height:1.6}
main{max-width:720px;margin:0 auto;padding:64px 24px 96px}
p.kicker{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
h1{font-family:Georgia,"Times New Roman",serif;font-weight:500;letter-spacing:-.02em;font-size:40px;margin:0 0 24px}
#log{margin:0 0 16px}
.msg{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:16px 20px;margin-bottom:12px;white-space:pre-wrap}
.msg.you{border-left:3px solid var(--ink)}
.msg .who{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.row{display:flex;gap:8px}
input{flex:1;border:1px solid var(--line);border-radius:6px;padding:10px 12px;font:inherit}
button{background:var(--ink);color:#fff;border:0;border-radius:6px;padding:10px 18px;font:inherit;cursor:pointer}
button:active{transform:scale(.98)}
#token{margin-bottom:12px}
p.note{color:var(--muted);font-size:13px}
</style></head>
<body><main>
<p class="kicker">Nexus ARC</p><h1>Operator chat</h1>
<input id="token" type="password" placeholder="Bridge bearer token">
<div id="log"></div>
<div class="row"><input id="q" placeholder="Ask about the system, e.g. how do I add a tool?">
<button onclick="send()">Send</button>
<button onclick="newChat()" style="background:transparent;color:var(--ink);border:1px solid var(--line)">New chat</button></div>
<p class="note">Answers are grounded in the live registry. Token is kept in this browser only.</p>
</main>
<script>
const t = document.getElementById("token");
t.value = localStorage.getItem("nexus_token") || "";
t.onchange = () => localStorage.setItem("nexus_token", t.value);
function add(who, text){
  const d = document.createElement("div");
  d.className = "msg" + (who === "you" ? " you" : "");
  d.innerHTML = "";
  const w = document.createElement("div"); w.className = "who"; w.textContent = who;
  const b = document.createElement("div"); b.textContent = text;
  d.appendChild(w); d.appendChild(b);
  document.getElementById("log").appendChild(d);
  d.scrollIntoView(false);
}
async function send(){
  const q = document.getElementById("q"); if(!q.value.trim()) return;
  const text = q.value; q.value = ""; add("you", text);
  const r = await fetch("/api/v1/chat", {method: "POST",
    headers: {"Content-Type": "application/json", "Authorization": "Bearer " + t.value},
    body: JSON.stringify({task: text, session_id: localStorage.getItem("nexus_session") || undefined})});
  const data = await r.json();
  if(r.ok && data.ok){
    if(data.session_id) localStorage.setItem("nexus_session", data.session_id);
    add("nexus", data.output);
  } else {
    add("nexus", "Error: " + (data.error || r.status));
  }
}
function newChat(){
  localStorage.removeItem("nexus_session");
  document.getElementById("log").innerHTML = "";
}
document.getElementById("q").addEventListener("keydown", e => { if(e.key === "Enter") send(); });
</script></body></html>
"""


async def handle_chat_run(payload: dict[str, Any], config: dict | None = None) -> dict[str, Any]:
    """Entry point for POST /api/v1/chat. Resolves a provider and answers."""
    from nexus.adapters.ai.base import ExecutionContext
    from nexus.core.command_bridge.agents_handler import _get_ai_provider

    task = str(payload.get("task") or payload.get("message") or "").strip()
    if not task:
        return {"ok": False, "error": "task is required"}

    provider = None
    if config:
        try:
            factory = config.get("ai_provider_factory")
            if callable(factory):
                provider = factory()
        except Exception as exc:
            logger.debug("ai_provider_factory failed: %s", exc)
    if provider is None:
        provider = await _get_ai_provider(config)
    if provider is None:
        return {"ok": False, "error": "no AI provider available"}

    capabilities = None
    if config:
        try:
            get_caps = config.get("capabilities_provider")
            if callable(get_caps):
                capabilities = get_caps()
        except Exception as exc:
            logger.debug("capabilities_provider failed: %s", exc)

    enriched = str(payload.get("context") or "system").lower() != "none"
    session_id = _valid_session_id(payload.get("session_id")) or uuid.uuid4().hex[:12]
    if not enriched:
        prompt = task
    else:
        prompt = build_system_prompt(capabilities)
        prior = _render_history(session_id)
        if prior:
            prompt += f"\nConversation so far:\n{prior}\n"
        prompt += f"\nUser question: {task}\n"

    workspace = Path(tempfile.gettempdir()) / "nexus-chat"
    try:
        result = await provider.execute_agent(
            ExecutionContext(agent_name="chat-operator", prompt=prompt, workspace=workspace)
        )
    except Exception as exc:
        logger.exception("chat_run failed")
        return {"ok": False, "error": str(exc)}
    if not result.success:
        return {"ok": False, "error": result.error or "provider failed"}
    if enriched:
        _remember(session_id, task, result.output)
    return {
        "ok": True,
        "output": result.output,
        "provider": result.provider_used,
        "session_id": session_id,
    }

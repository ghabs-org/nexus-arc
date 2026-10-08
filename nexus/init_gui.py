"""Browser UI behind ``nexus init --gui`` for authoring agents and workflows.

Stdlib only (no Flask, no JS build): one page with two forms, vanilla JS.
Listens on 127.0.0.1 with no auth — run locally, stop when done.
Targeted at generating ARC agents/workflows, not at runtime operation.

Endpoints (target dir fixed at startup):
    GET  /                  form page
    GET  /api/list          {agents: [...], workflows: [...]} in target dir
    GET  /api/providers     ARC providers with CLI availability plus the live
                             model catalog (cost/free flags) for model picking
    POST /api/agent         {name, description, agent_type, provider,
                             timeout_seconds, max_retries, purpose}
                             -> writes agents/<slug>.yaml, returns {file, yaml}
    POST /api/workflow      {name, description, steps: [{id?, name?,
                             agent_type, prompt?}]}
                             -> writes workflows/<slug>.yaml, returns {file, yaml}
    POST /api/profiles      {profiles: {<profile>: {provider, model}}} ->
                             returns validated model_profiles YAML snippet
                             (paste into project_config.yaml)
    GET  /api/tools          registered workflow tools (name/description/category)
    POST /api/tools/call     {name, args?, kwargs?} -> {ok, output, error}

Agent YAML is validated for required spec keys; workflow YAML is validated
with the real :class:`YamlWorkflowLoader` before anything is written.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from nexus.init_project import PROVIDERS

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus ARC &mdash; Authoring</title>
<style>
:root{--canvas:#F7F6F3;--surface:#FFFFFF;--ink:#111111;--muted:#787774;
--line:#EAEAEA;--blue-bg:#E1F3FE;--blue-fg:#1F6C9F;--green-bg:#EDF3EC;--green-fg:#346538}
*{box-sizing:border-box}
body{background:var(--canvas);color:var(--ink);margin:0;
font-family:"SF Pro Display","Geist Sans","Helvetica Neue",Helvetica,Arial,sans-serif;line-height:1.6}
main{max-width:880px;margin:0 auto;padding:64px 24px 96px}
header p.kicker{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
h1{font-family:"Newsreader","Instrument Serif",Georgia,"Times New Roman",serif;
font-weight:500;letter-spacing:-.02em;line-height:1.1;font-size:44px;margin:0 0 12px}
header p.sub{color:var(--muted);max-width:34em;margin:0 0 40px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:32px;margin-bottom:24px;animation:rise .6s cubic-bezier(.16,1,.3,1) both}
.card:nth-child(3){animation-delay:80ms}.card:nth-child(4){animation-delay:160ms}
@keyframes rise{from{transform:translateY(12px);opacity:0}to{transform:none;opacity:1}}
.card h2{font-family:"Newsreader",Georgia,serif;font-weight:500;letter-spacing:-.01em;
font-size:26px;margin:0 0 4px}
.card p.hint{color:var(--muted);font-size:14px;margin:0 0 20px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.grid2{grid-template-columns:1fr}}
label{display:block;margin:0 0 14px}
label span{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);margin-bottom:6px}
input,select,textarea{width:100%;border:1px solid var(--line);border-radius:6px;
padding:10px 12px;font:inherit;color:var(--ink);background:var(--surface)}
input:focus,select:focus,textarea:focus{outline:2px solid var(--blue-bg);border-color:var(--blue-fg)}
.badge{display:inline-block;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
border-radius:9999px;padding:2px 10px;background:var(--blue-bg);color:var(--blue-fg)}
.badge.free{background:var(--green-bg);color:var(--green-fg)}
button{background:var(--ink);color:#fff;border:0;border-radius:6px;
padding:10px 18px;font:inherit;cursor:pointer;margin:4px 8px 0 0}
button:active{transform:scale(.98)}button.secondary{background:transparent;color:var(--ink);
border:1px solid var(--line)}
pre{background:var(--canvas);border:1px solid var(--line);border-radius:8px;
padding:16px;white-space:pre-wrap;font-size:13px;max-height:320px;overflow:auto}
pre:empty{display:none}
.step{border-top:1px solid var(--line);padding-top:16px;margin-top:16px}
ul.files{list-style:none;margin:12px 0 0;padding:0}
ul.files li{padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
ul.files li:last-child{border-bottom:0}
ul.files code{font-family:"SF Mono","JetBrains Mono",ui-monospace,monospace;font-size:13px}
kbd{border:1px solid var(--line);border-radius:4px;background:var(--canvas);
padding:1px 6px;font-family:ui-monospace,monospace;font-size:12px}
footer{color:var(--muted);font-size:13px;text-align:center;margin-top:48px}
</style></head>
<body><main>
<header><p class="kicker">Nexus ARC</p>
<h1>Authoring</h1>
<p class="sub">Generate agents and workflows as versioned YAML. Every file is
validated by the framework before it is written. Serve with
<kbd>nexus init --gui</kbd>.</p></header>
<section class="card"><h2>Agents</h2>
<p class="hint">Registered under <code>agents/</code> and resolvable by agent type.</p>
<ul class="files" id="agent-list"></ul>
<h2 style="margin-top:28px">New agent</h2>
<div id="agent-fields"></div>
<button onclick="submitAgent()">Generate agent</button>
<pre id="a-out"></pre></section>
<section class="card"><h2>Workflows</h2>
<p class="hint">Stored under <code>workflows/</code>. Steps run top to bottom.</p>
<ul class="files" id="workflow-list"></ul>
<h2 style="margin-top:28px">New workflow</h2>
<div class="grid2">
<label><span>Name</span><input id="w-name" value="Hello"></label>
<label><span>Description</span><input id="w-desc" value="Starter workflow"></label>
</div>
<div id="steps"></div>
<button class="secondary" onclick="addStep()">Add step</button>
<button onclick="submitWorkflow()">Generate workflow</button>
<pre id="w-out"></pre></section>
<section class="card"><h2>Models</h2>
<p class="hint">Pick provider and model per profile. Paste the snippet into
<code>project_config.yaml</code> &mdash; both runtimes honor <code>model_profiles</code>.
Unavailable providers are marked; free models carry a badge.</p>
<div id="profiles"></div>
<button class="secondary" onclick="addProfileRow()">＋ Add profile</button>
<button onclick="submitProfiles()">Generate snippet</button>
<button class="secondary" onclick="copySnippet()">Copy</button>
<button class="secondary" id="apply-btn" onclick="applyProfiles()" style="display:none">Apply to bot config</button>
<pre id="m-out"></pre></section>
<section class="card"><h2>Tools</h2>
<p class="hint">Registered workflow tools on this host. Pick one, pass JSON
arguments, and run it &mdash; same registry workflows use.</p>
<ul class="files" id="tool-list"></ul>
<h2 style="margin-top:28px">Try a tool</h2>
<div id="tool-fields"></div>
<button onclick="callTool()">Run tool</button>
<pre id="t-out"></pre></section>
<footer>Files are written to the directory the UI was started in. Existing files are never overwritten.</footer>
</main>
<script>
async function post(path, body, out){
  const r = await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await r.json();
  document.getElementById(out).textContent = r.ok ? ("Saved " + data.file + "\\n\\n" + data.yaml) : ("Error " + r.status + ": " + (data.error || "?"));
  if(r.ok) refreshLists();
}
async function refreshLists(){
  const r = await fetch("/api/list"); if(!r.ok) return;
  const data = await r.json();
  fill("agent-list", data.agents, "agent"); fill("workflow-list", data.workflows, "workflow");
}
function fill(id, items, kind){
  document.getElementById(id).innerHTML = items.length
    ? items.map(n => "<li><code>" + kind + "s/" + n + ".yaml</code></li>").join("")
    : "<li>No " + kind + "s yet &mdash; generate one below.</li>";
}
function submitAgent(){post("/api/agent",{name:v("a-name"),description:v("a-desc"),agent_type:v("a-type"),provider:v("a-provider"),model:v("a-model"),timeout_seconds:v("a-timeout"),max_retries:v("a-retries"),purpose:v("a-purpose")},"a-out");}
let DEFS = {agent: [], step: [], tool_call: []};
function fieldHtml(f){
  const val = f.type === "select" ? "" : (f.default !== undefined ? ' value="' + escapeHtml(f.default) + '"' : "");
  const ph = f.placeholder ? ' placeholder="' + escapeHtml(f.placeholder) + '"' : "";
  let control;
  if(f.type === "select"){
    const opts = (f.options || []).map(o => '<option value="' + escapeHtml(o) + '"' + (o === f.default ? " selected" : "") + ">" + escapeHtml(o) + "</option>").join("");
    control = '<select id="' + f.id + '">' + opts + "</select>";
  } else if(f.type === "textarea"){
    control = '<textarea id="' + f.id + '" rows="3">' + escapeHtml(f.default || "") + "</textarea>";
  } else {
    const itype = f.type === "number" ? "number" : "text";
    control = '<input id="' + f.id + '" type="' + itype + '"' + val + ph + ">";
  }
  return "<label><span>" + escapeHtml(f.label) + "</span>" + control + "</label>";
}
async function loadForms(){
  const r = await fetch("/api/field-defs"); if(!r.ok) return;
  DEFS = (await r.json()).forms || DEFS;
  document.getElementById("agent-fields").innerHTML =
    DEFS.agent.map(fieldHtml).join("");
  document.getElementById("tool-fields").innerHTML =
    DEFS.tool_call.map(fieldHtml).join("");
  addStep();
  await loadTools();
}
function addStep(){
  const d = document.createElement("div");
  d.className = "step grid2";
  d.innerHTML = DEFS.step.map(f =>
    "<label><span>" + escapeHtml(f.label) + "</span>" +
    '<input class="' + f.id + '" value="' + escapeHtml(f.default || "") + '"></label>'
  ).join("");
  document.getElementById("steps").appendChild(d);
}
function submitWorkflow(){const steps=[...document.querySelectorAll("#steps .step")].map(d=>({id:q(d,".s-id"),name:q(d,".s-name"),agent_type:q(d,".s-type")}));post("/api/workflow",{name:v("w-name"),description:v("w-desc"),steps},"w-out");}
let CATALOG=[];
let CONFIG_PATH=null;
async function configStatus(){
  const r = await fetch("/api/config-status"); if(!r.ok) return;
  CONFIG_PATH = (await r.json()).project_config;
  if(CONFIG_PATH) document.getElementById("apply-btn").style.display = "";
}
function profileNames(){
  return [...document.querySelectorAll("#profiles .prow")].map(r => r.dataset.profile);
}
function collectProfiles(){
  const profiles = {};
  profileNames().forEach(profile => {
    profiles[profile] = {provider: v("p-" + profile), model: v("m-" + profile)};
  });
  return profiles;
}
async function applyProfiles(){
  const profiles = collectProfiles();
  const r = await fetch("/api/profiles/apply", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({profiles})});
  const data = await r.json();
  document.getElementById("m-out").textContent = r.ok
    ? ("Applied to " + data.file + "\nBackup: " + data.backup + "\nRestart the bot to take effect.")
    : ("Error " + r.status + ": " + (data.error || "?"));
}
configStatus();
async function loadProviders(){
  const r = await fetch("/api/providers"); if(!r.ok) return;
  CATALOG = (await r.json()).providers || [];
  const box = document.getElementById("profiles"); box.innerHTML = "";
  ["fast","reasoning"].forEach(addProfileRow);
}
function addProfileRow(profile){
  profile = ((typeof profile === "string" && profile) ? profile : (prompt("Profile name:") || "")).trim().toLowerCase();
  if(!profile || document.getElementById("p-" + profile)) return;
  const box = document.getElementById("profiles");
  const row = document.createElement("div"); row.className = "grid2 prow"; row.dataset.profile = profile;
    row.innerHTML = '<label><span>' + profile + ' &middot; provider</span><select id="p-' + profile + '"></select></label>' +
      '<label><span>' + profile + ' &middot; model</span><input id="m-' + profile + '" list="dl-' + profile + '" placeholder="provider default"><datalist id="dl-' + profile + '"></datalist></label>';
    box.appendChild(row);
    const sel = document.getElementById("p-" + profile);
    CATALOG.forEach(p => {
      const o = document.createElement("option"); o.value = p.name;
      o.textContent = p.name + (p.available ? "" : " (unavailable)");
      sel.appendChild(o);
    });
    sel.value = "opencode";
    sel.onchange = () => fillModels(profile);
    fillModels(profile);
}
function submitProfiles(){
  post("/api/profiles", {profiles: collectProfiles()}, "m-out");
}
function fillModels(profile){
  const prov = document.getElementById("p-" + profile).value;
  const entry = CATALOG.find(p => p.name === prov) || {models: []};
  const dl = document.getElementById("dl-" + profile);
  const input = document.getElementById("m-" + profile);
  dl.innerHTML = "";
  entry.models.forEach(m => {
    const o = document.createElement("option"); o.value = m.id;
    o.label = (m.free ? "free" : (m.name || m.id));
    dl.appendChild(o);
  });
  const firstFree = entry.models.find(m => m.free);
  if(firstFree && !input.value) input.value = firstFree.id;
  if(!entry.models.length && !input.placeholder) input.placeholder = "provider default";
}
function copySnippet(){navigator.clipboard.writeText(document.getElementById("m-out").textContent);}
async function loadTools(){
  const r = await fetch("/api/tools"); if(!r.ok) return;
  const tools = (await r.json()).tools || [];
  document.getElementById("tool-list").innerHTML = tools.length
    ? tools.map(t => "<li><code>" + t.name + "</code> <span class='badge'>" + (t.category || "general") + "</span><br>" + escapeHtml(t.description || "") + "</li>").join("")
    : "<li>No tools registered.</li>";
  const sel = document.getElementById("t-name");
  sel.innerHTML = "";
  tools.forEach(t => {
    const o = document.createElement("option"); o.value = t.name; o.textContent = t.name;
    sel.appendChild(o);
  });
}
function escapeHtml(s){return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
async function callTool(){
  let args = [], kwargs = {};
  try {
    args = JSON.parse(document.getElementById("t-args").value || "[]");
    kwargs = JSON.parse(document.getElementById("t-kwargs").value || "{}");
  } catch(e){ document.getElementById("t-out").textContent = "Error: invalid JSON in arguments"; return; }
  const r = await fetch("/api/tools/call", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: v("t-name"), args, kwargs})});
  const data = await r.json();
  document.getElementById("t-out").textContent = data.ok
    ? (typeof data.output === "string" ? data.output : JSON.stringify(data.output, null, 2))
    : ("Error: " + (data.error || r.status));
}
loadForms();
loadProviders();
const v=id=>document.getElementById(id).value;const q=(d,s)=>d.querySelector(s).value;
refreshLists();
</script></body></html>
"""


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain letters or digits")
    return slug


def render_agent_yaml(fields: dict[str, Any]) -> str:
    """Render and validate an agent YAML doc; raises ValueError on bad input."""
    name = str(fields.get("name") or "").strip()
    agent_type = str(fields.get("agent_type") or "").strip()
    provider = str(fields.get("provider") or "").strip().lower()
    if not name:
        raise ValueError("agent name is required")
    if not agent_type:
        raise ValueError("agent_type is required")
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider or '(empty)'}")
    model = str(fields.get("model") or "").strip()
    try:
        timeout = int(fields.get("timeout_seconds") or 300)
        retries = int(fields.get("max_retries") or 2)
    except (ValueError, TypeError) as exc:
        raise ValueError("timeout_seconds and max_retries must be integers") from exc
    return (
        'apiVersion: "nexus-arc/v1"\n'
        'kind: "Agent"\n'
        "metadata:\n"
        f'  name: "{name}"\n'
        f'  description: "{fields.get("description") or ""}"\n'
        '  version: "0.1.0"\n'
        "spec:\n"
        f'  agent_type: "{agent_type}"\n'
        f'  provider: "{provider}"\n'
        + (f'  model: "{model}"\n' if model else "")
        + f"  timeout_seconds: {timeout}\n"
        f"  max_retries: {retries}\n"
        "  purpose: |\n"
        + "".join(f"    {line}\n" for line in str(fields.get("purpose") or "").splitlines())
    )


def render_workflow_yaml(fields: dict[str, Any]) -> str:
    """Render a workflow YAML doc; validated with YamlWorkflowLoader by the caller."""
    from nexus.core.yaml_loader import YamlWorkflowLoader

    name = str(fields.get("name") or "").strip()
    steps = fields.get("steps") or []
    if not name:
        raise ValueError("workflow name is required")
    if not isinstance(steps, list) or not steps:
        raise ValueError("at least one step is required")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or not str(step.get("agent_type") or "").strip():
            raise ValueError(f"step {index}: agent_type is required")
    doc = {
        "apiVersion": "nexus-arc/v1",
        "kind": "Workflow",
        "metadata": {
            "name": name,
            "description": str(fields.get("description") or ""),
            "version": "0.1.0",
        },
        "steps": [
            {
                "id": str(step.get("id") or step.get("name") or f"step_{index}").strip(),
                "name": str(step.get("name") or step.get("id") or f"step_{index}").strip(),
                "agent_type": str(step.get("agent_type")).strip(),
                **({"prompt": str(step["prompt"])} if str(step.get("prompt") or "") else {}),
            }
            for index, step in enumerate(steps, start=1)
        ],
    }
    YamlWorkflowLoader.load_from_dict(doc)  # raises on invalid workflow
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def apply_model_profiles(
    config_path: str | Path, profiles: dict[str, dict[str, str]]
) -> dict[str, str]:
    """Merge validated profiles into project_config.yaml (with backup).

    Only the submitted profile names are touched; everything else is kept.
    The merged config must pass :func:`validate_project_config` or nothing
    is written. Note: pyyaml round-trip drops comments — the timestamped
    backup restores them.
    """
    import shutil
    import time

    from nexus.core.config.validators import validate_project_config

    render_model_profiles(profiles)  # validates shape first
    path = Path(config_path)
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(f"cannot read project config: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("project config must be a mapping")
    merged_profiles = dict(config.get("model_profiles") or {})
    merged_priority = dict(config.get("profile_provider_priority") or {})
    for profile, entry in profiles.items():
        name = str(profile).strip().lower()
        provider = str(entry.get("provider")).strip().lower()
        merged_profiles[name] = {provider: str(entry.get("model")).strip()}
        merged_priority[name] = [provider]
    candidate = dict(config)
    candidate["model_profiles"] = merged_profiles
    candidate["profile_provider_priority"] = merged_priority
    validate_project_config(candidate)
    backup = path.with_name(f"{path.stem}.bak-{int(time.time())}{path.suffix}")
    shutil.copy2(path, backup)
    path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
    return {"file": str(path), "backup": str(backup)}


def _write(target_dir: Path, subdir: str, slug: str, content: str) -> Path:
    path = target_dir / subdir / f"{slug}.yaml"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


FIELD_DEFS: dict[str, list[dict[str, Any]]] = {
    "agent": [
        {"id": "a-name", "label": "Name", "type": "text", "default": "Triage"},
        {"id": "a-type", "label": "agent_type", "type": "text", "default": "triage"},
        {"id": "a-desc", "label": "Description", "type": "text",
         "default": "Triage incoming requests"},
        {"id": "a-provider", "label": "Provider", "type": "select",
         "options": list(PROVIDERS), "default": "opencode"},
        {"id": "a-model", "label": "Model override (optional, spec.model)",
         "type": "text", "placeholder": "provider default"},
        {"id": "a-timeout", "label": "Timeout (seconds)", "type": "number",
         "default": "300"},
        {"id": "a-retries", "label": "Max retries", "type": "number", "default": "2"},
        {"id": "a-purpose", "label": "Purpose", "type": "textarea",
         "default": "Classify the request and set priority."},
    ],
    "step": [
        {"id": "s-id", "label": "Step id", "type": "text", "default": "triage"},
        {"id": "s-name", "label": "Step name", "type": "text", "default": "Triage request"},
        {"id": "s-type", "label": "agent_type", "type": "text", "default": "triage"},
    ],
    "tool_call": [
        {"id": "t-name", "label": "Tool", "type": "select", "options": [],
         "options_from": "/api/tools"},
        {"id": "t-args", "label": "Arguments (JSON list)", "type": "text",
         "default": '["hello world"]'},
        {"id": "t-kwargs", "label": "Keyword arguments (JSON object)", "type": "text",
         "default": "{}"},
    ],
}


def get_field_defs() -> dict[str, list[dict[str, Any]]]:
    """Form schemas driving the UI; new providers/tools appear with no page edits."""
    return FIELD_DEFS


def list_providers(cli_binaries: dict[str, str] | None = None) -> list[dict]:
    """ARC providers with local availability and model catalog.

    ``cli_binaries`` maps provider -> binary for availability probing
    (defaults to provider name on PATH, or OPENCODE_CLI_PATH for opencode).
    Models come from the local model catalog when reachable; entries carry
    ``free: true`` when any cost tier is zero.
    """
    from nexus.init_project import PROVIDERS

    binaries = dict(cli_binaries or {})
    opencode_bin = (
        binaries.get("opencode") or os.getenv("OPENCODE_CLI_PATH", "opencode")
    ).strip() or "opencode"
    catalog: list[dict] = []
    try:
        result = subprocess.run(
            [opencode_bin, "api", "get", "/api/model"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for entry in (json.loads(result.stdout or "{}").get("data") or []):
                costs = entry.get("cost") or []
                catalog.append(
                    {
                        "id": f"{entry.get('providerID')}/{entry.get('modelID')}",
                        "name": entry.get("name") or entry.get("modelID"),
                        "free": any(
                            (tier.get("input") or 0) == 0 and (tier.get("output") or 0) == 0
                            for tier in costs
                        ),
                    }
                )
    except Exception:
        catalog = []

    providers = []
    for provider in PROVIDERS:
        binary = binaries.get(provider, opencode_bin if provider == "opencode" else provider)
        available = bool(shutil.which(binary))
        if available and provider == "opencode":
            try:
                probe = subprocess.run(
                    [binary, "--version"], capture_output=True, timeout=10
                )
                available = probe.returncode == 0
            except Exception:
                available = False
        providers.append(
            {
                "name": provider,
                "available": available,
                "models": catalog if provider == "opencode" else [],
            }
        )
    return providers


def render_model_profiles(profiles: dict[str, dict[str, str]]) -> str:
    """Render a validated model_profiles (+ priority) snippet for project_config.yaml."""
    from nexus.init_project import PROVIDERS

    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("at least one profile is required")
    snippet_profiles: dict[str, dict[str, str]] = {}
    priority: dict[str, list[str]] = {}
    for profile, entry in profiles.items():
        name = str(profile or "").strip().lower()
        if not name:
            raise ValueError("profile names must be non-empty")
        if not isinstance(entry, dict):
            raise ValueError(f"profile '{name}' must map provider/model")
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        if provider not in PROVIDERS:
            raise ValueError(f"profile '{name}': unsupported provider '{provider}'")
        if not model:
            raise ValueError(f"profile '{name}': model is required")
        snippet_profiles[name] = {provider: model}
        priority[name] = [provider]
    return (
        yaml.safe_dump({"model_profiles": snippet_profiles}, sort_keys=False)
        + yaml.safe_dump({"profile_provider_priority": priority}, sort_keys=False)
    )


def make_handler(target_dir: Path, project_config_path: str | Path | None = None):
    """Build a request handler class bound to *target_dir*."""

    class GuiHandler(BaseHTTPRequestHandler):
        server_version = "NexusInitGui/1"

        def log_message(self, *args):
            pass

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/api/list":
                def _names(subdir: str) -> list[str]:
                    root = target_dir / subdir
                    if not root.is_dir():
                        return []
                    return sorted(p.stem for p in root.glob("*.yaml"))
                return self._send_json(
                    200, {"agents": _names("agents"), "workflows": _names("workflows")}
                )
            if route == "/api/providers":
                return self._send_json(200, {"providers": list_providers()})
            if route == "/api/config-status":
                return self._send_json(
                    200, {"project_config": str(project_config_path or "") or None}
                )
            if route == "/api/field-defs":
                return self._send_json(200, {"forms": get_field_defs()})
            if route == "/api/tools":
                from nexus.tools import tool_registry

                items = []
                for name in tool_registry.names():
                    spec = tool_registry.get(name)
                    if spec is None:
                        continue
                    items.append(
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "category": spec.category,
                        }
                    )
                return self._send_json(200, {"tools": items})
            if route != "/":
                return self._send_json(404, {"error": "not found"})
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                fields = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send_json(400, {"error": "invalid JSON body"})
            path = urlparse(self.path).path
            try:
                if path == "/api/agent":
                    content = render_agent_yaml(fields)
                    saved = _write(target_dir, "agents", _slug(fields.get("name")), content)
                elif path == "/api/workflow":
                    content = render_workflow_yaml(fields)
                    saved = _write(target_dir, "workflows", _slug(fields.get("name")), content)
                elif path == "/api/profiles":
                    snippet = render_model_profiles(fields.get("profiles") or {})
                    return self._send_json(200, {"yaml": snippet})
                elif path == "/api/profiles/apply":
                    if project_config_path is None:
                        return self._send_json(
                            409, {"error": "no project config configured (start with --config)"}
                        )
                    try:
                        applied = apply_model_profiles(
                            project_config_path, fields.get("profiles") or {}
                        )
                    except (ValueError, OSError) as exc:
                        return self._send_json(400, {"error": str(exc)})
                    return self._send_json(200, applied)
                elif path == "/api/tools/call":
                    import asyncio as _asyncio

                    from nexus.tools import tool_registry as _registry

                    name = str(fields.get("name") or "").strip()
                    if not name:
                        return self._send_json(400, {"error": "tool name is required"})
                    args = fields.get("args") or []
                    kwargs = fields.get("kwargs") or {}
                    if not isinstance(args, list) or not isinstance(kwargs, dict):
                        return self._send_json(
                            400, {"error": "args must be a list and kwargs an object"}
                        )
                    result = _asyncio.run(_registry.call(name, *args, **kwargs))
                    return self._send_json(
                        200,
                        {"ok": result.ok, "output": result.output, "error": result.error},
                    )
                else:
                    return self._send_json(404, {"error": "not found"})
            except (ValueError, FileExistsError) as exc:
                return self._send_json(400 if isinstance(exc, ValueError) else 409, {"error": str(exc)})
            return self._send_json(200, {"file": str(saved), "yaml": content})

    return GuiHandler


def serve_gui(
    target_dir: str | Path,
    port: int = 5002,
    host: str = "127.0.0.1",
    project_config_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    """Start the authoring UI (blocking); Ctrl-C to stop.

    Binds localhost by default with no auth. Pass ``host="0.0.0.0"`` only
    behind trusted networking (e.g. the authoring container) — the UI can
    write files to *target_dir* and, with *project_config_path*, edit the
    live bot config (always with timestamped backup + validation gate).
    """
    resolved_config = project_config_path
    server = ThreadingHTTPServer(
        (host, port), make_handler(Path(target_dir), resolved_config)
    )
    print(f"Nexus ARC authoring UI at http://{host}:{server.server_port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return server

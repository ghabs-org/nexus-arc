"use strict";
const inbox={items:[],selected:null};
const reviewNames={gate:"Workflow approval",campaign:"Publishing review",effect:"External action recovery",remote:"Remote input",recovery:"Workflow recovery"};
function renderInbox(){
  const selected=$("inbox-filter").value,items=inbox.items.filter(item=>!selected||item.kind===selected);
  $("inbox-count").textContent=inbox.items.length;
  $("inbox-content").innerHTML=items.length?items.map(item=>`<article class="library-row"><div><span class="eyebrow">${escapeHtml(reviewNames[item.kind])}</span><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.status)}${item.owner_id?` · ${escapeHtml(item.owner_id)}`:""}</p></div><button class="secondary" data-inbox-review="${escapeHtml(item.id)}">Review details</button></article>`).join(""):empty("Nothing waiting for review","New approvals and recovery requests will appear here.");
}
async function refreshInbox(){if(!state.ready)return;try{inbox.items=(await api("/api/workbench/inbox")).items;renderInbox();$("inbox-error").textContent="";}catch(error){$("inbox-error").textContent=error.message;}}
function inboxSummary(item){
  const detail=item.detail,run=detail.run||{},saved=detail.workflow||{},graph=saved.graph||{},campaign=detail.campaign;
  if(campaign){const channels=campaign.state?.content_bundle?.platforms||[];return `<p>Revision ${campaign.revision} · ${escapeHtml(campaign.status)}</p>${channels.map(channel=>`<h3>${escapeHtml(channel.platform)}</h3><pre class="review-copy">${escapeHtml(channel.copy)}</pre>`).join("")}<p>${campaign.validation?.passed?"Content and account checks passed for this revision.":"Check the recorded outcomes before continuing."}</p>`;}
  const steps=graph.nodes||saved.workflow?.steps||saved.steps||[];
  return `<p><strong>Task</strong><br>${escapeHtml(run.task||item.title)}</p>${run.awaiting_approval?.length?`<p><strong>Waiting for your decision</strong><br>${run.awaiting_approval.map(gate=>escapeHtml(gate.name)).join(", ")}</p>`:""}${detail.effect?`<p><strong>External action</strong><br>${escapeHtml(detail.effect.connector_id)} · ${escapeHtml(detail.effect.action)}</p>${detail.effect.arguments?`<pre class="review-copy">${escapeHtml(JSON.stringify(detail.effect.arguments,null,2))}</pre>`:""}`:""}${steps.length?`<h3>Workflow steps</h3><ol>${steps.map(step=>`<li>${escapeHtml(step.name||step.id)}${step.kind==="integration"?` · external action: ${escapeHtml(step.action)} (${escapeHtml(step.connector_id)})`:step.kind==="approval"?" · your approval":""}</li>`).join("")}</ol>`:""}${run.output||Object.values(saved.outputs||{}).length?`<h3>Results so far</h3><pre class="review-copy">${escapeHtml(run.output||Object.values(saved.outputs).join("\n\n"))}</pre>`:""}${run.error?`<p class="form-error">${escapeHtml(run.error)}</p>`:""}`;
}
async function reviewInbox(id){
  const item=await api(`/api/workbench/inbox/${encodeURIComponent(id)}`);inbox.selected=item;
  const effect=item.kind==="effect";
  showDetail(reviewNames[item.kind],`<h3>${escapeHtml(item.title)}</h3><p>${effect?"Inspect the remote account before recording an outcome. Allowing another attempt does not run it; resume its checkpoint separately.":item.kind==="campaign"?"Approval records your decision for this revision. Open campaigns to review and confirm publishing.":"Review the saved task, workflow and pending steps before continuing."}</p>${inboxSummary(item)}<details><summary>Technical details</summary><pre class="workbench-output">${escapeHtml(JSON.stringify(item.detail,null,2))}</pre></details><a class="button-link secondary" href="${escapeHtml(item.link)}" data-review-location>Open full controls</a>${item.actions.length?`<form id="inbox-review-form"><label>Decision<select name="decision">${item.actions.map(action=>`<option value="${action}">${({approve:"Approve",reject:"Reject",completed:"Remote action already completed",retry:"Allow one reviewed retry"})[action]}</option>`).join("")}</select></label><label>Review notes<textarea name="notes" maxlength="2000"></textarea></label><label class="check-label"><input name="confirm" type="checkbox" required> I reviewed these details and confirm this decision</label><p id="inbox-review-error" class="form-error" role="alert"></p><button type="submit">Record decision</button></form>`:'<p>Use the full controls to continue. Decisions are available only to the responsible account.</p>'}`);
}
$("inbox-refresh").addEventListener("click",refreshInbox);$("inbox-filter").addEventListener("change",renderInbox);
document.addEventListener("click",event=>{const button=event.target.closest("[data-inbox-review]");if(button)reviewInbox(button.dataset.inboxReview).catch(error=>notify(error.message));if(event.target.closest("[data-review-location]"))$("detail").close();});
document.addEventListener("submit",async event=>{if(event.target.id!=="inbox-review-form")return;event.preventDefault();const button=event.target.querySelector("button"),item=inbox.selected;button.disabled=true;try{const values=new FormData(event.target),result=await api(`/api/workbench/inbox/${encodeURIComponent(item.id)}/review`,{digest:item.digest,decision:values.get("decision"),notes:values.get("notes"),confirm:values.has("confirm")});$("detail").close();notify("Decision recorded.");await refreshInbox();if(result.id&&["queued","running"].includes(result.status))pollRun(result.id).then(refreshInbox).catch(()=>refreshInbox());}catch(error){$("inbox-review-error").textContent=error.message;}finally{button.disabled=false;}});
window.addEventListener("hashchange",()=>{if(view()==="inbox")refreshInbox();});
window.addEventListener("studio-ready",refreshInbox);window.addEventListener("studio-review-changed",refreshInbox);
window.addEventListener("focus",()=>{if(view()==="inbox")refreshInbox();});
setInterval(()=>{if(state.ready&&!document.hidden&&navigator.onLine!==false)refreshInbox();},20000);

let installPrompt=null,appRegistration=null;
function isDesktopOrStandalone(){
  return Boolean(
    (typeof matchMedia==="function"&&matchMedia("(display-mode: standalone)").matches) ||
    (typeof navigator!=="undefined"&&navigator.standalone) ||
    (typeof window!=="undefined"&&(window.__TAURI__||window.__TAURI_INTERNALS__)) ||
    (typeof location!=="undefined"&&location.search&&(location.search.includes("desktop=1")||(typeof URLSearchParams!=="undefined"&&new URLSearchParams(location.search).has("desktop")))) ||
    (typeof document!=="undefined"&&document.cookie&&document.cookie.includes("nexus_desktop=1")) ||
    (typeof sessionStorage!=="undefined"&&sessionStorage.getItem("nexus-desktop")==="1")
  );
}
function hideInstallAppIfDesktop(){
  if(isDesktopOrStandalone()){
    if(typeof sessionStorage!=="undefined")sessionStorage.setItem("nexus-desktop","1");
    if(typeof document!=="undefined"){
      document.documentElement.classList.add("desktop-app");
      if(document.body)document.body.classList.add("desktop-app");
    }
    const btn=$("install-app");
    if(btn)btn.hidden=true;
  }
}
hideInstallAppIfDesktop();
window.addEventListener("beforeinstallprompt",event=>{if(isDesktopOrStandalone())return;event.preventDefault();installPrompt=event;});
window.addEventListener("appinstalled",()=>{const btn=$("install-app");if(btn)btn.hidden=true;installPrompt=null;});
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",hideInstallAppIfDesktop);
$("install-app").addEventListener("click",async()=>{
  if(installPrompt){const prompt=installPrompt;installPrompt=null;await prompt.prompt();await prompt.userChoice;return;}
  showDetail("Install Nexus Studio",'<p>On iPhone or iPad, open this workspace in Safari, tap Share, then Add to Home Screen. On Android or desktop, use your browser’s Install app or Add to Home Screen menu.</p><p>Installation needs HTTPS or localhost. To use your computer’s workspace from a phone, open its authenticated team HTTPS address.</p><p>The app can open its interface offline. Running agents, reviewing approvals and loading private data need a connection. No actions are queued offline.</p>');
});
async function registerStudioApp(){
  if(!("serviceWorker" in navigator)||!window.isSecureContext)return;
  try{appRegistration=await navigator.serviceWorker.register("/service-worker.js",{scope:"/",updateViaCache:"none"});
    const showUpdate=()=>{if(appRegistration.waiting&&navigator.serviceWorker.controller)$("app-update").hidden=false;};showUpdate();
    appRegistration.addEventListener("updatefound",()=>{const worker=appRegistration.installing;worker?.addEventListener("statechange",()=>{if(worker.state==="installed")showUpdate();});});
  }catch{notify("App installation is unavailable. Browser access still works.");}
}
$("app-update-button").addEventListener("click",()=>{if(watchedRuns.size){notify("Runs are still being monitored. Update after they finish, or reopen the app and reconnect through Activity.");return;}updateRequested=true;appRegistration?.waiting?.postMessage({type:"ACTIVATE_UPDATE"});});
let updateRequested=false;
let refreshingApp=false;navigator.serviceWorker?.addEventListener("controllerchange",()=>{if(refreshingApp||!updateRequested)return;refreshingApp=true;if(appRegistration?.active)location.reload();});
registerStudioApp();

const connectionGuide={record:null,check:null};
const guideServices={github:"GitHub",gitlab:"GitLab",telegram:"Telegram",discord:"Discord",slack:"Slack",openclaw:"OpenClaw",mcp:"MCP server",http:"HTTP GET",sqlite:"SQLite"};
function guideAccountHelp(type){
  if(state.mode==="team")return '<p>Use your existing Nexus account setup for personal credentials. Workspace bot credentials and installed MCP servers are configured by the server administrator.</p>'+`<a class="button-link secondary" href="${escapeHtml(state.accountUrl||"/login")}" target="_blank" rel="noopener">Open Nexus account setup</a>`;
  return `<p>${type==="mcp"?"Install and review the MCP server locally first. Enter its executable and environment variable names below.":type==="sqlite"?"Keep the database inside this workspace. Only read-only queries are allowed.":type==="http"?"Choose the final endpoint URL. Redirects are blocked. Private network access needs explicit permission.":"Configure credentials in the environment that starts Studio, then restart it. Enter environment variable names here; keep secret values outside the browser."}</p>`;
}
async function startConnectionGuide(type,id){
  connectionGuide.check=null;
  if(id){connectionGuide.record=await api(`/api/catalog/connectors/${encodeURIComponent(id)}`);return showConnectionCheck();}
  if(!guideServices[type])return;
  connectionGuide.record=null;
  const git=["github","gitlab"].includes(type),messaging=["telegram","discord","slack","openclaw"].includes(type);
  const credential=type!=="sqlite"&&type!=="mcp"?inputField("token_env","Credential environment variable",({github:"GITHUB_TOKEN",gitlab:"GITLAB_TOKEN",telegram:"TELEGRAM_BOT_TOKEN",discord:"DISCORD_BOT_TOKEN",slack:"SLACK_BOT_TOKEN",openclaw:"OPENCLAW_TOKEN"})[type]||""):"";
  showDetail(`Set up ${guideServices[type]}`,`<p class="eyebrow">1 OF 3 · SAVE CONNECTION</p>${guideAccountHelp(type)}<form id="connection-guide-form" data-type="${type}">${inputField("name","Connection name",guideServices[type])}${git?inputField("repo","Repository (owner/project)"):""}${["gitlab","openclaw"].includes(type)?inputField("base_url","Service origin",type==="gitlab"?"https://gitlab.com":""):""}${credential}${messaging?inputField("channel","Fixed destination channel or chat ID")+'<label>Account<select name="credential_scope"><option value="personal">Each user’s personal account</option><option value="service">Workspace bot account</option></select></label>':""}${type==="http"?inputField("url","Endpoint URL")+'<label class="check-label"><input type="checkbox" name="allow_private"> Allow private network destinations</label>':""}${type==="sqlite"?inputField("path","Database path inside workspace"):""}${type==="mcp"?inputField("command","Trusted installed executable")+'<label>Arguments (JSON list)<textarea name="args">[]</textarea></label><label>Environment references (JSON object)<textarea name="env">{}</textarea></label><label class="check-label"><input type="checkbox" name="persistent"> Keep sessions open between calls</label>':""}<p>Starts disabled, with read-only access. Add specific write permissions or MCP tools later in connection settings.</p><p class="form-error" id="connection-guide-error" role="alert"></p><button type="submit">Save and continue</button></form>`);
}
function showConnectionCheck(){
  const record=connectionGuide.record,check=connectionGuide.check,type=record.data.type,messaging=["telegram","slack","discord","openclaw"].includes(type);
  showDetail(`Set up ${record.name}`,`<p class="eyebrow">${check?"3 OF 3 · ENABLE":"2 OF 3 · CHECK ACCESS"}</p>${guideAccountHelp(type)}<p>${messaging?"This check confirms credential configuration only. It does not send messages or verify service authorization.":type==="mcp"?"This starts your installed MCP executable and discovers tools. No tool is called.":"This performs a read-only request. No issues, posts or messages are created."}</p><details><summary>Saved connection settings</summary><pre class="workbench-output">${escapeHtml(JSON.stringify(record.data,null,2))}</pre></details>${check?`<p class="callout">${escapeHtml(check.message)}</p><form id="connection-enable-form"><label class="check-label"><input name="confirm" type="checkbox" required> Enable this reviewed connection</label><button type="submit">Enable connection</button></form>`:'<button id="connection-check">Run read-only check</button>'}<p class="form-error" id="connection-guide-error" role="alert"></p><p id="connection-guide-status" role="status"></p>${messaging?'<p>For incoming messages, create an Entrypoint with a target agent, verified signatures, mapped users and allowed channels. Register its URL in your service’s developer settings, then enable it. Open Entrypoints → Incoming URL for its exact address.</p><a class="button-link secondary" href="#entrypoints" data-review-location>Set up incoming messages</a>':""}`);
}
document.addEventListener("click",async event=>{const button=event.target.closest("button");if(!button)return;try{if(button.dataset.guideCreate)await startConnectionGuide(button.dataset.guideCreate);if(button.dataset.guideResume)await startConnectionGuide(null,button.dataset.guideResume);if(button.id==="connection-check"){button.disabled=true;$("connection-guide-status").textContent="Checking…";try{connectionGuide.check=await api(`/api/workbench/onboarding/${encodeURIComponent(connectionGuide.record.id)}/check`,{revision:connectionGuide.record.revision});showConnectionCheck();}finally{button.disabled=false;}}}catch(error){if($("connection-guide-error"))$("connection-guide-error").textContent=error.message;else notify(error.message);}});
document.addEventListener("submit",async event=>{
  const form=event.target;if(!["connection-guide-form","connection-enable-form"].includes(form.id))return;event.preventDefault();const button=form.querySelector("button");button.disabled=true;
  try{const values=new FormData(form);if(form.id==="connection-guide-form"){const type=form.dataset.type,data=Object.fromEntries(values);delete data.name;Object.assign(data,{type,enabled:false,allow_private:values.has("allow_private"),persistent:values.has("persistent")});if(type==="mcp"){data.args=JSON.parse(values.get("args")||"[]");data.env=JSON.parse(values.get("env")||"{}");data.actions=["discover"];data.tools=[];data.read_only_tools=[];}else if(["github","gitlab"].includes(type))data.actions=["list_issues"];else if(!["http","sqlite"].includes(type))data.actions=["status"];connectionGuide.record=await api("/api/catalog/connectors",{name:values.get("name"),data});showConnectionCheck();}else{await api(`/api/workbench/onboarding/${encodeURIComponent(connectionGuide.record.id)}/enable`,{digest:connectionGuide.check.digest,confirm:values.has("confirm")});$("detail").close();notify("Connection enabled. Edit its settings to grant specific actions or tools.");}if(library.kind==="connectors")await refreshLibrary();}catch(error){$("connection-guide-error").textContent=error.message;}finally{button.disabled=false;}
});

const GUIDE_VERSION = 2;
const DEFAULT_GUIDE_STEPS = [
  {
    id: "workspace-navigation",
    version: 1,
    title: "Workspace navigation",
    view: "chat",
    target: "#workspace-navigation",
    fallback_target: ".sidebar",
    min_role: "viewer",
    summary: "Move between Conversations, Quick Build, Agent Library, Workflows, Knowledge and Connections, and the Activity and Approval Inbox from the navigation rail.",
    branch_title: "Keyboard & mobile navigation",
    branch_body: "On narrow screens, open the Navigation drawer from the top bar. You can move through guide steps with Back and Next or the Left and Right arrow keys.",
    practice: {
      label: "Preview workspace map",
      preview: "Sample map (local preview only): Work holds daily Conversations and Quick Build; Resources holds reusable Agents, Workflows, and Knowledge; Operations tracks Runs and Approvals."
    }
  },
  {
    id: "providers",
    version: 1,
    title: "Provider and account selection",
    view: "providers",
    target: "#view-providers",
    fallback_target: "#chat-provider",
    min_role: "viewer",
    summary: "Studio supports subscription-backed CLI tools (Codex, Claude Code, Antigravity, Copilot, OpenCode) alongside API-billed providers (OpenAI and Claude APIs). Review readiness here without starting sign-in or running a live check.",
    branch_title: "Subscription CLI vs API keys",
    branch_body: "Subscription CLIs reuse your local CLI sign-in (or an isolated user container in shared mode), while API providers bill per token using configured environment credentials.",
    practice: {
      label: "Compare provider modes",
      preview: "Sample comparison (local preview only): CLI subscription = existing account login + effort controls; API key = direct HTTP requests billed by token usage."
    }
  },
  {
    id: "conversations",
    version: 1,
    title: "Conversations",
    view: "chat",
    target: "#chat-form",
    fallback_target: "#view-chat",
    min_role: "operator",
    summary: "Pick an assistant (including the built-in Studio Assistant), provider, model, and reasoning effort, attach workspace files with drag-and-drop chips and artifact cards, and inspect structured answers and run history in one thread.",
    branch_title: "Working with attachments & effort",
    branch_body: "Drop files onto the upload target or select indexed workspace artifacts. Choose Studio Assistant for zero-config workspace help without using provider tokens.",
    practice: {
      label: "Try sample conversation preview",
      preview: "Sample prompt (not sent to any provider): 'Summarize open release risks in CHANGELOG.md and list three verification checks.' → Produces a structured checklist in the conversation panel."
    }
  },
  {
    id: "quick-build",
    version: 1,
    title: "Quick Build",
    view: "quick-build",
    target: "#builder-form",
    fallback_target: "#view-quick-build",
    min_role: "builder",
    summary: "Describe what you want to automate in plain language, then review the proposed agents, workflow steps, and file diffs before applying anything to your workspace.",
    branch_title: "Reviewing generated diffs safely",
    branch_body: "Quick Build stages a draft proposal first. You can inspect every generated definition and run a test before saving changes to disk.",
    practice: {
      label: "Preview sample build draft",
      preview: "Sample build draft (local preview only): Goal 'Triage incoming support notes' → Proposes 1 classifier agent + 1 two-step workflow for review before saving."
    }
  },
  {
    id: "agents",
    version: 1,
    title: "Agents",
    view: "agents",
    target: "#view-agents",
    fallback_target: "#agents-content",
    min_role: "builder",
    summary: "Create and manage specialist agents with instructions, provider, model, allowed tools, multiple skill assignments, and output guardrails, or use ready-made built-in specialists.",
    branch_title: "Versioning, multi-skills, and instructions",
    branch_body: "Every saved change records a version snapshot. You can assign multiple skills to an agent, import skill bundles from GitHub, or consult built-in specialists like Studio Assistant.",
    practice: {
      label: "Preview sample agent card",
      preview: "Sample specialist (local preview only): 'Release Auditor' using Claude Code (high effort) with read-only repository tools, multiple skills, and a 600s timeout."
    }
  },
  {
    id: "workflows",
    version: 1,
    title: "Workflows",
    view: "workflows",
    target: "#view-workflows",
    fallback_target: "#create-workflow",
    min_role: "builder",
    summary: "Inspect multi-step workflows, conditional branches, visual canvas nodes, input forms, and saved YAML definitions, or trigger workflows directly from schedules.",
    branch_title: "Canvas, YAML, and checkpoints",
    branch_body: "Switch between the step builder, visual canvas, and YAML view. Edit or remove steps, resume checkpoints, or schedule workflows with custom input payloads.",
    practice: {
      label: "Preview sample workflow branch",
      preview: "Sample workflow (local preview only): Step 1 'Analyze request' → Branch on confidence → Step 2 'Prepare response' or pause at an approval gate."
    }
  },
  {
    id: "projects",
    version: 1,
    title: "Knowledge projects",
    view: "projects",
    target: "#view-projects",
    fallback_target: "#projects-content",
    min_role: "builder",
    summary: "Organize shared instructions, reference documents, and related conversations into Knowledge projects so agents can ground their answers in your project material.",
    branch_title: "Document retrieval and citations",
    branch_body: "When a conversation selects a Knowledge project, matching passages are cited alongside the assistant response and can be exported or imported as a portable package.",
    practice: {
      label: "Preview sample knowledge project",
      preview: "Sample project (local preview only): 'Architecture RFCs' with 3 indexed markdown specs and automatic source citations in linked chats."
    }
  },
  {
    id: "workspace-files",
    version: 2,
    title: "Workspace files",
    view: "files",
    target: "#view-files",
    fallback_target: "#files-list",
    min_role: "builder",
    summary: "Browse and preview files in allowed server folders, upload documents up to 16 MiB, or create folders directly on the Studio VM.",
    branch_title: "Allowed roots and server security",
    branch_body: "Only explicitly configured folders (such as agents, workflows, and authorized project roots) can be opened. Files stay on the server until explicitly shared or attached.",
    practice: {
      label: "Preview workspace file browser",
      preview: "Sample browser (local preview only): Root 'agents' → Lists specialist markdown specs with file sizes and instant previews."
    }
  },
  {
    id: "connectors-knowledge",
    version: 1,
    title: "Library and connectors",
    view: "library",
    target: "#integrations-panel",
    fallback_target: "#view-library",
    min_role: "owner",
    summary: "Manage reusable Prompts, Skills, Artifacts, Toolsets, Connectors, Entrypoints, Schedules, Evaluations, External A2A agents, and Model profiles in the Library.",
    branch_title: "Schedules, skills, and connector checks",
    branch_body: "Schedules can trigger either an agent or a ready-made workflow with custom inputs. Skills support multi-selection import from GitHub, and connectors require read-only verification before enabling.",
    practice: {
      label: "Preview connector readiness states",
      preview: "Sample readiness (local preview only): Draft configuration → Read-only revision check → Explicit owner confirmation → Enabled for selected agents."
    }
  },
  {
    id: "tools",
    version: 1,
    title: "Registered tools",
    view: "tools",
    target: "#view-tools",
    fallback_target: "#tools-content",
    min_role: "operator",
    summary: "Browse the registered Nexus tools available to your agents and workflow tool nodes, inspect their categories, or run an isolated tool test with explicit JSON arguments.",
    branch_title: "Role and policy scoping",
    branch_body: "In shared team workspaces, the active team policy controls which registered tools are exposed to operators and builders.",
    practice: {
      label: "Preview sample tool call",
      preview: "Sample tool (local preview only): 'text:wordcount' with args ['Release notes draft'] → Returns '3' through the local tool registry without calling an AI model."
    }
  },
  {
    id: "workbench",
    version: 1,
    title: "Workspace tools",
    view: "workbench",
    target: "#view-workbench",
    fallback_target: ".workbench-tabs",
    min_role: "operator",
    summary: "Use Workspace tools to manage conversation memory, resume SQLite workflow checkpoints, coordinate multi-agent delegation, draft publishing campaigns, inspect audit analytics, transcribe audio, run typed decisions, configure file storage, set prompt budgets, and review deployment checks.",
    branch_title: "Checkpoints, budgets, and readiness",
    branch_body: "Each tab focuses on an operational capability: capture or resume paused workflows in Checkpoints, cap history size in Controls, or inspect environment readiness in Deployment checks.",
    practice: {
      label: "Preview workspace tool tabs",
      preview: "Sample tabs (local preview only): Memory · Checkpoints · Agents · Campaigns · Analytics · Transcription · Decisions · File storage · Controls · Deployment checks."
    }
  },
  {
    id: "workspace-mesh",
    version: 1,
    title: "Workspace, mesh, and sharing",
    view: "workspace",
    target: "#view-workspace",
    fallback_target: "#workspace-runtime",
    min_role: "viewer",
    summary: "Inspect local runtime queue health, live execution events, and portable definition exports, plus Response feedback and read-only Conversation sharing from the workspace navigation.",
    branch_title: "Portable exports & feedback loops",
    branch_body: "Export definitions as a single JSON bundle, turn response ratings into evaluation cases in Response feedback, or create revocable read-only snapshots in Conversation sharing.",
    practice: {
      label: "Preview runtime health summary",
      preview: "Sample runtime summary (local preview only): Queue idle · 0 active runs · Definitions exportable as nexus-workspace.json."
    }
  },
  {
    id: "activity-inbox",
    version: 1,
    title: "Activity and approval inbox",
    view: "inbox",
    target: "#view-inbox",
    fallback_target: "#inbox-content",
    min_role: "operator",
    summary: "Review paused workflow gates, publishing drafts, external action recovery, and run traces in the Approval inbox and Activity screens before any external change takes effect.",
    branch_title: "Auditing runs and approvals",
    branch_body: "Approval items bind your decision to the exact reviewed digest, while Activity records every conversation, workflow, build, and tool run with step-by-step execution traces.",
    practice: {
      label: "Preview sample approval item",
      preview: "Sample inbox item (local preview only): Waiting gate 'Publish release summary'. Inspect proposed output, then Approve or Reject without leaving Studio."
    }
  },
  {
    id: "studio-assistant",
    version: 2,
    title: "Studio assistant",
    view: "assistant",
    target: "#view-assistant",
    fallback_target: "#studio-help-form",
    min_role: "viewer",
    summary: "Ask how Studio works or find your agents, workflows, and server files. Uses built-in documentation and authorized workspace metadata with zero provider tokens.",
    branch_title: "Local help vs Ask AI",
    branch_body: "Find local answer uses built-in documentation and current resource names without API keys. Ask AI optionally routes the question to your configured provider.",
    practice: {
      label: "Preview Studio help search",
      preview: "Sample query (local preview only): 'Where are my agents saved?' → Points directly to server folder path and links to the Agents editor."
    }
  },
  {
    id: "help-reopen",
    version: 1,
    title: "Help and reopening the guide",
    view: "chat",
    target: "#open-interactive-guide",
    fallback_target: "#help-guide-button",
    min_role: "viewer",
    summary: "Use Help → Interactive guide in the navigation menu or top bar at any time to resume where you left off or restart the walkthrough from the beginning.",
    branch_title: "Versioned updates",
    branch_body: "When new Studio capabilities are added in future releases, Help → Interactive guide highlights only the new steps without resetting your saved progress.",
    practice: {
      label: "Preview guide controls",
      preview: "Tip: Press Escape at any time to close the guide and return focus to the control you were using."
    }
  }
];

const ROLE_RANKS = {viewer: 0, operator: 1, builder: 2, owner: 3};
const interactiveGuide = {
  state: null,
  memoryFallback: {},
  open: false,
  activeTarget: null,
  triggerElement: null,
  originHash: null
};

function guideStorageKey() {
  const mode = state.mode || "desktop";
  const userId = state.user?.nexus_id || "desktop";
  const workspace = state.workspacePath || $("workspace-name")?.textContent || "local";
  return `nexus-guide:${mode}:${userId}:${workspace}`;
}

function readLocalGuideState() {
  const key = guideStorageKey();
  for (const store of [window.localStorage, window.sessionStorage]) {
    try {
      const raw = store?.getItem(key);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") return parsed;
      }
    } catch {}
  }
  return interactiveGuide.memoryFallback[key] || null;
}

function writeLocalGuideState(record) {
  const key = guideStorageKey();
  const sanitized = {
    version: GUIDE_VERSION,
    seen_version: Number.isInteger(record.seen_version) ? record.seen_version : 0,
    status: ["new", "in_progress", "completed", "dismissed"].includes(record.status) ? record.status : "new",
    current_step: record.current_step || DEFAULT_GUIDE_STEPS[0].id,
    completed_steps: Array.isArray(record.completed_steps) ? record.completed_steps : [],
    origin_view: record.origin_view || null,
    updated_at: new Date().toISOString()
  };
  interactiveGuide.memoryFallback[key] = sanitized;
  for (const store of [window.localStorage, window.sessionStorage]) {
    try {
      store?.setItem(key, JSON.stringify(sanitized));
    } catch {}
  }
  return sanitized;
}

function buildClientFallbackSteps() {
  const role = state.mode === "team" ? (state.user?.role || "viewer") : "owner";
  const rank = ROLE_RANKS[role] ?? 0;
  const providers = Array.isArray(state.providers) ? state.providers : [];
  const cliReady = providers.filter(p => p.kind === "cli" && p.configured).map(p => p.name);
  const apiReady = providers.filter(p => p.kind === "api" && p.configured).map(p => p.name);
  const cliAll = providers.filter(p => p.kind === "cli").map(p => p.name);
  const apiAll = providers.filter(p => p.kind === "api").map(p => p.name);
  return DEFAULT_GUIDE_STEPS.map((step, index) => {
    const restricted = rank < (ROLE_RANKS[step.min_role] ?? 0);
    const role_note = restricted
      ? `Your current role (${role}) can inspect this area in read-only mode; creating or changing items here requires the ${step.min_role} role.`
      : state.mode === "team"
      ? `Available for your ${role} role in this shared workspace.`
      : "Available in your local desktop workspace.";
    let capability_note = "";
    if (step.id === "providers") {
      const parts = [];
      if (cliReady.length) parts.push(`ready CLI subscriptions: ${cliReady.join(", ")}`);
      if (apiReady.length) parts.push(`ready API providers: ${apiReady.join(", ")}`);
      capability_note = parts.length
        ? `Currently detected (${parts.join("; ")}).`
        : `Supports CLI subscriptions (${cliAll.join(", ") || "Codex, Claude Code, Antigravity"}) and API keys (${apiAll.join(", ") || "OpenAI, Claude"}). None are marked ready yet. Review setup here when needed.`;
    } else if (step.id === "workspace-navigation") {
      const agentsCount = (state.agents || []).length;
      const workflowsCount = (state.workflows || []).length;
      capability_note = agentsCount === 0 && workflowsCount === 0
        ? "This workspace has no saved agents or workflows yet. You can start in Conversations or draft your first setup with Quick Build."
        : `This workspace currently includes ${agentsCount} agent(s) and ${workflowsCount} workflow(s).`;
    }
    return {
      ...step,
      index: index + 1,
      total: DEFAULT_GUIDE_STEPS.length,
      restricted,
      role_note,
      capability_note
    };
  });
}

function mergeGuideState(serverState, localState) {
  const steps = Array.isArray(serverState?.steps) && serverState.steps.length
    ? serverState.steps
    : buildClientFallbackSteps();
  const base = serverState && serverState.status !== "new"
    ? serverState
    : localState && localState.status && localState.status !== "new"
    ? {...(serverState || {}), ...localState}
    : serverState || localState || {};
  const status = ["new", "in_progress", "completed", "dismissed"].includes(base.status) ? base.status : "new";
  const current_step = steps.some(s => s.id === base.current_step) ? base.current_step : steps[0].id;
  const completed_steps = Array.isArray(base.completed_steps)
    ? base.completed_steps.filter(id => steps.some(s => s.id === id))
    : [];
  const seen_version = Number.isInteger(base.seen_version) ? base.seen_version : 0;
  const new_steps = steps
    .filter(s => (s.version || 1) > seen_version && ["completed", "dismissed"].includes(status))
    .map(s => s.id);
  return {
    version: GUIDE_VERSION,
    seen_version,
    status,
    current_step,
    completed_steps,
    origin_view: base.origin_view || null,
    upgrade_available: new_steps.length > 0,
    new_steps,
    role: serverState?.role || (state.mode === "team" ? (state.user?.role || "viewer") : "owner"),
    steps
  };
}

function updateGuideBadge() {
  const badgeEl = $("guide-status-badge");
  const navBtn = $("open-interactive-guide");
  if (!badgeEl || !interactiveGuide.state) return;
  const st = interactiveGuide.state;
  if (st.upgrade_available) {
    badgeEl.textContent = "Updated";
    badgeEl.hidden = false;
  } else if (st.status === "in_progress") {
    const idx = Math.max(1, st.steps.findIndex(s => s.id === st.current_step) + 1);
    badgeEl.textContent = `${idx}/${st.steps.length}`;
    badgeEl.hidden = false;
  } else {
    badgeEl.hidden = true;
  }
  if (navBtn) {
    navBtn.dataset.guideActive = interactiveGuide.open ? "true" : "false";
  }
}

async function persistGuideAction(action, extra = {}) {
  const current = interactiveGuide.state || mergeGuideState(null, readLocalGuideState());
  const payload = {action, ...extra};
  const nextLocal = {
    version: GUIDE_VERSION,
    seen_version: current.seen_version,
    status: current.status,
    current_step: extra.current_step || current.current_step,
    completed_steps: [...(current.completed_steps || [])],
    origin_view: extra.origin_view !== undefined ? extra.origin_view : current.origin_view
  };
  if (extra.completed_step && !nextLocal.completed_steps.includes(extra.completed_step)) {
    nextLocal.completed_steps.push(extra.completed_step);
  }
  if (action === "start") {
    nextLocal.status = "in_progress";
    nextLocal.seen_version = GUIDE_VERSION;
  } else if (action === "step") {
    nextLocal.status = "in_progress";
    nextLocal.seen_version = Math.max(nextLocal.seen_version, GUIDE_VERSION);
  } else if (action === "complete") {
    nextLocal.status = "completed";
    nextLocal.seen_version = GUIDE_VERSION;
    nextLocal.completed_steps = current.steps.map(s => s.id);
  } else if (action === "dismiss") {
    if (nextLocal.status !== "completed") nextLocal.status = "dismissed";
    nextLocal.seen_version = Math.max(nextLocal.seen_version, GUIDE_VERSION);
  } else if (action === "reset") {
    nextLocal.status = "new";
    nextLocal.current_step = current.steps[0].id;
    nextLocal.completed_steps = [];
    nextLocal.seen_version = 0;
  }
  writeLocalGuideState(nextLocal);
  interactiveGuide.state = mergeGuideState({...current, ...nextLocal}, nextLocal);
  updateGuideBadge();
  try {
    const saved = await api("/api/workbench/guide", payload);
    interactiveGuide.state = mergeGuideState(saved, nextLocal);
    writeLocalGuideState(interactiveGuide.state);
    updateGuideBadge();
  } catch {}
  return interactiveGuide.state;
}

function shouldAutoOfferGuide() {
  if (!state.ready || !$("team-login")?.hidden) return false;
  if (location.pathname === "/agent.html" || document.body.classList.contains("single-agent")) return false;
  if (state.busy || watchedRuns.size > 0) return false;
  if (Array.isArray(state.runs) && state.runs.some(r => ["running", "queued"].includes(r.status))) return false;
  if (state.sessionId) return false;
  if (view() === "inbox" || location.hash.startsWith("#inbox")) return false;
  if (document.querySelector("dialog[open]")) return false;
  const st = interactiveGuide.state;
  if (!st || st.status !== "new" || st.upgrade_available) return false;
  return true;
}

function clearGuideTargetHighlight() {
  if (interactiveGuide.activeTarget) {
    interactiveGuide.activeTarget.removeAttribute("data-guide-target-active");
    interactiveGuide.activeTarget = null;
  }
  document.querySelectorAll("[data-guide-target-active]").forEach(el => el.removeAttribute("data-guide-target-active"));
}

function positionGuideCallout() {
  const callout = $("guide-callout");
  if (!callout || !interactiveGuide.open || callout.hidden) return;
  if (window.innerWidth <= 767) {
    callout.style.removeProperty("--guide-top");
    callout.style.removeProperty("--guide-left");
    return;
  }
  const target = interactiveGuide.activeTarget;
  const vw = window.innerWidth || 1280;
  const vh = window.visualViewport?.height || window.innerHeight || 800;
  const rect = callout.getBoundingClientRect();
  const width = rect.width || 360;
  const height = rect.height || 280;
  let top = 84;
  let left = Math.max(16, vw - width - 32);
  if (target && typeof target.getBoundingClientRect === "function") {
    const tRect = target.getBoundingClientRect();
    if (tRect.width > 0 || tRect.height > 0) {
      if (tRect.right + width + 24 <= vw - 16) {
        left = Math.max(16, tRect.right + 16);
        top = Math.min(Math.max(16, tRect.top), Math.max(16, vh - height - 16));
      } else if (tRect.bottom + height + 20 <= vh - 16) {
        left = Math.min(Math.max(16, tRect.left), Math.max(16, vw - width - 16));
        top = Math.max(16, tRect.bottom + 14);
      } else if (tRect.top - height - 20 >= 16) {
        left = Math.min(Math.max(16, tRect.left), Math.max(16, vw - width - 16));
        top = Math.max(16, tRect.top - height - 14);
      } else {
        left = Math.min(Math.max(16, vw - width - 24), Math.max(16, tRect.left));
        top = Math.min(Math.max(16, vh - height - 24), Math.max(76, tRect.top + 16));
      }
    }
  }
  callout.style.setProperty("--guide-top", `${Math.round(top)}px`);
  callout.style.setProperty("--guide-left", `${Math.round(left)}px`);
}

function navigateForGuideStep(step) {
  if (!step) return;
  if (step.id === "connectors-knowledge") {
    if (location.hash !== "#connectors") {
      location.hash = "connectors";
    }
    showView();
    if (typeof selectLibraryTab === "function") {
      selectLibraryTab("connectors");
    } else {
      const tab = document.querySelector('[data-library-kind="connectors"]');
      tab?.click();
    }
    return;
  }
  const targetHash = `#${step.view}`;
  if (view() !== step.view) {
    location.hash = targetHash;
    showView();
  }
}

function resolveGuideStepTarget(step) {
  if (!step) return null;
  if (window.innerWidth <= 767 && step.id === "workspace-navigation") {
    return $("mobile-menu-toggle") || document.querySelector(step.target);
  }
  if (window.innerWidth <= 767 && step.id === "help-reopen") {
    return $("help-guide-button") || document.querySelector(step.target);
  }
  const primary = step.target ? document.querySelector(step.target) : null;
  if (primary && !primary.hidden && primary.getClientRects?.().length) return primary;
  const fallback = step.fallback_target ? document.querySelector(step.fallback_target) : null;
  if (fallback && !fallback.hidden) return fallback;
  return primary || fallback;
}

function renderGuideStep(stepId, moveFocus = true) {
  const st = interactiveGuide.state;
  if (!st || !Array.isArray(st.steps) || !st.steps.length) return;
  const index = Math.max(0, st.steps.findIndex(s => s.id === stepId));
  const step = st.steps[index] || st.steps[0];
  st.current_step = step.id;
  navigateForGuideStep(step);
  clearGuideTargetHighlight();
  const target = resolveGuideStepTarget(step);
  if (target) {
    interactiveGuide.activeTarget = target;
    target.setAttribute("data-guide-target-active", "true");
    if (typeof target.scrollIntoView === "function") {
      const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      try {
        target.scrollIntoView({block: "nearest", inline: "nearest", behavior: reduced ? "auto" : "smooth"});
      } catch {
        target.scrollIntoView();
      }
    }
  }
  $("guide-step-progress").textContent = `STEP ${index + 1} OF ${st.steps.length}`;
  const selectEl = $("guide-step-select");
  if (selectEl) {
    selectEl.innerHTML = st.steps.map((s, idx) => `<option value="${escapeHtml(s.id)}" ${s.id === step.id ? "selected" : ""}>${idx + 1}. ${escapeHtml(s.title)}</option>`).join("");
    selectEl.value = step.id;
  }
  $("guide-step-title").textContent = step.title;
  $("guide-step-summary").textContent = step.summary;

  const capEl = $("guide-step-capability");
  if (step.capability_note) {
    capEl.textContent = step.capability_note;
    capEl.hidden = false;
  } else {
    capEl.textContent = "";
    capEl.hidden = true;
  }

  const roleEl = $("guide-step-role");
  if (step.role_note) {
    roleEl.textContent = step.role_note;
    roleEl.hidden = false;
  } else {
    roleEl.textContent = "";
    roleEl.hidden = true;
  }

  const branchEl = $("guide-branch");
  if (step.branch_title && step.branch_body) {
    $("guide-branch-title").textContent = step.branch_title;
    $("guide-branch-body").textContent = step.branch_body;
    branchEl.open = false;
    branchEl.hidden = false;
  } else {
    branchEl.hidden = true;
  }

  const practiceBox = $("guide-practice-box");
  const practiceBtn = $("guide-practice-toggle");
  const practicePreview = $("guide-practice-preview");
  if (step.practice && step.practice.preview) {
    practiceBtn.textContent = step.practice.label || "Preview sample";
    practiceBtn.setAttribute("aria-expanded", "false");
    practicePreview.textContent = step.practice.preview;
    practicePreview.hidden = true;
    practiceBox.hidden = false;
  } else {
    practiceBox.hidden = true;
  }

  $("guide-back").disabled = index === 0;
  $("guide-next").textContent = index === st.steps.length - 1 ? "Finish guide" : "Next";
  $("guide-restart").hidden = index === 0 && st.completed_steps.length === 0;

  const callout = $("guide-callout");
  callout.hidden = false;
  callout.dataset.guideOpenState = "true";
  if (typeof callout.showPopover === "function") {
    try {
      if (!callout.matches(":popover-open")) callout.showPopover();
    } catch {}
  }
  interactiveGuide.open = true;
  updateGuideBadge();
  positionGuideCallout();
  if (moveFocus) {
    $("guide-next")?.focus({preventScroll: true});
  }
}

function closeGuideCallout(restoreOrigin = false) {
  const callout = $("guide-callout");
  if (callout) {
    if (typeof callout.hidePopover === "function") {
      try {
        if (callout.matches(":popover-open")) callout.hidePopover();
      } catch {}
    }
    callout.dataset.guideOpenState = "false";
    callout.hidden = true;
  }
  interactiveGuide.open = false;
  clearGuideTargetHighlight();
  updateGuideBadge();
  if (restoreOrigin && interactiveGuide.originHash && location.hash !== interactiveGuide.originHash) {
    location.hash = interactiveGuide.originHash;
    showView();
  }
  const trigger = interactiveGuide.triggerElement || $("open-interactive-guide") || $("help-guide-button");
  if (trigger && typeof trigger.focus === "function") {
    trigger.focus({preventScroll: true});
  }
}

function openWelcomeDialog(trigger = null) {
  if (trigger) interactiveGuide.triggerElement = trigger;
  const dialog = $("guide-welcome");
  if (!dialog) return;
  const st = interactiveGuide.state || mergeGuideState(null, readLocalGuideState());
  interactiveGuide.state = st;
  const providers = Array.isArray(state.providers) ? state.providers : [];
  const readyCount = providers.filter(p => p.configured).length;
  const roleLabel = state.mode === "team" ? `Signed in as ${state.user?.nexus_id || "team member"} (${st.role} role).` : "Running in local desktop mode.";
  $("guide-welcome-context").textContent = `${roleLabel} ${readyCount} of ${providers.length || 7} provider option(s) currently marked ready. Viewing the guide never starts runs or changes saved files.`;
  const resumeBtn = $("guide-welcome-resume");
  if (resumeBtn) {
    const hasSavedStep = st.status === "in_progress" && st.current_step && st.current_step !== st.steps[0]?.id;
    resumeBtn.hidden = !hasSavedStep;
  }
  if (!dialog.open) dialog.showModal();
  $("guide-welcome-start")?.focus({preventScroll: true});
}

async function startInteractiveGuide({resume = false, restart = false, trigger = null} = {}) {
  if (trigger) interactiveGuide.triggerElement = trigger;
  if (document.body.classList.contains("mobile-nav-open")) {
    $("mobile-menu-toggle")?.click();
  }
  const welcomeDialog = $("guide-welcome");
  if (welcomeDialog?.open) welcomeDialog.close();
  if (!interactiveGuide.open) {
    interactiveGuide.originHash = location.hash || "#chat";
  }
  if (!interactiveGuide.state) {
    interactiveGuide.state = mergeGuideState(null, readLocalGuideState());
  }
  const st = interactiveGuide.state;
  let targetStep = st.steps[0].id;
  if (restart) {
    st.status = "new";
    st.current_step = st.steps[0].id;
    st.completed_steps = [];
    st.seen_version = 0;
    targetStep = st.steps[0].id;
  } else if (resume && st.current_step) {
    targetStep = st.current_step;
  } else if (st.upgrade_available && st.new_steps?.length) {
    targetStep = st.new_steps[0];
  } else if (st.status === "in_progress" && st.current_step) {
    targetStep = st.current_step;
  }
  st.status = "in_progress";
  st.current_step = targetStep;
  renderGuideStep(targetStep, true);
  if (restart) {
    await persistGuideAction("reset");
  }
  await persistGuideAction("start", {
    current_step: targetStep,
    origin_view: view()
  });
}

async function stepInteractiveGuide(delta) {
  const st = interactiveGuide.state;
  if (!st || !interactiveGuide.open) return;
  const currentIndex = Math.max(0, st.steps.findIndex(s => s.id === st.current_step));
  const currentStep = st.steps[currentIndex];
  if (delta > 0 && currentIndex === st.steps.length - 1) {
    closeGuideCallout(true);
    notify("Interactive guide completed. Reopen it anytime from Help → Interactive guide.");
    await persistGuideAction("complete", {
      current_step: currentStep.id,
      completed_step: currentStep.id
    });
    return;
  }
  const nextIndex = Math.min(st.steps.length - 1, Math.max(0, currentIndex + delta));
  const nextStep = st.steps[nextIndex];
  if (delta > 0 && !st.completed_steps.includes(currentStep.id)) {
    st.completed_steps.push(currentStep.id);
  }
  st.current_step = nextStep.id;
  renderGuideStep(nextStep.id, true);
  await persistGuideAction("step", {
    current_step: nextStep.id,
    ...(delta > 0 ? {completed_step: currentStep.id} : {})
  });
}

async function dismissInteractiveGuide(restoreOrigin = true) {
  const welcomeDialog = $("guide-welcome");
  if (welcomeDialog?.open) welcomeDialog.close();
  await persistGuideAction("dismiss");
  if (interactiveGuide.open) {
    closeGuideCallout(restoreOrigin);
  } else {
    const trigger = interactiveGuide.triggerElement || $("open-interactive-guide") || $("help-guide-button");
    trigger?.focus?.({preventScroll: true});
  }
}

async function initInteractiveGuide() {
  const local = readLocalGuideState();
  let server = null;
  try {
    server = await api("/api/workbench/guide");
  } catch {}
  interactiveGuide.state = mergeGuideState(server, local);
  writeLocalGuideState(interactiveGuide.state);
  updateGuideBadge();
  if (shouldAutoOfferGuide()) {
    openWelcomeDialog($("open-interactive-guide"));
  }
}

document.addEventListener("click", async event => {
  const openBtn = event.target.closest("[data-guide-open]");
  if (openBtn) {
    event.preventDefault();
    interactiveGuide.triggerElement = openBtn;
    if (document.body.classList.contains("mobile-nav-open")) {
      $("mobile-menu-toggle")?.click();
    }
    if (!interactiveGuide.state) {
      interactiveGuide.state = mergeGuideState(null, readLocalGuideState());
    }
    const st = interactiveGuide.state;
    if (st.status === "in_progress") {
      await startInteractiveGuide({resume: true, trigger: openBtn});
    } else if (st.status === "new") {
      openWelcomeDialog(openBtn);
    } else {
      await startInteractiveGuide({restart: !st.upgrade_available, trigger: openBtn});
    }
    return;
  }
  if (event.target.closest("[data-guide-start]")) {
    event.preventDefault();
    await startInteractiveGuide({resume: false, restart: true});
    return;
  }
  if (event.target.closest("[data-guide-resume-tour]")) {
    event.preventDefault();
    await startInteractiveGuide({resume: true});
    return;
  }
  if (event.target.closest("[data-guide-restart]")) {
    event.preventDefault();
    await startInteractiveGuide({restart: true});
    return;
  }
  if (event.target.closest("[data-guide-skip]")) {
    event.preventDefault();
    await dismissInteractiveGuide(false);
    return;
  }
  if (event.target.closest("[data-guide-dismiss]")) {
    event.preventDefault();
    await dismissInteractiveGuide(true);
    return;
  }
  if (event.target.closest("#guide-back")) {
    event.preventDefault();
    await stepInteractiveGuide(-1);
    return;
  }
  if (event.target.closest("#guide-next")) {
    event.preventDefault();
    await stepInteractiveGuide(1);
    return;
  }
  if (event.target.closest("#guide-practice-toggle")) {
    event.preventDefault();
    const btn = $("guide-practice-toggle");
    const preview = $("guide-practice-preview");
    const expanded = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", expanded ? "false" : "true");
    preview.hidden = expanded;
    positionGuideCallout();
  }
});

document.addEventListener("keydown", event => {
  if (!interactiveGuide.open) return;
  if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
    event.preventDefault();
    dismissInteractiveGuide(true);
    return;
  }
  const callout = $("guide-callout");
  if (callout && callout.contains(document.activeElement) && !/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      stepInteractiveGuide(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      stepInteractiveGuide(-1);
    }
  }
});

window.addEventListener("resize", positionGuideCallout);
window.visualViewport?.addEventListener("resize", positionGuideCallout);
$("guide-step-select")?.addEventListener("change", async event => {
  const targetStep = event.target.value;
  if (!interactiveGuide.state || !targetStep) return;
  interactiveGuide.state.current_step = targetStep;
  renderGuideStep(targetStep, true);
  await persistGuideAction("step", {current_step: targetStep});
});
window.addEventListener("studio-ready", () => { initInteractiveGuide().catch(() => {}); });

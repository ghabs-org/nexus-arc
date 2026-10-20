"use strict";
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state = {token:"",providers:[],agents:[],workflows:[],tools:[],sessions:[],runs:[],sessionId:null,busy:false,editor:null,ready:false};
let noticeTimer,bootstrapRetryTimer;
let bootstrapInFlight=false,bootstrapFailures=0,reconnectHandshake=null;
function notify(message){$("notice").textContent=message;$("notice").hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$("notice").hidden=true,5500);}
async function api(path, body){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),body===undefined?15000:90000);
  let response,data;
  try{
    response=await fetch(path,{...(body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Nexus-Studio-Token":state.token},body:JSON.stringify(body)}),signal:controller.signal});
    data=await response.json();
  }catch(cause){
    const error=new Error(body===undefined?"Connection unavailable. Reconnecting…":"No confirmation received. Check Activity before trying this action again; it may have reached the server.");
    error.retryable=true;error.cause=cause;throw error;
  }finally{clearTimeout(timeout);}
  if(response.status===401&&data.login_url){
    const login=new URL(data.login_url,location.href);
    if(login.origin===location.origin||(login.protocol==="https:"&&login.host===location.host)){$("deployment-mode").textContent="TEAM";$("workspace-name").textContent="Nexus team workspace";$("runtime-label").textContent="Sign in through Nexus";$("team-login-link").href=login.href;$("team-login").hidden=false;document.querySelectorAll(".view").forEach(section=>section.hidden=true);}
    state.ready=false;state.token="";$("recent-sessions").replaceChildren();document.querySelectorAll("dialog[open]").forEach(dialog=>dialog.close());
  }
  if(!response.ok){const error=new Error(data.error||`Request failed (${response.status})`);error.status=response.status;error.retryable=[401,408,429].includes(response.status)||response.status>=500;throw error;}
  return data;
}
function providerName(id){if(id==="whisper-api")return "Whisper API";if(id==="publishing")return "Publishing adapter";if(id==="agent-delegation")return "Agent delegation";return id==="local-tool"?"Local tool":state.providers.find(p=>p.id===id)?.name || id;}
function badge(text, kind=""){return `<span class="badge ${escapeHtml(kind)}">${escapeHtml(text)}</span>`;}
function providerOptions(selected="codex", optional=false){return (optional?'<option value="">Use each agent’s provider</option>':"")+state.providers.map(p=>`<option value="${escapeHtml(p.id)}" ${p.id===selected?"selected":""}>${escapeHtml(p.name)}${p.configured?"":" · setup needed"}</option>`).join("");}
function effortOptions(selected=""){return [["","Default"],["low","Low"],["medium","Medium"],["high","High"],["xhigh","Extra high"],["max","Max"]].map(([val,label])=>`<option value="${val}" ${val===selected?"selected":""}>${label}</option>`).join("");}
function empty(title,copy,action,label){return `<div class="empty-state"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(copy)}</p>${action?`<button data-action="${action}">${escapeHtml(label)}</button>`:""}</div>`;}
function view(){const name=location.hash.slice(1).split("/")[0]||"chat";if(["connectors","entrypoints","schedules","evaluations","models","externalagents"].includes(name))return "library";return ["chat","agents","workflows","providers","tools","activity","inbox","quick-build","library","workspace","workbench","projects","feedback","sharing"].includes(name)?name:"chat";}
function showView(){if(!$("team-login").hidden)return;const name=view();document.querySelectorAll(".view").forEach(section=>section.hidden=section.id!==`view-${name}`);document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("active",a.getAttribute("href")===`#${location.hash.slice(1).split("/")[0]||name}`));$("view-title").textContent={projects:"Knowledge projects",feedback:"Response feedback",sharing:"Conversation sharing",inbox:"Approval inbox",workbench:"Workspace tools",workspace:"Workspace & event mesh",library:"Library","quick-build":"Quick Build",chat:"Conversations",agents:"Agents",workflows:"Workflows",providers:"Providers & models",tools:"Tools",activity:"Activity"}[name];if(name==="tools")api("/api/tools").then(data=>{state.tools=data.items;renderTools();}).catch(error=>notify(error.message));if(name==="activity")refreshRuns().catch(error=>notify(error.message));}
function renderAgents(){
  $("agent-count").textContent=state.agents.length;
  $("agents-content").innerHTML=state.agents.length?`<div class="resource-grid">${state.agents.map(item=>{
    const doc=item.document||{},meta=doc.metadata||{},spec=doc.spec||{};
    if(item.error)return `<article class="resource-card"><h2>${escapeHtml(item.id)}</h2>${badge("Invalid definition","failed")}<p>${escapeHtml(item.error)}</p></article>`;
    return `<article class="resource-card"><div class="card-meta">${badge("AGENT")}<span>${escapeHtml(providerName(spec.provider))}</span></div><h2>${escapeHtml(meta.name||item.id)}</h2><p>${escapeHtml(meta.description||spec.purpose||"Ready for your next task.")}</p><div class="card-meta"><span>${escapeHtml(spec.model||"Provider default model")}</span>${spec.effort?`<span>· ${escapeHtml(spec.effort)} effort</span>`:""}<span>· ${escapeHtml(spec.timeout_seconds||600)}s timeout</span></div><div class="card-actions"><button data-agent="${escapeHtml(item.id)}">Start conversation</button><button class="secondary" data-versions="${escapeHtml(item.id)}" data-kind="agents">Versions</button><button class="secondary" data-edit-agent="${escapeHtml(item.id)}">Edit</button><button class="secondary" data-definition="${escapeHtml(item.id)}" data-kind="agents">Instructions</button></div></article>`;
  }).join("")}</div>`:empty("Give your team its first specialist","Create an agent with a clear purpose, then choose which provider does the work.","create-agent","Create an agent");
  const selected=$("chat-agent").value;
  $("chat-agent").innerHTML='<option value="">General assistant</option>'+state.agents.filter(a=>a.document).map(a=>`<option value="${escapeHtml(a.id)}">${escapeHtml(a.document.metadata?.name||a.id)}</option>`).join("");
  $("chat-agent").value=selected;
}
function renderWorkflows(){
  $("workflow-count").textContent=state.workflows.length;
  $("workflows-content").innerHTML=state.workflows.length?`<div class="resource-grid">${state.workflows.map(item=>{
    const doc=item.document||{},steps=doc.steps||doc.graph?.nodes||[];
    return `<article class="resource-card"><div class="card-meta">${badge(item.error?"Invalid definition":doc.graph?"GRAPH WORKFLOW":"WORKFLOW",item.error?"failed":"")}<span>${steps.length} steps</span></div><h2>${escapeHtml(doc.metadata?.name||item.id)}</h2><p>${escapeHtml(item.error||doc.metadata?.description||(doc.graph?steps.slice(0,4).map(s=>s.name||s.kind||s.id).join(" · ")+(steps.length>4?` · ${steps.length-4} more`:""):steps.map(s=>s.agent_type).join(" → ")))}</p><div class="card-actions"><button data-run-workflow="${escapeHtml(item.id)}" ${item.error?"disabled":""}>Run workflow</button>${doc.graph?`<button class="secondary" data-edit-graph="${escapeHtml(item.id)}">Edit canvas</button>`:""}<button class="secondary" data-versions="${escapeHtml(item.id)}" data-kind="workflows">Versions</button><button class="secondary" data-workflow="${escapeHtml(item.id)}">View & validate</button></div></article>`;
  }).join("")}</div>`:empty("Connect your agents into a workflow","Define the handoffs between specialists. Each step can use its agent’s own provider and model.","create-workflow","Create a workflow");
}
// Default model suggestions per provider; model inputs stay free text.
const MODEL_INPUTS='#chat-model,#builder-model,input[name="model"],input[data-build-field$=".model"]';
function renderModelLists(){let host=$("model-lists");if(!host){host=document.createElement("div");host.id="model-lists";document.body.appendChild(host);}host.innerHTML=state.providers.map(p=>`<datalist id="models-${escapeHtml(p.id)}">${(p.models||[]).map(m=>`<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join("")}</datalist>`).join("");linkModelInputs();}
function modelProvider(input){if(input.id==="chat-model")return $("chat-provider")?.value;if(input.id==="builder-model")return $("builder-provider")?.value;const field=input.dataset.buildField;if(field)return document.querySelector(`[data-build-field="${CSS.escape(field.slice(0,-6))}.provider"]`)?.value;return input.form?.querySelector('select[name="provider"]')?.value;}
function linkModelInputs(){document.querySelectorAll(MODEL_INPUTS).forEach(input=>{const provider=modelProvider(input);if(provider&&document.getElementById("models-"+provider))input.setAttribute("list","models-"+provider);else input.removeAttribute("list");if(!input.placeholder)input.placeholder="Provider default · pick or type a model";});}
// Model fields are rendered by many views; link new ones and follow provider changes.
let modelLinkQueued=false;
new MutationObserver(()=>{if(modelLinkQueued)return;modelLinkQueued=true;requestAnimationFrame(()=>{modelLinkQueued=false;linkModelInputs();});}).observe(document.body,{childList:true,subtree:true});
document.addEventListener("change",event=>{if(event.target instanceof HTMLSelectElement)linkModelInputs();});
document.addEventListener("focusin",event=>{if(event.target instanceof HTMLInputElement&&event.target.matches(MODEL_INPUTS))linkModelInputs();});
function renderProviders(){
  renderModelLists();
  $("providers-content").innerHTML=state.providers.map(p=>`<article class="provider-row"><div><h2>${escapeHtml(p.name)}</h2><p>${state.mode==="team"?"Your Nexus account · shared worker execution":p.kind==="cli"?"Official CLI · local account configuration":"API provider · environment credentials"}</p>${p.models?.length?`<p class="provider-models">Models: ${p.models.map(m=>escapeHtml(m.name)).join(" · ")}</p>`:""}</div><div class="provider-side">${badge(p.status,p.configured?"ready":"")}<button class="secondary" data-provider="${escapeHtml(p.id)}">${p.configured?"Configuration":"Setup instructions"}</button></div></article>`).join("");
}
function renderTools(){
  $("tools-content").innerHTML=state.tools.length?`<div class="resource-grid">${state.tools.map(t=>`<article class="resource-card"><div class="card-meta">${badge(t.category||"TOOL")}</div><h2 class="tool-name">${escapeHtml(t.id)}</h2><p>${escapeHtml(t.description||"Registered Nexus tool")}</p><div class="card-actions"><button class="secondary" data-tool="${escapeHtml(t.id)}">Run tool</button></div></article>`).join("")}</div>`:empty("No tools registered","Register tools with the Nexus tool registry to make them available here.");
}
function renderSessions(){
  $("session-count").textContent=state.sessions.length;
  $("recent-sessions").innerHTML=state.sessions.length?state.sessions.slice(0,12).map(s=>`<button class="recent" data-session="${escapeHtml(s.id)}">${escapeHtml(s.title)}</button>`).join(""):'<span class="recent empty">Your conversations will appear here.</span>';
}
function renderRuns(){
  $("activity-content").innerHTML=state.runs.length?state.runs.map(r=>`<article class="activity-row"><div class="activity-copy"><h2>${escapeHtml(r.task)}</h2><p>${escapeHtml(r.kind)} · ${escapeHtml(providerName(r.provider))} · ${escapeHtml(new Date(r.created_at).toLocaleString())}${r.step?" · "+escapeHtml(r.step):""}</p></div>${badge(r.status,r.status)}<button class="secondary" data-run="${escapeHtml(r.id)}">Details</button></article>`).join(""):empty("A clear record of the work","Conversations, workflow runs, and tool results appear here as you use the workspace.");
}
async function refreshDefinitions(){const [agents,workflows]=await Promise.all([api("/api/agents"),api("/api/workflows")]);state.agents=agents.items;state.workflows=workflows.items;renderAgents();renderWorkflows();}
async function refreshSessions(){state.sessions=(await api("/api/sessions")).items;renderSessions();}
async function refreshRuns(){state.runs=(await api("/api/runs")).items;renderRuns();}
function addMessage(role,text,provider="",runId=null,citations=[],structured=[],retrievalNotice=null){
  $("welcome")?.remove();const node=document.createElement("article");node.className=`message ${role}`;
  node.innerHTML=`<div class="message-label">${role==="user"?"You":"Nexus"}<span class="message-provider">${escapeHtml(provider)}</span></div><div class="message-body"></div>`;
  renderContent(node.querySelector(".message-body"),text);if(runId)node.dataset.runId=runId;showCitations(node,citations);renderStructuredContent(node,structured);renderRetrievalNotice(node,retrievalNotice);$("messages").appendChild(node);$("messages").scrollTop=$("messages").scrollHeight;return node;
}
function selectAgent(){const item=state.agents.find(a=>a.id===$("chat-agent").value);if(item){$("chat-provider").value=item.document.spec.provider;$("chat-model").value=item.document.spec.model||"";if($("chat-effort"))$("chat-effort").value=item.document.spec.effort||"";}}
function newChat(){if(state.busy){notify("Let the current conversation finish first.");return;}state.sessionId=null;$("messages").replaceChildren();addMessage("assistant","Ready when you are. Describe the task you’d like to work on.");location.hash="chat";$("chat-task").focus();}
const watchedRuns=new Map();
const disconnectedRuns=new Set();
let recoveryKey="";
function saveWatchedRuns(){if(!recoveryKey)return;try{sessionStorage.setItem(recoveryKey,JSON.stringify([...watchedRuns.keys()].slice(0,100)));}catch{}}
function connectionStatus(){const offline=navigator.onLine===false;$("connection-status").hidden=!offline&&!disconnectedRuns.size;$("connection-message").textContent=offline?"You’re offline. Runs may still be working on the server.":disconnectedRuns.has("bootstrap")?"Workspace unavailable. Reconnecting…":"Connection interrupted. Reconnecting to your runs…";}
function waitForConnection(delay){return new Promise(resolve=>{const timer=setTimeout(done,delay);function done(){clearTimeout(timer);window.removeEventListener("studio-reconnect",done);resolve();}window.addEventListener("studio-reconnect",done,{once:true});});}
async function pollRun(runId,onUpdate=()=>{}){
  if(watchedRuns.has(runId)){const monitor=watchedRuns.get(runId);monitor.listeners.add(onUpdate);if(monitor.last)onUpdate(monitor.last);return monitor.promise;}
  const monitor={listeners:new Set([onUpdate]),last:null,promise:null};watchedRuns.set(runId,monitor);saveWatchedRuns();
  monitor.promise=(async()=>{let failures=0;try{while(true){let run;try{run=await api(`/api/runs/${encodeURIComponent(runId)}`);}catch(error){if(!error.retryable)throw error;failures++;disconnectedRuns.add(runId);connectionStatus();await waitForConnection(Math.min(15000,1000*2**Math.min(failures-1,4)));continue;}if(failures)window.dispatchEvent(new Event("studio-connection-restored"));failures=0;disconnectedRuns.delete(runId);connectionStatus();monitor.last=run;for(const listener of monitor.listeners){try{listener(run);}catch(error){console.error("Run display failed",error);}}if(!["running","queued"].includes(run.status)){if(!["completed","waiting","cancelled"].includes(run.status)){const error=new Error(run.error||`Run ${run.status}`);error.run=run;throw error;}return run;}await waitForConnection(document.hidden?4000:750);}}
    finally{watchedRuns.delete(runId);disconnectedRuns.delete(runId);saveWatchedRuns();connectionStatus();}})();return monitor.promise;
}
async function restoreRunMonitoring(){
  recoveryKey=`nexus-runs:${state.mode}:${state.user?.nexus_id||"desktop"}:${$("workspace-name").textContent}`;
  let saved=[];try{saved=JSON.parse(sessionStorage.getItem(recoveryKey)||"[]");}catch{}if(!Array.isArray(saved))saved=[];
  const pending=state.runs.filter(run=>["running","queued"].includes(run.status)).map(run=>run.id);
  const ids=[...new Set([...saved,...pending])].filter(id=>typeof id==="string"&&/^[\w-]{1,100}$/.test(id)).slice(0,100);
  for(const id of ids)pollRun(id,run=>{const index=state.runs.findIndex(item=>item.id===id);if(index>=0)state.runs[index]=run;else state.runs.unshift(run);renderRuns();if(state.detailRunId===id)updateRunDetail(run);}).then(()=>{refreshRuns().catch(()=>{});window.dispatchEvent(new Event("studio-review-changed"));}).catch(()=>refreshRuns().catch(()=>{}));
  if(ids.length)notify(`Reconnected to ${ids.length} saved run${ids.length===1?"":"s"}. Open Activity for results.`);
}
for(const name of ["online","offline","pageshow"])window.addEventListener(name,()=>{connectionStatus();window.dispatchEvent(new Event("studio-reconnect"));if(name==="online"&&!state.ready&&$("team-login").hidden)bootstrap();});
document.addEventListener("visibilitychange",()=>{if(!document.hidden)window.dispatchEvent(new Event("studio-reconnect"));});

async function sendMessage(event){
  event.preventDefault();if(state.busy)return;const task=$("chat-task").value.trim();if(!task)return;
  if(location.pathname==="/agent.html"&&!state.agents.some(agent=>agent.id===new URLSearchParams(location.search).get("agent"))){notify("This embedded agent is unavailable. Open Nexus Studio to choose an agent.");return;}
  const payload={task,provider:$("chat-provider").value,model:$("chat-model").value.trim(),...($("chat-tools")?{worker_tools:$("chat-tools").value}:{}),...($("chat-effort")?.value?{effort:$("chat-effort").value}:{}),agent_id:$("chat-agent").value||null,session_id:state.sessionId,knowledge_project_id:$("chat-project")?.value||null,artifact_ids:[...$("chat-artifacts").selectedOptions].map(item=>item.value)};
  try{payload.prompt_values=await resolvePromptValues(task);}catch(error){notify(error.message);return;}state.busy=true;$("send-message").disabled=true;$("chat-task").value="";addMessage("user",task);
  const reply=addMessage("assistant","Working…",providerName(payload.provider));const body=reply.querySelector(".message-body");
  try{
    const queued=await api("/api/runs",payload);state.sessionId=queued.session_id;
    const finished=await pollRun(queued.id,run=>{renderRunMessage(reply,run);reply.dataset.runId=run.id;reply.querySelector(".message-provider").textContent=providerName(run.provider);$("messages").scrollTop=$("messages").scrollHeight;});
    if(finished.status==="completed")window.dispatchEvent(new CustomEvent("studio-response-completed",{detail:{text:finished.output}}));
    await refreshSessions();
  }catch(error){reply.classList.add("error");if(!body.textContent||body.textContent==="Working…")body.textContent=error.message;else{const note=document.createElement("p");note.className="form-error";note.setAttribute("role","alert");note.textContent=error.message;reply.append(note);}}
  finally{state.busy=false;$("send-message").disabled=false;refreshRuns().catch(()=>{});}
}
function inputField(name,label,value="",type="text"){return `<label>${label}<input name="${name}" type="${type}" value="${escapeHtml(value)}" ${["name","task"].includes(name)?"required":""}></label>`;}
function openEditor(kind,identity){
  state.editor={kind,identity};$("editor-error").textContent="";
  const fields=$("editor-fields");
  if(kind==="agent"||kind==="agent-edit"){
    $("editor-kicker").textContent="NEW SPECIALIST";$("editor-title").textContent="Create an agent";$("editor-save").textContent="Create agent";
    fields.innerHTML=inputField("name","Name")+inputField("description","Description")+`<div class="form-grid"><label>Provider<select name="provider">${providerOptions()}</select></label>${inputField("model","Model (optional)")}<label>Effort<select name="effort">${effortOptions()}</select></label></div><label>Instructions<textarea name="instructions" rows="5" placeholder="What should this agent do, and how should it work?" required></textarea></label>`+inputField("tool_ids","Allowed tool names (optional, comma separated)")+inputField("skill_ids","Saved skill IDs (optional, comma separated)")+inputField("timeout_seconds","Timeout in seconds",600,"number")+`<details><summary>Output guardrails</summary><label>Optional guardrail settings (JSON)<textarea name="guardrails_json" rows="4">{}</textarea></label><p>Use max_output_chars and forbidden_phrases. Guarded responses are checked before display, so streaming is disabled for this agent.</p></details>`;
  }else if(kind==="workflow"){
    if(!state.agents.some(a=>a.document)){notify("Create an agent before building a workflow.");location.hash="agents";return;}
    $("editor-kicker").textContent="A SERIES OF HANDOFFS";$("editor-title").textContent="Create a workflow";$("editor-save").textContent="Create workflow";
    fields.innerHTML=inputField("name","Name")+inputField("description","Description")+'<div id="workflow-steps"></div><button type="button" class="secondary" id="add-step">+ Add step</button>';addWorkflowStep();
  }else if(kind==="workflow-run"){
    const item=state.workflows.find(w=>w.id===identity);$("editor-kicker").textContent="RUN WORKFLOW";$("editor-title").textContent=item.document.metadata?.name||identity;$("editor-save").textContent="Start workflow";
    fields.innerHTML='<label>Task<textarea name="task" required placeholder="What should this workflow accomplish?"></textarea></label>'+`<label>Provider override<select name="provider">${providerOptions("",true)}</select></label>`+(item.document.graph?.version===2?'<label>Workflow input (JSON, optional)<textarea name="workflow_input_json" rows="4" placeholder="[&quot;First item&quot;, &quot;Second item&quot;]"></textarea></label><p>Supply the input shape expected by this workflow, or leave blank to use the task text.</p>':'');
  }else if(kind==="tool"){
    $("editor-kicker").textContent="RUN TOOL";$("editor-title").textContent=identity;$("editor-save").textContent="Run tool";
    fields.innerHTML='<label>Arguments (JSON list)<textarea name="args" required>[]</textarea></label><label>Keyword arguments (JSON object)<textarea name="kwargs" required>{}</textarea></label>';
  }
  if(kind==="agent-edit"){
    const item=state.agents.find(item=>item.id===identity),doc=item.document,spec=doc.spec||{};
    state.editor.revision=item.revision;
    $("editor-title").textContent="Edit agent";$("editor-save").textContent="Save changes";
    const values={name:doc.metadata?.name||identity,description:doc.metadata?.description||"",provider:spec.provider,model:spec.model||"",effort:spec.effort||"",instructions:spec.purpose||"",timeout_seconds:spec.timeout_seconds||600,skill_ids:(spec.skill_ids||[]).join(", "),tool_ids:(spec.tool_ids||[]).join(", "),guardrails_json:JSON.stringify(spec.guardrails||{},null,2)};
    for(const [name,value] of Object.entries(values)){const field=fields.querySelector(`[name="${name}"]`);if(field)field.value=value;}
    const agentType=document.createElement("input");agentType.type="hidden";agentType.name="agent_type";agentType.value=spec.agent_type||identity;fields.appendChild(agentType);
  }
  $("editor").showModal();fields.querySelector("input,textarea,select")?.focus();
}
function addWorkflowStep(){
  const box=$("workflow-steps");const count=box.children.length+1;const row=document.createElement("div");row.className="step-editor";
  row.innerHTML=`<div class="step-heading"><span>STEP ${count}</span><button type="button" class="secondary remove-step">Remove</button></div><label>Agent<select class="step-agent">${state.agents.filter(a=>a.document).map(a=>`<option value="${escapeHtml(a.document.spec.agent_type)}">${escapeHtml(a.document.metadata?.name||a.id)}</option>`).join("")}</select></label><label>Task instructions<textarea class="step-prompt" placeholder="Use {task} for the original task">{task}</textarea></label>`;box.appendChild(row);
}
async function saveEditor(event){
  event.preventDefault();const data=Object.fromEntries(new FormData(event.target));const {kind,identity}=state.editor;$("editor-save").disabled=true;$("editor-error").textContent="";
  try{
    if(kind==="agent"||kind==="agent-edit"){if(!data.effort)delete data.effort;if(data.guardrails_json&&data.guardrails_json.trim()!=="{}")data.guardrails=JSON.parse(data.guardrails_json);delete data.guardrails_json;if(kind==="agent-edit"){data.revision=state.editor.revision;await api(`/api/agents/${encodeURIComponent(identity)}/update`,data);}else await api("/api/agents",data);$("editor").close();await refreshDefinitions();location.hash="agents";notify(kind==="agent-edit"?"Agent updated.":"Agent created.");}
    else if(kind==="workflow"){
      data.steps=[...document.querySelectorAll(".step-editor")].map((row,index)=>({id:`step_${index+1}`,name:`Step ${index+1}`,agent_type:row.querySelector(".step-agent").value,prompt:row.querySelector(".step-prompt").value}));
      await api("/api/workflows",data);$("editor").close();await refreshDefinitions();location.hash="workflows";notify("Workflow created.");
    }else{
      const payload=kind==="tool"?{kind:"tool",tool:identity,args:JSON.parse(data.args),kwargs:JSON.parse(data.kwargs)}:{kind:"workflow",workflow_id:identity,task:data.task,...(data.workflow_input_json?.trim()?{workflow_input:JSON.parse(data.workflow_input_json)}:{}),...(data.provider?{provider:data.provider}:{})};
      const run=await api("/api/runs",payload);$("editor").close();location.hash="activity";showRun(run);pollRun(run.id,updateRunDetail).then(()=>refreshRuns()).catch(error=>{updateRunDetail(error.run||{id:run.id,error:error.message,status:"failed"});refreshRuns();});
    }
  }catch(error){$("editor-error").textContent=error.message;}
  finally{$("editor-save").disabled=false;}
}
function showDetail(title,content){$("detail-title").textContent=title;$("detail-content").innerHTML=content;if(!$("detail").open)$("detail").showModal();}
function showRun(run){state.detailRunId=run.id;showDetail("Task details",'<div id="run-status"></div><div id="run-output" class="message-body"></div>');updateRunDetail(run);}
function waitingRunActions(run){
  if(run.status!=="waiting")return "";
  if(run.kind==="external")return `<p>Remote agent needs input or authorization.</p><a href="#externalagents">Open remote task controls</a>`;
  const inputs=run.awaiting_input||[],gates=run.awaiting_approval||[];
  let content=inputs.length?`<p>Input required: ${escapeHtml(inputs.map(input=>input.name).join(", "))}</p>`:"";
  if(gates.length)content+=`<p>Approval required: ${escapeHtml(gates.map(gate=>gate.name).join(", "))}</p><div class="card-actions"><button data-approve-run="${escapeHtml(run.id)}">Approve and continue</button><button class="secondary" data-reject-run="${escapeHtml(run.id)}">${run.cancellation_cleanup?"Skip cleanup step":"Reject"}</button></div>`;
  return content||"<p>Waiting for your response.</p>";
}
function cleanupRunStatus(run){
  const status=run.cleanup_status?`<p role="status">Cancellation cleanup: ${escapeHtml(run.cleanup_status)}. Completed external actions remain in effect.</p>`:"";
  const reasons={CleanupEffectUncertainError:"Previous attempt may have changed external data. Step was not repeated.",CleanupRejected:"Skipped after your response.",LegacyScopeCapturesUnavailable:"Older save lacks nested cleanup context. Review previous external effects."};
  return status+(run.workflow_warnings?.length?`<p role="status">${run.workflow_warnings.length} cleanup warning(s). Review execution history before starting another run.</p><ul>${run.workflow_warnings.map(w=>`<li>${escapeHtml(String(w.node_id).split("/").at(-1))}: ${escapeHtml(reasons[w.error_type]||"Step could not finish; review its recorded effects.")}</li>`).join("")}</ul>`:"");
}
function updateRunDetail(run){if(!$("run-output")||run.id!==state.detailRunId)return;$("run-status").innerHTML=badge(run.status,run.status)+waitingRunActions(run)+cleanupRunStatus(run);renderContent($("run-output"),run.output||run.error||(run.status==="waiting"?"Waiting for your response.":["completed","cancelled"].includes(run.status)?"No text output.":"Working…"));if(!$("run-trace-button"))$("run-output").insertAdjacentHTML("afterend",`<button class="secondary" id="run-trace-button" data-run-trace="${escapeHtml(run.id)}">Inspect execution trace</button>`);}
document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.classList.contains("close-dialog"))button.closest("dialog").close();
    if(button.dataset.editAgent)openEditor("agent-edit",button.dataset.editAgent);
    if(button.dataset.action==="quick-build")location.hash="quick-build";
    if(button.dataset.action==="create-agent")openEditor("agent");
    if(button.dataset.action==="create-workflow"||button.id==="create-workflow")openEditor("workflow");
    if(button.id==="add-step")addWorkflowStep();
    if(button.classList.contains("remove-step"))button.closest(".step-editor").remove();
    if(button.dataset.prompt){$("chat-task").value=button.dataset.prompt;$("chat-task").focus();}
    if(button.dataset.agent){if(state.busy){notify("Let the current conversation finish first.");return;}newChat();$("chat-agent").value=button.dataset.agent;selectAgent();}
    if(button.dataset.session){if(state.busy){notify("Let the current conversation finish first.");return;}const session=await api(`/api/sessions/${encodeURIComponent(button.dataset.session)}`);state.sessionId=session.id;$("chat-project").value=session.knowledge_project_id||"";if(typeof updateKnowledgeDisclosure==="function")updateKnowledgeDisclosure();$("messages").replaceChildren();session.messages.forEach(m=>{const node=addMessage(m.role,m.content,providerName(m.provider||""),m.run_id,m.citations||[],m.structured_content||[],m.retrieval_notice);if(m.status==="failed")node.classList.add("error");});$("chat-agent").value=session.agent_id||"";$("chat-provider").value=session.provider||"codex";$("chat-model").value=session.model||"";if($("chat-effort"))$("chat-effort").value=session.effort||"";location.hash="chat";}
    if(button.dataset.definition){const item=state[button.dataset.kind].find(a=>a.id===button.dataset.definition);showDetail(item.document.metadata?.name||item.id,`<p>${escapeHtml(item.document.metadata?.description||"")}</p><pre>${escapeHtml(item.document.spec?.purpose||JSON.stringify(item.document,null,2))}</pre>`);}
    if(button.dataset.provider){const provider=state.providers.find(p=>p.id===button.dataset.provider);showDetail(provider.name,`<p class="callout">${state.mode==="team"?"Use your existing Nexus login and provider setup. Shared CLI workers receive your account configuration and submitted prompt content.":provider.kind==="cli"?"Sign in through the provider’s official CLI. Nexus uses that executable and does not collect or copy its account tokens.":"Configure the API key in the environment before starting Nexus Studio. It will be read by the existing provider adapter."}</p><pre>${escapeHtml(provider.setup)}</pre><p class="card-meta">Account authorization and usage limits are verified by the provider when you run a task.</p>`);}
    if(button.dataset.workflow){const item=state.workflows.find(w=>w.id===button.dataset.workflow);const validation=await api(`/api/workflows/${encodeURIComponent(item.id)}/validate`);showDetail(item.document?.metadata?.name||item.id,`${badge(validation.valid?"Valid workflow":"Needs attention",validation.valid?"ready":"failed")}${(item.document?.steps||[]).map((s,i)=>`<div class="flow-step"><b>${i+1}</b><div>${escapeHtml(s.name||s.id)}<small>${escapeHtml(s.agent_type)}</small></div></div>`).join("")}<pre>${escapeHtml([...validation.errors,...validation.warnings,...validation.flow].join("\n"))}</pre>`);}
    if(button.dataset.runWorkflow)openEditor("workflow-run",button.dataset.runWorkflow);
    if(button.dataset.tool)openEditor("tool",button.dataset.tool);
    if(button.dataset.run)showRun(await api(`/api/runs/${encodeURIComponent(button.dataset.run)}`));
    if(button.id==="refresh-activity")await refreshRuns();
    if(button.id==="refresh-providers"){state.providers=(await api("/api/bootstrap")).providers;renderProviders();notify("Provider installation status refreshed.");}
  }catch(error){notify(error.message);}
});
$("new-chat").addEventListener("click",newChat);$("chat-form").addEventListener("submit",sendMessage);$("editor-form").addEventListener("submit",saveEditor);$("chat-agent").addEventListener("change",selectAgent);
$("chat-task").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&(event.ctrlKey||event.metaKey||matchMedia("(pointer: fine)").matches)){event.preventDefault();$("chat-form").requestSubmit();}});
document.addEventListener("keydown",event=>{if(event.key.toLowerCase()==="n"&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!document.querySelector("dialog[open]")&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName))newChat();});
window.addEventListener("hashchange",showView);
async function bootstrap(){
  if(bootstrapInFlight)return;bootstrapInFlight=true;clearTimeout(bootstrapRetryTimer);
  try{const data=await api("/api/bootstrap");if(state.user&&data.user?.nexus_id!==state.user.nexus_id){location.reload();return;}bootstrapFailures=0;state.workspacePath=data.workspace.path;disconnectedRuns.delete("bootstrap");connectionStatus();state.accountUrl=data.account_url;state.token=data.token;state.mode=data.mode;state.user=data.user;state.providers=data.providers;$("team-login").hidden=true;$("send-message").disabled=false;
    if(data.mode==="team"){$("deployment-mode").textContent="TEAM";$("runtime-label").textContent=`${data.user.nexus_id} · ${data.user.role}`;$("composer-hint").textContent="Uses your Nexus provider configuration";$("view-providers").querySelector(".callout").textContent="Providers use your existing Nexus account setup. Shared Codex and Claude Code runs use isolated prompt-only containers. Complete provider setup through Nexus login.";}
    $("workspace-name").textContent=data.workspace.name;$("chat-provider").innerHTML=providerOptions(state.providers.find(p=>p.configured&&p.kind==="cli")?.id||"codex");renderProviders();await Promise.all([refreshDefinitions(),refreshSessions(),refreshRuns()]);state.tools=(await api("/api/tools")).items;renderTools();showView();state.ready=true;restoreRunMonitoring();window.dispatchEvent(new Event("studio-ready"));}
  catch(error){if(error.retryable){disconnectedRuns.add("bootstrap");connectionStatus();}if($("team-login").hidden)notify(`Could not load the workspace: ${error.message}`);$("send-message").disabled=true;if(error.retryable&&$("team-login").hidden)bootstrapRetryTimer=setTimeout(bootstrap,Math.min(15000,1000*2**Math.min(bootstrapFailures++,4)));}
  finally{bootstrapInFlight=false;}
}
window.addEventListener("studio-connection-restored",()=>{if(reconnectHandshake)return;reconnectHandshake=api("/api/bootstrap").then(data=>{if(data.workspace.path===state.workspacePath&&data.user?.nexus_id===state.user?.nexus_id)state.token=data.token;}).catch(()=>{}).finally(()=>{reconnectHandshake=null;});});
$("retry-login").addEventListener("click",bootstrap);
bootstrap();

// One navigation tree for desktop and mobile keeps counts and active links in sync.
(() => {
  const toggle = $("mobile-menu-toggle"), sidebar = $("workspace-sidebar"), main = $("main-content");
  const mobile = matchMedia("(max-width: 767px)");
  document.body.classList.add("mobile-ready");
  function closeNavigation(restoreFocus = false) {
    document.body.classList.remove("mobile-nav-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation");
    toggle.textContent = "Menu";
    sidebar.removeAttribute("role");
    sidebar.removeAttribute("aria-modal");
    sidebar.removeAttribute("aria-label");
    main.inert = false;
    if (restoreFocus) toggle.focus();
  }
  toggle.addEventListener("click", () => {
    if (toggle.getAttribute("aria-expanded") === "true") return closeNavigation(true);
    if (!mobile.matches) return;
    document.body.classList.add("mobile-nav-open");
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Close navigation");
    toggle.textContent = "Close";
    sidebar.setAttribute("role", "dialog");
    sidebar.setAttribute("aria-modal", "true");
    sidebar.setAttribute("aria-label", "Workspace navigation");
    main.inert = true;
  });
  sidebar.addEventListener("click", event => {
    if (!event.target.closest("a, #new-chat, [data-session]") || !mobile.matches) return;
    closeNavigation();
    main.focus({preventScroll: true});
  });
  document.addEventListener("keydown", event => {
    if (!document.body.classList.contains("mobile-nav-open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeNavigation(true);
    } else if (event.key === "Tab") {
      const items = [...sidebar.querySelectorAll("a[href],button:not(:disabled)")].filter(item => item.getClientRects().length);
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  function syncNavigation() {
    document.body.dataset.view = view();
    document.querySelectorAll("#workspace-navigation a").forEach(link => {
      if (link.classList.contains("active")) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  document.querySelector(".skip-link").addEventListener("click", event => { event.preventDefault(); main.focus(); });
  mobile.addEventListener("change", () => closeNavigation());
  window.addEventListener("hashchange", () => { closeNavigation(); syncNavigation(); window.scrollTo({top: 0, behavior: "instant"}); });
  window.addEventListener("studio-ready", syncNavigation);
  syncNavigation();
  function sizeViewport() {
    // The visual viewport follows phone keyboards; pinch zoom must stay usable.
    const viewport = window.visualViewport;
    if (!viewport || viewport.scale === 1) {
      const height = viewport?.height || innerHeight;
      document.documentElement.style.setProperty("--studio-viewport-height", `${height}px`);
      document.body.classList.toggle("compact-chat", height < 640);
    }
  }
  window.visualViewport?.addEventListener("resize", sizeViewport);
  window.addEventListener("resize", sizeViewport);
  sizeViewport();
})();

$("reconnect-now").addEventListener("click",()=>{window.dispatchEvent(new Event("studio-reconnect"));if(!state.ready)bootstrap();});

"use strict";
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state = {token:"",providers:[],agents:[],workflows:[],tools:[],skills:[],sessions:[],runs:[],sessionId:null,busy:false,editor:null,ready:false};
let noticeTimer,bootstrapRetryTimer;
let bootstrapInFlight=false,bootstrapFailures=0,reconnectHandshake=null;
function notify(message){$("notice").textContent=message;$("notice").hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$("notice").hidden=true,5500);}
async function api(path, body, method){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),body===undefined?15000:90000);
  let response,data;
  const verb=method||(body===undefined?"GET":"POST");
  const init={method:verb,signal:controller.signal};
  if(body!==undefined||verb==="POST"||verb==="DELETE"){
    init.headers={"Content-Type":"application/json","X-Nexus-Studio-Token":state.token};
    if(body!==undefined)init.body=JSON.stringify(body);
  }
  try{
    response=await fetch(path,init);
    data=await response.json();
  }catch(cause){
    const error=new Error(body===undefined&&verb==="GET"?"Connection unavailable. Reconnecting…":"No confirmation received. Check Activity before trying this action again; it may have reached the server.");
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
function view(){const name=location.hash.slice(1).split("/")[0]||"chat";if(["connectors","entrypoints","schedules","evaluations","datasets","evaluators","experiments","models","externalagents","decisionmodels","skills","artifacts","toolsets","prompts"].includes(name))return "library";return ["chat","agents","workflows","providers","tools","activity","inbox","quick-build","library","workspace","workbench","projects","feedback","sharing","files","assistant"].includes(name)?name:"chat";}
function showView(){if(!$("team-login").hidden)return;const name=view();document.querySelectorAll(".view").forEach(section=>section.hidden=section.id!==`view-${name}`);const hashBase=location.hash.slice(1).split("/")[0];const targetNav=document.querySelector(`nav a[href="#${hashBase}"]`);document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("active",targetNav?a===targetNav:a.getAttribute("href")===`#${name}`));const titles={files:"Workspace files",assistant:"Studio assistant",projects:"Knowledge projects",feedback:"Response feedback",sharing:"Conversation sharing",inbox:"Approval inbox",workbench:"Workspace tools",workspace:"Workspace & event mesh",library:"Library","quick-build":"Quick Build",chat:"Conversations",agents:"Agents",workflows:"Workflows",providers:"Providers & models",tools:"Tools",activity:"Activity"};const libraryTitles={experiments:"Experiment Lab",externalagents:"External agents",decisionmodels:"Decision models",evaluations:"Evaluations",datasets:"Datasets",evaluators:"Evaluators",schedules:"Schedules",connectors:"Connectors",entrypoints:"Entrypoints",models:"Models"};$("view-title").textContent=(name==="library"&&libraryTitles[hashBase])||titles[name]||"Conversations";if(name==="tools")api("/api/tools").then(data=>{state.tools=data.items;renderTools();}).catch(error=>notify(error.message));if(name==="agents"||name==="quick-build")loadSkills().catch(()=>{});if(name==="activity")refreshRuns().catch(error=>notify(error.message));if(name==="chat")loadKnowledgeProjects().catch(()=>{});if(name==="chat"&&typeof renderChatArtifacts==="function")renderChatArtifacts();}
function agentOptions(selected=""){
  let html='<optgroup label="Built-in specialists">';
  html+=`<option value="" ${selected===""?"selected":""}>General assistant</option>`;
  html+=`<option value="studio-assistant" ${selected==="studio-assistant"?"selected":""}>Studio assistant</option>`;
  html+='</optgroup>';
  const custom=(state.agents||[]).filter(a=>a.document);
  if(custom.length){
    html+='<optgroup label="Custom specialists">';
    for(const a of custom){
      const isSel=a.id===selected?"selected":"";
      html+=`<option value="${escapeHtml(a.id)}" ${isSel}>${escapeHtml(a.document.metadata?.name||a.id)}</option>`;
    }
    html+='</optgroup>';
  }
  return html;
}
function renderAgents(){
  $("agent-count").textContent=state.agents.length;
  const builtinHtml=`<div class="section-subheading" style="margin-bottom:14px"><span class="eyebrow">STANDARD ROLES</span><h2 style="font-size:18px;margin:4px 0 0">Built-in specialists</h2><p style="font-size:12px;color:var(--muted);margin:4px 0 0">Ready-made platform assistants available in every workspace.</p></div><div class="resource-grid"><article class="resource-card"><div class="card-meta">${badge("BUILT-IN")}<span>General assistant</span></div><h2>General assistant</h2><p>Direct conversation with your selected provider and tools. Flexible general-purpose assistant for any task.</p><div class="card-meta"><span>Provider default model</span><span>· Interactive chat</span></div><div class="card-actions"><button data-agent="">Start conversation</button></div></article><article class="resource-card"><div class="card-meta">${badge("BUILT-IN")}<span>Platform help</span></div><h2>Studio assistant</h2><p>Answers platform questions and reads, lists and edits workspace files. Every change waits for your Apply and is versioned in git.</p><div class="card-meta"><span>Platform documentation</span><span>· Workspace files</span><span>· Changes need approval</span></div><div class="card-actions"><button data-agent="studio-assistant">Start conversation</button><a class="button-link secondary" href="#assistant">Help center</a></div></article></div>`;
  const customCards=state.agents.length?`<div class="resource-grid">${state.agents.map(item=>{
    const doc=item.document||{},meta=doc.metadata||{},spec=doc.spec||{};
    if(item.error)return `<article class="resource-card"><h2>${escapeHtml(item.id)}</h2>${badge("Invalid definition","failed")}<p>${escapeHtml(item.error)}</p></article>`;
    return `<article class="resource-card"><div class="card-meta">${badge("AGENT")}<span>${escapeHtml(providerName(spec.provider))}</span></div><h2>${escapeHtml(meta.name||item.id)}</h2><p>${escapeHtml(meta.description||spec.purpose||"Ready for your next task.")}</p><div class="card-meta"><span>${escapeHtml(spec.model||"Provider default model")}</span>${spec.effort?`<span>· ${escapeHtml(spec.effort)} effort</span>`:""}<span>· ${escapeHtml(spec.timeout_seconds||600)}s timeout</span>${(spec.skill_ids||[]).length?`<span>· ${(spec.skill_ids.length>1?`Skills: ${escapeHtml(spec.skill_ids.join(", "))}`:`Skill: ${escapeHtml(spec.skill_ids[0])}`)}</span>`:""}</div><div class="card-actions"><button data-agent="${escapeHtml(item.id)}">Start conversation</button><button class="secondary" data-versions="${escapeHtml(item.id)}" data-kind="agents">Versions</button><button class="secondary" data-schedule-agent="${escapeHtml(item.id)}">Schedule</button><button class="secondary" data-edit-agent="${escapeHtml(item.id)}">Edit</button><button class="secondary" data-definition="${escapeHtml(item.id)}" data-kind="agents">Instructions</button><button class="secondary" data-delete-definition="${escapeHtml(item.id)}" data-kind="agents">Delete</button></div></article>`;
  }).join("")}</div>`:empty("Give your team its first custom specialist","Create an agent with a clear purpose, then choose which provider does the work.","create-agent","Create an agent");
  const customHtml=`<div class="section-subheading" style="margin:28px 0 14px"><span class="eyebrow">WORKSPACE SPECIALISTS</span><h2 style="font-size:18px;margin:4px 0 0">Custom specialists</h2><p style="font-size:12px;color:var(--muted);margin:4px 0 0">Agents configured for specific tasks, models, or workflows.</p></div>${customCards}`;
  $("agents-content").innerHTML=builtinHtml+customHtml;
  const selected=$("chat-agent").value;
  $("chat-agent").innerHTML=agentOptions(selected);
  if(selected)$("chat-agent").value=selected;
  if($("agents-search-input")?.value)filterResourceCards("agents-content","agents-search-input","agents");
}
function renderWorkflows(){
  $("workflow-count").textContent=state.workflows.length;
  $("workflows-content").innerHTML=state.workflows.length?`<div class="resource-grid">${state.workflows.map(item=>{
    const doc=item.document||{},steps=doc.steps||doc.graph?.nodes||[];
    return `<article class="resource-card"><div class="card-meta">${badge(item.error?"Invalid definition":doc.graph?"GRAPH WORKFLOW":"WORKFLOW",item.error?"failed":"")}<span>${steps.length} steps</span></div><h2>${escapeHtml(doc.metadata?.name||item.id)}</h2><p>${escapeHtml(item.error||doc.metadata?.description||(doc.graph?steps.slice(0,4).map(s=>s.name||s.kind||s.id).join(" · ")+(steps.length>4?` · ${steps.length-4} more`:""):steps.map(s=>s.agent_type).join(" → ")))}</p><div class="card-actions"><button data-run-workflow="${escapeHtml(item.id)}" ${item.error?"disabled":""}>Run workflow</button><button class="secondary" data-workflow-history="${escapeHtml(item.id)}">History</button><button class="secondary" data-schedule-workflow="${escapeHtml(item.id)}" ${item.error?"disabled":""}>Schedule</button>${doc.graph?`<button class="secondary" data-edit-graph="${escapeHtml(item.id)}">Edit canvas</button>`:`<button class="secondary" data-edit-workflow="${escapeHtml(item.id)}">Edit</button>`}<button class="secondary" data-versions="${escapeHtml(item.id)}" data-kind="workflows">Versions</button><button class="secondary" data-workflow="${escapeHtml(item.id)}">View & validate</button><button class="secondary" data-delete-definition="${escapeHtml(item.id)}" data-kind="workflows">Delete</button></div></article>`;
  }).join("")}</div>`:empty("Connect your agents into a workflow","Define the handoffs between specialists. Each step can use its agent’s own provider and model.","create-workflow","Create a workflow");
  if($("workflows-search-input")?.value)filterResourceCards("workflows-content","workflows-search-input","workflows");
}
// Default model suggestions per provider; model inputs stay free text.
const MODEL_INPUTS='#chat-model,#builder-model,input[name="model"],input[data-build-field$=".model"]';
function renderModelLists(){let host=$("model-lists");if(!host){host=document.createElement("div");host.id="model-lists";document.body.appendChild(host);}host.innerHTML=state.providers.map(p=>`<datalist id="models-${escapeHtml(p.id)}">${(p.models||[]).map(m=>`<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join("")}</datalist>`).join("");linkModelInputs();}
function modelProvider(input){if(input.id==="chat-model")return $("chat-provider")?.value;if(input.id==="builder-model")return $("builder-provider")?.value;const field=input.dataset.buildField;if(field)return document.querySelector(`[data-build-field="${CSS.escape(field.slice(0,-6))}.provider"]`)?.value;return input.form?.querySelector('select[name="provider"]')?.value;}
function linkModelInputs(){document.querySelectorAll(MODEL_INPUTS).forEach(input=>{const provider=modelProvider(input);if(provider&&document.getElementById("models-"+provider))input.setAttribute("list","models-"+provider);else input.removeAttribute("list");if(!input.placeholder)input.placeholder="Provider default · pick or type a model";});}
function firstModelForProvider(providerId){const p=(state.providers||[]).find(p=>p.id===providerId);return p?.models?.[0]?.id||"";}
async function loadSkills(){try{const res=await api("/api/catalog/skills");state.skills=(res.items||[]).filter(item=>!item.archived);return state.skills;}catch{return state.skills||[];}}
async function loadDecisionModels(){try{const res=await api("/api/catalog/decisionmodels");state.decisionmodels=(res.items||[]).filter(item=>!item.archived);return state.decisionmodels;}catch{return state.decisionmodels||[];}}
async function loadKnowledgeProjects(){
  try{
    const res=await api("/api/studio/projects");
    state.knowledgeProjects=res.items||[];
    state.knowledgeRetrievalOptions=res.retrieval_options;
    const select=$("chat-project");
    if(select){
      const selected=select.value;
      select.innerHTML='<option value="">No project</option>'+state.knowledgeProjects.filter(p=>!p.archived).map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join("");
      select.value=selected;
      if(typeof updateKnowledgeDisclosure==="function")updateKnowledgeDisclosure();
    }
    return state.knowledgeProjects;
  }catch{return state.knowledgeProjects||[];}
}
function skillOptions(selected="", extraSkills=[]){
  const allSkills=[...(state.skills||[])];
  for(const s of extraSkills){if(s&&!allSkills.some(item=>item.id===s.id))allSkills.push(s);}
  let options='<option value="">None (no skill)</option>';
  for(const s of allSkills){
    const isSelected=s.id===selected?"selected":"";
    const label=s.name?`${s.name} (${s.id})`:s.id;
    options+=`<option value="${escapeHtml(s.id)}" ${isSelected}>${escapeHtml(label)}</option>`;
  }
  if(selected&&!allSkills.some(s=>s.id===selected)){
    options+=`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`;
  }
  options+='<option value="__new__">+ New skill…</option>';
  return options;
}
function agentSkillOptions(selected=[], extraSkills=[], disabled=false){
  const allSkills=[...(state.skills||[])];
  for(const s of extraSkills){if(s&&!allSkills.some(item=>item.id===s.id))allSkills.push(s);}
  for(const id of selected){
    if(id&&!allSkills.some(s=>s.id===id)){
      allSkills.push({id,name:id,description:"(not in catalog)"});
    }
  }
  if(!allSkills.length){
    return '<p class="empty-note" style="padding:8px;font-size:12px;color:var(--muted)">No skills in catalog yet. Click "+ New skill…" or import from Library.</p>';
  }
  return allSkills.map(s=>{
    const isChecked=selected.includes(s.id)?"checked":"";
    const isDisabled=disabled?"disabled":"";
    const label=s.name?`${s.name} (${s.id})`:s.id;
    return `<label class="skills-select-item check-label"><input type="checkbox" name="skill_ids" value="${escapeHtml(s.id)}" ${isChecked} ${isDisabled}><div class="skills-select-meta"><span class="skills-select-name">${escapeHtml(label)}</span>${s.description?`<span class="skills-select-desc">${escapeHtml(s.description)}</span>`:""}</div></label>`;
  }).join("");
}
function filterSkillsList(listElement, query=""){
  if(!listElement)return;
  const q=String(query||"").trim().toLowerCase();
  const items=listElement.querySelectorAll(".skills-select-item");
  let visibleCount=0;
  items.forEach(item=>{
    const name=item.querySelector(".skills-select-name")?.textContent||"";
    const desc=item.querySelector(".skills-select-desc")?.textContent||"";
    const val=item.querySelector('input[type="checkbox"]')?.value||"";
    const match=!q||name.toLowerCase().includes(q)||desc.toLowerCase().includes(q)||val.toLowerCase().includes(q);
    item.hidden=!match;
    if(match)visibleCount++;
  });
  let emptyNote=listElement.querySelector(".skills-filter-empty");
  if(items.length>0&&visibleCount===0&&q){
    if(!emptyNote){
      emptyNote=document.createElement("p");
      emptyNote.className="empty-note skills-filter-empty";
      emptyNote.style.padding="8px";
      emptyNote.style.fontSize="12px";
      emptyNote.style.color="var(--muted)";
      listElement.appendChild(emptyNote);
    }
    const isTool=listElement.classList?.contains?.("agent-tools-list");
    emptyNote.textContent=`No ${isTool?"tools":"skills"} match "${query.trim()}".`;
    emptyNote.hidden=false;
  }else if(emptyNote){
    emptyNote.hidden=true;
  }
}
function handleSkillsFilter(event){
  const filterInput=event.target?.closest?.(".skills-filter-input");
  if(filterInput){
    const container=filterInput.closest?.(".agent-skills-section")||filterInput.parentElement;
    const list=container?.querySelector?.(".skills-select-list");
    if(list){
      filterSkillsList(list, filterInput.value);
    }
  }
}
if(typeof document!=="undefined"){
  document.addEventListener("input", handleSkillsFilter);
  document.addEventListener("search", handleSkillsFilter);
}
function filterResourceCards(containerId, inputId, itemName="items"){
  const container=$(containerId), input=$(inputId);
  if(!container||!input)return;
  const q=String(input.value||"").trim().toLowerCase();
  const items=container.querySelectorAll(".resource-card, .library-row, .activity-row");
  let matchCount=0;
  items.forEach(el=>{
    const text=(el.textContent+" "+(el.dataset?.search||"")).toLowerCase();
    const match=!q||text.includes(q);
    el.hidden=!match;
    if(match)matchCount++;
  });
  container.querySelectorAll(".section-subheading").forEach(sh=>{
    const nextGrid=sh.nextElementSibling;
    if(nextGrid&&nextGrid.classList.contains("resource-grid")){
      const anyVisible=[...nextGrid.querySelectorAll(".resource-card")].some(c=>!c.hidden);
      sh.hidden=!anyVisible;
      nextGrid.hidden=!anyVisible;
    }
  });
  let emptyNote=container.querySelector(".page-search-empty");
  if(items.length>0&&matchCount===0&&q){
    if(!emptyNote){
      emptyNote=document.createElement("p");
      emptyNote.className="empty-note page-search-empty";
      container.appendChild(emptyNote);
    }
    emptyNote.textContent=`No ${itemName} match "${input.value.trim()}".`;
    emptyNote.hidden=false;
  }else if(emptyNote){
    emptyNote.hidden=true;
  }
}
let activeSearchIndex=-1;
function searchStudio(query=""){
  const q=String(query||"").trim().toLowerCase();
  const results={pages:[],agents:[],workflows:[],tools:[],skills:[],projects:[],sessions:[]};
  const pagesList=[
    {id:"chat", title:"Conversations", desc:"Interactive chat with agents and providers"},
    {id:"quick-build", title:"Quick Build", desc:"Build multi-agent teams and workflows with AI"},
    {id:"agents", title:"Agents", desc:"Specialist team members and instructions"},
    {id:"workflows", title:"Workflows", desc:"Multi-step pipelines and canvas graphs"},
    {id:"tools", title:"Tools", desc:"Registered workspace tools and actions"},
    {id:"library", title:"Library", desc:"Prompts, skills, artifacts, connectors, and schedules"},
    {id:"providers", title:"Providers & models", desc:"CLI coding agents and API model providers"},
    {id:"inbox", title:"Approval inbox", desc:"Review gates, campaign drafts, and recoveries"},
    {id:"activity", title:"Activity", desc:"Task history, outcomes, and progress"},
    {id:"projects", title:"Knowledge projects", desc:"Instruction context and source documents"},
    {id:"files", title:"Workspace files", desc:"Server files and private downloads"},
    {id:"workbench", title:"Workspace tools", desc:"Memory, checkpoints, coordination, controls"},
    {id:"workspace", title:"Workspace & event mesh", desc:"Queue health and runtime status"},
    {id:"assistant", title:"Studio assistant", desc:"Platform help and guidance"},
  ];
  if(!q){
    results.pages=pagesList.slice(0, 6);
    return results;
  }
  results.pages=pagesList.filter(p=>p.title.toLowerCase().includes(q)||p.desc.toLowerCase().includes(q)||p.id.includes(q)).slice(0, 4);

  const allAgents=[
    {id:"general-assistant", name:"General assistant", desc:"Direct conversation with your selected provider", isBuiltin:true},
    {id:"studio-assistant", name:"Studio assistant", desc:"Platform documentation and help", isBuiltin:true},
    ...(state.agents||[]).map(a=>{
      const spec=a.document?.spec||{}, meta=a.document?.metadata||{};
      return {
        id: a.id,
        name: meta.name||a.id,
        desc: meta.description||spec.purpose||"",
        provider: spec.provider||"",
        model: spec.model||"",
        skills: (spec.skill_ids||[]).join(" ")
      };
    })
  ];
  results.agents=allAgents.filter(a=>(
    a.name.toLowerCase().includes(q) ||
    a.id.toLowerCase().includes(q) ||
    a.desc.toLowerCase().includes(q) ||
    (a.provider && a.provider.toLowerCase().includes(q)) ||
    (a.skills && a.skills.toLowerCase().includes(q))
  )).slice(0, 5);

  results.workflows=(state.workflows||[]).map(w=>{
    const doc=w.document||{}, meta=doc.metadata||{}, steps=doc.steps||doc.graph?.nodes||[];
    const stepSummary=steps.map(s=>s.agent_type||s.name||s.id).join(" ");
    return {
      id: w.id,
      name: meta.name||w.id,
      desc: meta.description||stepSummary||"Workflow",
      stepSummary
    };
  }).filter(w=>(
    w.name.toLowerCase().includes(q) ||
    w.id.toLowerCase().includes(q) ||
    w.desc.toLowerCase().includes(q) ||
    w.stepSummary.toLowerCase().includes(q)
  )).slice(0, 5);

  results.tools=(state.tools||[]).filter(t=>(
    t.id.toLowerCase().includes(q) ||
    (t.description && t.description.toLowerCase().includes(q)) ||
    (t.category && t.category.toLowerCase().includes(q))
  )).slice(0, 5);

  results.skills=(state.skills||[]).filter(s=>(
    s.id.toLowerCase().includes(q) ||
    (s.name && s.name.toLowerCase().includes(q)) ||
    (s.description && s.description.toLowerCase().includes(q))
  )).slice(0, 5);

  const projectsList=(state.knowledgeProjects||state.projects)||[];
  results.projects=projectsList.filter(p=>(
    (p.name && p.name.toLowerCase().includes(q)) ||
    p.id.toLowerCase().includes(q) ||
    (p.instructions && p.instructions.toLowerCase().includes(q))
  )).slice(0, 4);

  results.sessions=(state.sessions||[]).filter(s=>(
    (s.title && s.title.toLowerCase().includes(q)) ||
    s.id.toLowerCase().includes(q)
  )).slice(0, 4);

  return results;
}

function renderGlobalSearchResults(results, query=""){
  const resultsBox=$("global-search-results");
  if(!resultsBox)return;
  activeSearchIndex=-1;
  const q=String(query||"").trim();
  const totalCount=results.pages.length+results.agents.length+results.workflows.length+results.tools.length+results.skills.length+results.projects.length+results.sessions.length;
  if(totalCount===0&&q){
    resultsBox.innerHTML=`<div class="global-search-empty">No results found for "${escapeHtml(q)}".</div>`;
    return;
  }
  let html="";
  const appendSection=(title, items, kind, badgeType)=>{
    if(!items.length)return;
    html+=`<div class="global-search-group-title">${escapeHtml(title)}</div>`;
    items.forEach(item=>{
      html+=`<button type="button" class="global-search-item" data-search-kind="${kind}" data-search-id="${escapeHtml(item.id)}">${badge(badgeType)}<div class="global-search-item-meta"><span class="global-search-item-name">${escapeHtml(item.name||item.title||item.id)}</span>${item.desc?`<span class="global-search-item-desc">${escapeHtml(item.desc)}</span>`:""}</div></button>`;
    });
  };
  appendSection("Pages", results.pages, "page", "PAGE");
  appendSection("Agents", results.agents, "agent", "AGENT");
  appendSection("Workflows", results.workflows, "workflow", "FLOW");
  appendSection("Tools", results.tools, "tool", "TOOL");
  appendSection("Skills", results.skills, "skill", "SKILL");
  appendSection("Knowledge Projects", results.projects, "project", "PROJECT");
  appendSection("Recent Conversations", results.sessions, "session", "CHAT");
  resultsBox.innerHTML=html;
}

function openGlobalSearch(){
  const menu=$("global-search-menu");
  if(menu){
    menu.hidden=false;
    runGlobalSearch();
  }
}
function closeGlobalSearch(){
  const menu=$("global-search-menu");
  if(menu)menu.hidden=true;
  activeSearchIndex=-1;
}
function runGlobalSearch(){
  const val=$("global-search-input")?.value||"";
  const results=searchStudio(val);
  renderGlobalSearchResults(results, val);
}
function handleGlobalSearchSelect(itemEl){
  if(!itemEl)return;
  const kind=itemEl.dataset.searchKind;
  const id=itemEl.dataset.searchId;
  closeGlobalSearch();
  if(kind==="page"){
    location.hash=id;
  }else if(kind==="agent"){
    if(id==="studio-assistant"){
      location.hash="assistant";
    }else if(id==="general-assistant"){
      location.hash="chat";
    }else{
      location.hash="agents";
      const searchInput=$("agents-search-input");
      if(searchInput){
        searchInput.value=id;
        filterResourceCards("agents-content","agents-search-input","agents");
      }
    }
  }else if(kind==="workflow"){
    location.hash="workflows";
    const searchInput=$("workflows-search-input");
    if(searchInput){
      searchInput.value=id;
      filterResourceCards("workflows-content","workflows-search-input","workflows");
    }
  }else if(kind==="tool"){
    location.hash="tools";
    const searchInput=$("tools-search-input");
    if(searchInput){
      searchInput.value=id;
      filterResourceCards("tools-content","tools-search-input","tools");
    }
  }else if(kind==="skill"){
    location.hash="library/skills";
    if(typeof library!=="undefined"){library.kind="skills";if(typeof renderLibrary==="function")renderLibrary();}
    const searchInput=$("library-search-input");
    if(searchInput){
      searchInput.value=id;
      filterResourceCards("library-content","library-search-input","skills");
    }
  }else if(kind==="project"){
    location.hash="projects";
    const searchInput=$("projects-search-input");
    if(searchInput){
      searchInput.value=id;
      filterResourceCards("projects-content","projects-search-input","projects");
    }
  }else if(kind==="session"){
    openSession(id);
  }
}
if(typeof document!=="undefined"){
  document.addEventListener("input", event=>{
    const input=event.target;
    if(input?.classList?.contains("page-search-input")){
      const targetId=input.dataset.searchTarget;
      const itemName=input.dataset.searchName||"items";
      if(targetId)filterResourceCards(targetId, input.id, itemName);
    }else if(input?.id==="global-search-input"){
      runGlobalSearch();
    }
  });
  document.addEventListener("search", event=>{
    const input=event.target;
    if(input?.classList?.contains("page-search-input")){
      const targetId=input.dataset.searchTarget;
      const itemName=input.dataset.searchName||"items";
      if(targetId)filterResourceCards(targetId, input.id, itemName);
    }else if(input?.id==="global-search-input"){
      runGlobalSearch();
    }
  });
  document.addEventListener("click", event=>{
    const item=event.target.closest(".global-search-item");
    if(item){
      handleGlobalSearchSelect(item);
      return;
    }
    if(!event.target.closest(".global-search-container")){
      closeGlobalSearch();
    }
  });
  $("global-search-input")?.addEventListener("focus", openGlobalSearch);
  $("global-search-input")?.addEventListener("keydown", event=>{
    const menu=$("global-search-menu");
    if(menu&&!menu.hidden){
      const items=[...menu.querySelectorAll(".global-search-item")];
      if(event.key==="ArrowDown"){
        event.preventDefault();
        if(items.length){
          activeSearchIndex=(activeSearchIndex+1)%items.length;
          items.forEach((it,i)=>it.classList.toggle("active",i===activeSearchIndex));
          items[activeSearchIndex]?.scrollIntoView({block:"nearest"});
        }
      }else if(event.key==="ArrowUp"){
        event.preventDefault();
        if(items.length){
          activeSearchIndex=(activeSearchIndex-1+items.length)%items.length;
          items.forEach((it,i)=>it.classList.toggle("active",i===activeSearchIndex));
          items[activeSearchIndex]?.scrollIntoView({block:"nearest"});
        }
      }else if(event.key==="Enter"){
        event.preventDefault();
        if(items.length){
          const selected=activeSearchIndex>=0?items[activeSearchIndex]:items[0];
          if(selected)handleGlobalSearchSelect(selected);
        }
      }else if(event.key==="Escape"){
        event.preventDefault();
        closeGlobalSearch();
        $("global-search-input")?.blur();
      }
    }
  });
  document.addEventListener("keydown", event=>{
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
      event.preventDefault();
      const input=$("global-search-input");
      if(input){input.focus();input.select();openGlobalSearch();}
    }else if(event.key==="/"&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!document.querySelector("dialog[open]")&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)){
      event.preventDefault();
      const input=$("global-search-input");
      if(input){input.focus();input.select();openGlobalSearch();}
    }
  });
}
function agentToolOptions(selected=[]){
  const allTools=[...(state.tools||[])];
  for(const id of selected){
    if(id&&!allTools.some(t=>t.id===id)){
      allTools.push({id,description:"(custom tool)"});
    }
  }
  if(!allTools.length){
    return '<p class="empty-note" style="padding:8px;font-size:12px;color:var(--muted)">No registered tools available in this workspace.</p>';
  }
  return allTools.map(t=>{
    const isChecked=selected.includes(t.id)?"checked":"";
    const action=t.id.includes(":")?t.id.split(":").slice(-1)[0]:t.id;
    const label=action!==t.id?`${action} (${t.id})`:t.id;
    return `<label class="skills-select-item check-label"><input type="checkbox" name="tool_ids" value="${escapeHtml(t.id)}" ${isChecked}><div class="skills-select-meta"><span class="skills-select-name">${escapeHtml(label)}</span>${t.description?`<span class="skills-select-desc">${escapeHtml(t.description)}</span>`:""}</div></label>`;
  }).join("");
}
function createSkillDialog(){
  return new Promise(resolve=>{
    const dialog=document.createElement("dialog");dialog.className="feature-dialog";
    dialog.innerHTML=`<form><header class="dialog-heading"><h2>Create a new skill</h2><button type="button" class="quiet close-dialog" data-dismiss aria-label="Close">×</button></header><div class="dialog-hint-row"><span>Have an existing skill repository or SKILL.md?</span><button type="button" class="secondary import-skill-trigger">Import skill…</button></div><label>Skill name<input name="name" required placeholder="e.g. Content Strategy Guidelines" maxlength="200"></label><label>Description (optional)<input name="description" placeholder="Short description of this skill" maxlength="500"></label><label>Instructions<textarea name="content" rows="6" required placeholder="Detailed guidelines and instructions this skill provides to the agent..."></textarea></label><p class="form-error" role="alert"></p><footer><button type="button" class="secondary import-skill-trigger" style="margin-right:auto">Import skill…</button><button type="button" class="secondary" data-dismiss>Cancel</button><button type="submit">Create skill</button></footer></form>`;
    document.body.append(dialog);let resolved=false;
    const finish=val=>{if(!resolved){resolved=true;dialog.close();dialog.remove();resolve(val);}};
    dialog.querySelectorAll("[data-dismiss]").forEach(b=>b.onclick=()=>finish(null));
    dialog.querySelectorAll(".import-skill-trigger").forEach(b=>b.onclick=()=>finish({importRequested:true}));
    dialog.addEventListener("cancel",()=>finish(null));
    dialog.querySelector("form").onsubmit=event=>{event.preventDefault();const fd=new FormData(event.target);finish({name:fd.get("name")?.trim(),description:fd.get("description")?.trim(),content:fd.get("content")?.trim()});};
    dialog.showModal();dialog.querySelector('input[name="name"]')?.focus();
  });
}
async function promptNewSkill(targetSelect=null, extraSkills=[], targetList=null){
  const answer=await createSkillDialog();
  if(!answer){
    if(targetSelect&&targetSelect.value==="__new__")targetSelect.value=targetSelect.dataset.prevValue||"";
    return null;
  }
  const agentList=targetList || (targetSelect?targetSelect.closest?.(".agent-skills-section")?.querySelector?.(".skills-select-list"):null) || (typeof $ === "function" ? $("agent-skills-list") : null);
  if(answer.importRequested){
    if(typeof importSkill==="function"){
      const imported=await importSkill();
      if(imported){
        const importedIds=imported.skills?.map(s=>s.id)||(imported.skill?[imported.skill.id]:[]);
        if(typeof loadSkills==="function")await loadSkills().catch(()=>{});
        if(targetSelect&&importedIds.length&&typeof document!=="undefined"){
          document.querySelectorAll(".agent-skill-select").forEach(sel=>{
            const val=sel===targetSelect?importedIds[0]:sel.value;
            sel.innerHTML=skillOptions(val,extraSkills);
            sel.value=val;
            sel.dataset.prevValue=val;
          });
          targetSelect.dispatchEvent(new Event("change",{bubbles:true}));
        }
        if(agentList){
          const checked=Array.from(agentList.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
          importedIds.forEach(id=>{if(!checked.includes(id))checked.push(id);});
          agentList.innerHTML=agentSkillOptions(checked,extraSkills);
          const filterInput=agentList.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
          if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(agentList,filterInput.value);
        }
        if(typeof document!=="undefined"){
          document.querySelectorAll("#builder-proposal .skills-select-list").forEach(list=>{
            if(list===agentList)return;
            const checked=Array.from(list.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
            list.innerHTML=agentSkillOptions(checked,extraSkills);
            const filterInput=list.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
            if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(list,filterInput.value);
          });
        }
        return imported;
      }
    }
    if(targetSelect&&targetSelect.value==="__new__")targetSelect.value=targetSelect.dataset.prevValue||"";
    return null;
  }
  if(!answer.name||!answer.content){
    if(targetSelect&&targetSelect.value==="__new__")targetSelect.value=targetSelect.dataset.prevValue||"";
    return null;
  }
  try{
    const payload={name:answer.name,description:answer.description||"",data:{content:answer.content||""}};
    const created=await api("/api/catalog/skills",payload);
    await loadSkills();
    if(targetSelect&&typeof document!=="undefined"){
      document.querySelectorAll(".agent-skill-select").forEach(sel=>{
        const val=sel===targetSelect?created.id:sel.value;
        sel.innerHTML=skillOptions(val,extraSkills);
        sel.value=val;
        sel.dataset.prevValue=val;
      });
      targetSelect.dispatchEvent(new Event("change",{bubbles:true}));
    }
    if(agentList){
      const checked=Array.from(agentList.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
      if(!checked.includes(created.id))checked.push(created.id);
      agentList.innerHTML=agentSkillOptions(checked,extraSkills);
      const filterInput=agentList.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
      if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(agentList,filterInput.value);
    }
    if(typeof document!=="undefined"){
      document.querySelectorAll("#builder-proposal .skills-select-list").forEach(list=>{
        if(list===agentList)return;
        const checked=Array.from(list.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
        list.innerHTML=agentSkillOptions(checked,extraSkills);
        const filterInput=list.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
        if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(list,filterInput.value);
      });
    }
    notify("Skill created and assigned.");
    return created;
  }catch(error){
    notify(error.message);
    if(targetSelect&&targetSelect.value==="__new__")targetSelect.value=targetSelect.dataset.prevValue||"";
    return null;
  }
}
// Model fields are rendered by many views; link new ones and follow provider changes.
let modelLinkQueued=false;
new MutationObserver(()=>{if(modelLinkQueued)return;modelLinkQueued=true;requestAnimationFrame(()=>{modelLinkQueued=false;linkModelInputs();});}).observe(document.body,{childList:true,subtree:true});
document.addEventListener("change",event=>{
  if(event.target instanceof HTMLSelectElement){
    const select=event.target;
    const isProvider=select.id==="chat-provider"||select.id==="builder-provider"||select.name==="provider"||select.dataset?.buildField?.endsWith(".provider");
    if(isProvider){
      let modelInput=null;
      if(select.id==="chat-provider")modelInput=$("chat-model");
      else if(select.id==="builder-provider")modelInput=$("builder-model");
      else if(select.dataset?.buildField?.endsWith(".provider")){
        const prefix=select.dataset.buildField.slice(0,-9);
        modelInput=document.querySelector(`[data-build-field="${CSS.escape(prefix)}.model"]`);
      }else if(select.name==="provider"){
        modelInput=select.form?.querySelector('input[name="model"]');
      }
      if(modelInput){
        modelInput.value="";
        modelInput.dispatchEvent(new Event("input",{bubbles:true}));
      }
    }
    if(select.classList.contains("agent-skill-select")){
      if(select.value==="__new__"){
        let extraSkills=[];
        if(typeof builder!=="undefined"&&builder?.draft?.proposal?.resources){
          extraSkills=builder.draft.proposal.resources.filter(r=>r.kind==="skills");
        }
        promptNewSkill(select,extraSkills);
      }else{
        select.dataset.prevValue=select.value;
      }
    }
    linkModelInputs();
  }
});
async function openSession(sessionId){
  if(state.busy){notify("Let the current conversation finish first.");return;}
  const session=await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  state.sessionId=session.id;
  if($("chat-project"))$("chat-project").value=session.knowledge_project_id||"";
  if(typeof updateKnowledgeDisclosure==="function")updateKnowledgeDisclosure();
  $("messages")?.replaceChildren();
  (session.messages||[]).forEach(m=>{
    const node=addMessage(m.role,m.content,providerName(m.provider||""),m.run_id,m.citations||[],m.structured_content||[],m.retrieval_notice);
    if(m.status==="failed")node.classList.add("error");
    if(session.studio_assistant&&m.role==="assistant"&&m.run_id&&typeof renderWorkspaceProposals==="function")renderWorkspaceProposals(node,m.run_id);
  });
  if($("chat-agent"))$("chat-agent").value=session.studio_assistant?"studio-assistant":(session.agent_id||"");
  if($("chat-provider"))$("chat-provider").value=session.provider||"codex";
  if($("chat-model"))$("chat-model").value=session.model||"";
  if($("chat-effort"))$("chat-effort").value=session.effort||"";
  location.hash="chat";
}
document.addEventListener("click",async event=>{
  const btn=event.target.closest(".new-skill-btn");
  if(btn){
    event.preventDefault();
    const targetId=btn.dataset.skillTarget;
    const select=targetId?$(targetId):btn.closest(".agent-skill-field,label,div")?.querySelector(".agent-skill-select");
    const agentList=btn.closest(".agent-skills-section")?.querySelector(".skills-select-list") || $("agent-skills-list");
    let extraSkills=[];
    if(typeof builder!=="undefined"&&builder?.draft?.proposal?.resources){
      extraSkills=builder.draft.proposal.resources.filter(r=>r.kind==="skills");
    }
    await promptNewSkill(select,extraSkills,agentList);
  }
  const importBtn=event.target.closest(".import-skill-btn");
  if(importBtn){
    event.preventDefault();
    if(typeof importSkill==="function"){
      const imported=await importSkill();
      if(imported){
        const importedIds=imported.skills?.map(s=>s.id)||(imported.skill?[imported.skill.id]:[]);
        if(typeof loadSkills==="function")await loadSkills().catch(()=>{});
        let extraSkills=[];
        if(typeof builder!=="undefined"&&builder?.draft?.proposal?.resources){
          extraSkills=builder.draft.proposal.resources.filter(r=>r.kind==="skills");
        }
        const agentList=importBtn.closest(".agent-skills-section")?.querySelector(".skills-select-list") || $("agent-skills-list");
        if(agentList){
          const checked=Array.from(agentList.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
          importedIds.forEach(id=>{if(!checked.includes(id))checked.push(id);});
          agentList.innerHTML=agentSkillOptions(checked,extraSkills);
          const filterInput=agentList.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
          if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(agentList,filterInput.value);
        }
        if(typeof document!=="undefined"){
          document.querySelectorAll("#builder-proposal .skills-select-list").forEach(list=>{
            if(list===agentList)return;
            const checked=Array.from(list.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
            list.innerHTML=agentSkillOptions(checked,extraSkills);
            const filterInput=list.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
            if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(list,filterInput.value);
          });
        }
      }
    }
  }
});
window.addEventListener("studio-resource-saved",event=>{
  if(event.detail?.kind==="skills"){
    loadSkills().then(()=>{
      let extraSkills=[];
      if(typeof builder!=="undefined"&&builder?.draft?.proposal?.resources){
        extraSkills=builder.draft.proposal.resources.filter(r=>r.kind==="skills");
      }
      document.querySelectorAll(".skills-select-list").forEach(list=>{
        const checked=Array.from(list.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
        list.innerHTML=agentSkillOptions(checked,extraSkills);
        const filterInput=list.closest?.(".agent-skills-section")?.querySelector?.(".skills-filter-input");
        if(filterInput?.value&&typeof filterSkillsList==="function")filterSkillsList(list,filterInput.value);
      });
    }).catch(()=>{});
  }
});
document.addEventListener("focusin",event=>{if(event.target instanceof HTMLInputElement&&event.target.matches(MODEL_INPUTS))linkModelInputs();});
function renderProviders(){
  renderModelLists();
  $("providers-content").innerHTML=state.providers.map(p=>`<article class="provider-row"><div><h2>${escapeHtml(p.name)}</h2><p>${state.mode==="team"?"Your Nexus account · shared worker execution":p.kind==="cli"?"Official CLI · local account configuration":"API provider · environment credentials"}</p>${p.models?.length?`<p class="provider-models">Models: ${p.models.map(m=>escapeHtml(m.name)).join(" · ")}</p>`:""}</div><div class="provider-side">${badge(p.status,p.configured?"ready":"")}<button class="secondary" data-provider="${escapeHtml(p.id)}">${p.configured?"Configuration":"Setup instructions"}</button></div></article>`).join("");
}
function renderTools(){
  $("tools-content").innerHTML=state.tools.length?`<div class="resource-grid">${state.tools.map(t=>`<article class="resource-card"><div class="card-meta">${badge(t.category||"TOOL")}</div><h2 class="tool-name">${escapeHtml(t.id)}</h2><p>${escapeHtml(t.description||"Registered Nexus tool")}</p><div class="card-actions"><button class="secondary" data-tool="${escapeHtml(t.id)}">Run tool</button></div></article>`).join("")}</div>`:empty("No tools registered","Register tools with the Nexus tool registry to make them available here.");
  if($("tools-search-input")?.value)filterResourceCards("tools-content","tools-search-input","tools");
}
function renderSessions(){
  $("session-count").textContent=state.sessions.length;
  $("recent-sessions").innerHTML=state.sessions.length?state.sessions.slice(0,12).map(s=>`<button class="recent" data-session="${escapeHtml(s.id)}">${escapeHtml(s.title)}</button>`).join(""):'<span class="recent empty">Your conversations will appear here.</span>';
}
function renderRuns(){
  const filter=state.activityFilter||"all";
  const allRuns=state.runs||[];
  const runs=allRuns.filter(r=>{
    if(filter==="all")return true;
    if(filter==="workflow")return r.kind==="workflow";
    if(filter==="build")return r.kind==="build";
    if(filter==="chat")return r.kind==="chat";
    if(filter==="tool")return r.kind==="tool";
    return true;
  });
  const emptyMessages={
    all:"Conversations, workflow runs, and tool results appear here as you use the workspace.",
    workflow:"Runs of your saved workflows will appear here.",
    build:"Runs creating or revising multi-agent teams will appear here.",
    chat:"Direct conversations with agents will appear here.",
    tool:"Standalone tool executions will appear here."
  };
  $("activity-content").innerHTML=runs.length?runs.map(r=>`<article class="activity-row" data-search="${escapeHtml(r.workflow_definition_id||"")}"><div class="activity-copy"><h2>${escapeHtml(r.task)}</h2><p>${escapeHtml(r.kind)} · ${escapeHtml(providerName(r.provider))} · ${escapeHtml(new Date(r.created_at).toLocaleString())}${r.step?" · "+escapeHtml(r.step):""}${r.rerun_of?" · re-run of "+escapeHtml(r.rerun_of.step_name):""}</p></div>${badge(r.status,r.status)}<button class="secondary" data-run="${escapeHtml(r.id)}">Details</button></article>`).join(""):empty("No matching activity",emptyMessages[filter]||"No items found for this filter.");
  if($("activity-search-input")?.value)filterResourceCards("activity-content","activity-search-input","activity");
}
async function refreshDefinitions(){const [agents,workflows]=await Promise.all([api("/api/agents"),api("/api/workflows")]);state.agents=agents.items;state.workflows=workflows.items;renderAgents();renderWorkflows();}
async function refreshSessions(){state.sessions=(await api("/api/sessions")).items;renderSessions();}
async function refreshRuns(){state.runs=(await api("/api/runs")).items;renderRuns();}
function addMessage(role,text,provider="",runId=null,citations=[],structured=[],retrievalNotice=null){
  $("welcome")?.remove();const node=document.createElement("article");node.className=`message ${role}`;
  node.innerHTML=`<div class="message-label">${role==="user"?"You":"Nexus"}<span class="message-provider">${escapeHtml(provider)}</span></div><div class="message-body"></div>`;
  renderContent(node.querySelector(".message-body"),text);if(runId)node.dataset.runId=runId;showCitations(node,citations);renderStructuredContent(node,structured);renderRetrievalNotice(node,retrievalNotice);$("messages").appendChild(node);$("messages").scrollTop=$("messages").scrollHeight;return node;
}
function selectAgent(){
  const val=$("chat-agent").value;
  if(val==="studio-assistant"){if($("chat-tools"))$("chat-tools").value="none";return;}
  const item=state.agents.find(a=>a.id===val);
  if(item){$("chat-provider").value=item.document.spec.provider;$("chat-model").value=item.document.spec.model||"";if($("chat-effort"))$("chat-effort").value=item.document.spec.effort||"";}
}
function newChat(){if(state.busy){notify("Let the current conversation finish first.");return;}state.sessionId=null;$("messages").replaceChildren();const select=$("chat-artifacts");if(select&&select.options){for(const opt of select.options)opt.selected=false;if(typeof renderChatArtifacts==="function")renderChatArtifacts();}addMessage("assistant","Ready when you are. Describe the task you’d like to work on.");location.hash="chat";$("chat-task").focus();}
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
const watchRun=pollRun;
if(typeof window!=="undefined"){window.watchRun=pollRun;window.pollRun=pollRun;}
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
  event.preventDefault();if(state.busy||state.attachmentUpload)return;const task=$("chat-task").value.trim();if(!task)return;
  if(location.pathname==="/agent.html"&&!state.agents.some(agent=>agent.id===new URLSearchParams(location.search).get("agent"))){notify("This embedded agent is unavailable. Open Nexus Studio to choose an agent.");return;}
  const agentVal=$("chat-agent").value;
  const isStudioAssistant=agentVal==="studio-assistant";
  const payload={
    task,
    provider:$("chat-provider").value,
    model:$("chat-model").value.trim(),
    ...($("chat-tools")?{worker_tools:isStudioAssistant?"none":$("chat-tools").value}:{}),
    ...($("chat-effort")?.value?{effort:$("chat-effort").value}:{}),
    agent_id:isStudioAssistant?null:(agentVal||null),
    ...(isStudioAssistant?{studio_assistant:true}:{}),
    session_id:state.sessionId,
    knowledge_project_id:$("chat-project")?.value||null,
    artifact_ids:[...$("chat-artifacts").selectedOptions].map(item=>item.value)
  };
  try{payload.prompt_values=await resolvePromptValues(task);}catch(error){notify(error.message);return;}state.busy=true;$("send-message").disabled=true;$("chat-task").value="";
  const select=$("chat-artifacts");if(select&&select.options){for(const opt of select.options)opt.selected=false;if(typeof renderChatArtifacts==="function")renderChatArtifacts();}
  addMessage("user",task);
  const reply=addMessage("assistant","Working…",providerName(payload.provider));const body=reply.querySelector(".message-body");
  try{
    const queued=await api("/api/runs",payload);state.sessionId=queued.session_id;
    const finished=await pollRun(queued.id,run=>{renderRunMessage(reply,run);reply.dataset.runId=run.id;reply.querySelector(".message-provider").textContent=providerName(run.provider);$("messages").scrollTop=$("messages").scrollHeight;});
    if(isStudioAssistant&&typeof renderWorkspaceProposals==="function")renderWorkspaceProposals(reply,finished.id);
    if(finished.status==="completed")window.dispatchEvent(new CustomEvent("studio-response-completed",{detail:{text:finished.output}}));
    await refreshSessions();
  }catch(error){reply.classList.add("error");if(!body.textContent||body.textContent==="Working…")body.textContent=error.message;else{const note=document.createElement("p");note.className="form-error";note.setAttribute("role","alert");note.textContent=error.message;reply.append(note);}}
  finally{state.busy=false;$("send-message").disabled=false;refreshRuns().catch(()=>{});}
}
function inputField(name,label,value="",type="text"){return `<label>${label}<input name="${name}" type="${type}" value="${escapeHtml(value)}" ${["name","task"].includes(name)?"required":""}></label>`;}
async function openEditor(kind,identity){
  if(kind==="agent"||kind==="agent-edit"){
    await Promise.all([
      loadSkills().catch(()=>{}),
      api("/api/tools").then(data=>{state.tools=data.items;}).catch(()=>{})
    ]);
  }
  state.editor={kind,identity};$("editor-error").textContent="";
  const fields=$("editor-fields");
  if(kind==="agent"||kind==="agent-edit"){
    $("editor-kicker").textContent="NEW SPECIALIST";$("editor-title").textContent="Create an agent";$("editor-save").textContent="Create agent";
    fields.innerHTML=inputField("name","Name")+inputField("description","Description")+`<div class="form-grid"><label>Provider<select name="provider">${providerOptions()}</select></label>${inputField("model","Model (optional)","")}<label>Effort<select name="effort">${effortOptions()}</select></label></div><label>Instructions<textarea name="instructions" rows="5" placeholder="What should this agent do, and how should it work?" required></textarea></label>`+`<div class="agent-skills-section"><div class="skills-select-header"><label>Tools (optional)</label></div><input type="search" class="skills-filter-input" placeholder="Filter tools…" aria-label="Filter tools"><div class="skills-select-list agent-tools-list" id="agent-tools-list">${agentToolOptions()}</div></div>`+`<div class="agent-skills-section"><div class="skills-select-header"><label>Skills (optional)</label><div class="skills-select-actions"><button type="button" class="secondary new-skill-btn">+ New skill…</button><button type="button" class="secondary import-skill-btn">Import skill…</button></div></div><input type="search" class="skills-filter-input" placeholder="Filter skills…" aria-label="Filter skills"><div class="skills-select-list agent-skills-list" id="agent-skills-list">${agentSkillOptions()}</div></div>`+inputField("timeout_seconds","Timeout in seconds",600,"number")+`<details><summary>Output guardrails</summary><label>Optional guardrail settings (JSON)<textarea name="guardrails_json" rows="4">{}</textarea></label><p>Use max_output_chars and forbidden_phrases. Guarded responses are checked before display, so streaming is disabled for this agent.</p></details>`;
  }else if(kind==="workflow"||kind==="workflow-edit"){
    if(!state.agents.some(a=>a.document)){notify("Create an agent before building a workflow.");location.hash="agents";return;}
    const isEdit=kind==="workflow-edit";
    const item=isEdit?state.workflows.find(w=>w.id===identity):null;
    const doc=item?.document||{},meta=doc.metadata||{};
    if(isEdit)state.editor.revision=item?.revision;
    $("editor-kicker").textContent="A SERIES OF HANDOFFS";$("editor-title").textContent=isEdit?"Edit workflow":"Create a workflow";$("editor-save").textContent=isEdit?"Save changes":"Create workflow";
    fields.innerHTML=inputField("name","Name",meta.name||(isEdit?identity:""))+inputField("description","Description",meta.description||"")+'<div id="workflow-steps"></div><button type="button" class="secondary" id="add-step">+ Add step</button>';
    const steps=doc.steps||[];
    if(isEdit&&steps.length){steps.forEach(s=>addWorkflowStep(s));}else{addWorkflowStep();}
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
    const values={name:doc.metadata?.name||identity,description:doc.metadata?.description||"",provider:spec.provider,model:spec.model||"",effort:spec.effort||"",instructions:spec.purpose||"",timeout_seconds:spec.timeout_seconds||600,guardrails_json:JSON.stringify(spec.guardrails||{},null,2)};
    for(const [name,value] of Object.entries(values)){const field=fields.querySelector(`[name="${name}"]`);if(field)field.value=value;}
    const currentSkills=spec.skill_ids||[];
    fields.querySelectorAll(".skills-filter-input").forEach(i=>{i.value="";});
    const skillsList=fields.querySelector("#agent-skills-list");
    if(skillsList){
      skillsList.innerHTML=agentSkillOptions(currentSkills);
    }
    const currentTools=spec.tool_ids||[];
    const toolsList=fields.querySelector("#agent-tools-list");
    if(toolsList){
      toolsList.innerHTML=agentToolOptions(currentTools);
    }
    const agentType=document.createElement("input");agentType.type="hidden";agentType.name="agent_type";agentType.value=spec.agent_type||identity;fields.appendChild(agentType);
  }
  $("editor").showModal();fields.querySelector("input,textarea,select")?.focus();
}
function renumberSteps(){
  const rows=document.querySelectorAll("#workflow-steps .step-editor");
  rows.forEach((row,i)=>{
    const span=row.querySelector(".step-heading span");
    if(span)span.textContent=`STEP ${i+1}`;
  });
}
function addWorkflowStep(step={}){
  const box=$("workflow-steps");const count=box.children.length+1;const row=document.createElement("div");row.className="step-editor";
  const stepAgent=step.agent_type||"";
  const stepPrompt=step.prompt_template||step.prompt||"{task}";
  let agentOpts=state.agents.filter(a=>a.document).map(a=>{
    const agentType=a.document.spec?.agent_type||a.id;
    const isSel=(agentType===stepAgent||a.id===stepAgent)?"selected":"";
    return `<option value="${escapeHtml(agentType)}" ${isSel}>${escapeHtml(a.document.metadata?.name||a.id)}</option>`;
  }).join("");
  const knownAgent=state.agents.some(a=>(a.document?.spec?.agent_type||a.id)===stepAgent||a.id===stepAgent);
  if(stepAgent&&!knownAgent){
    agentOpts+=`<option value="${escapeHtml(stepAgent)}" selected>${escapeHtml(stepAgent)} (unregistered)</option>`;
  }
  row.innerHTML=`<div class="step-heading"><span>STEP ${count}</span><button type="button" class="secondary remove-step">Remove</button></div><label>Agent<select class="step-agent">${agentOpts}</select></label><label>Task instructions<textarea class="step-prompt" placeholder="Use {task} for the original task">${escapeHtml(stepPrompt)}</textarea></label>`;
  if(step.id)row.dataset.stepId=step.id;
  if(step.name)row.dataset.stepName=step.name;
  box.appendChild(row);
}
async function saveEditor(event){
  event.preventDefault();const data=Object.fromEntries(new FormData(event.target));const {kind,identity}=state.editor;$("editor-save").disabled=true;$("editor-error").textContent="";
  try{
    if(kind==="agent"||kind==="agent-edit"){
      if(!data.effort)delete data.effort;
      if(data.guardrails_json&&data.guardrails_json.trim()!=="{}")data.guardrails=JSON.parse(data.guardrails_json);
      delete data.guardrails_json;
      data.skill_ids=new FormData(event.target).getAll("skill_ids").filter(id=>id&&id!=="__new__");
      const checkedTools=new FormData(event.target).getAll("tool_ids").filter(Boolean);
      if(checkedTools.length>0||event.target?.querySelector?.('input[name="tool_ids"][type="checkbox"]')){
        data.tool_ids=checkedTools;
      }else if(typeof data.tool_ids==="string"){
        data.tool_ids=data.tool_ids?data.tool_ids.split(",").map(t=>t.trim()).filter(Boolean):[];
      }
      if(kind==="agent-edit"){data.revision=state.editor.revision;await api(`/api/agents/${encodeURIComponent(identity)}/update`,data);}else await api("/api/agents",data);
      $("editor").close();await refreshDefinitions();location.hash="agents";notify(kind==="agent-edit"?"Agent updated.":"Agent created.");
    }
    else if(kind==="workflow"||kind==="workflow-edit"){
      const stepRows=[...document.querySelectorAll(".step-editor")];
      if(!stepRows.length){$("editor-error").textContent="Add at least one workflow step.";$("editor-save").disabled=false;return;}
      data.steps=stepRows.map((row,index)=>({id:row.dataset.stepId||`step_${index+1}`,name:row.dataset.stepName||`Step ${index+1}`,agent_type:row.querySelector(".step-agent").value,prompt:row.querySelector(".step-prompt").value}));
      if(kind==="workflow-edit"){
        data.revision=state.editor.revision;
        await api(`/api/workflows/${encodeURIComponent(identity)}/update`,data);
      }else{
        await api("/api/workflows",data);
      }
      $("editor").close();await refreshDefinitions();location.hash="workflows";notify(kind==="workflow-edit"?"Workflow updated.":"Workflow created.");
    }else{
      const payload=kind==="tool"?{kind:"tool",tool:identity,args:JSON.parse(data.args),kwargs:JSON.parse(data.kwargs)}:{kind:"workflow",workflow_id:identity,task:data.task,...(data.workflow_input_json?.trim()?{workflow_input:JSON.parse(data.workflow_input_json)}:{}),...(data.provider?{provider:data.provider}:{})};
      const run=await api("/api/runs",payload);$("editor").close();showRun(run);pollRun(run.id,updateRunDetail).then(()=>refreshRuns()).catch(error=>{updateRunDetail(error.run||{id:run.id,error:error.message,status:"failed"});refreshRuns();});
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
  const current=run.evaluation_case;
  const caseLabel=current?`<p>Experiment case: ${escapeHtml(current.variant)} · case ${escapeHtml(current.case)} · repetition ${escapeHtml(current.repetition)}/${escapeHtml(current.runs_per_example)}</p>`:"";
  let content=caseLabel+(inputs.length?`<p>Input required: ${escapeHtml(inputs.map(input=>input.name).join(", "))}</p>`:"");
  if(gates.length)content+=`<p>Approval required: ${escapeHtml(gates.map(gate=>gate.name).join(", "))}</p><div class="card-actions"><button data-approve-run="${escapeHtml(run.id)}" data-evaluation-checkpoint="${escapeHtml(run.evaluation_checkpoint||'')}">Approve and continue</button><button class="secondary" data-reject-run="${escapeHtml(run.id)}" data-evaluation-checkpoint="${escapeHtml(run.evaluation_checkpoint||'')}">${run.cancellation_cleanup?"Skip cleanup step":"Reject"}</button></div>`;
  return content||"<p>Waiting for your response.</p>";
}
function cleanupRunStatus(run){
  const status=run.cleanup_status?`<p role="status">Cancellation cleanup: ${escapeHtml(run.cleanup_status)}. Completed external actions remain in effect.</p>`:"";
  const reasons={CleanupEffectUncertainError:"Previous attempt may have changed external data. Step was not repeated.",CleanupRejected:"Skipped after your response.",LegacyScopeCapturesUnavailable:"Older save lacks nested cleanup context. Review previous external effects."};
  return status+(run.workflow_warnings?.length?`<p role="status">${run.workflow_warnings.length} cleanup warning(s). Review execution history before starting another run.</p><ul>${run.workflow_warnings.map(w=>`<li>${escapeHtml(String(w.node_id).split("/").at(-1))}: ${escapeHtml(reasons[w.error_type]||"Step could not finish; review its recorded effects.")}</li>`).join("")}</ul>`:"");
}
function splitStepOutput(output,names){
  // Workflow output joins completed steps as "name\nresult" separated by blank lines.
  // Names that did not run are skipped; a section ends where the next listed name starts.
  const sections=[];let cursor=0;
  for(let i=0;i<names.length;i++){
    const head=(sections.length?"\n\n":"")+names[i]+"\n";
    if(output.slice(cursor,cursor+head.length)!==head)continue;
    const start=cursor+head.length;let end=output.length;
    for(const next of names.slice(i+1)){const at=output.indexOf("\n\n"+next+"\n",start);if(at>=0){end=at;break;}}
    sections.push({name:names[i],text:output.slice(start,end)});cursor=end;
  }
  return sections.length&&cursor===output.length?sections:null;
}
function runStepNames(run){
  if(Array.isArray(run.output_steps))return run.output_steps;
  // Runs from before output_steps: YAML steps are named by their id in the saved definition.
  const steps=(state.workflows||[]).find(w=>w.id===run.workflow_definition_id)?.document?.steps;
  return Array.isArray(steps)?steps.map(step=>String(step.id||step.name||"")).filter(Boolean):null;
}
function renderRunOutput(node,run,fallback){
  const names=run.kind==="workflow"&&run.output?runStepNames(run):null;
  const sections=names?splitStepOutput(run.output,names):null;
  if(!sections){renderContent(node,fallback);return;}
  node.replaceChildren();
  const index=document.createElement("nav");index.className="run-step-index";index.setAttribute("aria-label","Workflow steps");
  sections.forEach((section,i)=>{
    const link=document.createElement("button");link.type="button";link.className="run-step-link";link.textContent=`${i+1}. ${section.name}`;
    link.onclick=()=>$(`run-step-${i+1}`)?.scrollIntoView({behavior:"smooth",block:"start"});
    index.append(link);
  });
  node.append(index);
  sections.forEach((section,i)=>{
    const block=document.createElement("section");block.className="run-step";block.id=`run-step-${i+1}`;
    block.innerHTML=`<h3 class="run-step-heading"><span class="run-step-number">${i+1}</span>${escapeHtml(section.name)}</h3><div class="message-body"></div>`;
    renderContent(block.querySelector(".message-body"),section.text||"No text output.");
    node.append(block);
  });
}
function updateRunDetail(run){
  if(!$("run-output")||run.id!==state.detailRunId)return;
  $("run-status").innerHTML=badge(run.status,run.status)+waitingRunActions(run)+cleanupRunStatus(run)+'<button type="button" class="secondary run-history-jump">Execution history</button>';
  $("run-status").querySelector(".run-history-jump").onclick=()=>($("run-execution")||$("run-trace-button"))?.scrollIntoView({behavior:"smooth",block:"start"});
  renderRunOutput($("run-output"),run,run.output||run.error||(run.status==="waiting"?"Waiting for your response.":["completed","cancelled"].includes(run.status)?"No text output.":"Working…"));
  let actionsBar=$("run-actions-bar");
  if(!actionsBar){
    actionsBar=document.createElement("div");
    actionsBar.id="run-actions-bar";
    actionsBar.className="card-actions run-detail-actions";
    $("run-output").after(actionsBar);
  }
  const finished=["completed","failed","cancelled","interrupted"].includes(run.status);
  actionsBar.innerHTML=`<button type="button" class="secondary" data-fix-run-chat="${escapeHtml(run.id)}">Fix in conversation</button><button type="button" class="secondary" id="run-trace-button" data-run-trace="${escapeHtml(run.id)}">Inspect execution trace</button>`+(finished?'<button type="button" class="secondary" id="run-print">Print / Save as PDF</button><button type="button" class="secondary" id="run-markdown">Download Markdown</button><button type="button" class="secondary" id="run-share">Share snapshot</button>':"");
  if(!finished)return;
  $("run-print").onclick=()=>{document.body.classList.add("print-run");window.addEventListener("afterprint",()=>document.body.classList.remove("print-run"),{once:true});window.print();};
  $("run-markdown").onclick=()=>{
    const url=URL.createObjectURL(new Blob([runMarkdown(run)],{type:"text/markdown"})),a=document.createElement("a");
    a.href=url;a.download=`${(run.workflow_definition_id||"run").replace(/[^\w.-]+/g,"-")}-${run.id.slice(0,8)}.md`;a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $("run-share").onclick=()=>shareSnapshot({run_id:run.id},"Share run","Create a read-only snapshot of this run's task and output.").catch(error=>notify(error.message));
}
function runMarkdown(run){
  const names=run.kind==="workflow"&&run.output?runStepNames(run):null;
  const sections=names&&splitStepOutput(run.output,names);
  const body=sections?sections.map((s,i)=>`## ${i+1}. ${s.name}\n\n${s.text}`).join("\n\n"):(run.output||run.error||"");
  return `# ${run.workflow_definition_id||"Run"}\n\n**Task:** ${run.task}\n\n*Status: ${run.status} · ${run.updated_at||run.created_at||""}*\n\n${body}\n`;
}
function toggleDetailMaximized(force){
  const dialog=$("detail");
  if(!dialog)return;
  const isMax=typeof force==="boolean"?force:!dialog.classList.contains("maximized");
  dialog.classList.toggle("maximized",isMax);
  const expandBtn=$("detail-toggle-expand");
  const footerBtn=$("detail-expand-btn");
  if(expandBtn){
    expandBtn.setAttribute("aria-label",isMax?"Exit full view":"Full view");
    expandBtn.setAttribute("title",isMax?"Exit full view (F)":"Full view (F)");
    expandBtn.innerHTML=isMax
      ? '<svg viewBox="0 0 24 24" aria-hidden="true" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>';
  }
  if(footerBtn)footerBtn.textContent=isMax?"Exit full view":"Full view";
}
$("detail")?.addEventListener("cancel",event=>{
  if($("detail")?.classList?.contains("maximized")){
    event.preventDefault();
    toggleDetailMaximized(false);
  }
});
$("detail")?.addEventListener("close",()=>toggleDetailMaximized(false));
let dialogClickStartedOutside=false;
const registerDialogDown=event=>{
  const d=event.target;
  if(d&&d.tagName==="DIALOG"&&d.open){
    const rect=d.getBoundingClientRect();
    dialogClickStartedOutside=(
      event.clientX<rect.left||event.clientX>rect.right||
      event.clientY<rect.top||event.clientY>rect.bottom
    );
  }else{
    dialogClickStartedOutside=false;
  }
};
document.addEventListener("pointerdown",registerDialogDown);
document.addEventListener("mousedown",registerDialogDown);
document.addEventListener("click",async event=>{
  const targetDialog=event.target;
  if(targetDialog&&targetDialog.tagName==="DIALOG"&&targetDialog.open&&dialogClickStartedOutside){
    const rect=targetDialog.getBoundingClientRect();
    const isOutside=(
      event.clientX<rect.left||event.clientX>rect.right||
      event.clientY<rect.top||event.clientY>rect.bottom
    );
    if(isOutside){
      dialogClickStartedOutside=false;
      const cancelEvt=new Event("cancel",{cancelable:true});
      if(targetDialog.dispatchEvent(cancelEvt)){
        targetDialog.close();
      }
      return;
    }
  }
  dialogClickStartedOutside=false;
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.id==="detail-toggle-expand"||button.id==="detail-expand-btn"){toggleDetailMaximized();return;}
    if(button.classList.contains("close-dialog"))button.closest("dialog").close();
    if(button.dataset.editAgent)openEditor("agent-edit",button.dataset.editAgent);
    if(button.dataset.editWorkflow)openEditor("workflow-edit",button.dataset.editWorkflow);
    if(button.dataset.action==="quick-build")location.hash="quick-build";
    if(button.dataset.action==="create-agent")openEditor("agent");
    if(button.dataset.action==="create-workflow"||button.id==="create-workflow")openEditor("workflow");
    if(button.id==="add-step"){addWorkflowStep();renumberSteps();}
    if(button.classList.contains("remove-step")){button.closest(".step-editor").remove();renumberSteps();}
    if(button.dataset.deleteDefinition){
      const kind=button.dataset.kind;
      const identity=button.dataset.deleteDefinition;
      const singular=kind==="workflows"?"workflow":"agent";
      const item=(state[kind]||[]).find(x=>x.id===identity);
      const name=item?.document?.metadata?.name||identity;
      const confirmed=typeof window.confirm==="function"?window.confirm(`Are you sure you want to delete ${singular} "${name}"?`):true;
      if(confirmed){
        try{
          button.disabled=true;
          await api(`/api/${kind}/${encodeURIComponent(identity)}`,undefined,"DELETE");
          notify(`${singular.charAt(0).toUpperCase()+singular.slice(1)} deleted.`);
          await refreshDefinitions();
        }catch(error){
          notify(error.message);
        }finally{
          button.disabled=false;
        }
      }
    }
    if(button.dataset.prompt){$("chat-task").value=button.dataset.prompt;$("chat-task").focus();}
    if(button.dataset.agent !== undefined){if(state.busy){notify("Let the current conversation finish first.");return;}newChat();$("chat-agent").value=button.dataset.agent;selectAgent();}
    if(button.dataset.session){await openSession(button.dataset.session);}
    if(button.dataset.definition){const item=state[button.dataset.kind].find(a=>a.id===button.dataset.definition);showDetail(item.document.metadata?.name||item.id,`<p>${escapeHtml(item.document.metadata?.description||"")}</p><pre>${escapeHtml(item.document.spec?.purpose||JSON.stringify(item.document,null,2))}</pre>`);}
    if(button.dataset.provider){
      const provider=state.providers.find(p=>p.id===button.dataset.provider);
      const calloutText=state.mode==="team"
        ? "Use your existing Nexus login and provider setup. Shared CLI workers receive your account configuration and submitted prompt content."
        : provider.status==="Logged in"
          ? "Authenticated with your local account credentials. You can use this provider directly or install the CLI for full terminal sessions."
          : provider.kind==="cli"
            ? "Sign in through the provider’s official CLI. Nexus uses that executable and does not collect or copy its account tokens."
            : "Configure the API key in the environment before starting Nexus Studio. It will be read by the existing provider adapter.";
      showDetail(provider.name,`<p class="callout">${calloutText}</p><pre>${escapeHtml(provider.setup)}</pre><p class="card-meta">Account authorization and usage limits are verified by the provider when you run a task.</p>`);
    }
    if(button.dataset.workflow){const item=state.workflows.find(w=>w.id===button.dataset.workflow);const validation=await api(`/api/workflows/${encodeURIComponent(item.id)}/validate`);showDetail(item.document?.metadata?.name||item.id,`${badge(validation.valid?"Valid workflow":"Needs attention",validation.valid?"ready":"failed")}${(item.document?.steps||[]).map((s,i)=>`<div class="flow-step"><b>${i+1}</b><div>${escapeHtml(s.name||s.id)}<small>${escapeHtml(s.agent_type)}</small></div></div>`).join("")}<pre>${escapeHtml([...validation.errors,...validation.warnings,...validation.flow].join("\n"))}</pre>`);}
    if(button.dataset.workflowHistory){
      const wfId=button.dataset.workflowHistory;
      const item=(state.workflows||[]).find(w=>w.id===wfId);
      const name=item?.document?.metadata?.name||wfId;
      const wfRuns=(state.runs||[]).filter(r=>r.kind==="workflow"&&(r.workflow_definition_id===wfId||r.workflow_id===wfId||r.workflow_snapshot?.id===wfId));
      const content=wfRuns.length?`
        <div class="card-actions" style="margin-bottom:14px">
          <button data-run-workflow="${escapeHtml(wfId)}">Run workflow</button>
          <button class="secondary" data-filter-activity-workflow="${escapeHtml(wfId)}">Filter in Activity</button>
        </div>
        <div class="workflow-history-list">
          ${wfRuns.map(r=>`
            <article class="workflow-history-row">
              <div class="workflow-history-info">
                <strong>${escapeHtml(r.task||"Workflow execution")}</strong>
                <small>${escapeHtml(providerName(r.provider))} · ${escapeHtml(new Date(r.created_at).toLocaleString())}${r.step?" · "+escapeHtml(r.step):""}</small>
              </div>
              <div class="workflow-history-actions">
                ${badge(r.status,r.status)}
                <button class="secondary" data-run="${escapeHtml(r.id)}">Details</button>
              </div>
            </article>
          `).join("")}
        </div>
      `:`
        <p>No runs recorded yet for this workflow.</p>
        <div class="card-actions" style="margin-top:16px">
          <button data-run-workflow="${escapeHtml(wfId)}">Run workflow now</button>
          <button class="secondary" data-filter-activity-workflow="${escapeHtml(wfId)}">View in Activity</button>
        </div>
      `;
      showDetail(`${name} · Run history`,content);
      return;
    }
    if(button.dataset.filterActivityWorkflow){
      const wfId=button.dataset.filterActivityWorkflow;
      $("detail")?.close();
      location.hash="activity";
      state.activityFilter="workflow";
      document.querySelectorAll(".activity-filter-pill").forEach(p=>p.classList.toggle("active",p.dataset.activityFilter==="workflow"));
      const searchInput=$("activity-search-input");
      if(searchInput)searchInput.value=wfId;
      renderRuns();
      return;
    }
    if(button.dataset.fixRunChat){
      const runId=button.dataset.fixRunChat;
      const run=(state.runs||[]).find(r=>r.id===runId)||{id:runId};
      $("detail")?.close();
      location.hash="chat";
      newChat();
      const taskInput=$("chat-task");
      if(taskInput){
        taskInput.value=`Help me fix run "${runId}" (${(run.task||"task").slice(0,200)}). The run had output:\n\n${(run.output||run.error||"").slice(0,1000)}`;
        taskInput.focus();
      }
      return;
    }
    if(button.dataset.activityFilter){
      state.activityFilter=button.dataset.activityFilter;
      document.querySelectorAll(".activity-filter-pill").forEach(p=>p.classList.toggle("active",p.dataset.activityFilter===state.activityFilter));
      renderRuns();
      return;
    }
    if(button.dataset.runWorkflow)openEditor("workflow-run",button.dataset.runWorkflow);
    if(button.dataset.scheduleWorkflow){
      const wfId=button.dataset.scheduleWorkflow;
      const item=(state.workflows||[]).find(w=>w.id===wfId);
      const name=item?.document?.metadata?.name||wfId;
      library.kind="schedules";
      location.hash="schedules";
      await refreshLibrary();
      await openLibraryEditor(null,{name:`${name} Schedule`,data:{target_kind:"workflow",target_id:wfId,enabled:true}});
    }
    if(button.dataset.scheduleAgent){
      const agentId=button.dataset.scheduleAgent;
      const item=(state.agents||[]).find(a=>a.id===agentId);
      const name=item?.document?.metadata?.name||agentId;
      library.kind="schedules";
      location.hash="schedules";
      await refreshLibrary();
      await openLibraryEditor(null,{name:`${name} Schedule`,data:{target_kind:"agent",target_id:agentId,enabled:true}});
    }
    if(button.dataset.tool)openEditor("tool",button.dataset.tool);
    if(button.dataset.run)showRun(await api(`/api/runs/${encodeURIComponent(button.dataset.run)}`));
    if(button.id==="refresh-activity")await refreshRuns();
    if(button.id==="refresh-providers"){state.providers=(await api("/api/bootstrap")).providers;renderProviders();notify("Provider installation status refreshed.");}
  }catch(error){notify(error.message);}
});
$("new-chat").addEventListener("click",newChat);$("chat-form").addEventListener("submit",sendMessage);$("editor-form").addEventListener("submit",saveEditor);$("chat-agent").addEventListener("change",selectAgent);
$("chat-task").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&(event.ctrlKey||event.metaKey||matchMedia("(pointer: fine)").matches)){event.preventDefault();$("chat-form").requestSubmit();}});
document.addEventListener("keydown",event=>{
  if(event.key.toLowerCase()==="n"&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!document.querySelector("dialog[open]")&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName))newChat();
  if(event.key.toLowerCase()==="f"&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&$("detail")?.open&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)){event.preventDefault();toggleDetailMaximized();}
});
window.addEventListener("hashchange",showView);
async function bootstrap(){
  if(bootstrapInFlight)return;bootstrapInFlight=true;clearTimeout(bootstrapRetryTimer);
  try{const data=await api("/api/bootstrap");if(state.user&&data.user?.nexus_id!==state.user.nexus_id){location.reload();return;}bootstrapFailures=0;state.workspacePath=data.workspace.path;disconnectedRuns.delete("bootstrap");connectionStatus();state.accountUrl=data.account_url;state.token=data.token;state.mode=data.mode;state.user=data.user;state.providers=data.providers;$("team-login").hidden=true;$("send-message").disabled=false;
    if(data.mode==="team"){$("deployment-mode").textContent="TEAM";$("runtime-label").textContent=`${data.user.nexus_id} · ${data.user.role}`;$("composer-hint").textContent="Uses your Nexus provider configuration";$("view-providers").querySelector(".callout").textContent="Providers use your existing Nexus account setup. Shared Codex and Claude Code runs use isolated prompt-only containers. Complete provider setup through Nexus login.";}
    $("workspace-name").textContent=data.workspace.name;$("chat-provider").innerHTML=providerOptions(state.providers.find(p=>p.configured&&p.kind==="cli")?.id||"codex");renderProviders();await Promise.all([refreshDefinitions(),refreshSessions(),refreshRuns(),loadSkills(),loadDecisionModels(),loadKnowledgeProjects()]);state.tools=(await api("/api/tools")).items;renderTools();showView();state.ready=true;restoreRunMonitoring();window.dispatchEvent(new Event("studio-ready"));}
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
      // Keep short phones contained; use page scrolling only when the keyboard
      // or a landscape viewport leaves too little room for the full composer.
      document.body.classList.toggle("compact-chat", height < 450);
    }
  }
  window.visualViewport?.addEventListener("resize", sizeViewport);
  window.addEventListener("resize", sizeViewport);
  sizeViewport();
})();

$("reconnect-now").addEventListener("click",()=>{window.dispatchEvent(new Event("studio-reconnect"));if(!state.ready)bootstrap();});

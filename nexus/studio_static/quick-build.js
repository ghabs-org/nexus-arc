"use strict";
const builder = {draft:null, id:null, busy:false, mode:"build", test:null, review:null};
const buildError = error => {$("builder-error").textContent=error?.message || String(error);$("builder-error").hidden=false;};
function builderBusy(busy, message="") {
  builder.busy=busy;
  ["builder-generate","builder-new","builder-history","builder-provider","builder-model","builder-effort","builder-tools","builder-task","builder-test-run","builder-test-task","builder-slash","builder-mention","builder-artifacts","builder-upload"].forEach(id=>$(id).disabled=busy);
  document.querySelector('.builder-provider-fields .model-profile')?.toggleAttribute('disabled',busy);
  $("builder-progress").hidden=!busy;$("builder-progress").textContent=message;
  $("builder-save")?.toggleAttribute("disabled",busy);
  $("builder-proposal")?.querySelectorAll("input,textarea,select").forEach(field=>field.disabled=busy||builder.draft?.status==="saved");
  $("builder-test-tab").disabled=busy||builder.draft?.status!=="saved";
  $("builder-build-tab").disabled=busy;
}
async function refreshBuilds() {
  const builds=(await api("/api/builds")).items;
  $("builder-history").innerHTML='<option value="">New build</option>'+builds.map(b=>`<option value="${escapeHtml(b.id)}">${escapeHtml(b.proposal.name)} · ${b.status==="saved"?"Saved":"Draft"} ${b.revision}</option>`).join("");
  $("builder-history").value=builder.draft?.id||"";
}
function builderMode(mode) {
  if(mode==="test"&&builder.draft?.status!=="saved")return;
  builder.mode=mode;
  $("builder-build-pane").hidden=mode!=="build";$("builder-test-pane").hidden=mode!=="test";
  $("builder-build-tab").setAttribute("aria-pressed",String(mode==="build"));
  $("builder-test-tab").setAttribute("aria-pressed",String(mode==="test"));
}
function resetBuilder() {
  builder.draft=null;builder.id=null;builder.test=null;builder.review=null;builder.transition=null;document.getElementById("builder-lifecycle")?.remove();
  $("builder-artifacts").value="";$("builder-upload-status").textContent="";
  $("builder-history").value="";$("builder-intro").hidden=false;$("builder-proposal").hidden=true;
  $("builder-conversation").replaceChildren();$("builder-form").hidden=false;$("builder-task").value="";
  $("builder-task").placeholder="A research team that compares options, checks the evidence, and writes a recommendation…";
  $("builder-generate").textContent="Generate build ↗";
  $("builder-error").hidden=true;$("builder-test-result").hidden=true;$("builder-test-task").value="";
  $("builder-note").textContent="Draft first. Review and save before running your team. Invalid drafts may use one extra AI call.";
  $("builder-model").value="";$("builder-effort").value="";$("builder-tools").value="web";
  const profile=document.querySelector('.builder-provider-fields .model-profile');if(profile)profile.value="";
  builderMode("build");builderBusy(false);$("builder-task").focus();
}
function proposalFromFields() {
  const proposal=structuredClone(builder.draft.proposal);
  const root=$("builder-proposal");
  root.querySelectorAll("[data-build-field]").forEach(field=>{
    const path=field.dataset.buildField.split(".");let target=proposal;
    for(const part of path.slice(0,-1))target=target[part];
    if(field.dataset.buildField.endsWith(".skill_ids")){
      target[path.at(-1)]=field.value&&field.value!=="__new__"?[field.value]:[];
    }else{
      target[path.at(-1)]=field.hasAttribute("data-build-json")?JSON.parse(field.value):field.value;
    }
  });
  root.querySelectorAll("[data-build-agent-skills]").forEach(container=>{
    const index=Number(container.dataset?.buildAgentSkills);
    if(Number.isInteger(index)&&proposal.agents&&proposal.agents[index]&&typeof container.querySelectorAll==="function"){
      proposal.agents[index].skill_ids=Array.from(container.querySelectorAll('input[name="skill_ids"]:checked'),b=>b.value);
    }
  });
  root.querySelectorAll("[data-build-agent-options]").forEach(field=>{
    const options=JSON.parse(field.value),agent=proposal.agents[Number(field.dataset.buildAgentOptions)];
    if(!options||Array.isArray(options)||typeof options!=="object")throw new Error("Advanced agent settings must be an object.");
    const basic=new Set(["id","name","description","instructions","provider","model"]);
    if(Object.keys(options).some(key=>basic.has(key)||["__proto__","constructor","prototype"].includes(key)))throw new Error("Edit agent identity and instructions in their own fields.");
    const assignedSkillIds=agent.skill_ids;
    Object.keys(agent).filter(key=>!basic.has(key)).forEach(key=>delete agent[key]);
    Object.assign(agent,options);
    if(assignedSkillIds!==undefined&&!("skill_ids" in options)){
      agent.skill_ids=assignedSkillIds;
    }
  });
  return proposal;
}
function builderField(path,label,value, multiline=false) {
  const field=multiline?`<textarea rows="3" data-build-field="${path}" ${builder.draft.status==="saved"?"disabled":""}>${escapeHtml(value)}</textarea>`:`<input data-build-field="${path}" value="${escapeHtml(value)}" ${builder.draft.status==="saved"?"disabled":""}>`;
  return `<label>${label}${field}</label>`;
}
function buildAgentOptions(agent,index,saved){
 const basic=new Set(["id","name","description","instructions","provider","model","skill_ids"]);
 const options=Object.fromEntries(Object.entries(agent).filter(([key])=>!basic.has(key)));
 return `<details><summary>Runtime limits and resource assignments</summary><p>Advanced settings: timeout, call limits, tools, skills, connections and output schema. Changes require review before activation.</p><label>Advanced agent settings<textarea rows="9" aria-label="Advanced agent settings" data-build-agent-options="${index}" ${saved?"disabled":""}>${escapeHtml(JSON.stringify(options,null,2))}</textarea></label></details>`;
}
function renderBuild() {
  const draft=builder.draft,p=draft.proposal,saved=draft.status==="saved",workflow=p.workflow,steps=workflow?.graph?.nodes||workflow?.steps||[],resources=p.resources||[];
  $("builder-intro").hidden=true;$("builder-proposal").hidden=false;
  $("builder-conversation").innerHTML=draft.messages.map(m=>`<article class="build-message ${m.role}"><span>${m.role==="user"?"You":"Nexus builder"}</span><p>${escapeHtml(m.content)}</p></article>`).join("");
  const jsonField=(path,label,value)=>`<label>${label}<textarea rows="7" aria-label="${escapeHtml(label)}" data-build-field="${path}" data-build-json ${saved?"disabled":""}>${escapeHtml(JSON.stringify(value,null,2))}</textarea></label>`;
  $("builder-proposal").innerHTML=`<div class="build-review-heading"><div><span class="eyebrow">${saved?"SAVED TO WORKSPACE":"REVIEW YOUR BUILD"} · REVISION ${draft.revision}</span><h2>${escapeHtml(p.name)}</h2></div>${badge(saved?"Saved":"Draft",saved?"ready":"")}</div>
    <p class="build-summary">${escapeHtml(p.summary)}</p>
    ${workflow?`<div class="build-plan"><div class="build-plan-heading"><h3>${workflow.graph?"Graph workflow":"Workflow"}</h3><span>${p.agents.length} agents · ${steps.length} ${steps.length===1?"step":"steps"}</span></div><ol>${steps.map((step,i)=>`<li><span class="build-step-index">${i+1}</span><div><b>${escapeHtml(step.name)}</b><span>${escapeHtml(step.kind||step.agent_type||"Agent")}</span></div></li>`).join("")}</ol>${workflow.graph?`<p>Connections: ${workflow.graph.edges.map(edge=>`${escapeHtml(edge.source)} → ${escapeHtml(edge.target)}${edge.choice?` (${escapeHtml(edge.choice)})`:""}`).join(" · ")}</p>`:""}</div>`:""}
    ${p.assumptions.length?`<aside class="build-assumptions"><h3>Before you run</h3><ul>${p.assumptions.map(a=>`<li>${escapeHtml(a)}</li>`).join("")}</ul></aside>`:""}
    ${p.agents.length?`<div class="build-agents"><h3>Your agents</h3>${p.agents.map((a,i)=>`<details class="build-agent"><summary><span><b>${escapeHtml(a.name)}</b><small>${escapeHtml(a.description)}${(a.skill_ids||[]).length?` · ${(a.skill_ids.length>1?`Skills: ${escapeHtml(a.skill_ids.join(", "))}`:`Skill: ${escapeHtml(a.skill_ids[0])}`)}`:""}</small></span><span class="build-agent-provider">${escapeHtml(providerName(a.provider))}</span></summary><div class="build-agent-fields">${builderField(`agents.${i}.name`,"Agent name",a.name)}${builderField(`agents.${i}.description`,"Description",a.description)}${builderField(`agents.${i}.instructions`,"Instructions",a.instructions,true)}<div class="build-field-pair"><label>Provider<select data-build-field="agents.${i}.provider" ${saved?"disabled":""}>${providerOptions(a.provider)}</select></label>${builderField(`agents.${i}.model`,"Model (optional)",a.model)}</div><div class="agent-skills-section"><div class="skills-select-header"><label>Skills (optional)</label><div class="skills-select-actions"><button type="button" class="secondary new-skill-btn" ${saved?"disabled":""}>+ New skill…</button><button type="button" class="secondary import-skill-btn" ${saved?"disabled":""}>Import skill…</button></div></div><input type="search" class="skills-filter-input" placeholder="Filter skills…" aria-label="Filter skills for ${escapeHtml(a.name||"agent")}" ${saved?"disabled":""}><div class="skills-select-list agent-skills-list" data-build-agent-skills="${i}">${typeof agentSkillOptions==="function"?agentSkillOptions(a.skill_ids||[],(p.resources||[]).filter(r=>r.kind==="skills"),saved):""}</div></div>${buildAgentOptions(a,i,saved)}</div></details>`).join("")}</div>`:""}
    ${workflow?`<details class="build-step-details"><summary>Review workflow configuration</summary><div class="build-agent-fields">${workflow.graph?jsonField("workflow.graph","Nodes and connections",workflow.graph):steps.map((step,i)=>builderField(`workflow.steps.${i}.prompt`,`${i+1}. ${escapeHtml(step.name)}`,step.prompt,true)).join("")}</div></details>`:""}
    <details class="build-step-details"><summary>Named child workflows${p.workflows?.length?` · ${p.workflows.length}`:""}</summary><p>Reusable workflows created with this build. Ask the builder to add or refine them, or edit their configuration below.</p>${jsonField("workflows","Child workflow definitions",p.workflows||[])}</details>
    ${resources.length?`<div class="build-agents"><h3>Related resources</h3><p>Connections, schedules and entrypoints are saved disabled. Configure accounts and enable them separately.</p>${resources.map((item,i)=>`<details class="build-agent"><summary>${escapeHtml(item.name)} · ${escapeHtml(item.kind)}</summary><div class="build-agent-fields">${builderField(`resources.${i}.name`,"Resource name",item.name)}${builderField(`resources.${i}.description`,"Description",item.description||"")}${jsonField(`resources.${i}.data`,"Resource settings",item.data)}</div></details>`).join("")}</div>`:""}
    <details class="build-files"><summary>${saved?"Saved resources":"Resources this build will create"}</summary><ul>${p.agents.map(a=>`<li>agents/${escapeHtml(a.id)}.yaml</li>`).join("")}${[...(p.workflows||[]),...(workflow?[workflow]:[])].map(w=>`<li>workflows/${escapeHtml(w.id)}.yaml</li>`).join("")}${resources.map(item=>`<li>${escapeHtml(item.kind)} / ${escapeHtml(item.id)}</li>`).join("")}</ul></details>
    <div class="build-save-row">${saved?'<button id="builder-reopen" class="secondary">Continue editing this build</button>':""}<button id="builder-refresh" class="secondary">Refresh from workspace</button><button id="builder-delete" class="secondary">Delete build</button><p>${saved?"Resources saved. Configure prerequisites before execution.":"Review instructions, graph and related resources before saving."}</p><button id="${saved?"builder-open-test":"builder-save"}" ${saved&&!workflow?"disabled":""}>${saved?(workflow?"Test your team →":"No workflow to test"):"Review changes"}</button></div>`;
  $("builder-form").hidden=saved;$("builder-generate").textContent="Refine build ↗";$("builder-task").placeholder="What would you like to change?";
  $("builder-test-title").textContent=p.name;$("builder-note").textContent=saved?"Saved in Agents, Workflows and Library. Review account setup before enabling connections.":"Refine in chat, or edit the proposed settings before saving. Invalid drafts may use one extra AI call.";
  $("builder-provider").value=draft.provider;$("builder-model").value=draft.model;$("builder-effort").value=draft.effort||"";$("builder-tools").value=draft.worker_tools||"web";
  const profile=document.querySelector('.builder-provider-fields .model-profile');if(profile)profile.value="";
  builderBusy(false);refreshBuildLifecycle().catch(buildError);
}
async function loadBuild(id) {
  $("builder-error").hidden=true;
  builder.draft=await api(`/api/builds/${encodeURIComponent(id)}`);builder.id=id;builder.test=null;builder.review=null;builder.transition=null;
  $("builder-test-result").hidden=true;$("builder-test-task").value="";$("builder-task").value="";$("builder-test-output").replaceChildren();
  renderBuild();builderMode("build");$("builder-history").value=id;
}
async function generateBuild(event) {
  event.preventDefault();if(builder.busy||state.attachmentUpload)return;
  const task=$("builder-task").value.trim();if(!task)return;
  const payload={task,provider:$("builder-provider").value,model:$("builder-model").value.trim(),worker_tools:$("builder-tools").value,...($("builder-effort").value?{effort:$("builder-effort").value}:{}),artifact_ids:[...$("builder-artifacts").selectedOptions].map(option=>option.value)};
  try{payload.prompt_values=await resolvePromptValues(task);}catch(error){buildError(error);return;}
  if(builder.id)payload.build_id=builder.id;
  try{if(builder.draft){payload.revision=builder.draft.revision;payload.proposal=proposalFromFields();}}catch(error){buildError(error);return;}
  builder.review=null;$("builder-error").hidden=true;builderBusy(true,"Designing your team… This may take a few minutes.");
  const previousId=builder.id;
  try {
    const queued=await api("/api/builds",payload);builder.id=queued.build_id;
    const run=await pollRun(queued.id,run=>{const live=(run.output||"").slice(-6000);$("builder-progress").textContent=(run.phase==="correcting-proposal"?"Correcting the draft… One validation correction is allowed.":"Designing your team…")+(live?"\n\n"+live:"");$("builder-progress").scrollTop=$("builder-progress").scrollHeight;});
    if(!run||run.status!=="completed"){
      throw new Error(run?.error||`Build run ${run?.status||"did not complete"}`);
    }
    builder.draft=await api(`/api/builds/${encodeURIComponent(builder.id)}`);
    $("builder-task").value="";renderBuild();await refreshBuilds();
    $("builder-proposal").scrollIntoView({behavior:"smooth",block:"start"});
  }catch(error){
    if(!builder.draft)builder.id=previousId;
    buildError(error);
  }
  finally{builderBusy(false);refreshRuns().catch(()=>{});}
}
async function saveBuild() {
  if(builder.busy||!builder.draft)return;
  let proposal;try{proposal=proposalFromFields();}catch(error){buildError(error);return;}$("builder-error").hidden=true;builderBusy(true,"Preparing review…");
  try {
    builder.review=await featureApi(`builds/${encodeURIComponent(builder.id)}/review`,{revision:builder.draft.revision,proposal});
    renderBuildReview(builder.review);
  }catch(error){buildError(error);}
  finally{builderBusy(false);}
}
async function testBuild(event) {
  event.preventDefault();if(builder.busy||state.attachmentUpload)return;
  $("builder-error").hidden=true;builderBusy(true,"Running your saved workflow…");
  $("builder-test-result").hidden=false;
  const out=$("builder-test-output");out.textContent="Waiting for the selected provider…";
  const update=run=>{
    $("builder-test-status").innerHTML=badge(run.status,run.status);
    $("builder-test-step").textContent=run.step?`Current step: ${run.step}`:"";
    const text=run.error||run.output||"Working…";
    if(run.output||run.error){
      renderContent(out,text);
    }else{
      out.textContent=text;
    }
  };
  try {
    const run=await api(`/api/builds/${encodeURIComponent(builder.id)}/test`,{task:$("builder-test-task").value.trim()});
    builder.test=run.id;update(run);await pollRun(run.id,update);
  }catch(error){buildError(error);$("builder-test-status").innerHTML=badge("failed","failed");}
  finally{builderBusy(false);refreshRuns().catch(()=>{});}
}
$("builder-form").addEventListener("submit",generateBuild);
$("builder-test-form").addEventListener("submit",testBuild);
$("builder-new").addEventListener("click",()=>{if(!builder.busy)resetBuilder();});
$("builder-history").addEventListener("change",async event=>{try{if(event.target.value)await loadBuild(event.target.value);else resetBuilder();}catch(error){buildError(error);}});
$("builder-build-tab").addEventListener("click",()=>builderMode("build"));
$("builder-test-tab").addEventListener("click",()=>builderMode("test"));
document.addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button||builder.busy)return;
  if(button.dataset.buildPrompt){$("builder-task").value=button.dataset.buildPrompt;$("builder-task").focus();}
  if(button.id==="builder-save")saveBuild();
  if(button.id==="builder-open-test")builderMode("test");
});
async function initializeQuickBuild(){
  const ids=["codex","claude-code","agy","openai","claude"];
  $("builder-provider").innerHTML=state.providers.filter(p=>ids.includes(p.id)).map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}${p.configured?"":" · setup needed"}</option>`).join("");
  $("builder-provider").value=state.providers.find(p=>ids.includes(p.id)&&p.configured)?.id||"codex";
  loadSkills().catch(()=>{});
  try{await refreshBuilds();}catch(error){buildError(error);}
}
if(state.ready)initializeQuickBuild();else window.addEventListener("studio-ready",initializeQuickBuild,{once:true});

document.addEventListener("click",async event=>{if(event.target.id!=="builder-reopen")return;try{builder.draft=await api(`/api/builds/${encodeURIComponent(builder.id)}/reopen`,{revision:builder.draft.revision});renderBuild();builderMode("build");}catch(error){buildError(error);}});

function buildLifecycleRoot(){let root=document.getElementById("builder-lifecycle");if(!root){root=document.createElement("section");root.id="builder-lifecycle";root.className="build-agent-fields";$("builder-proposal").after(root);}return root;}
function buildChangesHtml(plan){return `<ul>${plan.changes.map(change=>`<li><strong>${escapeHtml(change.resource)}</strong> · ${escapeHtml(change.action)}${change.paths?.length?`<br><small>Changed: ${escapeHtml(change.paths.join(", "))}</small>`:""}</li>`).join("")}</ul>`;}
function renderBuildReview(plan){const root=buildLifecycleRoot();root.innerHTML=`<h3>Review changes · revision ${plan.revision}</h3>${buildChangesHtml(plan)}<p>Saving keeps resources inactive. Review prerequisites and activate afterward.</p><button data-build-apply-reviewed>Apply reviewed build</button>`;root.scrollIntoView({block:"nearest"});}
function buildPrerequisiteHtml(component){
 const checks=component.prerequisite_checks||[];
 return checks.length?`<details><summary>Software and worker setup</summary><ul>${checks.map(check=>`<li>${escapeHtml(check.name)}: ${escapeHtml(check.status)}</li>`).join("")}</ul><p>Local prerequisites only; account access and connectivity stay separate. Activation review checks selected workers without running your workflow actions.</p></details>`:"";
}
async function refreshBuildLifecycle(){
 const root=buildLifecycleRoot(),id=builder.id,revision=builder.draft?.revision;
 if(builder.draft?.status!=="saved"){root.replaceChildren();return;}
 const data=await featureApi(`builds/${encodeURIComponent(id)}/lifecycle`);if(builder.id!==id||builder.draft?.revision!==revision)return;
 const mayChange=state.mode!=="team"||state.user?.role==="owner";
 root.innerHTML=`<h3>Resources & readiness</h3><p>Setup checks stay separate from connectivity. Use Check connection for a read-only probe; results belong to this release. Activation review also checks selected local worker software in isolated containers.</p><div>${data.components.map(component=>`<article class="library-row"><div><strong>${escapeHtml(component.name)}</strong><p>${escapeHtml(component.key.split(":")[0])} · ${escapeHtml(component.status.replaceAll("_"," "))}</p>${component.issues.filter(issue=>!["inactive","missing_credentials"].includes(issue)).length?`<p>${escapeHtml(component.issues.filter(issue=>!["inactive","missing_credentials"].includes(issue)).map(issue=>issue.replaceAll("_"," ")).join(", "))}</p>`:""}${buildPrerequisiteHtml(component)}${buildHealthHtml(component,mayChange)}${component.missing_environment.length?`<p>Configure: ${escapeHtml(component.missing_environment.join(", "))}</p>`:""}</div>${component.managed&&["connectors","entrypoints","schedules","externalagents"].includes(component.key.split(":")[0])?`<label><input type="checkbox" data-enable-build-component value="${escapeHtml(component.key)}" ${component.enabled?"checked":""} ${mayChange?"":"disabled"}> Enable after review</label>`:""}</article>`).join("")}</div><details><summary>Component dependencies</summary>${data.dependencies.length?`<ul>${data.dependencies.map(edge=>`<li>${escapeHtml(edge.source)} → ${escapeHtml(edge.target)}</li>`).join("")}</ul>`:"<p>No component dependencies.</p>"}</details>${data.errors.length?`<p role="alert">${escapeHtml(data.errors.map(e=>`${e.component}: ${e.reason}`).join("; "))}</p>`:""}${mayChange?`<div class="card-actions"><button class="secondary" data-build-transition="activate">Review activation</button><button class="secondary" data-build-transition="deactivate">Review deactivation</button></div><label>Saved release<select id="builder-release" aria-label="Saved release">${data.releases.map(release=>`<option value="${escapeHtml(release.id)}">Revision ${release.revision} · ${escapeHtml(release.action||"save")} · ${escapeHtml(release.created_at||release.id.slice(0,8))}</option>`).join("")}</select></label><button class="secondary" data-build-transition="rollback" ${data.releases.length?"":"disabled"}>Review rollback</button>`:"<p>Lifecycle changes require the workspace owner.</p>"}<div id="builder-transition-review"></div>`;
}
async function applyBuildReview(){
 if(builder.busy||!builder.review)return;builderBusy(true,"Applying reviewed changes…");
 try{builder.draft=await api(`/api/builds/${encodeURIComponent(builder.id)}/apply`,{revision:builder.draft.revision,proposal:proposalFromFields(),review_id:builder.review.id,digest:builder.review.digest});builder.review=null;renderBuild();await Promise.all([refreshDefinitions(),refreshBuilds()]);}catch(error){buildError(error);}finally{builderBusy(false);}
}
async function reviewBuildTransition(action){
 if(builder.busy)return;builderBusy(true,"Checking this revision and its dependencies…");
 const label={activate:"activation",deactivate:"deactivation",rollback:"rollback"}[action];
 try{const plan=await featureApi(`builds/${encodeURIComponent(builder.id)}/plan`,{revision:builder.draft.revision,action,enable:action==="activate"?Array.from(document.querySelectorAll("[data-enable-build-component]:checked"),e=>e.value):[],...(action==="rollback"?{release:$("builder-release").value}:{})});builder.transition=plan;$("builder-transition-review").innerHTML=`<h3>Review ${escapeHtml(label)}</h3>${buildChangesHtml(plan)}${plan.errors.length?`<p role="alert">Resolve before proceeding:</p><ul>${plan.errors.map(error=>`<li>${escapeHtml(error.component)}: ${escapeHtml(error.reason.replaceAll("_"," "))}</li>`).join("")}</ul>${plan.errors.some(error=>error.reason.includes("unreviewed_changes"))?"<p>These resources were edited outside Quick Build. Use Refresh from workspace to review the edits, then review activation again.</p>":""}`:"<p>This plan pins the current revision and workspace contents.</p>"}<button data-build-apply-transition ${plan.errors.length?"disabled":""}>Apply reviewed ${escapeHtml(label)}</button>`;}catch(error){buildError(error);}finally{builderBusy(false);}
}
async function applyBuildTransition(){
 if(builder.busy||!builder.transition)return;builderBusy(true,"Applying reviewed resource transition…");
 try{await featureApi(`builds/${encodeURIComponent(builder.id)}/transition`,{plan_id:builder.transition.id,digest:builder.transition.digest});builder.transition=null;await loadBuild(builder.id);await Promise.all([refreshDefinitions(),refreshBuilds()]);}catch(error){buildError(error);}finally{builderBusy(false);}
}
document.addEventListener("click",event=>{const button=event.target.closest("button");if(!button||builder.busy)return;if(button.hasAttribute("data-build-apply-reviewed"))applyBuildReview();if(button.dataset.buildTransition)reviewBuildTransition(button.dataset.buildTransition);if(button.hasAttribute("data-build-apply-transition"))applyBuildTransition();});

function buildHealthHtml(component,mayChange){
 const health=component.health;if(!health||(!health.supported&&!health.result))return "";
 const result=health.result;
 return `<p>Connectivity: ${escapeHtml(health.status.replaceAll("_"," "))}${result?` · ${escapeHtml(new Date(result.checked_at).toLocaleString())} · release ${escapeHtml(result.release_id.slice(0,8))}`:""}</p>${health.supported&&mayChange?`<button class="secondary" data-build-health="${escapeHtml(component.key)}">Check connection</button>`:""}`;
}
async function checkBuildHealth(component){
 if(builder.busy)return;builderBusy(true,"Checking connection without running tools…");
 try{await featureApi(`builds/${encodeURIComponent(builder.id)}/health`,{revision:builder.draft.revision,release_id:builder.draft.release_id,component});await refreshBuildLifecycle();}catch(error){buildError(error);}finally{builderBusy(false);}
}
document.addEventListener("click",event=>{const button=event.target.closest("[data-build-health]");if(button&&!builder.busy)checkBuildHealth(button.dataset.buildHealth);});

async function refreshBuildFromWorkspace(){
 if(builder.busy||!builder.draft)return;builderBusy(true,"Comparing this build with the workspace…");
 try{
  const id=encodeURIComponent(builder.id),preview=await featureApi(`builds/${id}/refresh`,{revision:builder.draft.revision});
  if(!preview.changes.length){notify("This build already matches its workspace resources.");return;}
  builderBusy(false);
  const confirmed=await fieldsDialog("Refresh build from workspace",`<p>These resources were changed or removed outside Quick Build:</p>${buildChangesHtml(preview)}<p>Refreshing replaces this build's proposal with the current workspace files${builder.draft.status==="draft"?" and discards unsaved build edits":""}. Nothing in the workspace is rewritten. Changed resources become inactive until you review activation.</p>`,"Refresh build");
  if(!confirmed)return;builderBusy(true,"Refreshing build…");
  const result=await featureApi(`builds/${id}/refresh`,{revision:preview.revision,digest:preview.digest});
  builder.draft=result.build;builder.review=null;builder.transition=null;renderBuild();await refreshBuilds();
  notify(result.deactivated.length?"Build refreshed. Review activation to run the changed resources again.":"Build refreshed from the workspace.");
 }catch(error){buildError(error);}finally{builderBusy(false);}
}
async function deleteBuild(){
 if(builder.busy||!builder.draft)return;
 const confirmed=await fieldsDialog("Delete build",`<p>Delete <strong>${escapeHtml(builder.draft.proposal.name)}</strong> from Quick Build?</p><p>Its agents, workflows and resources stay in the workspace as ordinary resources. They are no longer managed by this build, and their activation review no longer applies.</p>`,"Delete build");
 if(!confirmed)return;builderBusy(true,"Deleting build…");
 try{await featureApi(`builds/${encodeURIComponent(builder.id)}/delete`,{revision:builder.draft.revision});resetBuilder();await refreshBuilds();notify("Build deleted. Its resources remain in Agents, Workflows and Library.");}
 catch(error){buildError(error);}finally{builderBusy(false);}
}
document.addEventListener("click",event=>{if(event.target.id==="builder-refresh")refreshBuildFromWorkspace();if(event.target.id==="builder-delete")deleteBuild();});

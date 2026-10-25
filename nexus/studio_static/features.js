"use strict";

async function showA2ADeliveryHealth(identity,after=null,existing=[]){
  const data=await featureApi(`entrypoints/${encodeURIComponent(identity)}/a2a-deliveries${after?`?after=${encodeURIComponent(after)}`:""}`),items=[...existing,...data.items];
  const date=value=>typeof value==="number"?new Date(value*1000).toLocaleString():"Not recorded";
  showDetail("Callback delivery health",`<p>${data.ready?"Callback prerequisites are ready. Delivery still requires a running scheduler and reachable receiver.":"Callback delivery is disabled or setup is incomplete."}</p><p>This view only reads status. Notifications can arrive more than once; receivers must deduplicate delivery IDs. Already dispatched notifications may complete after removal.</p>${items.length?items.map(item=>`<article class="library-row"><div><h3>${escapeHtml(item.config_id)}</h3><p>${escapeHtml(item.status)} · attempt ${item.attempt??"unknown"} of ${item.max_attempts}</p><p>Last attempt: ${escapeHtml(date(item.checked_at))}${item.next_due?` · Next eligible: ${escapeHtml(date(item.next_due))}`:""}</p><p>Task ${escapeHtml(item.task_id)}</p></div><button class="secondary" data-run="${escapeHtml(item.task_id)}">View task</button></article>`).join(""):"<p>No callbacks registered for this entrypoint.</p>"}<div class="card-actions"><button class="secondary" id="a2a-health-refresh">Refresh status</button>${data.next_cursor?'<button class="secondary" id="a2a-health-more">Load more</button>':""}</div><p class="form-error" role="alert" id="a2a-health-error"></p>`);
  const refresh=$("a2a-health-refresh");refresh.onclick=async()=>{refresh.disabled=true;try{await showA2ADeliveryHealth(identity);}catch(error){$("a2a-health-error").textContent=error.message;}finally{refresh.disabled=false;}};
  const more=$("a2a-health-more");if(more)more.onclick=async()=>{more.disabled=true;try{await showA2ADeliveryHealth(identity,data.next_cursor,items);}catch(error){$("a2a-health-error").textContent=error.message;more.disabled=false;}};
}
document.addEventListener("click",async event=>{const button=event.target.closest("[data-a2a-health]");if(!button)return;try{await showA2ADeliveryHealth(button.dataset.a2aHealth);}catch(error){notify(error.message);}});
const featureApi=(path,data)=>api(`/api/studio/${path}`,data);
function fieldsDialog(title,html,label="Save",validate=null) {
  return new Promise(resolve=>{
    const dialog=document.createElement("dialog");dialog.className="feature-dialog";
    dialog.innerHTML=`<form><header class="dialog-heading"><h2>${escapeHtml(title)}</h2><button type="button" data-dismiss aria-label="Close">×</button></header>${html}<p class="form-error" role="alert"></p><footer><button type="button" class="secondary" data-dismiss>Cancel</button><button type="submit">${escapeHtml(label)}</button></footer></form>`;
    document.body.append(dialog);let result=null;
    dialog.querySelectorAll("[data-dismiss]").forEach(b=>b.onclick=()=>dialog.close());
    dialog.addEventListener("close",()=>{dialog.remove();resolve(result);},{once:true});
    dialog.querySelector("form").onsubmit=event=>{event.preventDefault();const values=new FormData(event.target),error=validate?.(values);if(error){dialog.querySelector(".form-error").textContent=error;return;}result=values;dialog.close();};dialog.showModal();
  });
}
function downloadJson(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function refreshFeatureViews(){
  if(!state.ready)return;
  const projectListing=await featureApi("projects"),projects=projectListing.items;
  state.knowledgeProjects=projects;state.knowledgeRetrievalOptions=projectListing.retrieval_options;
  let disclosure=$("knowledge-disclosure");if(!disclosure){disclosure=document.createElement("p");disclosure.id="knowledge-disclosure";disclosure.className="callout";$("chat-project").closest(".chat-controls").after(disclosure);$("chat-project").addEventListener("change",updateKnowledgeDisclosure);}
  const selected=$("chat-project").value;
  $("chat-project").innerHTML='<option value="">No project</option>'+projects.filter(p=>!p.archived).map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join("");$("chat-project").value=selected;updateKnowledgeDisclosure();
  $("projects-content").innerHTML=projects.map(p=>`<article class="library-row"><div><h2>${escapeHtml(p.name)}</h2><p>${escapeHtml(p.instructions)}</p><small>Revision ${p.revision}${p.archived?" · Archived":""}</small></div><div class="card-actions"><button data-project-open="${escapeHtml(p.id)}" ${p.archived?"disabled":""}>Open</button><button class="secondary" data-project-edit="${escapeHtml(p.id)}">Edit</button><button class="secondary" data-project-archive="${escapeHtml(p.id)}">${p.archived?"Restore":"Archive"}</button></div></article>`).join("")||empty("No knowledge projects","Create a project, add documents and ask questions grounded in your sources.");
  if(view()==="feedback")await refreshFeedbackView();
  if(view()==="sharing"){
    const sharing=await featureApi("sharing");
    $("sharing-content").innerHTML=`<p>Public snapshot links: ${sharing.public_enabled?"enabled":"disabled"}.</p><button class="secondary" id="sharing-toggle">${sharing.public_enabled?"Disable":"Enable"} public links</button>`+sharing.items.map(s=>`<article class="library-row"><div><h2>${escapeHtml(s.title)}</h2><p>${s.public?"Public link":"Private snapshot"} · expires ${escapeHtml(new Date(s.expires_at).toLocaleString())}${s.revoked?" · Revoked":""}</p></div>${s.revoked?"":`<button class="secondary" data-share-revoke="${escapeHtml(s.id)}">Revoke</button>`}</article>`).join("");
    $("sharing-toggle").onclick=async()=>{if(!sharing.public_enabled){const answer=await fieldsDialog("Enable public sharing",'<p>Anyone holding an explicitly created public link can read its snapshot until expiry or revocation. Existing private conversations stay private.</p>',"Enable public links");if(!answer)return;}await featureApi("sharing",{enabled:!sharing.public_enabled});await refreshFeatureViews();};
  }
}
function knowledgeRetrievalNotice(){return state.knowledgeRetrievalOptions?.notice||"Semantic and hybrid search use this workspace's configured embedding service.";}
function updateKnowledgeDisclosure(){const note=$("knowledge-disclosure");if(!note)return;const project=state.knowledgeProjects?.find(project=>project.id===$("chat-project").value);note.hidden=!project||!['semantic','hybrid'].includes(project.retrieval_mode)||['local','none'].includes(state.knowledgeRetrievalOptions?.embedding_kind);note.textContent=note.hidden?"":knowledgeRetrievalNotice();}
async function editProject(id){
  const p=id?await featureApi(`projects/${encodeURIComponent(id)}`):{};
  const form=await fieldsDialog(id?"Edit knowledge project":"Create knowledge project",inputField("name","Name",p.name||"")+`<label>Instructions<textarea name="instructions" rows="5" maxlength="16000">${escapeHtml(p.instructions||"")}</textarea></label><label>Default agent<select name="agent"><option value="">General assistant</option>${state.agents.map(a=>`<option value="${escapeHtml(a.id)}" ${a.id===p.default_agent_id?"selected":""}>${escapeHtml(a.id)}</option>`).join("")}</select></label><label>Default search<select name="retrieval_mode">${[["lexical","Local text search"],["semantic","Semantic"],["hybrid","Hybrid"]].map(([value,label])=>`<option value="${value}" ${value===(p.retrieval_mode||"lexical")?"selected":""}>${label}</option>`).join("")}</select></label><p>${escapeHtml(knowledgeRetrievalNotice())}</p>`);
  if(!form)return;await featureApi("projects",{...p,name:form.get("name"),instructions:form.get("instructions"),default_agent_id:form.get("agent")||null,retrieval_mode:form.get("retrieval_mode")});await refreshFeatureViews();
}
function folderSyncSummary(last){
  if(!last)return "Not synced yet.";const s=last.summary;
  return `${last.completed?"Last sync":"Last sync stopped"} ${new Date(last.finished_at).toLocaleString()}: ${s.added} added, ${s.updated} updated, ${s.unchanged} unchanged, ${s.removed} removed${s.restored?`, ${s.restored} restored`:""}${s.failed?`, ${s.failed} could not be indexed`:""}${s.skipped.length?`, ${s.skipped.length} skipped`:""}.${s.truncated?" Sync limit reached: link a narrower folder or add exclude patterns.":""}`;
}
function linkedFolders(status){
  if(!status)return "";
  const rows=status.folders.map(f=>`<article class="library-row"><div><h4><code>${escapeHtml(f.root)}:/${escapeHtml(f.path)}</code></h4><p>${Number(f.files)} files${f.include.length?` · include ${escapeHtml(f.include.join(", "))}`:""}${f.exclude.length?` · exclude ${escapeHtml(f.exclude.join(", "))}`:""}</p></div><button class="secondary" data-folder-unlink="${escapeHtml(f.id)}">Unlink</button></article>`).join("");
  return `<h3>Linked folders</h3>${rows||"<p>No linked folders. Open Workspace files, choose a folder and select Link folder to knowledge project.</p>"}<p id="project-folder-sync">${escapeHtml(folderSyncSummary(status.last_sync))}</p><div class="card-actions"><button id="project-sync" ${!status.folders.length||status.running_run_id?"disabled":""}>${status.running_run_id?"Sync running…":"Sync now"}</button><a class="button-link" href="#files">Link a folder in Workspace files</a></div><p>Sync copies new and changed files into private artifacts and indexes them on this server. Files deleted from the folder leave search; earlier answers keep their citations.</p>`;
}
async function openProject(id){
  const [project,docs,artifacts,sessions,folders]=await Promise.all([featureApi(`projects/${id}`),featureApi(`projects/${id}/documents`),api("/api/catalog/artifacts"),featureApi(`projects/${id}/sessions`),featureApi(`projects/${id}/folders`).catch(()=>null)]);
  showDetail(project.name,`<div class="card-actions"><button id="project-chat">New conversation</button><button class="secondary" id="project-export">Export configuration</button><button class="secondary" id="project-package">Export with documents</button></div><p>Default search: ${escapeHtml(project.retrieval_mode||"lexical")}</p><h3>Documents</h3><form id="project-attach"><label>Saved file<select name="artifact" required><option value="">Choose a file</option>${artifacts.items.filter(a=>!a.archived).map(a=>`<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`).join("")}</select></label><button type="submit">Add document</button></form><p>Upload files in Library → Artifacts first, or link a server folder below.</p>${linkedFolders(folders)}${docs.items.map(d=>`<p>${escapeHtml(d.source?d.source.path:d.filename)} · revision ${d.artifact_revision} · ${escapeHtml(d.status)}${d.source_removed?" · removed from folder":""}${d.error?` · ${escapeHtml(d.error)}`:""}${(d.retryable||["failed","interrupted"].includes(d.status))?` <button class="secondary" data-document-retry="${escapeHtml(d.id)}">Retry indexing</button>`:""}</p>`).join("")}<h3>Search documents</h3><p>${escapeHtml(knowledgeRetrievalNotice())}</p><form id="project-search"><label>Question<input name="query" required maxlength="2000"></label><label>Search method<select name="mode"><option value="lexical">Local text search</option><option value="semantic">Semantic</option><option value="hybrid">Hybrid</option></select></label><button type="submit">Search</button></form><div id="project-search-results"></div><h3>Related conversations</h3>${sessions.items.length?sessions.items.map(session=>`<button class="secondary" data-project-session="${escapeHtml(session.id)}">${escapeHtml(session.title)}</button>`).join(""):"<p>No related conversations yet.</p>"}<p id="project-detail-error" role="alert"></p>`);
  const syncButton=$("project-sync");
  const watchSync=runId=>{syncButton.disabled=true;syncButton.textContent="Sync running…";pollRun(runId).then(()=>{if($("detail").open)openProject(id);}).catch(error=>{$("project-detail-error").textContent=error.message;syncButton.textContent="Sync now";syncButton.disabled=false;});};
  if(syncButton){syncButton.onclick=async()=>{try{watchSync((await featureApi(`projects/${id}/sync`,{})).id);}catch(error){$("project-detail-error").textContent=error.message;}};if(folders.running_run_id)watchSync(folders.running_run_id);}
  $("detail-content").querySelectorAll("[data-folder-unlink]").forEach(button=>button.onclick=async()=>{if(!await fieldsDialog("Unlink folder","<p>Sync stops for this folder and its copies leave search. Earlier answers keep their citations.</p>","Unlink"))return;try{await featureApi(`projects/${id}/folders/${encodeURIComponent(button.dataset.folderUnlink)}/unlink`,{});await openProject(id);}catch(error){$("project-detail-error").textContent=error.message;}});
  $("project-chat").onclick=()=>{newChat();$("chat-project").value=id;updateKnowledgeDisclosure();$("detail").close();};
  $("project-search").elements.mode.value=project.retrieval_mode||"lexical";
  $("detail-content").querySelectorAll("[data-document-retry]").forEach(button=>button.onclick=async()=>{button.disabled=true;try{await featureApi(`projects/${id}/documents/${button.dataset.documentRetry}/reindex`,{});await openProject(id);}catch(error){button.disabled=false;$("project-detail-error").textContent=error.message;}});
  $("project-package").onclick=async()=>{try{downloadJson("knowledge-project-with-documents.json",await featureApi(`projects/${id}/package`));}catch(error){$("project-detail-error").textContent=error.message;}};
  $("detail-content").querySelectorAll("[data-project-session]").forEach(button=>button.onclick=async()=>{try{const session=await api(`/api/sessions/${encodeURIComponent(button.dataset.projectSession)}`);if(state.busy)throw new Error("Let the current conversation finish first.");state.sessionId=session.id;$("chat-project").value=id;updateKnowledgeDisclosure();$("messages").replaceChildren();session.messages.forEach(message=>{const node=addMessage(message.role,message.content,providerName(message.provider||""),message.run_id,message.citations||[],message.structured_content||[],message.retrieval_notice);if(message.status==="failed")node.classList.add("error");});$("chat-agent").value=session.agent_id||"";$("chat-provider").value=session.provider||"codex";$("chat-model").value=session.model||"";if($("chat-effort"))$("chat-effort").value=session.effort||"";$("detail").close();location.hash="chat";}catch(error){$("project-detail-error").textContent=error.message;}});
  $("project-export").onclick=async()=>downloadJson("knowledge-project.json",await featureApi(`projects/${id}/export`));
  $("project-attach").onsubmit=async e=>{e.preventDefault();try{await featureApi(`projects/${id}/documents`,{artifact_id:new FormData(e.target).get("artifact")});await openProject(id);}catch(error){$("project-detail-error").textContent=error.message;}};
  $("project-search").onsubmit=async e=>{e.preventDefault();try{const form=new FormData(e.target),result=await featureApi(`projects/${id}/search?q=${encodeURIComponent(form.get("query"))}&mode=${form.get("mode")}`);const root=$("project-search-results");root.replaceChildren();if(result.notice){const p=document.createElement("p");p.textContent=result.notice;root.append(p);}for(const item of result.items){const article=document.createElement("article"),title=document.createElement("h4"),text=document.createElement("p");title.textContent=`${item.source_path||item.filename} · page ${item.page}`;text.textContent=item.text;article.append(title,text);root.append(article);}if(!result.items.length)root.textContent="No matching documents.";}catch(error){$("project-detail-error").textContent=error.message;}};
}
async function resolvePromptValues(task){
  const prompts=(await api("/api/catalog/prompts")).items.filter(p=>!p.archived),result={};
  for(const p of prompts){if(!task.split(/\s+/).some(token=>token===`/${p.id}`||p.data.shortcut&&token===`/${p.data.shortcut}`)||!Object.keys(p.data.variables||{}).length)continue;
    const names=Object.keys(p.data.variables);const form=await fieldsDialog(p.name,names.map((name,index)=>inputField(`v${index}`,name,p.data.variables[name])).join(""),"Use prompt");
    if(!form)throw new Error("Prompt insertion cancelled");result[p.id]=Object.fromEntries(names.map((name,index)=>[name,form.get(`v${index}`)]));
  }return result;
}
async function designResource(){
  const kind=library.kind==="schedules"?"schedule":"prompt";
  const form=await fieldsDialog(`Build ${kind} with AI`,`<label>Describe what you need<textarea name="task" rows="5" required maxlength="16000"></textarea></label><label>Provider<select name="provider">${providerOptions($("chat-provider").value)}</select></label>`,"Generate draft");if(!form)return;
  let task=form.get("task"),conversation="",proposal;
  while(true){
    const run=await featureApi("design",{design_kind:kind,task,conversation,provider:form.get("provider")});showRun(run);
    const completed=await pollRun(run.id,updateRunDetail);proposal=completed.design_proposal;
    let choice;
    while(true){
      choice=await fieldsDialog(`Review ${kind} draft`,`<p>Edit the draft or describe a refinement.${kind==="schedule"?" Schedules stay disabled until you enable them.":""}</p><label>Draft JSON<textarea name="proposal" rows="14" required>${escapeHtml(JSON.stringify(proposal,null,2))}</textarea></label><label>Refine with AI (optional)<textarea name="refine" rows="3" maxlength="4000" placeholder="What should change?"></textarea></label>`,"Continue");
      if(!choice)return;
      try{proposal=JSON.parse(choice.get("proposal"));break;}catch{notify("Draft must contain valid JSON. Your previous draft is retained.");}
    }
    if(String(choice.get("refine")||"").trim()){
      conversation=`Original request: ${form.get("task")}\nCurrent draft: ${JSON.stringify(proposal)}`;task=choice.get("refine");continue;
    }
    if(kind==="schedule"&&proposal.data)proposal.data.enabled=false;
    await api(`/api/catalog/${kind==="prompt"?"prompts":"schedules"}`,proposal);window.dispatchEvent(new CustomEvent("studio-resource-saved",{detail:{kind:kind==="prompt"?"prompts":"schedules"}}));$("detail").close();await refreshLibrary();return;
  }
}
async function refreshFeedbackView(){
  const feedback=(await featureApi("feedback")).items;
  const role=state.mode==="team"?state.user?.role:"owner";
  const edit=["operator","builder","owner"].includes(role),promote=["builder","owner"].includes(role);
  $("feedback-content").innerHTML='<p>Your ratings and comments stay in your account. Creating an evaluation case does not run it.</p><button class="secondary" id="feedback-export">Export feedback</button>'+feedback.map(f=>`<article class="library-row"><div><h2>${f.rating>0?"Helpful":"Needs improvement"}</h2><p>${escapeHtml(f.task)}</p><p class="feedback-comment">${escapeHtml(f.comment)}</p><small>Updated ${escapeHtml(new Date(f.updated_at).toLocaleString())}</small></div><div class="library-actions"><button class="secondary" data-run="${escapeHtml(f.run_id)}">View response</button><button class="secondary" data-feedback-edit="${escapeHtml(f.run_id)}" ${edit?"":'disabled title="Requires an operator, builder or owner role"'}>Edit feedback</button><button class="secondary" data-feedback-promote="${escapeHtml(f.id)}" data-feedback-run="${escapeHtml(f.run_id)}" ${promote?"":'disabled title="Requires a builder or owner role"'}>Create evaluation case</button></div></article>`).join("")+(feedback.length?"":"<p>No feedback yet. Rate a completed response in a conversation to add it here.</p>");
  $("feedback-export").onclick=async()=>{const button=$("feedback-export");button.disabled=true;try{const current=(await featureApi("feedback")).items;downloadJson("response-feedback.json",{format:"nexus-response-feedback/v1",items:current});}catch(error){notify(error.message);}finally{button.disabled=false;}};
}
async function rateResponse(runId,rating=null){
  const prior=(await featureApi("feedback")).items.find(item=>item.run_id===runId);
  const selected=rating??prior?.rating??1;
  const form=await fieldsDialog(prior?"Edit response feedback":selected>0?"Helpful response":"Improve this response",`<label>Rating<select name="rating"><option value="1" ${selected>0?"selected":""}>Helpful</option><option value="-1" ${selected<0?"selected":""}>Needs improvement</option></select></label><label>Comment (optional)<textarea name="comment" maxlength="2000" rows="4">${escapeHtml(prior?.comment||"")}</textarea></label>`,"Save feedback");
  if(!form)return null;
  const saved=await featureApi("feedback",{run_id:runId,rating:Number(form.get("rating")),comment:form.get("comment"),revision:prior?.revision??0});
  notify("Feedback saved.");return saved;
}
async function shareConversation(){
  if(!state.sessionId)throw new Error("Start a conversation first");
  const sessionId=state.sessionId;
  const files=(await api("/api/catalog/artifacts")).items.filter(file=>!file.archived);
  const selection=`<fieldset class="share-files"><legend>Include saved files</legend><p id="share-files-help">Optional. Choose up to ten files from your library. No files are selected automatically. Each file's latest revision is frozen when the snapshot is created.</p><div class="share-file-options">${files.length?files.map(file=>`<label class="share-file-option"><input type="checkbox" name="artifacts" value="${escapeHtml(file.id)}" aria-describedby="share-files-help"><span>${escapeHtml(file.data.filename)}<small>${escapeHtml(file.name)}</small></span></label>`).join(""):"<p>No saved files available. Upload files in Library → Artifacts first.</p>"}</div></fieldset>`;
  const form=await fieldsDialog("Share conversation",'<p>Create a read-only snapshot of current messages. Recipients can download included files.</p>'+selection+'<label>Nexus recipient IDs (comma separated)<input name="recipients"></label><label>Expires after (hours)<input name="expires" type="number" value="168" min="1" max="8760" required></label><label class="check-label"><input name="public" type="checkbox">Create public link (owner must enable first)</label>',"Create snapshot",values=>values.getAll("artifacts").length>10?"Choose up to ten files, or leave every file unchecked to share messages only.":"");if(!form)return;
  const result=await featureApi("shares",{session_id:sessionId,artifact_ids:form.getAll("artifacts"),recipients:String(form.get("recipients")||"").split(",").map(v=>v.trim()).filter(Boolean),public:form.has("public"),expires_hours:Number(form.get("expires"))});
  const url=new URL(result.token?`/shared.html#public/${result.token}`:`/shared.html#private/${result.id}`,location.href);
  showDetail("Snapshot created",`<p>Snapshot includes current messages and ${result.artifacts.length} selected file${result.artifacts.length===1?"":"s"}. Revoke it under Conversation sharing.</p>${result.artifacts.length?`<ul>${result.artifacts.map(file=>`<li>${escapeHtml(file.filename)} · revision ${file.revision}</li>`).join("")}</ul>`:""}<a class="button-link" href="${escapeHtml(url.href)}" target="_blank" rel="noopener">Open shared snapshot</a><label>Snapshot link<input readonly value="${escapeHtml(url.href)}"></label>`);
}
document.addEventListener("click",async event=>{const b=event.target.closest("button");if(!b)return;try{
  if(b.id==="project-import"){
    const form=await fieldsDialog("Import knowledge project",'<p>Choose an exported configuration or package with documents. Imported projects and files receive new IDs in this workspace.</p><label>Project file<input name="package" type="file" accept=".json,application/json" required></label>',"Import");
    if(form){const file=form.get("package");if(!file||!file.size||file.size>24*1024*1024)throw new Error("Choose a JSON package up to 24 MiB.");let value;try{value=JSON.parse(await file.text());}catch{throw new Error("Choose a valid JSON project export.");}const project=await featureApi("projects/import",value);await refreshFeatureViews();notify("Project imported. Documents are indexed locally in the background.");await openProject(project.id);}
  }
  if(b.id==="project-create"||b.dataset.projectEdit)await editProject(b.dataset.projectEdit);
  if(b.dataset.projectOpen)await openProject(b.dataset.projectOpen);
  if(b.dataset.projectArchive){const p=await featureApi(`projects/${b.dataset.projectArchive}`);await featureApi("projects",{...p,archived:!p.archived});await refreshFeatureViews();}
  if(b.dataset.shareRevoke){await featureApi(`shares/${b.dataset.shareRevoke}/revoke`,{});await refreshFeatureViews();}
  if(b.dataset.feedbackEdit){if(await rateResponse(b.dataset.feedbackEdit))await refreshFeedbackView();}
  if(b.dataset.feedbackPromote){
    const run=await api(`/api/runs/${encodeURIComponent(b.dataset.feedbackRun)}`);
    const reports=(run.structured_content||[]).filter(item=>item.type==="artifact"&&item.filename==="retrieval-evidence.json");
    const evidenceField=reports.length?`<label>Reference context<select name="evidence"><option value="">Do not copy reference context</option>${reports.map((item,index)=>`<option value="${index}">Saved retrieval ${index+1}</option>`).join("")}</select></label><p>Copies cited passages from the saved revision into this evaluation. A configured AI judge receives those passages.</p>`:"";
    const form=await fieldsDialog("Create evaluation case",'<p>Creates a saved evaluation from the original task and your expected phrase. Your feedback comment is not copied. Nothing runs until you start the evaluation.</p>'+inputField("name","Evaluation name","")+targetField()+inputField("expected","Expected answer phrase","")+evidenceField);
    if(form){const [target_kind,target_id]=form.get("target").split(":"),selected=form.get("evidence"),report=selected!==null&&selected!==""?reports[Number(selected)]:null;const saved=await featureApi("feedback/promote",{feedback_id:b.dataset.feedbackPromote,name:form.get("name"),target_kind,target_id,expected:form.get("expected"),...(report?{evidence:{artifact_id:report.artifact_id,revision:report.revision}}:{})});showDetail("Evaluation case created",`<p>${escapeHtml(saved.name)} is saved. Review its target and expected answer before running it.</p><button id="feedback-review-evaluation">Review evaluation case</button>`);$("feedback-review-evaluation").onclick=async()=>{try{$("detail").close();library.kind="evaluations";location.hash="evaluations";await openLibraryEditor(saved.id);}catch(error){notify(error.message);}};}
  }
  if(b.id==="design-resource")await designResource();
  if(b.id==="share-conversation")await shareConversation();
  if(b.dataset.rate){
    const msg=b.closest(".message");
    const ok=await rateResponse(msg?.dataset.runId,Number(b.dataset.rate));
    if(ok&&msg){
      msg.querySelectorAll(".rate-btn").forEach(btn=>{btn.classList.remove("active");btn.removeAttribute("aria-pressed");});
      const selected=Array.from(msg.querySelectorAll(".rate-btn")).find(button=>Number(button.dataset.rate)===ok.rating);
      if(selected){selected.classList.add("active");selected.setAttribute("aria-pressed","true");}
    }
  }
}catch(error){notify(error.message);}});
function initializeFeatures(){
  if($("share-conversation")){refreshFeatureViews().catch(e=>notify(e.message));return;}
  const share=document.createElement("button");share.id="share-conversation";share.className="secondary";share.textContent="Share snapshot";$("chat-project").closest(".chat-controls").append(share);
  const design=document.createElement("button");design.id="design-resource";design.className="secondary";design.textContent="Build with AI";$("library-create").after(design);
  new MutationObserver(()=>{$("design-resource").hidden=!["prompts","schedules"].includes(library.kind);}).observe($("library-heading"),{childList:true});
  refreshFeatureViews().catch(e=>notify(e.message));
  new MutationObserver(()=>document.querySelectorAll(".message[data-run-id]").forEach(message=>{if(message.querySelector(".response-actions"))return;const actions=document.createElement("div");actions.className="response-actions";actions.innerHTML='<button class="secondary rate-btn" data-rate="1" title="Helpful" aria-label="Helpful"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path></svg></button><button class="secondary rate-btn" data-rate="-1" title="Needs improvement" aria-label="Needs improvement"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"></path></svg></button>';message.append(actions);})).observe($("messages"),{subtree:true,attributes:true,attributeFilter:["data-run-id"],childList:true});
}
if(state.ready)initializeFeatures();else window.addEventListener("studio-ready",initializeFeatures);
window.addEventListener("hashchange",()=>{if(["chat","projects","feedback","sharing"].includes(view()))refreshFeatureViews().catch(e=>notify(e.message));});

const originalLibraryEditor=openLibraryEditor;
openLibraryEditor=async function(id,template){
  await originalLibraryEditor(id,template);
  if(library.kind!=="prompts")return;
  const data=library.editing?.data||{};
  const box=document.createElement("div");box.innerHTML=inputField("prompt_shortcut","Chat shortcut",data.shortcut||"")+inputField("prompt_tags","Tags (comma separated)",(data.tags||[]).join(", "))+'<p>Use {{Variable Name}} in prompt content. Users fill these values when inserting the prompt.</p>'+`<label>Variable defaults (JSON object)<textarea name="prompt_defaults" rows="3">${escapeHtml(JSON.stringify(data.variables||{},null,2))}</textarea></label>`;
  $("library-fields").append(box);
};
const originalApi=api;
api=async function(path,body){
  if(path==="/api/catalog/prompts"&&body&&$("library-editor").open){
    const tags=$("library-form").elements.prompt_tags?.value||"";
    body={...body,data:{...body.data,variables:JSON.parse($("library-form").elements.prompt_defaults?.value||"{}"),shortcut:$("library-form").elements.prompt_shortcut?.value||"",tags:tags.split(",").map(v=>v.trim()).filter(Boolean)}};
  }
  return originalApi(path,body);
};
const originalOperationFields=operationFields;
operationFields=function(item){
  const result=originalOperationFields(item);
  if(library.kind==="entrypoints")return result+`<label class="check-label"><input type="checkbox" name="mcp_enabled" ${item?.data?.mcp_enabled?"checked":""}>Expose this bearer webhook as an MCP task gateway</label><p>Use /api/mcp/ENTRYPOINT_ID with the same bearer token. Only this target is exposed; approvals remain in Studio.</p><details><summary>A2A callback notifications</summary><label>Approved callback URLs (one per line)<textarea name="a2a_push_destinations" rows="3">${escapeHtml((item?.data?.a2a_push?.destinations||[]).join("\n"))}</textarea></label><label class="check-label"><input type="checkbox" name="a2a_push_private" ${item?.data?.a2a_push?.allow_private?"checked":""}>Allow explicitly listed private destinations</label><p>Enable A2A first. Blank list keeps callbacks off. Clients register Bearer credentials through the task API. HTTPS required; local HTTP allowed only on loopback with private allowance. Three delivery attempts; receivers must deduplicate delivery IDs. Credentials require server encryption setup.</p></details>`;
  if(library.kind!=="schedules")return result;
  const data=item?.data||{};
  return result+inputField("cron","Cron schedule (optional; overrides fixed interval)",data.cron||"")+inputField("timezone","Time zone",data.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone||"UTC")+'<p>Example: 0 9 * * 1-5 runs weekdays at 09:00 in the selected time zone. Missing DST times are skipped; repeated times run once.</p><button type="button" class="secondary" id="cron-preview">Preview upcoming dates</button><p id="cron-preview-output"></p>';
};
const originalOperationFormData=operationFormData;
operationFormData=function(form){
  if(library.kind==="schedules"&&String(form.get("cron")||"").trim()){
    const [target_kind,...target]=String(form.get("target")||"").split(":");
    const rawTask=form.get("task")?.trim()||"";
    const wfInputRaw=form.get("workflow_input_json")?.trim();
    let wfInput=undefined;
    if(wfInputRaw){try{wfInput=JSON.parse(wfInputRaw);}catch{}}
    return {target_kind,target_id:target.join(":"),task:rawTask,cron:String(form.get("cron")).trim(),timezone:form.get("timezone")||"UTC",interval_seconds:0,enabled:form.has("enabled"),...(wfInput!==undefined?{workflow_input:wfInput}:{})};
  }
  const data=originalOperationFormData(form);
  if(library.kind==="schedules")data.timezone=form.get("timezone")||"UTC";
  if(library.kind==="entrypoints"){
    data.mcp_enabled=form.has("mcp_enabled");
    const destinations=String(form.get("a2a_push_destinations")||"").split("\n").map(v=>v.trim()).filter(Boolean);
    if(destinations.length)data.a2a_push={destinations,allow_private:form.has("a2a_push_private")};
  }
  return data;
};
document.addEventListener("click",async event=>{if(event.target.id!=="cron-preview")return;try{const form=new FormData($("library-form"));const result=await featureApi("schedules/preview",{cron:form.get("cron"),timezone:form.get("timezone")});$("cron-preview-output").textContent=result.items.map(v=>new Date(v).toLocaleString(undefined,{timeZone:form.get("timezone")})).join(" · ");}catch(error){$("cron-preview-output").textContent=error.message;}});
const connectorEditor=openLibraryEditor;
openLibraryEditor=async function(id,template){
  await connectorEditor(id,template);if(library.kind!=="connectors")return;
  const data=library.editing?.data||{};
  const select=$("library-form").elements.connector_type;
  for(const [value,label] of [["sql","SQL database"],["openapi","OpenAPI service"]]){const option=document.createElement("option");option.value=value;option.textContent=label;select.append(option);}select.value=data.type||select.value;
  const fields=document.createElement("div");fields.innerHTML=`<fieldset data-extra-service="sql"><legend>SQL database</legend><label>Database type<select name="sql_dialect">${["postgresql","mysql","mariadb","mssql","oracle"].map(v=>`<option ${data.dialect===v?"selected":""}>${v}</option>`).join("")}</select></label>${inputField("dsn_env","Database connection environment reference",data.dsn_env||"")}<p>Use a read-only database account. Configure the connection string on the server; do not paste it here.</p><label><input type="checkbox" name="sql_writes" ${data.allow_writes?"checked":""}>Allow reviewed write operations</label></fieldset><fieldset data-extra-service="openapi"><legend>OpenAPI service</legend><label>OpenAPI JSON definition<input type="file" id="openapi-definition" accept=".json,application/json"></label><input name="openapi_spec" type="hidden" value="${escapeHtml(JSON.stringify(data.spec||{}))}"><label>Enabled operations<select name="openapi_operations" multiple size="6"></select></label><label>Connectivity check<select name="openapi_health_operation" aria-label="Connectivity check operation"></select></label><p>Choose a read-only operation without required inputs. Runs only when you request a build connectivity check.</p><label><input name="openapi_writes" type="checkbox" ${(data.actions||[]).includes("invoke")?"checked":""}>Allow reviewed mutating operations</label></fieldset><fieldset data-extra-service="mcp"><legend>MCP transport</legend><label>Transport<select name="mcp_transport">${[["stdio","Installed local executable"],["streamable-http","Streamable HTTP"],["sse","Legacy SSE"]].map(([v,label])=>`<option value="${v}" ${(data.transport||"stdio")===v?"selected":""}>${label}</option>`).join("")}</select></label></fieldset><fieldset data-extra-service="remote"><legend>Remote service account</legend>${inputField("remote_url","Service base URL",data.url||"")}<label>Authentication<select name="remote_auth">${["none","bearer","api-key","basic","query","url-token"].map(v=>`<option ${(data.auth?.type||"none")===v?"selected":""}>${v}</option>`).join("")}</select></label>${inputField("remote_token","Credential environment reference",data.auth?.token_env||data.token_env||"")}${inputField("remote_header","API-key header",data.auth?.header||"X-API-Key")}${inputField("remote_parameter","API-key query parameter",data.auth?.parameter||"token")}${inputField("remote_user","Username environment reference",data.auth?.username_env||"")}${inputField("remote_password","Password environment reference",data.auth?.password_env||"")}</fieldset>`;
  $("library-fields").append(fields);
  function sync(){const kind=select.value;fields.querySelectorAll("[data-extra-service]").forEach(f=>f.hidden=f.dataset.extraService!==kind&&!(f.dataset.extraService==="remote"&&(kind==="openapi"||kind==="mcp"&&$("library-form").elements.mcp_transport.value!=="stdio")));}
  fields.querySelector('[name="mcp_transport"]').onchange=sync;select.addEventListener("change",sync);sync();
  let currentSpec=data.spec||{};
  function fillHealth(selection){const form=$("library-form"),root=form.elements.openapi_health_operation,selected=Array.from(form.elements.openapi_operations.selectedOptions,option=>option.value);const previous=selection??root.value;root.replaceChildren();const empty=document.createElement("option");empty.value="";empty.textContent="No connectivity check";root.append(empty);for(const operation of openAPIHealthOptions(currentSpec,selected)){const option=document.createElement("option");option.value=operation.id;option.textContent=operation.label;root.append(option);}root.value=Array.from(root.options).some(option=>option.value===previous)?previous:"";}
  function fillOperations(spec){currentSpec=spec;const selected=new Set(data.operations||[]),root=$("library-form").elements.openapi_operations;root.replaceChildren();for(const [path,entry] of Object.entries(spec.paths||{}))for(const [method,operation] of Object.entries(entry)){if(!["get","post","put","patch","delete","head","options"].includes(method))continue;const option=document.createElement("option");option.value=operation.operationId||`${method}:${path}`;option.textContent=`${method.toUpperCase()} ${path}`;option.selected=selected.has(option.value);root.append(option);}fillHealth(data.health_operation||"");}
  $("library-form").elements.openapi_operations.addEventListener("change",()=>fillHealth());
  fillOperations(data.spec||{});
  $("openapi-definition").onchange=async event=>{try{const file=event.target.files[0];if(!file||file.size>512000)throw new Error("Choose an OpenAPI JSON file no larger than 500 KiB");const spec=JSON.parse(await file.text());$("library-form").elements.openapi_spec.value=JSON.stringify(spec);fillOperations(spec);}catch(error){$("library-form-error").textContent=error.message;}};
};
const connectorFormData=operationFormData;
operationFormData=function(form){
  const type=form.get("connector_type");
  if(library.kind==="connectors"&&type==="sql")return {type:"sql",dialect:form.get("sql_dialect"),dsn_env:form.get("dsn_env"),allow_writes:form.has("sql_writes"),actions:["query","inspect_schema",...(form.has("sql_writes")?["execute"]:[])],enabled:form.has("enabled")};
  const remote={url:form.get("remote_url"),allow_private:form.has("allow_private"),auth:{type:form.get("remote_auth"),token_env:form.get("remote_token")||undefined,header:form.get("remote_header")||"X-API-Key",parameter:form.get("remote_parameter")||"token",username_env:form.get("remote_user")||undefined,password_env:form.get("remote_password")||undefined}};
  if(library.kind==="connectors"&&type==="openapi")return {type:"openapi",...remote,spec:JSON.parse(form.get("openapi_spec")||"{}"),operations:form.getAll("openapi_operations"),...(form.get("openapi_health_operation")?{health_operation:form.get("openapi_health_operation")}:{}),actions:["list_operations","read",...(form.has("openapi_writes")?["invoke"]:[])],enabled:form.has("enabled")};
  const data=connectorFormData(form);
  if(library.kind==="connectors"&&type==="mcp"){data.transport=form.get("mcp_transport")||"stdio";if(data.transport!=="stdio")Object.assign(data,remote);}
  return data;
};
let voiceRecorder=null,voiceStream=null,voiceChunks=[];
async function chooseSpeech(action,text=""){
  const available=(await featureApi("speech")).items.filter(p=>p.action===action);
  if(!available.length)throw new Error("No configured speech provider supports this action");
  const warnings=available.filter(p=>p.local&&!p.model_ready).map(p=>`<li>${escapeHtml(p.id)}: ${escapeHtml((p.missing_prerequisites||["Installed local model"]).join(", "))}</li>`).join("");
  const html=`<label>Speech provider<select name="profile">${available.map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} · ${escapeHtml(p.billing)}${p.local&&!p.model_ready?" · setup required":""}</option>`).join("")}</select></label><p>API voice uses separately billed credentials. Local voice requires installed models and runtime software.</p>${warnings?`<p>Local setup required:</p><ul>${warnings}</ul>`:""}`+(action==="speak"?'<label>Voice (API profile)<input name="voice" placeholder="Provider default"></label>':'<label>Language hint (optional)<input name="language" maxlength="20" placeholder="en"></label>');
  const form=await fieldsDialog(action==="speak"?"Generate spoken response":"Record a voice message",html,action==="speak"?"Generate speech":"Start recording");if(!form)return null;
  const selected=available.find(p=>p.id===form.get("profile"));
  if(!selected)throw new Error("Choose a configured speech provider");
  if(selected.local&&!selected.model_ready)throw new Error("Local speech setup required: "+(selected.missing_prerequisites||["Installed local model"]).join(", "));
  return {profile:selected.id,voice:form.get("voice")||undefined,language:form.get("language")||undefined,text};
}
async function beginRecording(){
  if(voiceRecorder){voiceRecorder.stop();return;}
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error("Voice recording needs a supported browser on localhost or HTTPS");
  const options=await chooseSpeech("transcribe");if(!options)return;
  voiceStream=await navigator.mediaDevices.getUserMedia({audio:true});voiceChunks=[];
  const mime=MediaRecorder.isTypeSupported("audio/webm;codecs=opus")?"audio/webm;codecs=opus":"audio/mp4";
  voiceRecorder=new MediaRecorder(voiceStream,{mimeType:mime});let bytes=0;
  const button=$("record-voice");button.textContent="Stop recording";button.setAttribute("aria-pressed","true");
  voiceRecorder.ondataavailable=e=>{bytes+=e.data.size;voiceChunks.push(e.data);if(bytes>4*1024*1024&&voiceRecorder?.state==="recording")voiceRecorder.stop();};
  voiceRecorder.onstop=async()=>{voiceStream.getTracks().forEach(t=>t.stop());voiceStream=null;voiceRecorder=null;button.textContent="Record voice";button.setAttribute("aria-pressed","false");try{
    const blob=new Blob(voiceChunks,{type:mime});voiceChunks=[];if(blob.size>4*1024*1024)throw new Error("Recording exceeded 4 MiB. Try a shorter message.");
    const run=await featureApi("speech",{...options,audio_base64:await fileContent(blob),format:mime.startsWith("audio/webm")?"webm":"mp4"});notify("Transcribing audio…");const result=await pollRun(run.id);$("chat-task").value=[$("chat-task").value,result.output].filter(Boolean).join("\n");$("chat-task").focus();notify("Transcript ready. Review before sending.");
  }catch(error){notify(error.message);}};
  voiceRecorder.onerror=()=>{voiceStream?.getTracks().forEach(t=>t.stop());voiceRecorder=null;voiceStream=null;button.textContent="Record voice";notify("Recording failed; check microphone permission.");};voiceRecorder.start(1000);
}
async function speakResponse(text){
  const options=await chooseSpeech("speak",text.slice(0,3000));if(!options)return;
  const run=await featureApi("speech",options);notify("Generating speech…");const completed=await pollRun(run.id);
  const preview=await api(`/api/catalog/artifacts/${completed.speech_artifact_id}/preview`);showDetail("Spoken response","");$("detail-content").replaceChildren(previewContent(preview));
}
const PLAY_DEVICE_SVG='<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>';
const PAUSE_SVG='<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16" rx="1"></rect><rect x="14" y="4" width="4" height="16" rx="1"></rect></svg>';
const RESUME_SVG='<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><polygon points="6 4 20 12 6 20 6 4"></polygon></svg>';
const STOP_SVG='<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"></rect></svg>';

let activeSpeechMessage=null,activeUtterance=null;

function resetSpeechControls(message=null){
  const target=message||activeSpeechMessage;
  if(target){
    const slot=target.querySelector(".speech-playback-slot");
    if(slot){
      slot.innerHTML=`<button class="secondary device-speak-btn" data-speak="device" title="Play on device">${PLAY_DEVICE_SVG}<span>Play on device</span></button>`;
    }
  }
  if(!message||message===activeSpeechMessage){
    activeSpeechMessage=null;activeUtterance=null;
  }
}

function stopDeviceSpeech(){
  if(window.speechSynthesis)window.speechSynthesis.cancel();
  resetSpeechControls();
}

function playOnDevice(text,message=null){
  if(!window.speechSynthesis)throw new Error("Device speech is unavailable in this browser");
  const voices=speechSynthesis.getVoices().filter(v=>v.localService);if(!voices.length)throw new Error("No local device voice is available. Choose server speech instead.");
  stopDeviceSpeech();
  const utterance=new SpeechSynthesisUtterance(text);
  utterance.voice=voices.find(v=>v.voiceURI===$("device-speech-voice")?.value)||voices[0];
  utterance.rate=Number($("device-speech-rate")?.value||1);
  activeUtterance=utterance;
  activeSpeechMessage=message;
  if(message){
    const slot=message.querySelector(".speech-playback-slot");
    if(slot){
      slot.innerHTML=`<button class="secondary speech-control-btn" data-speech-cmd="toggle" title="Pause playback">${PAUSE_SVG}<span>Pause</span></button><button class="secondary speech-control-btn" data-speech-cmd="stop" title="Stop playback">${STOP_SVG}<span>Stop</span></button>`;
    }
  }
  utterance.onend=()=>resetSpeechControls(message);
  utterance.onerror=()=>resetSpeechControls(message);
  speechSynthesis.speak(utterance);
}
function initializeVoicePlayback(){
  if($("record-voice"))return;
  const button=document.createElement("button");button.id="record-voice";button.type="button";button.className="secondary";button.textContent="Record voice";button.setAttribute("aria-pressed","false");$("chat-project").closest(".chat-controls").append(button);
  const prefs=document.createElement("details");prefs.className="speech-preferences";prefs.innerHTML='<summary class="speech-summary"><div class="speech-summary-title"><svg class="speech-icon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg><span>Voice playback</span></div><div class="speech-summary-meta"><span class="speech-badge">Device speech</span><span class="speech-chevron" aria-hidden="true">▾</span></div></summary><div class="speech-body"><div class="speech-grid"><label class="speech-field"><span class="speech-field-label">Device voice</span><select id="device-speech-voice" aria-label="Device voice"></select></label><label class="speech-field speech-field-rate"><span class="speech-field-label">Playback speed</span><select id="device-speech-rate" aria-label="Playback speed"><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label></div><div class="speech-footer"><label class="check-label speech-autoplay-label"><input id="speech-autoplay" type="checkbox"><span>Automatically play completed responses</span></label><button class="secondary speech-stop-btn" type="button" id="stop-speech"><svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg><span>Stop playback</span></button></div></div>';
  $("chat-form").before(prefs);
  new MutationObserver(()=>document.querySelectorAll(".response-actions").forEach(actions=>{if(actions.querySelector("[data-speak]"))return;const buttons=document.createElement("span");buttons.className="speech-actions";buttons.innerHTML=`<span class="speech-playback-slot"><button class="secondary device-speak-btn" data-speak="device" title="Play on device">${PLAY_DEVICE_SVG}<span>Play on device</span></button></span><button class="secondary" data-speak="server">Generate speech</button>`;actions.append(buttons);})).observe($("messages"),{childList:true,subtree:true});
}
if(state.ready)initializeVoicePlayback();else window.addEventListener("studio-ready",initializeVoicePlayback);
document.addEventListener("click",async event=>{const button=event.target.closest("button");if(!button)return;try{
  if(button.id==="record-voice")await beginRecording();
  if(button.id==="stop-speech"){stopDeviceSpeech();document.querySelectorAll("audio").forEach(a=>a.pause());}
  if(button.dataset.speechCmd){
    const cmd=button.dataset.speechCmd;
    if(cmd==="stop"){
      stopDeviceSpeech();
    }else if(cmd==="toggle"){
      if(window.speechSynthesis){
        if(window.speechSynthesis.paused){
          window.speechSynthesis.resume();
          button.innerHTML=`${PAUSE_SVG}<span>Pause</span>`;
          button.title="Pause playback";
        }else{
          window.speechSynthesis.pause();
          button.innerHTML=`${RESUME_SVG}<span>Play</span>`;
          button.title="Resume playback";
        }
      }
    }
    return;
  }
  if(button.dataset.speak){const msg=button.closest(".message");const text=msg?.querySelector(".message-body")?.innerText||"";if(button.dataset.speak==="device")playOnDevice(text,msg);else await speakResponse(text);}
}catch(error){notify(error.message);}});
window.addEventListener("pagehide",()=>{voiceStream?.getTracks().forEach(t=>t.stop());stopDeviceSpeech();});

window.addEventListener("studio-response-completed",event=>{if($("speech-autoplay")?.checked){try{const msg=$("messages")?.lastElementChild;playOnDevice(event.detail.text,msg);}catch(error){notify(error.message);}}});
function refreshDeviceVoices(){const root=$("device-speech-voice");if(!root||!window.speechSynthesis)return;root.replaceChildren();speechSynthesis.getVoices().filter(v=>v.localService).forEach(voice=>{const option=document.createElement("option");option.value=voice.voiceURI;option.textContent=voice.name+" · "+voice.lang;root.append(option);});}
if(state.ready)refreshDeviceVoices();else window.addEventListener("studio-ready",refreshDeviceVoices);window.speechSynthesis?.addEventListener("voiceschanged",refreshDeviceVoices);
function executionGraph(data,cursor=data.timeline.length){
  const box=document.createElement("div");box.className="execution-graph";box.tabIndex=0;box.setAttribute("role","region");box.setAttribute("aria-label","Scrollable execution graph");box.style.maxHeight="28rem";box.style.overflow="auto";const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg"),nodes=data.graph.nodes;
  if(!nodes.length)return box;
  const states={},events=data.timeline.slice(0,cursor);for(const step of events){if(step.graph_node_id)states[step.graph_node_id]=step.node_status||step.status;else if(step.node_id&&["node","branch","approval","input"].includes(step.kind))states[step.node_id]=step.node_status||step.status;}
  if(cursor===data.timeline.length)Object.assign(states,data.states);
  const rank=new Map(nodes.map(node=>[node.id,0]));for(let pass=0;pass<nodes.length;pass++){let changed=false;for(const edge of data.graph.edges){if(!rank.has(edge.source)||!rank.has(edge.target))continue;const next=Math.min(nodes.length,rank.get(edge.source)+1);if(rank.get(edge.target)<next){rank.set(edge.target,next);changed=true;}}if(!changed)break;}
  const lanes=new Map(),position=new Map();for(const node of nodes){const column=rank.get(node.id),lane=lanes.get(column)||0;lanes.set(column,lane+1);position.set(node.id,{x:20+column*190,y:20+lane*90});}
  const width=Math.max(600,(Math.max(...rank.values())+1)*190+20),height=Math.max(130,Math.max(...lanes.values())*90+20);svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.style.maxHeight="none";svg.style.width=`${width}px`;svg.style.height=`${height}px`;svg.setAttribute("role","img");svg.setAttribute("aria-label",`Execution graph at event ${cursor} of ${data.timeline.length}. Detailed node states follow in the timeline.`);
  for(const edge of data.graph.edges){const a=position.get(edge.source),b=position.get(edge.target);if(!a||!b)continue;const line=document.createElementNS(ns,"line");for(const [key,value] of Object.entries({x1:a.x+160,y1:a.y+30,x2:b.x,y2:b.y+30}))line.setAttribute(key,value);line.classList.add("execution-edge");if(edge.relationship){const title=document.createElementNS(ns,"title");title.textContent=edge.relationship==="run-membership"?"Part of this run; no execution order implied":"Explicitly recorded parent relationship";line.append(title);if(edge.relationship==="run-membership")line.setAttribute("stroke-dasharray","4 4");}if(edge.choice){const title=document.createElementNS(ns,"title");title.textContent=`Branch: ${edge.choice}`;line.append(title);}svg.append(line);}
  for(const node of nodes){const p=position.get(node.id),g=document.createElementNS(ns,"g"),rect=document.createElementNS(ns,"rect"),title=document.createElementNS(ns,"text"),status=document.createElementNS(ns,"text"),description=document.createElementNS(ns,"title");for(const [key,value] of Object.entries({x:p.x,y:p.y,width:160,height:65,rx:8}))rect.setAttribute(key,value);title.setAttribute("x",p.x+10);title.setAttribute("y",p.y+24);title.textContent=(node.name||node.id).slice(0,21);status.setAttribute("x",p.x+10);status.setAttribute("y",p.y+46);status.textContent=states[node.id]||"Pending";description.textContent=`${node.name||node.id} · ${node.kind} · ${status.textContent}`;g.append(description,rect,title,status);svg.append(g);}
  box.append(svg);if(data.graph.relationship_note){const note=document.createElement("p");note.textContent=data.graph.relationship_note;box.append(note);}return box;
}
const executionReplay=new Map(),executionRequests=new Map(),executionTimes=new Map();
function renderExecutionReplay(root,data,run){
  root._stopExecutionReplay?.();
  let replay=executionReplay.get(run.id);if(!replay){replay={cursor:data.timeline.length,live:true,playing:false};executionReplay.set(run.id,replay);if(executionReplay.size>20)executionReplay.delete(executionReplay.keys().next().value);}
  const selectedEvent=!replay.live&&replay.cursor?replay.events?.[replay.cursor-1]?.id:null;
  if(replay.events){const known=new Map(replay.events.map(event=>[event.id,event]));for(const event of data.timeline)known.set(event.id,event);data={...data,timeline:Array.from(known.values()).sort((a,b)=>a.sequence-b.sequence)};}replay.events=data.timeline;
  if(selectedEvent){const selectedIndex=data.timeline.findIndex(event=>event.id===selectedEvent);if(selectedIndex>=0)replay.cursor=selectedIndex+1;}
  data.replay={...data.replay,previous_cursor:data.timeline[0]?.sequence>1?data.timeline[0].id:null};
  if(replay.live)replay.cursor=data.timeline.length;replay.cursor=Math.min(replay.cursor,data.timeline.length);
  const heading=document.createElement("h3");heading.textContent="Execution history";
  const controls=document.createElement("div");controls.className="card-actions";controls.style.flexWrap="wrap";
  const makeButton=(text,label)=>{const button=document.createElement("button");button.type="button";button.className="secondary";button.textContent=text;button.setAttribute("aria-label",label);controls.append(button);return button;};
  const previous=makeButton("Previous","Previous execution event"),play=makeButton("Play","Play recorded execution events"),next=makeButton("Next","Next execution event"),live=makeButton("Live","Show latest execution events");
  const slider=document.createElement("input");slider.type="range";slider.min="0";slider.max=String(data.timeline.length);slider.step="1";slider.setAttribute("aria-label","Execution replay position");slider.style.minWidth="140px";slider.style.flex="1";controls.append(slider);
  const count=document.createElement("p");count.setAttribute("role","status");count.setAttribute("aria-live","polite");
  const graph=document.createElement("div"),timeline=document.createElement("ol");timeline.className="execution-timeline";timeline.style.overflowWrap="anywhere";
  const note=document.createElement("p");note.className="muted";note.textContent="Replay reviews saved events; it does not run tasks. "+(data.coverage.missing_timestamps?data.coverage.legacy_ordering+" ":"")+data.coverage.provider_tool_events;
  let timer=null;
  const stop=()=>{if(timer)clearInterval(timer);timer=null;replay.playing=false;play.textContent="Play";play.setAttribute("aria-label","Play recorded execution events");};
  function draw(){
    slider.value=String(replay.cursor);count.textContent=`Event ${replay.cursor} of ${data.timeline.length}${replay.live?" · Live":" · Replay"}`;previous.disabled=replay.cursor===0;next.disabled=replay.cursor===data.timeline.length;live.setAttribute("aria-pressed",String(replay.live));graph.replaceChildren(executionGraph(data,replay.cursor));timeline.replaceChildren();
    for(const step of data.timeline.slice(0,replay.cursor)){const item=document.createElement("li"),details=document.createElement("details"),summary=document.createElement("summary");const name=step.node_name||step.agent||step.tool||step.message||step.kind;summary.textContent=`${step.sequence}. ${name} · ${step.status||step.phase||step.kind}`;const meta=document.createElement("p");meta.textContent=[step.at?new Date(step.at).toLocaleString():"Time unavailable",step.kind,step.provider,step.model,step.duration_seconds!==undefined?`${Number(step.duration_seconds).toFixed(2)}s`:null,step.node_id].filter(Boolean).join(" · ");details.append(summary,meta);if(step.artifact_id){const button=document.createElement("button");button.type="button";button.className="secondary";button.textContent="View artifact";button.onclick=async()=>{try{const preview=await api(`/api/catalog/artifacts/${encodeURIComponent(step.artifact_id)}/preview${step.artifact_revision?`?revision=${step.artifact_revision}`:""}`);showDetail("Artifact","");$("detail-content").replaceChildren(previewContent(preview));}catch(error){notify(error.message);}};details.append(button);}item.append(details);timeline.append(item);}
    if(!replay.cursor){const item=document.createElement("li");item.textContent="No events shown.";timeline.append(item);}
  }
  const seek=value=>{stop();replay.live=false;replay.cursor=Math.max(0,Math.min(data.timeline.length,value));draw();};
  previous.onclick=()=>seek(replay.cursor-1);next.onclick=()=>seek(replay.cursor+1);slider.oninput=()=>seek(Number(slider.value));live.onclick=()=>{stop();replay.live=true;replay.cursor=data.timeline.length;draw();};
  play.onclick=()=>{if(replay.playing){stop();return;}replay.live=false;if(replay.cursor===data.timeline.length)replay.cursor=0;replay.playing=true;play.textContent="Pause";play.setAttribute("aria-label","Pause execution replay");draw();timer=setInterval(()=>{if(!root.isConnected||state.detailRunId!==run.id||!replay.playing){stop();return;}replay.cursor++;draw();if(replay.cursor>=data.timeline.length)stop();},700);};
  root._stopExecutionReplay=stop;root.replaceChildren(heading,controls,count,graph,timeline,note);if(data.replay.previous_cursor){const older=makeButton("Load earlier events","Load earlier execution events");older.onclick=async()=>{older.disabled=true;try{const earlier=await featureApi(`runs/${encodeURIComponent(run.id)}/execution?before=${encodeURIComponent(data.replay.previous_cursor)}`);if(root.isConnected&&state.detailRunId===run.id){stop();const actions=Array.from(root.children).filter(element=>element.dataset.executionAction);renderExecutionReplay(root,{...data,timeline:earlier.timeline,replay:earlier.replay},run);root.append(...actions);}}catch(error){older.disabled=false;notify(error.message);}};}if(data.coverage.graph_nodes_omitted)note.textContent+=` ${data.coverage.graph_nodes_omitted} graph nodes omitted from this overview; all events remain available in the timeline.`;draw();
}
async function renderRunRetryActions(root,run){
  if(!["failed","interrupted"].includes(run.status))return;
  $('run-retry-controls')?.remove();
  const section=document.createElement('section');section.id='run-retry-controls';section.className='run-retry-actions';$('run-output').before(section);
  const heading=document.createElement('h3');heading.textContent='Retry failed steps';section.append(heading);
  const error=document.createElement('p');error.className='form-error';error.setAttribute('role','alert');
  try{
    const plan=await featureApi(`runs/${encodeURIComponent(run.id)}/retry`);
    if(!section.isConnected||state.detailRunId!==run.id)return;
    const usesSavedOutput=plan.steps.some(step=>step.uses_saved_output);
    const note=document.createElement('p');note.textContent=plan.can_retry?(usesSavedOutput?'Retry only validation and saving using the completed LLM response. No new LLM call or token cost.':plan.continues_workflow?'Retry a failed step and continue unfinished work. Completed steps and saved outputs stay intact.':'Retry this failed task with its original provider and settings. Provider billing applies.'):plan.blocked_reason;
    section.append(note);
    if(!plan.can_retry){if(plan.blocked_reason.includes('uncertain external action')){const link=document.createElement('a');link.href='#workbench/effects';link.className='button-link secondary';link.textContent='Review external actions';section.append(link);}return;}
    for(const step of plan.steps){
      const row=document.createElement('div');row.className='run-retry-step';
      const name=document.createElement('strong');name.textContent=step.name;
      const path=document.createElement('small');path.textContent=step.id.startsWith('__')?'':step.id;
      const label=document.createElement('div');label.append(name,path);row.append(label);
      let confirm;
      if(step.requires_effect_review){const review=document.createElement('label');review.className='check-label';confirm=document.createElement('input');confirm.type='checkbox';const text=document.createElement('span');text.textContent='I checked the failed tools’ external effects. Repeating them is safe.';review.append(confirm,text);row.append(review);}
      const button=document.createElement('button');button.type='button';button.textContent=step.uses_saved_output?'Retry step':plan.continues_workflow?'Retry and continue':'Retry task';button.setAttribute('aria-label',`Retry ${step.name}${plan.continues_workflow?' and continue':''}`);
      button.onclick=async()=>{
        if(confirm&&!confirm.checked){error.textContent='Review this tool’s external effects before confirming a retry.';return;}
        section.querySelectorAll('button').forEach(item=>item.disabled=true);error.textContent='';
        try{
          const queued=await featureApi(`runs/${encodeURIComponent(run.id)}/retry`,{digest:plan.digest,step_id:step.id,...(confirm?{confirm_effects:confirm.checked}:{})});
          showRun(queued);await refreshRuns();
          pollRun(queued.id,updateRunDetail).catch(failure=>updateRunDetail(failure.run||{id:queued.id,status:'failed',error:failure.message})).finally(()=>refreshRuns().catch(()=>{}));
        }catch(failure){notify(failure.message);if(section.isConnected)renderRunRetryActions(root,run);}
      };
      row.append(button);section.append(row);
    }
    section.append(error);
  }catch(failure){if(section.isConnected){error.textContent=failure.message;section.append(error);}}
}
const basicRunDetail=updateRunDetail;
updateRunDetail=function(run){
  basicRunDetail(run);if(state.detailRunId!==run.id||!$("run-output"))return;
  if(!["failed","interrupted"].includes(run.status))$('run-retry-controls')?.remove();
  if(typeof renderStructuredContent==="function")renderStructuredContent($("run-output"),(run.structured_content||[]).filter(part=>run.kind!=="external"||part.type!=="text"));
  let root=$("run-execution");if(!root){root=document.createElement("section");root.id="run-execution";$("run-output").after(root);}
  if(executionRequests.has(run.id)||(["running","queued"].includes(run.status)&&Date.now()-(executionTimes.get(run.id)||0)<1000))return;executionTimes.set(run.id,Date.now());
  const request=featureApi(`runs/${encodeURIComponent(run.id)}/execution`).then(data=>{if(state.detailRunId!==run.id||root!==$("run-execution"))return;const signature=JSON.stringify([data.timeline,data.states,run.status,run.cancel_requested,run.awaiting_input,run.evaluation_checkpoint]);if(root.dataset.signature===signature)return;root.dataset.signature=signature;
    const focused=document.activeElement?.getAttribute("aria-label");renderExecutionReplay(root,data,run);if(focused)Array.from(root.querySelectorAll("[aria-label]")).find(element=>element.getAttribute("aria-label")===focused)?.focus();
    renderRunRetryActions(root,run);
    if(["running","queued","waiting"].includes(run.status)||(run.status==="interrupted"&&run.cancellation_cleanup)){const stop=document.createElement("button");stop.className="secondary";stop.dataset.executionAction="true";const pendingCleanup=run.cancellation_cleanup&&run.status==="waiting",interruptedCleanup=run.cancellation_cleanup&&run.status==="interrupted";stop.textContent=pendingCleanup?"Skip this cleanup step":interruptedCleanup?"Continue cleanup":run.cancel_requested?"Stop requested":"Stop task";stop.disabled=!!run.cancel_requested&&!pendingCleanup&&!interruptedCleanup;stop.onclick=async()=>{try{await featureApi(`runs/${run.id}/cancel`,{});stop.textContent="Stop requested";stop.disabled=true;pollRun(run.id,updateRunDetail).catch(error=>notify(error.message));}catch(error){notify(error.message);}};root.append(stop);}
    for(const requested of run.awaiting_input||[]){const form=document.createElement("form");form.dataset.executionAction="true";form.innerHTML=`<label>${escapeHtml(requested.name)}<textarea name="answer" required></textarea></label><button type="submit">Provide input</button><p>Input fields requiring structured data accept JSON.</p>`;form.onsubmit=async event=>{event.preventDefault();try{const raw=new FormData(form).get("answer");let value=raw;if(requested.schema?.type&&requested.schema.type!=="string")value=JSON.parse(raw);await featureApi(`runs/${run.id}/input`,{values:{[requested.id]:value},evaluation_checkpoint:run.evaluation_checkpoint||undefined});pollRun(run.id,updateRunDetail).catch(error=>notify(error.message));}catch(error){notify(error.message);}};root.append(form);}
  }).catch(error=>{if(state.detailRunId===run.id&&root===$("run-execution")&&!root.dataset.signature)root.textContent=error.message;}).finally(()=>executionRequests.delete(run.id));executionRequests.set(run.id,request);
};
const embeddedAgent=location.pathname==="/agent.html"?new URLSearchParams(location.search).get("agent"):null;
if(location.pathname==="/agent.html"){
  document.body.classList.add("single-agent");
  document.querySelector('#welcome [data-action="quick-build"]')?.remove();
  function syncEmbeddedAgent(){$("chat-agent").disabled=true;const agent=state.agents.find(item=>item.id===embeddedAgent);if(!agent){$("send-message").disabled=true;$("chat-task").disabled=true;$("view-title").textContent="Agent unavailable";return;}$("chat-agent").value=embeddedAgent;selectAgent();$("view-title").textContent=agent.document.metadata?.name||embeddedAgent;document.title=$("view-title").textContent+" · Nexus Studio";$("chat-agent").disabled=true;}
  window.addEventListener("studio-ready",()=>{syncEmbeddedAgent();if(!state.agents.some(item=>item.id===embeddedAgent))notify("This agent is unavailable in your workspace.");location.hash="chat";renderSessions();});
  const workspaceRenderSessions=renderSessions;
  renderSessions=function(){const original=state.sessions;state.sessions=original.filter(s=>s.agent_id===embeddedAgent);workspaceRenderSessions();state.sessions=original;};
  window.addEventListener("hashchange",()=>{if(location.hash!=="#chat")location.hash="chat";syncEmbeddedAgent();});
}

let toolReadinessGeneration=0;
const baseRenderTools=renderTools;
renderTools=function(){
  baseRenderTools();const generation=++toolReadinessGeneration;
  featureApi("tools").then(result=>{
    if(generation!==toolReadinessGeneration)return;
    const readiness=new Map(result.items.map(row=>[row.id,row]));
    $("tools-content").querySelectorAll("[data-tool]").forEach(button=>{
      const row=readiness.get(button.dataset.tool);if(!row)return;
      const card=button.closest("article"),note=document.createElement("p");note.className="tool-readiness";
      note.textContent=`${row.configured?"Configured · live check pending":"Setup required"} · ${row.billing}. ${row.setup}`;
      card.querySelector(".card-actions").before(note);button.disabled=!row.configured;
    });
  }).catch(error=>notify(error.message));
};
function integrationSetupDetails(row){
  const setup=row.setup_metadata;if(!setup)return "";
  const list=items=>`<ul>${(items||[]).map(item=>`<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  const checks=(row.readiness?.checks||[]).map(check=>`${check.name}: ${check.status}${check.installed_version?` (${check.installed_version})`:""}`).join("; ");
  return `<p><strong>Billing:</strong> ${escapeHtml(setup.billing)}</p><details><summary>Requirements and setup</summary>${list(setup.requirements)}${checks?`<p>${escapeHtml(checks)}</p>`:""}<p>${escapeHtml(row.readiness?.notice||"")}</p><h4>Installation instructions</h4>${list(setup.installation)}<p>Instructions are for review. Opening this menu never installs or enables anything.</p><h4>Configuration template</h4><p>Replace selection placeholders before saving. Keep credential values in account settings.</p><pre>${escapeHtml(JSON.stringify(setup.configuration_template,null,2))}</pre>${setup.alias_of?`<p>Shared implementation: ${escapeHtml(setup.alias_of)}</p>`:""}</details>`;
}
async function showIntegrationCatalog(){
  const result=await featureApi("integrations");
  const rows=items=>items.map(row=>`<article class="library-row"><div><h3>${escapeHtml(row.name)}</h3><p>${escapeHtml(row.description)}</p><p>${row.mode==="tool"?(row.configured?"Configured · live check pending":"Setup required"):row.mode==="planned"?"Planned":row.mode==="feature"?"Uses an existing workspace feature":"Configure and review before enabling"}</p>${integrationSetupDetails(row)}</div><button class="secondary" data-integration-setup="${escapeHtml(row.id)}" ${row.mode==="planned"?"disabled":""}>${row.mode==="tool"?"Tool setup":row.mode==="feature"?"Open":"Set up"}</button></article>`).join("");
  showDetail("Integration catalog",`<p>${escapeHtml(result.note)}</p><label>Search integrations<input type="search" id="catalog-search" placeholder="Name or capability"></label><p id="catalog-result-count" role="status"></p><div class="integration-catalog"><section><h2>Built-in services</h2>${rows(result.official_items||[])}</section><section><h2>Community equivalents</h2>${rows(result.items)}</section></div>`);
  const filter=()=>{const query=$("catalog-search").value.trim().toLowerCase();let count=0;document.querySelectorAll('.integration-catalog article').forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(query);if(!row.hidden)count++;});document.querySelectorAll('.integration-catalog section').forEach(group=>group.hidden=!Array.from(group.querySelectorAll('article')).some(row=>!row.hidden));$("catalog-result-count").textContent=count?`${count} ${count===1?"integration":"integrations"}`:"No integrations match this search.";};$("catalog-search").oninput=filter;filter();
}
async function configureIntegration(id){
  const result=await featureApi("integrations"),row=[...result.items,...(result.official_items||[])].find(item=>item.id===id);if(!row)throw new Error("Integration unavailable");
  if(row.mode==="feature"){$("detail").close();location.hash=row.target;if(row.target==="library"){library.kind="artifacts";await refreshLibrary();}return;}
  if(row.mode==="connection"){$("detail").close();library.kind="connectors";location.hash="connectors";await refreshLibrary();await openLibraryEditor();$("library-form").elements.name.value=row.name;const select=$("library-form").elements.connector_type;select.value=row.target;select.dispatchEvent(new Event("change",{bubbles:true}));return;}
  if(row.mode==="tool"){
    showDetail(row.name,`<p>${escapeHtml(row.description)}</p><p>${escapeHtml(row.setup)}</p>${integrationSetupDetails(row)}${row.tool_id?`<code>${escapeHtml(row.tool_id)}</code><div class="card-actions"><button data-tool="${escapeHtml(row.tool_id)}" ${row.configured&&row.readiness?.state!=="dependencies-missing"?"":"disabled"}>Run tool</button></div>`:""}`);return;
  }
  if(row.mode==="sharing"){location.hash="sharing";$("detail").close();return;}
  if(row.mode==="cli"){showDetail("Terminal chat",'<p>Use your existing Nexus account. Set your session through an environment reference when team login is enabled.</p><pre>nexus chat --url '+escapeHtml(location.origin)+' "Hello"\nnexus chat --url '+escapeHtml(location.origin)+' --session-env NEXUS_SESSION</pre><p>Continue with --session-id. Inspect an existing task with --run-id; this does not resubmit it.</p>');return;}
  const html=integrationSetupDetails(row)+inputField("name","Connection name",row.name)+(row.mode==="filesystem"?inputField("command","Installed executable or runtime","")+inputField("server_script","Server script (optional)","")+'<p>For the npm filesystem server, select the absolute Node executable and its installed dist/index.js script. Node 24 or later is required. Leave the script empty for a standalone executable. Only this workspace folder is added to the server arguments.</p>':'<p>Secrets stay in server-managed account credentials or environment references. This creates a disabled connection; discover and select tools before enabling them.</p>'+inputField("token_env","Credential environment reference",row.token_env||"SENDGRID_API_KEY"));
  const form=await fieldsDialog(`Set up ${row.name}`,html,"Create disabled connection");if(!form)return;
  await featureApi(`integrations/${id}/configure`,Object.fromEntries(form));location.hash="connectors";$("detail").close();await refreshLibrary();notify("Disabled connection created. Review configuration and permissions before enabling it.");
}
window.addEventListener("studio-ready",()=>{
  for(const [id,target] of [["tools-integrations",$("view-tools").querySelector(".section-heading")],["library-integrations",$("library-create").parentElement]]){
    if($(id))continue;const button=document.createElement("button");button.id=id;button.className="secondary";button.textContent="Integration catalog";target.append(button);
  }
});
if(typeof state!=="undefined"&&state.ready)for(const [id,target] of [["tools-integrations",$("view-tools").querySelector(".section-heading")],["library-integrations",$("library-create").parentElement]]){if(!$(id)){const button=document.createElement("button");button.id=id;button.className="secondary";button.textContent="Integration catalog";target.append(button);}}
document.addEventListener("click",async event=>{const button=event.target.closest("button");if(!button)return;try{
  if(["tools-integrations","library-integrations"].includes(button.id))await showIntegrationCatalog();
  if(button.dataset.integrationSetup)await configureIntegration(button.dataset.integrationSetup);
}catch(error){notify(error.message);}});

const enterpriseTypes={teams:"Microsoft Teams",bedrock:"Amazon Bedrock Agent","bedrock-kb":"Amazon Bedrock knowledge base","bedrock-flow":"Amazon Bedrock Flow",mongodb:"MongoDB",nuclia:"Nuclia",powerbi:"Power BI"};
function nucliaEvaluationFields(data){
  return `<label>Evaluation context<select name="enterprise_evaluation_context"><option value="cited" ${data.evaluation_context!=="retrieved"?"selected":""}>Cited sources only</option><option value="retrieved" ${data.evaluation_context==="retrieved"?"selected":""}>All retrieved paragraphs</option></select></label><p>When private evidence capture is enabled, the full retrieval option includes uncited text. Nothing is sent to a judge until you explicitly evaluate the saved context.</p>`;
}
function nucliaAnswerFields(data){
  const custom=Object.hasOwn(data,"answer_options"),options={output_response_as_artifact:true,output_filename_base:"nuclia-answer",artifact_description_query_max_length:150,inline_citation_links:true,include_citations_in_tool_response:false,...data.answer_options};
  const checked=key=>options[key]?"checked":"";
  return `<details class="nuclia-answer-settings" ${custom?"open":""}><summary>Answer presentation</summary><input type="hidden" name="enterprise_answer_controls" value="1"><label class="check-label"><input type="checkbox" name="enterprise_answer_customize" ${custom?"checked":""}>Customize answer presentation</label><p>Keep the original answer text and private source files. These settings change the formatted answer and optional answer file.</p><fieldset data-answer-controls><label class="check-label"><input type="checkbox" name="enterprise_answer_artifact" ${checked("output_response_as_artifact")}>Save a Markdown answer file</label>${inputField("enterprise_answer_filename","Answer filename",options.output_filename_base)}${inputField("enterprise_answer_prefix","Query characters in file description",options.artifact_description_query_max_length,"number")}<label>Citation mode<select name="enterprise_answer_citations">${[["default","Source markers"],["none","No citations"],["llm_footnotes","Model footnotes"]].map(([value,label])=>`<option value="${value}" ${value===(data.ask_settings?.citations||"default")?"selected":""}>${label}</option>`).join("")}</select></label><label class="check-label"><input type="checkbox" name="enterprise_answer_links" ${checked("inline_citation_links")}>Link inline source markers</label><label class="check-label"><input type="checkbox" name="enterprise_answer_include" ${checked("include_citations_in_tool_response")}>Include separate citation details</label><p>No citations leaves cited-only evidence empty. Model footnotes require support from the configured Nuclia model.</p></fieldset></details>`;
}
const enterpriseEditor=openLibraryEditor;
openLibraryEditor=async function(id,template){
  await enterpriseEditor(id,template);if(library.kind!=="connectors")return;
  const data=library.editing?.data||{},select=$("library-form").elements.connector_type;
  for(const [value,label] of Object.entries(enterpriseTypes)){const option=document.createElement("option");option.value=value;option.textContent=label;select.append(option);}
  const fields=document.createElement("div");fields.className="enterprise-fields";
  const field=(key,label,fallback="")=>inputField("enterprise_"+key,label,data[key]||fallback);
  fields.innerHTML=`<fieldset data-enterprise="teams"><legend>Microsoft Teams channel</legend>${field("team_id","Team ID")}${field("channel_id","Channel ID")}<p>Use a delegated work/school Microsoft Graph account. Read actions: status, query. Reviewed writes: send, update_message. Token permissions and channel membership are required.</p></fieldset><fieldset data-enterprise="aws"><legend>Amazon Bedrock</legend>${field("region","AWS region","eu-west-1")}${field("resource_id","Agent, knowledge base or flow ID")}${field("alias_id","Agent or flow alias ID")}${field("access_key_env","AWS access-key environment reference","AWS_ACCESS_KEY_ID")}${field("secret_key_env","AWS secret-key environment reference","AWS_SECRET_ACCESS_KEY")}${field("session_token_env","AWS session-token reference (optional)")}${field("input_node","Flow input node","FlowInputNode")}${field("input_name","Flow output name","document")}<p>Knowledge retrieval is read-only. Agent and flow invocations require reviewed confirmation because they may run external actions.</p></fieldset><fieldset data-enterprise="mongodb"><legend>MongoDB</legend>${field("dsn_env","MongoDB URI environment reference")}${field("database","Database")}${inputField("enterprise_collections","Allowed collections (comma separated)",(data.collections||[]).join(", "))}<label><input name="enterprise_allow_writes" type="checkbox" ${data.allow_writes?"checked":""}>Allow reviewed single-document writes</label></fieldset><fieldset data-enterprise="nuclia"><legend>Nuclia knowledge</legend>${field("url","Nuclia service base URL")}${field("knowledge_box","Knowledge box ID")}<label><input type="checkbox" name="enterprise_private" ${data.allow_private?"checked":""}>Allow private Nuclia server</label><label><input type="checkbox" name="enterprise_capture_evaluation" ${data.capture_evaluation?"checked":""}>Save private evaluation evidence</label><p>Stores each generated answer, question and selected context as a private file. Choose an AI judge separately when evaluating. Add download_original to Enabled actions to allow saving cited file originals. Copies reflect the current remote file.</p></fieldset><fieldset data-enterprise="powerbi"><legend>Power BI</legend>${field("dataset_id","Dataset ID")}${field("workspace_id","Workspace ID (optional)")}<p>Read dataset metadata and execute DAX queries against this dataset.</p></fieldset><fieldset data-enterprise="account"><legend>Service account</legend>${field("token_env","Token environment reference")}</fieldset><fieldset data-enterprise="actions"><legend>Permissions</legend>${inputField("enterprise_actions","Enabled actions (comma separated)",(data.actions||[]).join(", "))}<p>Leave empty for the connector’s default actions. Credentials stay in Nexus account settings or server environment references. API billing belongs to the selected service account.</p></fieldset>`;
  $("library-fields").append(fields);
  const nuclia=fields.querySelector('[data-enterprise="nuclia"]');nuclia.insertAdjacentHTML("beforeend",nucliaEvaluationFields(data)+nucliaAnswerFields(data));
  const customize=nuclia.querySelector('[name="enterprise_answer_customize"]'),answerFields=nuclia.querySelector('[data-answer-controls]');
  function syncAnswers(){answerFields.disabled=!customize.checked;}
  customize.addEventListener("change",syncAnswers);syncAnswers();
  const prefix=nuclia.querySelector('[name="enterprise_answer_prefix"]');prefix.min="1";prefix.max="1000";prefix.step="1";
  function sync(){const type=select.value;fields.querySelectorAll("[data-enterprise]").forEach(box=>{const key=box.dataset.enterprise;box.hidden=!(key===type||key==="aws"&&type.startsWith("bedrock")||key==="account"&&["nuclia","powerbi","teams"].includes(type)||key==="actions"&&Object.hasOwn(enterpriseTypes,type));});
    ["alias_id","input_node","input_name"].forEach(key=>{const show=key==="alias_id"?["bedrock","bedrock-flow"].includes(type):type==="bedrock-flow";fields.querySelector(`[name="enterprise_${key}"]`).closest("label").hidden=!show;});}
  select.addEventListener("change",sync);const selectedType=data.type||template?.id||template?.target;if(Object.hasOwn(enterpriseTypes,selectedType)){select.value=selectedType;select.dispatchEvent(new Event("change",{bubbles:true}));}sync();
};
const enterpriseFormData=operationFormData;
operationFormData=function(form){
  const type=form.get("connector_type");if(library.kind!=="connectors"||!Object.hasOwn(enterpriseTypes,type))return enterpriseFormData(form);
  const data={type,enabled:form.has("enabled")},read=key=>String(form.get("enterprise_"+key)||"").trim();
  const keys=type.startsWith("bedrock")?["region","resource_id","access_key_env","secret_key_env","session_token_env",...(type!=="bedrock-kb"?["alias_id"]:[]),...(type==="bedrock-flow"?["input_node","input_name"]:[])]:type==="mongodb"?["dsn_env","database"]:type==="nuclia"?["url","knowledge_box","token_env"]:type==="teams"?["team_id","channel_id","token_env"]:["dataset_id","workspace_id","token_env"];
  keys.forEach(key=>data[key]=read(key));
  if(type==="mongodb"){data.collections=read("collections").split(",").map(v=>v.trim()).filter(Boolean);data.allow_writes=form.has("enterprise_allow_writes");}
  if(type==="nuclia"){
    data.allow_private=form.has("enterprise_private");
    data.capture_evaluation=form.has("enterprise_capture_evaluation");
    const evaluationContext=read("evaluation_context"),previousEvaluation=library.editing?.data?.type==="nuclia"?library.editing.data.evaluation_context:undefined;
    if((evaluationContext&&evaluationContext!=="cited")||previousEvaluation!==undefined)data.evaluation_context=evaluationContext||previousEvaluation;
    if(library.editing?.data?.type==="nuclia"&&Object.hasOwn(library.editing.data,"template"))data.template=JSON.parse(JSON.stringify(library.editing.data.template));
    if(library.editing?.data?.type==="nuclia"&&Object.hasOwn(library.editing.data,"ask_settings"))data.ask_settings=JSON.parse(JSON.stringify(library.editing.data.ask_settings));
    if(library.editing?.data?.type==="nuclia"&&Object.hasOwn(library.editing.data,"answer_options"))data.answer_options=JSON.parse(JSON.stringify(library.editing.data.answer_options));
    if(form.has("enterprise_answer_controls")){
      if(form.has("enterprise_answer_customize")){
        data.answer_options={output_response_as_artifact:form.has("enterprise_answer_artifact"),output_filename_base:read("answer_filename"),artifact_description_query_max_length:Number(read("answer_prefix")),inline_citation_links:form.has("enterprise_answer_links"),include_citations_in_tool_response:form.has("enterprise_answer_include")};
        data.ask_settings={...data.ask_settings,citations:read("answer_citations")};
      }else delete data.answer_options;
    }
  }
  if(read("actions"))data.actions=read("actions").split(",").map(v=>v.trim()).filter(Boolean);return data;
};

let promptSuggestionCache=[];
async function refreshPromptSuggestions(){promptSuggestionCache=(await api('/api/catalog/prompts')).items.filter(p=>!p.archived);}
function attachPromptSuggestions(input){
  if(!input||input.dataset.promptSuggestions)return;input.dataset.promptSuggestions='true';
  const list=document.createElement('div');list.className='prompt-suggestions';list.id=input.id+'-suggestions';list.setAttribute('role','listbox');list.setAttribute('aria-label','Saved prompts');list.hidden=true;input.after(list);input.setAttribute('aria-controls',list.id);input.setAttribute('aria-autocomplete','list');let choices=[],active=0,match;
  function close(){list.hidden=true;input.setAttribute('aria-expanded','false');}
  function insert(index){const prompt=choices[index];if(!prompt||!match)return;const end=input.selectionStart,start=end-match[1].length-1;input.setRangeText('/'+(prompt.data.shortcut||prompt.id)+' ',start,end,'end');close();input.focus();}
  function render(){list.replaceChildren();choices.forEach((p,index)=>{const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.setAttribute('aria-selected',String(index===active));button.textContent=`/${p.data.shortcut||p.id} · ${p.name}`;button.addEventListener('click',()=>insert(index));list.append(button);});list.hidden=!choices.length;input.setAttribute('aria-expanded',String(!!choices.length));}
  input.addEventListener('input',()=>{match=input.value.slice(0,input.selectionStart).match(/(?:^|\s)\/([\w-]*)$/);if(!match){close();return;}const query=match[1].toLowerCase();choices=promptSuggestionCache.filter(p=>[p.id,p.name,p.data.shortcut||'',...(p.data.tags||[])].some(v=>v.toLowerCase().includes(query))).slice(0,8);active=0;render();});
  input.addEventListener('keydown',event=>{if(list.hidden||event.shiftKey)return;if(['ArrowDown','ArrowUp','Enter','Escape'].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();if(event.key==='Escape')close();else if(event.key==='Enter')insert(active);else{active=(active+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length;render();}}},true);
  input.addEventListener('blur',()=>setTimeout(()=>{if(!list.contains(document.activeElement))close();},150));
}
function setChatOptionsExpanded(expanded){
  const button=$('mobile-chat-options');
  if(!button)return;
  button.setAttribute('aria-expanded',String(expanded));
  button.textContent=expanded?'Close settings':'Settings';
  $('chat-configuration').hidden=!expanded;
}
function initializeFeatureControls(){
  refreshPromptSuggestions().catch(error=>notify(error.message));['chat-task','builder-task'].forEach(id=>attachPromptSuggestions($(id)));
  if($('mobile-chat-options'))return;
  const controls=$('chat-project').closest('.chat-controls'),actions=document.createElement('div');actions.className='chat-extra-actions';
  const button=document.createElement('button');button.id='mobile-chat-options';button.type='button';button.className='secondary mobile-chat-options';button.setAttribute('aria-controls','chat-configuration');button.onclick=()=>setChatOptionsExpanded(button.getAttribute('aria-expanded')!=='true');
  actions.append(button);['share-conversation','record-voice'].forEach(id=>{if($(id))actions.append($(id));});controls.prepend(actions);
  const mobile=matchMedia('(max-width: 767px)');
  const sync=()=>{if(mobile.matches&&$('chat-configuration').contains(document.activeElement))button.focus();setChatOptionsExpanded(!mobile.matches||document.body.classList.contains('single-agent'));};
  mobile.addEventListener('change',sync);sync();
  controls.addEventListener('keydown',event=>{if(event.key==='Escape'&&mobile.matches&&!document.body.classList.contains('single-agent')){event.preventDefault();setChatOptionsExpanded(false);button.focus();}});
  $('chat-form').addEventListener('submit',()=>{if(mobile.matches&&!document.body.classList.contains('single-agent'))setChatOptionsExpanded(false);});
}
if(state.ready)initializeFeatureControls();else window.addEventListener('studio-ready',initializeFeatureControls);
window.addEventListener('studio-resource-saved',event=>{if(event.detail.kind==='prompts')refreshPromptSuggestions().catch(error=>notify(error.message));});

const calendarEditor=openLibraryEditor;
openLibraryEditor=async function(id,template){
  await calendarEditor(id,template);if(library.kind!=="schedules")return;
  const form=$('library-form'),cron=form.elements.cron;
  const sync=()=>{if(form.elements.run_at)form.elements.run_at.required=!cron.value.trim();};cron.addEventListener('input',sync);sync();
};

const baseConnectionFieldVisibility=updateConnectionFields;
updateConnectionFields=function(){
  baseConnectionFieldVisibility();const form=$('library-form'),type=form.elements.connector_type?.value;if(!type)return;
  const privateField=form.elements.allow_private?.closest('label');if(privateField)privateField.hidden=!["http","openapi"].includes(type)&&!(type==="mcp"&&form.elements.mcp_transport?.value!=="stdio");
  const notes=$('library-fields').querySelectorAll(':scope > p.callout');
  if(notes[0])notes[0].hidden=["sql","openapi",...Object.keys(enterpriseTypes)].includes(type);
  if(notes[1])notes[1].hidden=!["http","sqlite"].includes(type);
};
document.addEventListener('change',event=>{if(event.target.name==='mcp_transport')updateConnectionFields();});

function interopDiagnostics(report){return `<p role="alert">Resolve these settings before continuing. Nothing was saved.</p><ul>${report.diagnostics.map(item=>`<li><code>${escapeHtml(item.path)}</code>: ${escapeHtml(item.message)}</li>`).join("")}</ul>`;}
function interopAdaptations(report){return (report.adaptations||[]).map(item=>`<section><h3>Nuclia import changes to review</h3><p>${escapeHtml(item.policy)}</p><ul>${item.changes.map(change=>`<li>${escapeHtml(change)}</li>`).join("")}</ul></section>`).join("");}
async function importPortableConfiguration(draft={}){
  const form=await fieldsDialog("Import external agents and workflows",`<p>Paste external YAML, JSON or an exported bundle. Conversion runs locally and checks every field before review.</p><label>External configuration<textarea name="source" rows="10" maxlength="1048576" required>${escapeHtml(draft.source||"")}</textarea></label><label>Provider<select name="provider"><option value="">Use mappings included in the bundle</option>${state.providers.map(p=>`<option value="${escapeHtml(p.id)}" ${draft.provider===p.id?"selected":""}>${escapeHtml(p.name)} · ${p.kind==="cli"?"local CLI account":"API billing"}</option>`).join("")}</select></label><label>External model alias<input name="alias" value="${escapeHtml(draft.alias||"general")}" maxlength="200"></label><label>External agent name (runtime YAML only)<input name="agent" value="${escapeHtml(draft.agent||"")}" maxlength="200"></label><details><summary>Tool mappings</summary><p>Map external builtin tool names to registered Nexus tool IDs. Remote connections need separate setup.</p><label>Tool mappings (JSON)<textarea name="tools" rows="3">${escapeHtml(draft.tools||"{}")}</textarea></label></details><details><summary>Decision model mappings</summary><p>Bind each external decision alias to a Nexus decision backend and an explicit model. Uses server-managed credentials; decision calls use API billing.</p><label>Decision model mappings (JSON)<textarea name="decisions" rows="3" maxlength="65536" placeholder='{"triage":{"backend":"openrouter","model":"your-selected-model"}}'>${escapeHtml(draft.decisions||"{}")}</textarea></label></details><details><summary>Resource mappings</summary><p>Bind existing model profiles, skills, toolsets and MCP connections. Conversion never reads skill paths or launches servers.</p><label>Resource mappings (JSON)<textarea name="resources" rows="4" maxlength="65536" placeholder='{"skills":{"source-name":"saved-skill"},"toolsets":{"group-name":"saved-toolset"}}'>${escapeHtml(draft.resources||"{}")}</textarea></label></details><p>Import does not sign in, call a provider, run tools or activate resources. API execution can incur charges after you run a task.</p>`,"Check configuration");
  if(!form)return;
  const edit=()=>{$("detail").close();importPortableConfiguration(Object.fromEntries(form)).catch(error=>notify(error.message));};
  const payload={text:form.get("source")};let tools,decisions,resources;
  try{tools=JSON.parse(form.get("tools"));if(!tools||typeof tools!=="object"||Array.isArray(tools))throw new Error();}
  catch(error){showDetail("Tool mappings need changes",'<p role="alert">Use a JSON object mapping each external tool name to a Nexus tool ID.</p><button id="interop-edit">Edit configuration</button>');$("interop-edit").onclick=edit;return;}
  try{decisions=JSON.parse(form.get("decisions"));if(!decisions||typeof decisions!=="object"||Array.isArray(decisions))throw new Error();}
  catch(error){showDetail("Decision model mappings need changes",'<p role="alert">Use a JSON object mapping each external decision alias to its backend and model. Do not enter credentials here.</p><button id="interop-edit">Edit configuration</button>');$("interop-edit").onclick=edit;return;}
  try{resources=JSON.parse(form.get("resources")||"{}");if(!resources||typeof resources!=="object"||Array.isArray(resources)||Object.keys(resources).some(key=>!["models","agents","skills","toolsets","connectors","nuclia"].includes(key)))throw new Error();}
  catch(error){showDetail("Resource mappings need changes",'<p role="alert">Use an object containing explicit model, agent, skill, toolset or connector mappings. Keep credentials in server settings.</p><button id="interop-edit">Edit configuration</button>');$("interop-edit").onclick=edit;return;}
  if(form.get("provider")||Object.keys(tools).length||Object.keys(decisions).length||Object.keys(resources).length){
    payload.bindings={...resources,tools};
    if(Object.keys(decisions).length)payload.bindings.decisions=decisions;
    if(form.get("provider")){
      payload.bindings.models={...(resources.models||{}),[form.get("alias")]:{provider:form.get("provider")}};
      if(form.get("agent"))payload.bindings.agents={...(resources.agents||{}),[form.get("agent")]:{provider:form.get("provider")}};
    }
  }
  const report=await featureApi("configuration/interop/convert",payload);
  if(!report.valid){showDetail("External conversion needs changes",interopDiagnostics(report)+'<button id="interop-edit">Edit configuration</button>');$("interop-edit").onclick=edit;return;}
  showDetail("Review external conversion",`<p>${report.document.definitions.length} agents/workflows passed conversion. Review before saving.</p><ul>${report.document.definitions.map(item=>`<li>${escapeHtml(item.document.metadata.name)} · ${item.collection==="agents"?escapeHtml(providerName(item.document.spec.provider)):"workflow"}</li>`).join("")}</ul>${interopAdaptations(report)}<details><summary>Converted configuration</summary><pre>${escapeHtml(JSON.stringify(report.document,null,2))}</pre></details><button id="interop-plan">Review changes</button>`);
  $("interop-plan").onclick=async()=>{try{const plan=await featureApi("configuration/plan",{document:report.document});showDetail("Save reviewed external configuration",`<p>These local definitions will be saved. This action does not execute or deploy them.</p><ul>${plan.changes.map(item=>`<li>${escapeHtml(item.action)} · ${escapeHtml(item.resource)}</li>`).join("")}</ul><details><summary>Reviewed configuration</summary><pre>${escapeHtml(JSON.stringify(plan.document,null,2))}</pre></details><button id="interop-apply">Save reviewed configuration</button>`);$("interop-apply").onclick=async()=>{const button=$("interop-apply");button.disabled=true;try{await featureApi("configuration/apply",{plan_id:plan.id,digest:plan.digest});$("detail").close();await refreshDefinitions();notify("Agents and workflows saved. Review provider setup before running them.");}catch(error){notify(error.message);}finally{button.disabled=false;}};}catch(error){notify(error.message);}};
}
async function exportPortableConfiguration(){
  const document={format:"nexus-studio-config/v1",definitions:[...state.agents.map(item=>["agents",item]),...state.workflows.map(item=>["workflows",item])].filter(([,item])=>item.document).map(([collection,item])=>({collection,id:item.id,document:item.document})),resources:[]};
  const report=await featureApi("configuration/interop/export",{document});
  if(!report.valid){showDetail("External export needs changes",interopDiagnostics(report));return;}
  showDetail("External agents and workflows export",`<p>The bundle contains external resource documents plus explicit Nexus provider/tool mappings. Review before downloading. Other workspace resources use native export.</p><pre>${escapeHtml(JSON.stringify(report.bundle,null,2))}</pre><button id="interop-download">Download external bundle</button>`);$("interop-download").onclick=()=>downloadJson("interop-agents-workflows.json",report.bundle);
}
document.addEventListener("click",async event=>{const button=event.target.closest("button");if(!button)return;try{if(button.id==="import-interop")await importPortableConfiguration();if(button.id==="export-interop")await exportPortableConfiguration();}catch(error){notify(error.message);}});

function openAPIHealthOptions(spec,selected){
 const enabled=new Set(selected),result=[];
 const local=(value,seen=[])=>{
  if(!value||typeof value!=="object")return null;
  if(!Object.hasOwn(value,"$ref"))return value;
  const ref=value.$ref;if(typeof ref!=="string"||!ref.startsWith("#/")||seen.includes(ref)||seen.length>=20)return null;
  let target=spec;for(const part of ref.slice(2).split("/")){const key=part.replaceAll("~1","/").replaceAll("~0","~");if(!target||!Object.hasOwn(target,key))return null;target=target[key];}
  const resolved=local(target,[...seen,ref]);return resolved?{...resolved,...Object.fromEntries(Object.entries(value).filter(([key])=>key!=="$ref"))}:null;
 };
 for(const [path,value] of Object.entries(spec.paths||{})){
  const entry=local(value);if(!entry||/\{[^}]+\}/.test(path))continue;
  for(const method of ["get","head","options"]){
   const operation=local(entry[method]);if(!operation)continue;
   const id=operation.operationId||`${method}:${path}`,parameters=new Map();let valid=true;
   for(const group of [entry.parameters||[],operation.parameters||[]]){if(!Array.isArray(group)){valid=false;break;}for(const raw of group){const parameter=local(raw);if(!parameter){valid=false;break;}parameters.set(`${parameter.in}:${parameter.in==="header"?String(parameter.name).toLowerCase():parameter.name}`,parameter);}}
   const body=operation.requestBody?local(operation.requestBody):null;
   if(valid&&enabled.has(id)&&(!body||!Object.keys(body).length)&&![...parameters.values()].some(p=>p.required||p.in==="path"))result.push({id,label:`${method.toUpperCase()} ${path}`});
  }
 }
 return result;
}

// Delegated MCP accounts use the current Nexus identity; no token enters the form.
const delegatedConnectorEditor=openLibraryEditor;
openLibraryEditor=async function(id,template){
  await delegatedConnectorEditor(id,template);if(library.kind!=="connectors")return;
  const form=$("library-form"),data=library.editing?.data||{},auth=data.auth||{};
  const option=document.createElement("option");option.value="oauth";option.textContent="Sign in with provider (OAuth)";form.elements.remote_auth.append(option);
  if(auth.type==="oauth")form.elements.remote_auth.value="oauth";
  const panel=document.createElement("fieldset");panel.className="oauth-account";panel.innerHTML=`<legend>Delegated account</legend><p>Register a client with your provider first. Callback address: <code>${escapeHtml(location.origin+"/api/studio/oauth/callback")}</code>. Account stays private to your Nexus user. API usage may be billed separately from coding subscriptions.</p>${inputField("oauth_issuer","Authorization issuer URL",auth.issuer||"")}${inputField("oauth_client","Registered client ID",auth.client_id||"")}${inputField("oauth_scopes","Requested scopes (separated by spaces)",(auth.scopes||[]).join(" "))}${inputField("oauth_resource","Resource URI (defaults to service URL)",auth.resource||"")}${inputField("oauth_secret","Client secret environment reference (optional)",auth.client_secret_env||"")}${inputField("oauth_origins","Additional endpoint origins (separated by spaces)",(auth.endpoint_origins||[]).join(" "))}<p data-oauth-status role="status">Save connector settings before signing in.</p>`;
  $("library-fields").append(panel);
  const sync=()=>{const remote=form.elements.connector_type.value==="mcp"&&form.elements.mcp_transport.value!=="stdio";option.disabled=!remote;panel.hidden=!remote||form.elements.remote_auth.value!=="oauth";for(const name of ["remote_token","remote_header","remote_parameter","remote_user","remote_password"])form.elements[name].closest("label").hidden=form.elements.remote_auth.value==="oauth";};
  for(const field of [form.elements.connector_type,form.elements.mcp_transport,form.elements.remote_auth])field.addEventListener("change",sync);sync();
  let changed=false;panel.addEventListener("input",()=>changed=true);for(const name of ["remote_url","remote_auth","mcp_transport","allow_private"])form.elements[name]?.addEventListener("change",()=>changed=true);
  if(!id)return;
  const status=panel.querySelector("[data-oauth-status]"),actions=document.createElement("div");actions.className="button-row";panel.append(actions);
  const show=async()=>{const result=await featureApi(`connectors/${encodeURIComponent(id)}/oauth`);status.textContent=`Your account: ${result.status.replaceAll("_"," ")}.`;if(result.connected)panel.querySelector("[data-oauth-link]")?.remove();};
  for(const [label,action] of [["Sign in","start"],["Refresh status","status"],["Disconnect my account","disconnect"]]){const button=document.createElement("button");button.type="button";button.textContent=label;actions.append(button);button.onclick=async()=>{button.disabled=true;try{if(action==="status")await show();else{if(action==="start"&&changed)throw new Error("Save settings and reopen this connector before signing in.");const result=await featureApi(`connectors/${encodeURIComponent(id)}/oauth/${action}`,{});panel.querySelector("[data-oauth-link]")?.remove();if(action==="start"){const link=document.createElement("a");link.dataset.oauthLink="true";link.href=result.authorization_url;link.target="_blank";link.rel="noopener noreferrer";link.textContent="Continue provider sign-in";panel.append(link);status.textContent="Sign-in ready. Follow link, then refresh status here.";}else await show();}}catch(error){status.textContent=error.message;}finally{button.disabled=false;}};}
  if(auth.type==="oauth")try{await show();}catch(error){status.textContent=error.message;}
};
const delegatedConnectorFormData=operationFormData;
operationFormData=function(form){
  const data=delegatedConnectorFormData(form);
  if(library.kind==="connectors"&&data.type==="mcp"&&data.transport!=="stdio"&&form.get("remote_auth")==="oauth")data.auth={type:"oauth",issuer:form.get("oauth_issuer"),client_id:form.get("oauth_client"),scopes:String(form.get("oauth_scopes")||"").trim().split(/\s+/).filter(Boolean),resource:form.get("oauth_resource")||undefined,client_secret_env:form.get("oauth_secret")||undefined,endpoint_origins:String(form.get("oauth_origins")||"").trim().split(/\s+/).filter(Boolean)};
  return data;
};

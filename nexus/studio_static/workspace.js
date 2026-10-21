"use strict";
async function refreshWorkspace(){
  try{
    const status=await api("/api/mesh");$("workspace-error").textContent="";
    $("workspace-runtime").innerHTML=`<p class="callout">${escapeHtml(status.workspace)}<br>${escapeHtml(status.access)} · ${escapeHtml(status.transport)} · ${status.workers} execution workers</p><dl class="workspace-statistics"><div><dt>Queued</dt><dd>${status.queued}</dd></div><div><dt>Running</dt><dd>${status.running}</dd></div><div><dt>Awaiting approval</dt><dd>${status.waiting}</dd></div></dl><p class="workspace-scope">Counts cover ${escapeHtml(status.scope)}. Event transport carries status metadata; ${status.durable_jobs?"Jobs persist in "+escapeHtml(status.state_backend)+" and execute on configured workers.":"Workspace tasks execute on this machine."}</p>${status.event_error?`<p class="form-error">Event delivery error: ${escapeHtml(status.event_error)}</p>`:""}`;
    $("workspace-events").innerHTML=status.events.length?status.events.map(event=>`<article class="activity-row"><div class="activity-copy"><h2>${escapeHtml(event.kind)} · ${escapeHtml(event.run_id)}</h2><p>${escapeHtml(new Date(event.updated_at).toLocaleString())} · ${escapeHtml(event.source)}</p></div>${badge(event.status,event.status)}<button class="secondary" data-run="${escapeHtml(event.run_id)}">Details</button></article>`).join(""):'<p>Execution events appear when tasks change status.</p>';
  }catch(error){$("workspace-error").textContent=error.message;}
}
async function refreshModelChoices(){
  try{
    const artifacts=(await api("/api/catalog/artifacts")).items;const files=$("chat-artifacts");const chosen=[...files.selectedOptions].map(item=>item.value);files.innerHTML=artifacts.map(item=>`<option value="${escapeHtml(item.id)}" data-size="${Number(item.data.size)||0}" ${chosen.includes(item.id)?"selected":""}>${escapeHtml(item.name)} · ${escapeHtml(item.data.filename)}</option>`).join("");
    const models=(await api("/api/catalog/models")).items.filter(item=>!item.archived);
    for(const [section,providerId,modelId] of [[".chat-controls","chat-provider","chat-model"],[".builder-provider-fields","builder-provider","builder-model"]]){
      const parent=document.querySelector(section);let select=parent.querySelector(".model-profile");
      if(!select){const label=document.createElement("label");label.textContent="Profile";select=document.createElement("select");select.className="model-profile";select.setAttribute("aria-label",providerId==="chat-provider"?"Chat model profile":"Build model profile");label.appendChild(select);parent.appendChild(label);}
      const selected=select.value;select.innerHTML='<option value="">Custom configuration</option>'+models.filter(item=>providerId!=="builder-provider"||["codex","claude-code","openai","claude"].includes(item.data.provider)).map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");select.value=selected;
      select.onchange=()=>{const item=models.find(item=>item.id===select.value);if(item){$(providerId).value=item.data.provider;$(modelId).value=item.data.model;}};
    }
  }catch(error){notify(error.message);}
}
$("refresh-workspace").addEventListener("click",refreshWorkspace);
window.addEventListener("hashchange",()=>{if(view()==="workspace")refreshWorkspace();refreshModelChoices();});
async function initializeWorkspace(){await refreshModelChoices();if(view()==="workspace")await refreshWorkspace();}
if(state.ready)initializeWorkspace();else window.addEventListener("studio-ready",initializeWorkspace,{once:true});

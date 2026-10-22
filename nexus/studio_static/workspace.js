"use strict";
function formatBytes(bytes){
  const n=Number(bytes);
  if(!n||isNaN(n)||n<=0)return "0 B";
  if(n<1024)return `${n} B`;
  if(n<1024*1024)return `${(n/1024).toFixed(1)} KB`;
  return `${(n/(1024*1024)).toFixed(1)} MB`;
}

function getFileCategory(filename=""){
  const ext=String(filename||"").split(".").pop().toLowerCase();
  if(["png","jpg","jpeg","gif","webp","svg","bmp"].includes(ext))return "image";
  if(["pdf"].includes(ext))return "pdf";
  if(["py","js","ts","jsx","tsx","json","yaml","yml","sh","html","css","sql"].includes(ext))return "code";
  return "text";
}

function fileIconSvg(cat){
  if(cat==="image")return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
  if(cat==="pdf")return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M10 12v6"/><path d="M14 12v6"/></svg>';
  if(cat==="code")return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
  return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
}

function renderChatArtifacts(){
  const select=$("chat-artifacts");if(!select)return;
  const attachedSection=$("chat-attached-chips"),gallery=$("chat-artifacts-gallery");
  const countEl=$("chat-attached-count"),pillEl=$("chat-attachment-pill"),totalEl=$("chat-artifacts-total");
  const searchInput=$("chat-artifacts-search"),query=(searchInput?.value||"").toLowerCase().trim();
  const options=Array.from(select.options||[]);
  const selectedOptions=options.filter(opt=>opt.selected);
  const selectedCount=selectedOptions.length;
  if(countEl)countEl.textContent=`${selectedCount} / 5`;
  if(pillEl){
    if(selectedCount>0){pillEl.textContent=`${selectedCount} attached`;pillEl.hidden=false;}
    else pillEl.hidden=true;
  }
  if(totalEl)totalEl.textContent=`${options.length} available`;
  if(attachedSection){
    if(!selectedCount){
      attachedSection.innerHTML='<p class="chat-attached-empty">No files attached yet. Drop files above or select from workspace artifacts below.</p>';
    }else{
      attachedSection.innerHTML=selectedOptions.map(opt=>{
        const id=opt.value,text=opt.textContent||"",parts=text.split(" · ");
        const title=parts[0]||id,filename=parts[1]||parts[0]||id,size=Number(opt.dataset.size)||0;
        const cat=getFileCategory(filename);
        return `<div class="chat-file-chip" data-artifact-id="${escapeHtml(id)}"><span class="chat-chip-icon cat-${cat}">${fileIconSvg(cat)}</span><div class="chat-chip-meta"><span class="chat-chip-name" title="${escapeHtml(filename)}">${escapeHtml(filename)}</span><span class="chat-chip-size">${formatBytes(size)}</span></div><button type="button" class="chat-chip-remove" data-remove-artifact="${escapeHtml(id)}" aria-label="Remove ${escapeHtml(filename)}">×</button></div>`;
      }).join("");
    }
  }
  if(gallery){
    if(!options.length){
      gallery.innerHTML='<p class="chat-artifacts-empty">No saved workspace files yet. Upload a file above to attach it.</p>';
    }else{
      const filtered=options.filter(opt=>{
        if(!query)return true;
        return (opt.textContent||"").toLowerCase().includes(query);
      });
      if(!filtered.length){
        gallery.innerHTML=`<p class="chat-artifacts-empty">No artifacts matching “${escapeHtml(query)}”.</p>`;
      }else{
        gallery.innerHTML=filtered.map(opt=>{
          const id=opt.value,isSelected=opt.selected,text=opt.textContent||"",parts=text.split(" · ");
          const title=parts[0]||id,filename=parts[1]||parts[0]||id,size=Number(opt.dataset.size)||0;
          const cat=getFileCategory(filename);
          return `<button type="button" role="option" aria-selected="${isSelected}" class="chat-artifact-card ${isSelected?'is-selected':''}" data-toggle-artifact="${escapeHtml(id)}"><div class="chat-card-checkbox" aria-hidden="true">${isSelected?'✓':''}</div><span class="chat-card-icon cat-${cat}">${fileIconSvg(cat)}</span><div class="chat-card-content"><span class="chat-card-title">${escapeHtml(title)}</span><span class="chat-card-sub">${escapeHtml(filename)} · ${formatBytes(size)}</span></div><span class="chat-card-badge">${isSelected?'Attached':'Attach'}</span></button>`;
        }).join("");
      }
    }
  }
}

let chatAttachmentEventsBound=false;
function setupChatAttachmentEvents(){
  if(chatAttachmentEventsBound)return;
  chatAttachmentEventsBound=true;
  const dropzone=$("chat-dropzone");
  if(dropzone){
    ["dragenter","dragover"].forEach(name=>{
      dropzone.addEventListener(name,e=>{e.preventDefault();dropzone.classList.add("is-dragover");});
    });
    ["dragleave","drop"].forEach(name=>{
      dropzone.addEventListener(name,e=>{e.preventDefault();dropzone.classList.remove("is-dragover");});
    });
    dropzone.addEventListener("drop",async e=>{
      e.preventDefault();
      dropzone.classList.remove("is-dragover");
      const dt=e.dataTransfer;
      if(dt&&dt.files&&dt.files.length){
        await attachUploadedFiles({files:dt.files},"chat-artifacts","chat-upload-status",refreshModelChoices);
        renderChatArtifacts();
      }
    });
  }
  $("chat-artifacts-search")?.addEventListener("input",()=>renderChatArtifacts());
  $("chat-attachments-details")?.addEventListener("click",e=>{
    const removeBtn=e.target.closest("[data-remove-artifact]");
    if(removeBtn){
      e.preventDefault();e.stopPropagation();
      const id=removeBtn.dataset.removeArtifact,select=$("chat-artifacts");
      if(select){
        for(const opt of select.options)if(opt.value===id)opt.selected=false;
        select.dispatchEvent(typeof Event!=="undefined"?new Event("change"):{type:"change"});
        renderChatArtifacts();
      }
      return;
    }
    const card=e.target.closest("[data-toggle-artifact]");
    if(card){
      e.preventDefault();
      const id=card.dataset.toggleArtifact,select=$("chat-artifacts");
      if(select){
        const opt=[...select.options].find(o=>o.value===id);
        if(opt){
          if(!opt.selected){
            if([...select.selectedOptions].length>=5){notify("Attach at most five files.");return;}
            opt.selected=true;
          }else{
            opt.selected=false;
          }
          select.dispatchEvent(typeof Event!=="undefined"?new Event("change"):{type:"change"});
          renderChatArtifacts();
        }
      }
    }
  });
  $("chat-artifacts")?.addEventListener("change",()=>renderChatArtifacts());
}

if(typeof window!=="undefined"){
  window.renderChatArtifacts=renderChatArtifacts;
}

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
    renderChatArtifacts();
    const models=(await api("/api/catalog/models")).items.filter(item=>!item.archived);
    for(const [section,providerId,modelId] of [[".chat-controls","chat-provider","chat-model"],[".builder-provider-fields","builder-provider","builder-model"]]){
      const parent=document.querySelector(section);let select=parent.querySelector(".model-profile");
      if(!select){const label=document.createElement("label");label.textContent="Profile";select=document.createElement("select");select.className="model-profile";select.setAttribute("aria-label",providerId==="chat-provider"?"Chat model profile":"Build model profile");label.appendChild(select);parent.appendChild(label);}
      const selected=select.value;select.innerHTML='<option value="">Custom configuration</option>'+models.filter(item=>providerId!=="builder-provider"||["codex","claude-code","agy","openai","claude"].includes(item.data.provider)).map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");select.value=selected;
      select.onchange=()=>{const item=models.find(item=>item.id===select.value);if(item){$(providerId).value=item.data.provider;$(modelId).value=item.data.model;}};
    }
  }catch(error){notify(error.message);}
}
$("refresh-workspace").addEventListener("click",refreshWorkspace);
window.addEventListener("hashchange",()=>{if(view()==="workspace")refreshWorkspace();refreshModelChoices();});
async function initializeWorkspace(){setupChatAttachmentEvents();await refreshModelChoices();if(view()==="workspace")await refreshWorkspace();}
if(state.ready)initializeWorkspace();else window.addEventListener("studio-ready",initializeWorkspace,{once:true});

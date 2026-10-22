"use strict";
const serverFiles={root:"files",path:"",selection:null,generation:0};
function fileQuery(root,path){return `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`;}
function fileParent(path){return path.split("/").slice(0,-1).join("/");}
function workspaceFileRows(items){return items.map((item,index)=>`<article class="library-row"><div><h2>${escapeHtml(item.name)}</h2><p>${item.kind==="directory"?"Folder":`${Number(item.size)} bytes`}</p></div><button type="button" class="secondary" data-server-file="${index}">${item.kind==="directory"?"Open folder":"Preview"}</button></article>`).join("")||"<p>No exposed files in this folder.</p>";}
async function refreshServerFiles(){
  const generation=++serverFiles.generation;$("files-error").textContent="";$("files-preview").hidden=true;serverFiles.selection=null;
  try{
    const data=await featureApi(`files?${fileQuery(serverFiles.root,serverFiles.path)}`);
    if(generation!==serverFiles.generation)return;
    $("files-root").innerHTML=data.roots.map(root=>`<option value="${escapeHtml(root.id)}" ${root.id===data.root?"selected":""}>${escapeHtml(root.name)}${root.writable?"":" (read-only)"}</option>`).join("");
    $("files-path").textContent=`${data.root}/${data.path}`;$("files-parent").disabled=!data.path;
    $("files-upload-form").hidden=!data.writable;
    $("files-list").innerHTML=workspaceFileRows(data.items)+(data.truncated?"<p>Listing limited. Choose a more specific subfolder.</p>":"");
    $("files-list").querySelectorAll("[data-server-file]").forEach(button=>button.onclick=async()=>{
      const item=data.items[Number(button.dataset.serverFile)];
      if(item.kind==="directory"){serverFiles.path=item.path;await refreshServerFiles();}
      else await previewServerFile(data.root,item.path);
    });
  }catch(error){if(generation!==serverFiles.generation)return;$("files-list").replaceChildren();$("files-error").textContent=error.message;$("files-upload-form").hidden=true;}
}
async function previewServerFile(root,path){
  const generation=++serverFiles.generation;
  try{
    const file=await featureApi(`files/preview?${fileQuery(root,path)}`);
    if(generation!==serverFiles.generation)return;
    serverFiles.selection=file;
    const panel=$("files-preview");panel.hidden=false;
    panel.innerHTML=`<h2>${escapeHtml(path)}</h2><p>${Number(file.size)} bytes${file.truncated?" · Preview truncated":""}</p><pre class="workspace-file-text"></pre><div class="card-actions"><button type="button" id="files-save-copy">Save private copy</button>${file.edit_view?`<a class="button-link" href="${escapeHtml(file.edit_view)}">Open validated editor</a>`:""}</div><p>Saving a copy uses private Library artifact storage. It does not run an agent or send content to AI.</p><div id="files-copy-actions"></div>`;
    panel.querySelector("pre").textContent=file.text===null?"Binary file. Save a private copy to preview or download.":file.text;
    $("files-save-copy").onclick=async()=>{
      const button=$("files-save-copy");button.disabled=true;$("files-error").textContent="";
      try{
        const saved=await featureApi("files/snapshot",{root,path,revision:file.revision});
        if(generation!==serverFiles.generation)return;
        const actions=$("files-copy-actions");actions.innerHTML=`<p>Private copy saved in Library → Artifacts.</p><div class="card-actions"><a class="button-link" download href="${escapeHtml(saved.download_url)}">Download copy</a><button type="button" class="secondary" id="files-attach-copy">Attach to next chat</button><a class="button-link" href="#projects">Add through Knowledge projects</a></div>`;
        $("files-attach-copy").onclick=async()=>{
          if(state.busy){notify("Let the current conversation finish before attaching this file.");return;}
          await refreshModelChoices();const select=$("chat-artifacts");
          if([...select.selectedOptions].length>=5){notify("Attach at most five files.");return;}
          const option=[...select.options].find(option=>option.value===saved.artifact_id);
          if(!option)throw new Error("Saved copy is unavailable; refresh Library artifacts.");
          option.selected=true;location.hash="chat";notify("Private copy attached. Sent only when you submit the next message.");
        };
      }catch(error){$("files-error").textContent=error.message;}finally{button.disabled=false;}
    };
  }catch(error){if(generation===serverFiles.generation)$("files-error").textContent=error.message;}
}
async function fileBase64(file){
  if(file.size>16*1024*1024)throw new Error("Upload at most 16 MiB.");
  const bytes=new Uint8Array(await file.arrayBuffer());let binary="";
  for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
  return btoa(binary);
}
$("files-root").addEventListener("change",()=>{serverFiles.root=$("files-root").value;serverFiles.path="";refreshServerFiles();});
$("files-parent").addEventListener("click",()=>{serverFiles.path=fileParent(serverFiles.path);refreshServerFiles();});
$("files-refresh").addEventListener("click",refreshServerFiles);
$("files-upload-form").addEventListener("submit",async event=>{
  event.preventDefault();const file=$("files-upload").files[0];if(!file)return;
  const button=$("files-upload-button");button.disabled=true;const root=serverFiles.root,path=serverFiles.path;
  try{await featureApi("files/upload",{root,path,filename:file.name,content_base64:await fileBase64(file)});$("files-upload").value="";await refreshServerFiles();}
  catch(error){$("files-error").textContent=error.message;}finally{button.disabled=false;}
});
$("files-new-folder").addEventListener("click",async()=>{
  const root=serverFiles.root,path=serverFiles.path;
  const form=await fieldsDialog("New server folder",inputField("name","Folder name",""),"Create folder");if(!form)return;
  try{await featureApi("files/folder",{root,path,name:form.get("name")});await refreshServerFiles();}catch(error){$("files-error").textContent=error.message;}
});
function renderStudioHelp(data){
  $("studio-help-answer").innerHTML=`<p class="workspace-scope">${escapeHtml(data.notice)}</p><div class="workspace-help-text"></div><p>Server workspace: <code>${escapeHtml(data.workspace.path)}</code></p><div class="card-actions">${data.sources.map(source=>`<a class="button-link" href="${escapeHtml(source.url)}">${escapeHtml(source.title)}</a>`).join("")}</div>${Object.entries(data.resources).map(([kind,items])=>`<h2>${kind==="agents"?"Agents":"Workflows"}</h2>${items.length?items.map(item=>`<p><a href="${escapeHtml(item.url)}">${escapeHtml(item.name)}</a> · <code>${escapeHtml(item.id)}</code></p>`).join(""):"<p>None saved yet.</p>"}`).join("")}`;
  $("studio-help-answer").querySelector(".workspace-help-text").textContent=data.answer;
}
$("studio-help-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=$("studio-help-local");button.disabled=true;$("studio-help-error").textContent="";
  try{renderStudioHelp(await featureApi("assistant/answer",{question:$("studio-help-question").value}));}
  catch(error){$("studio-help-error").textContent=error.message;}finally{button.disabled=false;}
});
$("studio-help-ai").addEventListener("click",async()=>{
  const question=$("studio-help-question").value.trim();if(!question){$("studio-help-question").reportValidity();return;}
  const button=$("studio-help-ai");button.disabled=true;$("studio-help-error").textContent="";
  try{
    const run=await api("/api/runs",{kind:"chat",studio_assistant:true,provider:$("studio-help-provider").value,task:question});
    $("studio-help-answer").innerHTML=`<p>AI question submitted. Reopening Activity never resubmits it.</p><button type="button" class="secondary" data-run="${escapeHtml(run.id)}">View answer in Activity</button>`;
    pollRun(run.id,updated=>{
      if(updated.status==="completed")$("studio-help-answer").textContent=updated.output;
      else if(!["queued","running"].includes(updated.status))$("studio-help-error").textContent=updated.error||`Run ${run.id}: ${updated.status}. Review it in Activity.`;
    }).catch(error=>{$("studio-help-error").textContent=error.message;});
  }catch(error){$("studio-help-error").textContent=error.message;}finally{button.disabled=false;}
});
async function refreshWorkspaceAccess(){
  if(!state.ready)return;
  if(view()==="files")await refreshServerFiles();
  if(view()==="assistant"){
    const select=$("studio-help-provider"),selected=select.value;select.innerHTML=state.providers.map(provider=>`<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.name)}</option>`).join("");if(selected)select.value=selected;
    if(!$("studio-help-answer").textContent){try{renderStudioHelp(await featureApi("assistant"));}catch(error){$("studio-help-error").textContent=error.message;}}
  }
}
window.addEventListener("hashchange",()=>{serverFiles.generation++;refreshWorkspaceAccess();});
window.addEventListener("studio-ready",refreshWorkspaceAccess);
if(state.ready)refreshWorkspaceAccess();

"use strict";
const library = {kind:"prompts",items:[],editing:null};
const operationKinds=["externalagents","evaluations","datasets","evaluators","experiments","schedules","connectors","entrypoints","models","decisionmodels"];
const libraryNames={experiments:"experiment",evaluators:"evaluator",datasets:"dataset",externalagents:"external agent",models:"model profile",decisionmodels:"decision model",evaluations:"evaluation suite",schedules:"schedule",connectors:"connection",entrypoints:"entrypoint",prompts:"prompt",skills:"skill",artifacts:"artifact",toolsets:"toolset"};
const libraryDescriptions={experiments:"Save repeatable comparisons against a pinned evaluation suite revision. Each variant must meet its pass threshold. Review planned case counts before explicitly running; repetitions can invoke workflow tools and account usage.",evaluators:"Save reusable AI grading instructions with an explicit provider and threshold. Optional choice labels map to fixed scores. Apply to draft cases; judging uses your selected account only when you run an evaluation.",datasets:"Save reusable evaluation cases and reference passages. Import CSV, YAML or JSON, then copy reviewed cases into an evaluation suite. Datasets are shared with this workspace; saving never runs an evaluation.",externalagents:"Connect an external A2A agent, inspect its capabilities and run tasks through your own account. A2A 1.0 and 0.3 JSON-RPC are supported. Connections stay disabled until you enable them.",models:"Save provider/model combinations, then select them in chat or Quick Build. Profiles use existing adapters and local provider configuration.",decisionmodels:"Save decision models with backend, model and timeout for typed decisions in workflows and workbench.",evaluations:"Run saved test cases against an agent or workflow. Experiment Lab compares up to four provider/model variants using phrase/structured checks and optional provider-backed rubric judges.",schedules:"Run an agent or workflow once, at a fixed interval or on a calendar. Enabled schedules run only while this Studio server is open.",connectors:"Browse built-in ARC integrations or manage saved workspace connections. HTTP GET and SQLite can be configured directly in Studio.",entrypoints:"Connect verified messaging events or authenticated webhooks to an agent or workflow. Map channel users and configure verification before enabling.",prompts:"Save reusable instructions. Insert them with /prompt-id in conversations or Quick Build.",skills:"Reusable instruction sets. Add their IDs when creating an agent to include them in every task.",artifacts:"Upload text, images and PDFs; preview files and retain immutable versions. OpenAI API supports image/PDF inputs; other providers receive extracted PDF text. Select files explicitly before sending them.",toolsets:"Group existing registered tools for explicit use. Saving a toolset does not install or execute code."};
async function refreshLibrary(){
  try{
    if(library.kind==="toolsets"){state.tools=(await api("/api/tools")).items;renderTools();}
    library.items=(await api(`/api/catalog/${library.kind}`)).items;
    $("library-error").textContent="";renderLibrary();await refreshContextChoices();if(typeof refreshOperationResults==="function")await refreshOperationResults();if(typeof refreshIntegrationCatalog==="function")await refreshIntegrationCatalog();
  }catch(error){$("library-error").textContent=error.message;}
}
function renderLibrary(){
  $("library-subtitle").textContent=library.kind==="connectors"?"Built-in ARC integrations and saved workspace connections.":"Reusable resources for your agents.";
  if(typeof updateIntegrationVisibility==="function")updateIntegrationVisibility();
  const title=library.kind==="experiments"?"Experiment Lab":library.kind==="externalagents"?"External agents":library.kind==="decisionmodels"?"Decision models":operationKinds.includes(library.kind)?library.kind[0].toUpperCase()+library.kind.slice(1):"Library";
  $("library-heading").textContent=title;if(view()==="library")$("view-title").textContent=title;
  document.querySelectorAll("[data-library-kind]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.libraryKind===library.kind)));
  $("library-create").textContent=library.kind==="artifacts"?"+ Upload file":`+ Create ${libraryNames[library.kind]}`;
  $("library-description").textContent=libraryDescriptions[library.kind];
  $("library-import").hidden=library.kind!=="skills";
  const items=library.items.filter(item=>$("library-archived").checked||!item.archived);
  $("library-content").innerHTML=items.length?items.map(item=>`<article class="library-row"><div><h2>${escapeHtml(item.name)} ${item.archived?badge("Archived"):""}</h2><p>${escapeHtml(item.description||(item.data.backend?item.data.backend+" · "+item.data.model+" · "+(item.data.timeout_seconds||10)+"s":item.data.filename)||item.id)}</p><small>${escapeHtml(item.id)} · revision ${item.revision}${item.data.source?` · from ${escapeHtml(item.data.source.repository)}@${escapeHtml(item.data.source.commit.slice(0,7))}`:""}${library.kind==="artifacts"?` · ${Math.ceil(item.data.size/1024)} KiB`:""}</small></div><div class="library-actions">${typeof operationActions==="function"?operationActions(item):""}${!item.archived&&library.kind==="artifacts"?`<a class="button-link" href="/api/catalog/artifacts/${encodeURIComponent(item.id)}/download" download>Download</a><button class="secondary" data-artifact-preview="${escapeHtml(item.id)}">Preview</button>`:""}${!item.archived?`<button class="secondary" data-library-edit="${escapeHtml(item.id)}">Edit</button>`:""}${!item.archived&&library.kind==="toolsets"?`<button class="secondary" data-toolset-open="${escapeHtml(item.id)}">Use tools</button>`:""}<button class="secondary" data-library-history="${escapeHtml(item.id)}">History</button><button class="secondary" data-library-archive="${escapeHtml(item.id)}">${item.archived?"Restore":"Archive"}</button></div></article>`).join(""):library.kind==="connectors"?'<p class="integration-empty">No saved connections. HTTP GET and SQLite can be created here. Other built-ins are listed in Available integrations.</p>':`<div class="empty-state"><h2>No ${library.kind==="decisionmodels"?"decision models":library.kind} yet</h2><p>${escapeHtml(libraryDescriptions[library.kind])}</p></div>`;
}
async function openLibraryEditor(id,template){
  const item=id?await api(`/api/catalog/${library.kind}/${encodeURIComponent(id)}`):null;
  library.editing=item;
  const draft=item||(template?{name:template.name,data:template.data||{type:template.id}}:null);
  $("library-editor-title").textContent=`${item?"Edit":"Create"} ${libraryNames[library.kind]}`;
  $("library-form-error").textContent="";
  $("library-fields").innerHTML=inputField("name","Name",draft?.name||"")+inputField("description","Description",draft?.description||"")+(operationKinds.includes(library.kind)?operationFields(draft):library.kind==="artifacts"?'<label>File (maximum 16 MiB)<input type="file" name="file" required></label>':library.kind==="toolsets"?`<fieldset class="toolset-options"><legend>Registered tools</legend>${state.tools.map(tool=>`<label><input type="checkbox" name="tools" value="${escapeHtml(tool.id)}" ${item?.data.tools.includes(tool.id)?"checked":""}>${escapeHtml(tool.id)}<small>${escapeHtml(tool.description)}</small></label>`).join("")}</fieldset>`:`<label>${library.kind==="skills"?"Instructions":"Prompt content"}<textarea name="content" rows="8" required>${escapeHtml(item?.data.content||"")}</textarea></label>`);
  if(typeof updateScheduleTargetFields==="function")updateScheduleTargetFields();
  $("library-editor").showModal();
}
async function fileContent(file){
  if(!file||file.size>16*1024*1024)throw new Error("Choose a file no larger than 16 MiB.");
  return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("Could not read file."));reader.readAsDataURL(file);});
}
async function uploadArtifact(file){
  return await api("/api/catalog/artifacts",{id:crypto.randomUUID(),name:file.name,description:"Uploaded file",data:{filename:file.name,content_base64:await fileContent(file)}});
}
$("library-form").addEventListener("submit",async event=>{
  event.preventDefault();$("library-save").disabled=true;$("library-form-error").textContent="";
  try{
    const form=new FormData(event.target),payload={name:form.get("name"),description:form.get("description")};
    if(library.editing){payload.id=library.editing.id;payload.revision=library.editing.revision;}
    if(operationKinds.includes(library.kind))payload.data=operationFormData(form);
    else if(library.kind==="artifacts"){const file=form.get("file");if(!library.editing)payload.id=crypto.randomUUID();payload.data={filename:file.name,content_base64:await fileContent(file)};}
    else if(library.kind==="toolsets")payload.data={tools:form.getAll("tools")};
    else payload.data={content:form.get("content")};
    await api(`/api/catalog/${library.kind}`,payload);window.dispatchEvent(new CustomEvent("studio-resource-saved",{detail:{kind:library.kind}}));$("library-editor").close();await refreshLibrary();
  }catch(error){$("library-form-error").textContent=error.message;}
  finally{$("library-save").disabled=false;}
});
async function refreshContextChoices(){
  const [prompts,artifacts]=(await Promise.all([api("/api/catalog/prompts"),api("/api/catalog/artifacts")])).map(result=>result.items.filter(item=>!item.archived));
  $("builder-slash").innerHTML='<option value="">Choose a /prompt</option>'+prompts.map(item=>`<option value="${escapeHtml(item.id)}">/${escapeHtml(item.id)} · ${escapeHtml(item.name)}</option>`).join("");
  $("builder-mention").innerHTML='<option value="">Choose an @agent</option>'+state.agents.filter(item=>item.document).map(item=>`<option value="${escapeHtml(item.id)}">@${escapeHtml(item.id)}</option>`).join("");
  const selected=new Set([...$("builder-artifacts").selectedOptions].map(option=>option.value));
  $("builder-artifacts").innerHTML=artifacts.map(item=>`<option value="${escapeHtml(item.id)}" data-size="${Number(item.data.size)||0}" ${selected.has(item.id)?"selected":""}>${escapeHtml(item.name)}</option>`).join("");
}
for(const [id,prefix] of [["builder-slash","/"],["builder-mention","@"]])$(id).addEventListener("change",event=>{
  if(!event.target.value)return;const field=$("builder-task"),text=`${prefix}${event.target.value} `;
  field.setRangeText(text,field.selectionStart,field.selectionEnd,"end");field.focus();event.target.value="";
});
async function attachUploadedFiles(input, selectionId, statusId, refresh){
  const status=$(statusId),files=Array.from(input.files||[]);if(!files.length)return;
  if(state.attachmentUpload||state.busy||builder.busy){status.textContent="Wait for the current upload or run to finish.";input.value="";return;}
  const selected=new Set(Array.from($(selectionId).selectedOptions,option=>option.value));
  try{
    if(selected.size+files.length>5)throw new Error("Attach at most five files.");
    if(files.some(file=>file.size>16*1024*1024))throw new Error("Each file must be no larger than 16 MiB.");
    const existing=Array.from($(selectionId).selectedOptions).reduce((sum,item)=>sum+(Number(item.dataset.size)||0),0);
    if(existing+files.reduce((sum,file)=>sum+file.size,0)>32*1024*1024)throw new Error("Combined files must be no larger than 32 MiB.");
    state.attachmentUpload=true;input.disabled=true;$("send-message").disabled=true;$("builder-generate").disabled=true;
    for(const file of files){
      status.textContent=`Uploading ${file.name}…`;
      const artifact=await uploadArtifact(file);selected.add(artifact.id);
      await refresh();
      for(const option of $(selectionId).options)option.selected=selected.has(option.value);
    }
    status.textContent=`${selected.size} file${selected.size===1?"":"s"} attached. Sent only when you submit.`;
  }catch(error){status.textContent=error.message;}
  finally{if(state.attachmentUpload){state.attachmentUpload=false;input.disabled=false;$("send-message").disabled=Boolean(state.busy);$("builder-generate").disabled=Boolean(builder.busy);}input.value="";}
}
$("builder-upload").addEventListener("change",event=>attachUploadedFiles(event.target,"builder-artifacts","builder-upload-status",refreshContextChoices));
$("chat-upload")?.addEventListener("change",event=>attachUploadedFiles(event.target,"chat-artifacts","chat-upload-status",refreshModelChoices));
$("library-create").addEventListener("click",()=>openLibraryEditor().catch(error=>notify(error.message)));
// Import reads a public GitHub skill; commands are parsed, never run.
async function importSkill(){
  const form=await fieldsDialog("Import a skill",`<p>Paste a GitHub link to a repository, folder or SKILL.md, or a command such as <code>npx skills add https://github.com/owner/repo --skill name</code>. Studio reads the skill from GitHub; nothing is installed or run. SKILL.md and its Markdown references are imported; scripts and media are listed as not imported.</p><label>Source<input name="source" required maxlength="2000" placeholder="https://github.com/owner/repo"></label>`,"Import");
  if(!form)return null;const payload={source:form.get("source")};
  const libImport=$("library-import");if(libImport)libImport.disabled=true;notify("Importing skill from GitHub…");
  try{
    let result=await api("/api/catalog/skills/import",payload);
    if(result.choices){
      setTimeout(()=>{
        const toggleAll=document.getElementById("skills-toggle-all");
        const countBadge=document.getElementById("skills-selected-count");
        const boxes=document.querySelectorAll('input[name="skills"]');
        const dialogForm=toggleAll?.closest("form");
        if(toggleAll && boxes.length){
          const update=()=>{
            const checked=[...boxes].filter(b=>b.checked).length;
            if(countBadge)countBadge.textContent=`${checked} selected`;
            toggleAll.checked=checked===boxes.length;
            toggleAll.indeterminate=checked>0&&checked<boxes.length;
          };
          toggleAll.onchange=()=>{
            boxes.forEach(b=>{b.checked=toggleAll.checked;});
            update();
          };
          boxes.forEach(b=>b.onchange=update);
          dialogForm?.addEventListener("submit",e=>{
            if(![...boxes].some(b=>b.checked)){
              e.preventDefault();
              e.stopImmediatePropagation();
              const err=dialogForm.querySelector(".form-error");
              if(err)err.textContent="Select at least one skill to import.";
            }
          },true);
        }
      },0);
      const pick=await fieldsDialog("Choose skills",`<p>This source holds ${result.choices.length} skills${result.truncated?" (showing the first 30)":""}. Select one or more skills to import.</p><div class="skills-select-header"><label class="check-label"><input type="checkbox" id="skills-toggle-all"> <strong>Select all</strong></label><span id="skills-selected-count" class="badge">0 selected</span></div><div class="skills-select-list">${result.choices.map(choice=>`<label class="skills-select-item check-label"><input type="checkbox" name="skills" value="${escapeHtml(choice.skill)}"><div class="skills-select-meta"><span class="skills-select-name">${escapeHtml(choice.skill)}</span>${choice.description?`<span class="skills-select-desc">${escapeHtml(choice.description.slice(0,140))}</span>`:""}</div></label>`).join("")}</div>`,"Import");
      if(!pick)return null;
      const chosenSkills=pick.getAll("skills").filter(Boolean);
      if(!chosenSkills.length)return null;
      result=await api("/api/catalog/skills/import",{...payload,skills:chosenSkills});
    }
    const count=result.skills?result.skills.length:(result.skill?1:0);
    const skipped=(result.skipped_files||[]).length;
    const importedCount=(result.imported_files||[]).length;
    if(count>1){
      notify(`Imported ${count} skills: ${importedCount} file${importedCount===1?"":"s"} imported${skipped?`, ${skipped} not imported (scripts, data or media)`:""}.`);
    }else if(result.skill){
      notify(`${result.updated?"Updated":"Imported"} skill ${result.skill.name}: ${importedCount} file${importedCount===1?"":"s"} imported${skipped?`, ${skipped} not imported (scripts, data or media)`:""}.`);
    }
    if(typeof window!=="undefined"&&window.dispatchEvent){
      window.dispatchEvent(new CustomEvent("studio-resource-saved",{detail:{kind:"skills"}}));
    }
    if(typeof loadSkills==="function")await loadSkills().catch(()=>{});
    await refreshLibrary();
    return result;
  }catch(error){notify(error.message);return null;}
  finally{const libImport=$("library-import");if(libImport)libImport.disabled=false;}
}
$("library-import").addEventListener("click",()=>importSkill());
$("library-archived").addEventListener("change",renderLibrary);
document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.dataset.libraryKind){library.kind=button.dataset.libraryKind;await refreshLibrary();}
    if(button.dataset.libraryEdit)await openLibraryEditor(button.dataset.libraryEdit);
    if(button.dataset.libraryArchive){const item=library.items.find(item=>item.id===button.dataset.libraryArchive);await api(`/api/catalog/${library.kind}/${encodeURIComponent(item.id)}/archive`,{revision:item.revision,archived:!item.archived});await refreshLibrary();}
    if(button.dataset.libraryHistory&&library.kind==="prompts")await showPromptHistory(button.dataset.libraryHistory);
    if(button.dataset.libraryHistory&&library.kind!=="prompts"){const result=await api(`/api/catalog/${library.kind}/${encodeURIComponent(button.dataset.libraryHistory)}/history`);showDetail("Revision history",result.items.length?result.items.map(item=>`<details><summary>Revision ${item.revision} · ${escapeHtml(new Date(item.updated_at).toLocaleString())}</summary>${library.kind==="artifacts"?`<a class="button-link" href="/api/catalog/artifacts/${encodeURIComponent(item.id)}/versions/${item.revision}" download>Download this version</a>`:""}<pre>${escapeHtml(JSON.stringify(item,null,2))}</pre></details>`).join(""):'<p>No earlier revisions. Current version is available in the library.</p>');}
    if(button.dataset.toolsetOpen){const item=await api(`/api/catalog/toolsets/${encodeURIComponent(button.dataset.toolsetOpen)}`);showDetail(item.name,item.data.tools.map(tool=>`<div class="flow-step"><span>${escapeHtml(tool)}</span><button class="secondary" data-tool="${escapeHtml(tool)}">Run tool</button></div>`).join(""));}
  }catch(error){$("library-error").textContent=error.message;}
});
window.addEventListener("hashchange",()=>{if(view()==="library"){const alias=location.hash.slice(1);if(operationKinds.includes(alias))library.kind=alias;else library.kind="prompts";refreshLibrary();}});
async function initializeLibrary(){const alias=location.hash.slice(1);if(operationKinds.includes(alias))library.kind=alias;await refreshLibrary();}
if(state.ready)initializeLibrary();else window.addEventListener("studio-ready",initializeLibrary,{once:true});

document.addEventListener("click",async event=>{const button=event.target.closest("[data-artifact-preview]");if(!button)return;try{const item=library.items.find(item=>item.id===button.dataset.artifactPreview);const preview=await api(`/api/catalog/artifacts/${encodeURIComponent(item.id)}/preview`);showDetail(item.name,'');$("detail-content").replaceChildren(previewContent(preview));}catch(error){notify(error.message);}});

async function showPromptHistory(id, revision){
  const current=await api(`/api/catalog/prompts/${encodeURIComponent(id)}`);
  if(!revision){const history=(await api(`/api/catalog/prompts/${encodeURIComponent(id)}/history`)).items;revision=Math.max(0,...history.map(v=>v.revision))||current.revision;}
  const result=await api(`/api/studio/prompts/${encodeURIComponent(id)}/compare?revision=${revision}`);
  const names={name:"Name",description:"Description",content:"Prompt content",variables:"Variable defaults",tags:"Tags",shortcut:"Shortcut"};
  const value=(record,key)=>typeof record[key]==="string"?record[key]||"(empty)":JSON.stringify(record[key],null,2);
  showDetail("Prompt version history",`<label>Compare saved version<select id="prompt-history-version">${result.revisions.map(v=>`<option value="${v}" ${v===result.selected_revision?"selected":""}>Revision ${v}${v===result.current_revision?" · current":""}</option>`).join("")}</select></label><p>${result.changed_fields.length?`${result.changed_fields.length} fields changed.`:"No content differences."} Restoring creates a new revision and preserves existing history.</p><div class="version-comparison">${["selected","current"].map((side,index)=>`<section aria-label="${index?"Current":"Selected"} prompt version"><h3>${index?"Current":"Selected"} · revision ${index?result.current_revision:result.selected_revision}</h3>${Object.entries(names).map(([key,label])=>`<div class="version-field${result.changed_fields.includes(key)?" version-changed":""}"><h4>${label}${result.changed_fields.includes(key)?" · changed":""}</h4><pre>${escapeHtml(value(result[side],key))}</pre></div>`).join("")}</section>`).join("")}</div><p id="prompt-history-error" role="alert" class="form-error"></p><button id="prompt-history-restore" ${result.archived||!result.changed_fields.length?"disabled":""}>Restore revision ${result.selected_revision}</button>`);
  $("prompt-history-version").onchange=()=>showPromptHistory(id,Number($("prompt-history-version").value)).catch(error=>$("prompt-history-error").textContent=error.message);
  $("prompt-history-restore").onclick=async()=>{const button=$("prompt-history-restore");button.disabled=true;try{await api(`/api/studio/prompts/${encodeURIComponent(id)}/restore`,{revision:result.current_revision,restore_revision:result.selected_revision});window.dispatchEvent(new CustomEvent("studio-resource-saved",{detail:{kind:"prompts"}}));await refreshLibrary();await showPromptHistory(id,result.selected_revision);}catch(error){button.disabled=false;$("prompt-history-error").textContent=error.message;}};
}

"use strict";
let experimentSuite=null;
function targetField(data={}){
  return `<label>Run target<select name="target" id="operation-target" required><option value="">Choose an agent or workflow</option><optgroup label="Ready-made workflows">${state.workflows.filter(item=>item.document).map(item=>`<option value="workflow:${escapeHtml(item.id)}" ${data.target_kind==="workflow"&&data.target_id===item.id?"selected":""}>Workflow · ${escapeHtml(item.document.metadata?.name||item.id)}</option>`).join("")}</optgroup><optgroup label="Specialist agents">${state.agents.filter(item=>item.document).map(item=>`<option value="agent:${escapeHtml(item.id)}" ${data.target_kind==="agent"&&data.target_id===item.id?"selected":""}>Agent · ${escapeHtml(item.document.metadata?.name||item.id)}</option>`).join("")}</optgroup></select></label>`;
}
function localDate(value){const date=value?new Date(value):new Date(Date.now()+60000);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function caseFields(item={}){
  return `<div class="evaluation-case"><div class="step-heading"><span>TEST CASE</span><button type="button" class="secondary" data-remove-case>Remove</button></div><label>Task input<textarea class="case-input" required>${escapeHtml(item.input||"")}</textarea></label><label>Output must contain (one phrase per line)<textarea class="case-contains" rows="2">${escapeHtml((item.contains||[]).join("\n"))}</textarea></label><label>Output must exclude (one phrase per line)<textarea class="case-excludes" rows="2">${escapeHtml((item.excludes||[]).join("\n"))}</textarea></label>${caseFileFields(item,"input_files")}${caseFileFields(item,"reference_files")}<input type="hidden" class="case-context-source" value="${escapeHtml(JSON.stringify(item.context_source||{}))}"><details><summary>Structured checks and optional AI judge</summary><label>Reference passages (JSON list, optional)<textarea class="case-contexts" rows="4">${escapeHtml(JSON.stringify(item.contexts||[],null,2))}</textarea></label><label>Structured checks (JSON list)<textarea class="case-checks" rows="4">${escapeHtml(JSON.stringify(item.checks||[],null,2))}</textarea></label><label>AI judge settings (JSON object, optional)<textarea class="case-judge" rows="4">${escapeHtml(JSON.stringify(item.judge||{},null,2))}</textarea></label><p class="workspace-scope">Checks support exact output, JSON values and numeric ranges. A judge needs provider, rubric and threshold; its model calls consume your account usage. Reference passages are copied data, never instructions. A judge receives them through the provider you select. Judge scores are estimates.</p></details></div>`;
}
function caseFileFields(item,role){
  const files=item[role]||[],label=role==="input_files"?"Input files":"Grading-reference files";
  return `<details><summary>${label} · ${files.length} selected</summary><p>${role==="input_files"?"Extracted text goes to the target agent or workflow.":"Extracted text goes only to the AI judge. Configure a judge below."} Choose UTF-8 text, PDF or Office files from Library → Artifacts. Each file supports up to 16000 extracted characters.</p><input type="hidden" class="case-${role}" value="${escapeHtml(JSON.stringify(files))}"><p class="case-${role}-list evaluation-file-list">${escapeHtml(files.map(file=>`${file.id} · revision ${file.revision}`).join("; ")||"No files selected")}</p><button type="button" class="secondary" data-case-file-role="${role}">Choose ${role==="input_files"?"input":"reference"} file</button><button type="button" class="secondary" data-clear-case-files="${role}">Clear files</button></details>`;
}
async function chooseEvaluationFile(button){
  const row=button.closest(".evaluation-case"),role=button.dataset.caseFileRole,field=row.querySelector(`.case-${role}`);
  button.disabled=true;
  try{
    const items=(await api("/api/catalog/artifacts")).items.filter(item=>!item.archived);
    if(!row.isConnected||!$("library-editor").open)return;
    if(!items.length)throw new Error("Upload a file in Library → Artifacts first, then choose it here.");
    const form=await fieldsDialog("Choose a pinned file",`<p>The exact revision will be used. File access is checked when you run the evaluation. Saved datasets do not grant access to private files.</p><label>File revision<select name="file">${items.map(item=>`<option value="${escapeHtml(JSON.stringify({id:item.id,revision:item.revision}))}">${escapeHtml(item.data.filename||item.name)} · revision ${item.revision}</option>`).join("")}</select></label>`,"Add file");
    if(!form||!row.isConnected||!$("library-editor").open)return;
    const ref=JSON.parse(form.get("file")),files=JSON.parse(field.value||"[]");
    if(files.length>=5)throw new Error("Choose at most five files per role.");
    if(files.some(file=>file.id===ref.id&&file.revision===ref.revision))throw new Error("This file revision is already selected.");
    files.push(ref);field.value=JSON.stringify(files);
    row.querySelector(`.case-${role}-list`).textContent=files.map(file=>`${file.id} · revision ${file.revision}`).join("; ");
    field.closest("details").querySelector("summary").textContent=`${role==="input_files"?"Input files":"Grading-reference files"} · ${files.length} selected`;
  }catch(error){if(row.isConnected)$("library-form-error").textContent=error.message;}
  finally{if(button.isConnected)button.disabled=false;}
}
function clearEvaluationFiles(button){
  const row=button.closest(".evaluation-case"),role=button.dataset.clearCaseFiles;
  row.querySelector(`.case-${role}`).value="[]";row.querySelector(`.case-${role}-list`).textContent="No files selected";
  button.closest("details").querySelector("summary").textContent=`${role==="input_files"?"Input files":"Grading-reference files"} · 0 selected`;
}
function evaluatorFields(data){
  return `<p class="workspace-scope">Shared with this workspace. Saving never invokes AI. Only explicitly running an evaluation sends task, answer and reference passages to this provider.</p><label>Judge provider<select name="provider">${providerOptions(data.provider||"codex")}</select></label>`+inputField("model","Judge model (optional)",data.model||"")+`<label>Grading instructions<textarea name="rubric" rows="5" required>${escapeHtml(data.rubric||"")}</textarea></label><label>Pass threshold (0–1)<input name="threshold" type="number" min="0" max="1" step="any" value="${data.threshold??0.7}" required></label><label>Choice scores (optional, one label = score per line)<textarea name="choices" rows="4" placeholder="Supported = 1&#10;Partial = 0.5&#10;Unsupported = 0">${escapeHtml(Object.entries(data.choices||{}).map(([label,score])=>`${label} = ${score}`).join("\n"))}</textarea></label><p class="callout">Leave choices empty for a numeric score. With choices, only configured labels are accepted; their saved scores determine the grade. Judging consumes the selected account's usage. Model grades are estimates.</p>`;
}
function evaluatorFormData(form){
  const choices=Object.create(null);
  for(const line of String(form.get("choices")||"").split("\n").filter(line=>line.trim())){
    const split=line.lastIndexOf("="),label=line.slice(0,split).trim(),number=line.slice(split+1).trim(),score=Number(number);
    if(split<1||!label||!number||Object.hasOwn(choices,label)||!Number.isFinite(score)||score<0||score>1)throw new Error("Use unique labels with scores from 0 to 1: Supported = 1");
    choices[label]=score;
  }
  return {kind:"evaluator",version:1,type:"llm_judge",provider:form.get("provider"),model:form.get("model")||"",rubric:form.get("rubric"),threshold:Number(form.get("threshold")),...(Object.keys(choices).length?{choices}:{})};
}
function savedEvaluatorFields(){
  return '<details><summary>Apply a saved evaluator</summary><label>Workspace evaluator<select id="evaluation-saved-evaluator"><option value="">Loading evaluators…</option></select></label><button type="button" class="secondary" id="evaluation-apply-evaluator" disabled>Replace AI judges in all draft cases</button><p>Copies the selected grading instructions and provider into each current case. Existing phrase checks and reference passages stay intact. Review before saving; judging uses account usage only when the suite runs.</p><p id="evaluation-evaluator-status" class="workspace-scope" role="status"></p></details>';
}
async function loadEvaluatorChoices(){
  const select=$("evaluation-saved-evaluator"),button=$("evaluation-apply-evaluator");if(!select)return;
  try{
    const items=(await api("/api/catalog/evaluators")).items.filter(item=>!item.archived);
    if(!select.isConnected)return;
    select.innerHTML='<option value="">Choose an evaluator</option>'+items.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · revision ${item.revision} · ${escapeHtml(item.data.provider)}</option>`).join("");
    if(!items.length)select.innerHTML='<option value="">No saved evaluators</option>';
    select.onchange=()=>{button.disabled=!select.value;};
  }catch(error){if(select.isConnected){select.innerHTML='<option value="">Evaluators unavailable</option>';$("library-form-error").textContent=error.message;}}
}
async function applySavedEvaluator(button){
  const select=$("evaluation-saved-evaluator"),root=$("evaluation-cases"),id=select.value,status=$("evaluation-evaluator-status");
  if(!id)return;button.disabled=true;$("library-form-error").textContent="";
  try{
    const item=await api(`/api/catalog/evaluators/${encodeURIComponent(id)}`);
    if(!root.isConnected||select.value!==id)return;
    if(item.archived)throw new Error("Evaluator was archived. Restore it before copying.");
    const judges=root.querySelectorAll(".case-judge");if(!judges.length)throw new Error("Add at least one case first.");
    const judge=Object.fromEntries(Object.entries(item.data).filter(([key])=>["provider","model","rubric","threshold","choices"].includes(key)));
    judges.forEach(field=>{field.value=JSON.stringify(judge,null,2);});
    status.textContent=`Copied ${item.name}, revision ${item.revision}, into ${judges.length} draft case${judges.length===1?"":"s"}. Review and save; no AI was called.`;
  }catch(error){if(root.isConnected)$("library-form-error").textContent=error.message;}
  finally{if(button.isConnected)button.disabled=!select.value;}
}
function evaluationDatasetFields(data){
  const suite=library.kind==="evaluations";
  if(suite)queueMicrotask(loadEvaluationDatasetChoices);
  queueMicrotask(loadEvaluatorChoices);
  return (suite?targetField(data):'<p class="workspace-scope">Shared with this workspace. Saving cases never runs an evaluation.</p>')+`<div id="evaluation-cases">${(data.cases||[{}]).map(caseFields).join("")}</div><button type="button" class="secondary" id="evaluation-add-case">+ Add test case</button>${suite?'<details><summary>Use a saved dataset</summary><label>Workspace dataset<select id="evaluation-saved-dataset"><option value="">Loading datasets…</option></select></label><button type="button" class="secondary" id="evaluation-use-dataset" disabled>Replace cases from dataset</button><p>Copies the selected revision into this draft. Later dataset edits never change your suite.</p></details>':""}<details><summary>Import cases from a file</summary><label>CSV, YAML or JSON cases file (up to 900 KiB)<input type="file" id="evaluation-dataset" accept=".csv,.yaml,.yml,.json,text/csv,application/json,application/yaml"></label><button type="button" class="secondary" id="evaluation-import">Replace draft cases from file</button><p>CSV supports input and expected columns. JSON and YAML support case arrays or a version 1 dataset. All cases are validated before replacement. Nothing executes until you run a saved suite.</p></details>${datasetDraftFields()}${savedEvaluatorFields()}<p id="evaluation-dataset-status" class="workspace-scope" role="status"></p><p class="callout">Checks compare phrases without case sensitivity. Each case needs a phrase check, structured assertion or explicitly configured rubric judge.</p>`;
}
function datasetDraftFields(){
  return `<details><summary>Draft cases with AI</summary><p>Describe the behavior and expected answers. Only this request goes to the selected provider; account usage applies. Existing cases stay unchanged until you apply the draft.</p><label>What should these cases test?<textarea id="dataset-draft-task" rows="4" maxlength="16000" placeholder="For example: test a support agent that must refuse refund requests after 30 days."></textarea></label><div class="form-grid"><label>Provider<select id="dataset-draft-provider">${providerOptions("codex")}</select></label><label>Model (optional)<input id="dataset-draft-model" maxlength="200" placeholder="Provider default"></label><label>Number of cases<input id="dataset-draft-count" type="number" min="1" max="100" step="1" value="5"></label></div><button type="button" class="secondary" id="dataset-draft-generate">Generate draft cases</button><p id="dataset-draft-status" role="status" aria-live="polite"></p><div id="dataset-draft-review" hidden><label>Review generated dataset JSON<textarea id="dataset-draft-preview" rows="10"></textarea></label><div class="form-grid"><button type="button" class="secondary" id="dataset-draft-append">Append reviewed cases</button><button type="button" class="secondary" id="dataset-draft-replace">Replace all cases</button></div><p>Review expected answers carefully. AI-generated checks can be wrong. Applying changes only this editor; save separately.</p></div></details>`;
}
async function generateDatasetDraft(button){
  const root=$("evaluation-cases"),status=$("dataset-draft-status"),preview=$("dataset-draft-preview"),review=$("dataset-draft-review");
  const active=()=>root.isConnected&&$("library-editor").open;
  button.disabled=true;$("library-form-error").textContent="";
  try{
    const task=$("dataset-draft-task").value.trim(),count=Number($("dataset-draft-count").value);
    if(!task||task.length>16000||!Number.isInteger(count)||count<1||count>100)throw new Error("Describe the cases and choose a count from 1 to 100.");
    status.textContent="Generating draft cases… You can keep editing your current cases.";
    const run=await api("/api/studio/design",{design_kind:"dataset",task,case_count:count,provider:$("dataset-draft-provider").value,model:$("dataset-draft-model").value.trim()});
    const completed=await pollRun(run.id);
    if(!active()||!preview.isConnected)return;
    if(completed.status!=="completed"||!completed.design_proposal)throw new Error("No completed draft is available. Inspect the run in Activity.");
    preview.value=JSON.stringify(completed.design_proposal.data,null,2);review.hidden=false;
    status.textContent="Draft ready. Review, then append or replace cases. Existing edits are unchanged.";
  }catch(error){if(active()){$("library-form-error").textContent=error.message;status.textContent="Draft generation failed. Existing cases are unchanged.";}}
  finally{if(button.isConnected)button.disabled=false;}
}
async function applyDatasetDraft(button,append){
  const root=$("evaluation-cases"),preview=$("dataset-draft-preview"),content=preview.value;
  const active=()=>root.isConnected&&$("library-editor").open;
  button.disabled=true;$("library-form-error").textContent="";
  try{
    const generated=await api("/api/studio/datasets/parse",{format:"json",content});
    if(!active()||!preview.isConnected||preview.value!==content)return;
    if(append){
      const current=Array.from(root.querySelectorAll(".case-input")).map(field=>field.value.trim().toLocaleLowerCase());
      const incoming=generated.cases.map(item=>item.input.trim().toLocaleLowerCase());
      if(current.length+incoming.length>100)throw new Error("Appending would exceed 100 cases. Remove cases or replace instead.");
      if(incoming.some((value,index)=>current.includes(value)||incoming.indexOf(value)!==index))throw new Error("Duplicate case inputs found. Edit the generated JSON before appending.");
      root.insertAdjacentHTML("beforeend",generated.cases.map(caseFields).join(""));
    }else root.innerHTML=generated.cases.map(caseFields).join("");
    $("dataset-draft-review").hidden=true;
    $("evaluation-dataset-status").textContent=`${generated.cases.length} reviewed cases ${append?"appended":"applied"}. Save to keep them. No evaluation has run.`;
  }catch(error){if(active())$("library-form-error").textContent=error.message;}
  finally{if(button.isConnected)button.disabled=false;}
}
async function loadEvaluationDatasetChoices(){
  const select=$("evaluation-saved-dataset"),button=$("evaluation-use-dataset");if(!select)return;
  try{
    const items=(await api("/api/catalog/datasets")).items.filter(item=>!item.archived);
    if(!select.isConnected)return;
    select.innerHTML='<option value="">Choose a dataset</option>'+items.map(item=>`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · revision ${item.revision} · ${item.data.cases.length} case${item.data.cases.length===1?"":"s"}</option>`).join("");
    if(!items.length)select.innerHTML='<option value="">No saved datasets</option>';
    select.onchange=()=>{button.disabled=!select.value;};
  }catch(error){if(select.isConnected){select.innerHTML='<option value="">Datasets unavailable</option>';$("library-form-error").textContent=error.message;}}
}
async function importEvaluationDataset(button){
  const input=$("evaluation-dataset"),root=$("evaluation-cases"),status=$("evaluation-dataset-status"),file=input.files[0];
  button.disabled=true;$("library-form-error").textContent="";
  try{
    if(!file||file.size>900*1024)throw new Error("Choose a CSV, YAML or JSON file no larger than 900 KiB.");
    const extension=file.name.split(".").at(-1).toLowerCase(),format={csv:"csv",yaml:"yaml",yml:"yaml",json:"json"}[extension];
    if(!format)throw new Error("Choose a .csv, .yaml, .yml or .json dataset file.");
    const data=await api("/api/studio/datasets/parse",{format,content:await file.text()});
    if(!root.isConnected||input.files[0]!==file)return;
    root.innerHTML=data.cases.map(caseFields).join("");status.textContent=`${data.cases.length} validated case${data.cases.length===1?"":"s"} imported into this draft. Save to keep them.`;
  }catch(error){if(root.isConnected)$("library-form-error").textContent=error.message;}
  finally{if(button.isConnected)button.disabled=false;}
}
async function copySavedEvaluationDataset(button){
  const select=$("evaluation-saved-dataset"),root=$("evaluation-cases"),status=$("evaluation-dataset-status"),id=select.value;
  if(!id)return;button.disabled=true;$("library-form-error").textContent="";
  try{
    const item=await api(`/api/catalog/datasets/${encodeURIComponent(id)}`);
    if(!root.isConnected||select.value!==id)return;
    if(item.archived)throw new Error("Dataset was archived. Restore it before copying cases.");
    root.innerHTML=item.data.cases.map(caseFields).join("");
    status.textContent=`Copied ${item.data.cases.length} case${item.data.cases.length===1?"":"s"} from ${item.name}, revision ${item.revision}. Review and save this independent suite.`;
  }catch(error){if(root.isConnected)$("library-form-error").textContent=error.message;}
  finally{if(button.isConnected)button.disabled=!select.value;}
}
function savedExperimentFields(data){
  queueMicrotask(()=>loadExperimentSuites(data));
  return '<label>Pinned evaluation suite<select name="suite" id="saved-experiment-suite" required><option value="">Loading suites…</option></select></label>'+`<p>Suite edits require reviewing and updating this pinned revision before a new run.</p><div class="form-grid"><label>Runs per case<input type="number" name="runsPerExample" min="1" max="20" step="1" value="${data.runsPerExample??1}" required></label><label>Minimum pass rate per variant (0–1)<input type="number" name="passThreshold" min="0" max="1" step="any" value="${data.passThreshold??1}" required></label></div><div id="saved-experiment-variants">${(data.variants||[{name:"Baseline"}]).map((variant,index)=>variantFields(index+1,variant,true)).join("")}</div><button type="button" class="secondary" id="saved-experiment-add-variant">+ Add variant</button><p id="saved-experiment-estimate" role="status" class="workspace-scope"></p><p class="callout">Every variant must meet the threshold independently. Each repetition is a fresh execution and can invoke workflow tools or external changes. Approval and input steps pause the current case. Review each repetition in Activity before continuing. Judges and workflow steps can add provider calls. Saving never executes; running uses your selected accounts.</p>`;
}
async function loadExperimentSuites(data){
  const select=$("saved-experiment-suite");if(!select)return;
  try{
    const items=(await api("/api/catalog/evaluations")).items.filter(item=>!item.archived);
    if(!select.isConnected)return;
    const selected=data.evaluation_id?JSON.stringify([data.evaluation_id,data.evaluation_revision]):"";
    select.innerHTML='<option value="">Choose a suite revision</option>'+items.map(item=>`<option value="${escapeHtml(JSON.stringify([item.id,item.revision]))}" data-cases="${item.data.cases.length}">${escapeHtml(item.name)} · revision ${item.revision}</option>`).join("");
    if(selected&&!items.some(item=>item.id===data.evaluation_id&&item.revision===data.evaluation_revision))select.insertAdjacentHTML("beforeend",`<option value="${escapeHtml(selected)}">${escapeHtml(data.evaluation_id)} · pinned revision ${data.evaluation_revision} (review latest suite before running)</option>`);
    select.value=selected;updateExperimentEstimate();
  }catch(error){if(select.isConnected){select.innerHTML='<option value="">Suites unavailable</option>';$("library-form-error").textContent=error.message;}}
}
function updateExperimentEstimate(){
  const select=$("saved-experiment-suite"),root=$("saved-experiment-variants"),status=$("saved-experiment-estimate");if(!select||!root||!status)return;
  const cases=Number(select.selectedOptions[0]?.dataset.cases||0),repeats=Number(document.querySelector('[name="runsPerExample"]')?.value||1),count=cases*repeats*root.children.length;
  status.textContent=cases?`${count} planned case executions (${cases} case${cases===1?"":"s"} × ${root.children.length} variant${root.children.length===1?"":"s"} × ${repeats} repetition${repeats===1?"":"s"}). Maximum 400.`:"Choose a current suite revision to see planned execution count.";
}
function savedExperimentFormData(form){
  const [evaluation_id,evaluation_revision]=JSON.parse(form.get("suite")||"[]");
  return {kind:"experiment",version:1,evaluation_id,evaluation_revision,runsPerExample:Number(form.get("runsPerExample")),passThreshold:Number(form.get("passThreshold")),variants:[...$("saved-experiment-variants").querySelectorAll(".experiment-variant")].map(row=>({name:row.querySelector(".variant-name").value,provider:row.querySelector(".variant-provider").value,model:row.querySelector(".variant-model").value,...(row.querySelector(".variant-effort").value?{effort:row.querySelector(".variant-effort").value}:{})}))};
}
function experimentReportSummary(report){
  if(!report.experiment_id)return "";
  return `<p class="callout">${report.complete?(report.acceptance?"Pass threshold met by every variant.":"Pass threshold not met by every variant."):"Incomplete experiment. No acceptance result."} ${report.total||0}/${report.expected_total} case executions recorded · minimum ${Number(report.pass_threshold)*100}% per variant · experiment revision ${report.experiment_revision}.</p>${report.variant_results?.length?`<ul>${report.variant_results.map(item=>`<li>${escapeHtml(item.name)}: ${item.passed}/${item.expected_total} passed${report.complete?` · ${item.accepted?"Accepted":"Below threshold"}`:" · incomplete"}</li>`).join("")}</ul>`:""}`;
}
function operationFields(item){
  const data=item?.data||{};
  if(library.kind==="connectors")queueMicrotask(updateConnectionFields);
  if(library.kind==="entrypoints")queueMicrotask(updateIncomingServiceFields);
  if(library.kind==="externalagents")return inputField("external_url","Agent service URL",data.url||"")+inputField("external_token_env","Personal credential environment variable (optional)",data.token_env||"")+inputField("external_card_path","Agent card path",data.card_path||"/.well-known/agent-card.json")+`<label class="check-label"><input name="external_streaming" type="checkbox" ${data.streaming?"checked":""}> Stream remote task updates</label>`+`<label>A2A protocol<select name="protocol_version"><option value="1.0" ${data.protocol_version==="1.0"?"selected":""}>1.0 JSON-RPC</option><option value="0.3" ${data.protocol_version==="0.3"?"selected":""}>0.3 JSON-RPC</option></select></label><label class="check-label"><input name="enabled" type="checkbox" ${data.enabled?"checked":""}> Enable this external agent</label><label class="check-label"><input name="allow_private" type="checkbox" ${data.allow_private?"checked":""}> Allow a private-network service (including local HTTP)</label><p class="callout">Discovery only reads the agent card. Running a task sends your supplied text to this service and may invoke its tools. Cards cannot change the credential destination. Remote sends are never retried automatically.</p>`;
  if(library.kind==="experiments")return savedExperimentFields(data);
  if(library.kind==="evaluators")return evaluatorFields(data);
  if(library.kind==="models")return `<label>Provider<select name="provider">${providerOptions(data.provider||"codex")}</select></label>`+inputField("model","Model ID",data.model||"")+`<label>Effort<select name="effort">${effortOptions(data.effort||"")}</select></label>`;
  if(library.kind==="decisionmodels")return `<label>Decision service<select name="backend" id="decisionmodel-backend"><option value="openrouter" ${(data.backend||"openrouter")==="openrouter"?"selected":""}>OpenRouter</option><option value="opencode" ${data.backend==="opencode"?"selected":""}>OpenCode</option><option value="openai" ${data.backend==="openai"?"selected":""}>OpenAI (gpt-6-luna)</option><option value="typesafe" ${data.backend==="typesafe"?"selected":""}>TypeSafe (jev-1.13)</option></select></label>`+inputField("model","Model ID",data.model||"")+inputField("timeout_seconds","Timeout seconds (1–300)",data.timeout_seconds||10,"number");
  if(["evaluations","datasets"].includes(library.kind))return evaluationDatasetFields(data);
  if(library.kind==="schedules"){
    const isWf=data.target_kind==="workflow";
    if(typeof queueMicrotask==="function")queueMicrotask(updateScheduleTargetFields);
    return targetField(data)+`<p id="schedule-target-help" class="callout">${isWf?"Ready-made workflow: will execute all configured steps and specialists in sequence.":"Agent task: the specialist will execute your instructions at each scheduled time."}</p><label id="schedule-task-label">${isWf?"Task / input (optional for workflows)":"Task instructions"}<textarea name="task" id="schedule-task" ${isWf?"":"required"} placeholder="${isWf?"Optional task text (leave blank to run the workflow directly)":"What should this agent do on schedule?"}">${escapeHtml(data.task||"")}</textarea></label><details id="schedule-workflow-input" ${isWf?"":'style="display:none"'}><summary>Workflow input (JSON, optional)</summary><label>Workflow input (JSON)<textarea name="workflow_input_json" rows="3" placeholder='{"items": ["sample"]}'>${escapeHtml(data.workflow_input?JSON.stringify(data.workflow_input,null,2):"")}</textarea></label><p>Supply initial input for graph workflows, or leave blank to use the task text.</p></details>`+inputField("run_at","First run (your browser’s local time)",localDate(data.run_at),"datetime-local")+inputField("interval_minutes","Repeat every minutes (0 = once)",(data.interval_seconds||0)/60,"number")+`<label class="check-label"><input type="checkbox" name="enabled" ${data.enabled?"checked":""}> Enable schedule</label><p class="callout">Runs while Studio is open. Missed intervals are not replayed in a burst. Schedules do not overlap their previous run.</p>`;
  }
  if(library.kind==="entrypoints")return targetField(data)+`<label>Incoming service<select name="entrypoint_type">${["webhook","telegram","slack","discord","openclaw","teams"].map(type=>`<option value="${type}" ${(data.type||"webhook")===type?"selected":""}>${type==="webhook"?"Webhook":type[0].toUpperCase()+type.slice(1)}</option>`).join("")}</select></label>`+`<label id="entrypoint-auth-mode-label">Authentication mode<select name="auth_mode" id="entrypoint-auth-mode"><option value="bearer" ${(data.auth_mode||"bearer")==="bearer"?"selected":""}>Bearer token</option><option value="hmac" ${data.auth_mode==="hmac"?"selected":""}>HMAC signature</option></select></label>`+inputField("token_env","Verification secret environment variable (never the secret value)",data.token_env||"NEXUS_WEBHOOK_TOKEN")+`<details data-hmac-entrypoint ${data.auth_mode==="hmac"?"open":"hidden"}><summary>HMAC signature & routing</summary>`+inputField("slug","Gateway slug (e.g. acme-helpdesk for /gw/acme-helpdesk)",data.slug||"")+inputField("route_path","Route subpath (e.g. /tickets)",data.route_path||"")+inputField("hmac_header","Signature header name",data.hmac_header||"X-Hub-Signature-256")+`<label>HMAC algorithm<select name="hmac_algorithm"><option value="sha256" ${(data.hmac_algorithm||"sha256")==="sha256"?"selected":""}>SHA-256</option><option value="sha1" ${data.hmac_algorithm==="sha1"?"selected":""}>SHA-1</option><option value="sha512" ${data.hmac_algorithm==="sha512"?"selected":""}>SHA-512</option></select></label>`+inputField("hmac_prefix","Signature prefix (e.g. sha256=)",data.hmac_prefix!==undefined?data.hmac_prefix:"sha256=")+`<label>HMAC encoding<select name="hmac_encoding"><option value="hex" ${(data.hmac_encoding||"hex")==="hex"?"selected":""}>Hexadecimal</option><option value="base64" ${data.hmac_encoding==="base64"?"selected":""}>Base64</option></select></label></details>`+`<details data-teams-entrypoint ${data.type==="teams"?"open":"hidden"}><summary>Microsoft Teams subscription</summary>`+inputField("teams_team_id","Team ID",data.team_id||"")+inputField("teams_channel_id","Channel ID",data.channel_id||"")+inputField("teams_tenant_id","Tenant ID",data.tenant_id||"")+inputField("teams_subscription_id","Subscription ID (after registration)",data.subscription_id||"")+inputField("teams_graph_token_env","Reader account credential reference",data.graph_token_env||"")+inputField("teams_command_prefix","Run command prefix",data.command_prefix||"/nexus ")+`<p>Save disabled first, register basic Graph notifications, then add the returned subscription ID and enable. Only new messages with this prefix start tasks. Map Microsoft user IDs below; allowed channels must contain exactly this channel. Results and approvals stay in Studio. Setup never creates or renews remote subscriptions.</p></details>`+`<details open><summary>Channel identity and replies</summary><label>Platform user → Nexus user mapping (JSON)<textarea name="identity_map">${escapeHtml(JSON.stringify(data.identity_map||{},null,2))}</textarea></label><label>Allowed channel IDs (one per line)<textarea name="allowed_channels">${escapeHtml((data.allowed_channels||[]).join("\n"))}</textarea></label>`+inputField("notification_connector_id","Reply connection ID (fixed destination)",data.notification_connector_id||"")+`<label class="check-label"><input name="a2a_enabled" type="checkbox" ${data.a2a_enabled?"checked":""}> Expose this bearer webhook as an incoming A2A text agent</label>`+`<label class="check-label"><input type="checkbox" name="auto_reply" ${data.auto_reply?"checked":""}> Send results through reply connection</label><label class="check-label" id="slack-thread-option"><input type="checkbox" name="threaded_conversations" ${data.threaded_conversations?"checked":""}> Keep each person's conversation in its Slack thread (agents only)</label><p data-slack-thread-help>Saving changes starts fresh Studio history. Replies remain visible to Slack thread members.</p><label class="check-label" id="slack-files-option"><input type="checkbox" name="capture_slack_files" ${data.capture_slack_files?"checked":""}> Attach Slack files to agent messages</label><p data-slack-files-help>Copies up to five hosted files into the mapped user’s private Library and sends them to the selected agent/provider. Requires threaded conversations, a matching enabled Slack connection, and files:read scope. Maximum 16 MiB per file, 32 MiB total.</p><label class="check-label"><input type="checkbox" name="approval_notifications" ${data.approval_notifications?"checked":""}> Send approval buttons through reply connection</label><p>For local use, map each allowed platform user to “desktop”. Team users must have project access. Reply connection must match incoming service and channel.</p></details><label class="check-label"><input type="checkbox" name="enabled" ${data.enabled?"checked":""}> Enable incoming service</label><p class="callout">Save never changes your remote account. Register the incoming URL in your service settings. Telegram uses its secret-token header; Slack signed events and interactions; Discord signed commands and buttons; OpenClaw uses the documented signed bridge envelope. Teams verifies its selected Graph subscription and reads each command from Graph. A bearer webhook needs a random secret of at least 24 characters.</p>`;
  return `<label>Connector type<select name="connector_type" id="connector-type"><option value="http" ${data.type==="http"?"selected":""}>HTTP GET</option><option value="sqlite" ${data.type==="sqlite"?"selected":""}>SQLite (read-only)</option>${["github","gitlab","telegram","discord","slack","openclaw","mcp"].map(type=>`<option value="${type}" ${data.type===type?"selected":""}>${({github:"GitHub",gitlab:"GitLab",telegram:"Telegram",discord:"Discord",slack:"Slack",openclaw:"OpenClaw",mcp:"MCP server"})[type]}</option>`).join("")}</select></label>`+inputField("url","HTTP endpoint URL",data.url||"")+inputField("token_env","Credential environment variable (never a secret value)",data.token_env||"")+inputField("path","SQLite database path (relative to workspace)",data.path||"")+inputField("repo","Git repository (owner/project)",data.repo||"")+inputField("base_url","Service origin (GitLab or OpenClaw)",data.base_url||"")+inputField("channel","Messaging channel or chat ID",data.channel||"")+`<label>Messaging account<select name="credential_scope"><option value="personal">Each user’s account</option><option value="service" ${data.credential_scope==="service"?"selected":""}>Workspace bot account</option></select></label>`+inputField("command","Trusted MCP executable",data.command||"")+`<label class="check-label"><input type="checkbox" name="mcp_persistent" ${data.persistent?"checked":""}> Keep MCP sessions open between calls</label>`+`<label>MCP arguments (JSON list)<textarea name="mcp_args">${escapeHtml(JSON.stringify(data.args||[]))}</textarea></label><label>MCP account environment references (JSON object)<textarea name="mcp_env">${escapeHtml(JSON.stringify(data.env||{}))}</textarea></label><label>Enabled MCP tools (one per line)<textarea name="mcp_tools">${escapeHtml((data.tools||[]).join("\n"))}</textarea></label><label>Read-only MCP tools allowed for agents (one per line)<textarea name="mcp_safe">${escapeHtml((data.read_only_tools||[]).join("\n"))}</textarea></label><label>Allowed actions (comma separated)<input name="actions" value="${escapeHtml((data.actions||[]).join(","))}" placeholder="list_issues, get_issue, comments"></label><p class="callout">Only fields for the chosen service are used. Store credential environment names, never secret values. For shared Git connections, Nexus uses each user's linked account. Discover MCP tools before enabling their names. Mutations require explicit confirmation or an approved workflow step.</p>`+`<label class="check-label"><input type="checkbox" name="allow_private" ${data.allow_private?"checked":""}> Allow private/local HTTP destinations</label><label class="check-label"><input type="checkbox" name="enabled" ${data.enabled?"checked":""}> Enable connector</label><p class="callout">HTTP uses GET, blocks redirects, and limits responses to 256 KiB. SQLite runs read-only queries with a 100-row limit. Save creates an explicit registered tool; agents are not automatically given access.</p>`;
}
function operationFormData(form){
  if(library.kind==="experiments")return savedExperimentFormData(form);
  if(library.kind==="evaluators")return evaluatorFormData(form);
  const [target_kind,...identity]=String(form.get("target")||"").split(":"),target_id=identity.join(":");
  if(library.kind==="externalagents")return {url:form.get("external_url"),protocol_version:form.get("protocol_version"),token_env:form.get("external_token_env"),card_path:form.get("external_card_path"),streaming:form.has("external_streaming"),enabled:form.has("enabled"),allow_private:form.has("allow_private")};
  if(library.kind==="models")return {provider:form.get("provider"),model:form.get("model"),...(form.get("effort")?{effort:form.get("effort")}:{})};
  if(library.kind==="decisionmodels")return {backend:form.get("backend"),model:form.get("model")?.trim()||"",timeout_seconds:Number(form.get("timeout_seconds")||10)};
  if(["evaluations","datasets"].includes(library.kind))return {...(library.kind==="evaluations"?{target_kind,target_id}:{kind:"dataset",version:1}),cases:[...document.querySelectorAll(".evaluation-case")].map(row=>({input:row.querySelector(".case-input").value,...Object.fromEntries(["input_files","reference_files"].map(role=>[role,JSON.parse(row.querySelector(`.case-${role}`)?.value||"[]")]).filter(([,files])=>files.length)),contains:row.querySelector(".case-contains").value.split("\n").map(s=>s.trim()).filter(Boolean),excludes:row.querySelector(".case-excludes").value.split("\n").map(s=>s.trim()).filter(Boolean),checks:JSON.parse(row.querySelector(".case-checks").value||"[]"),...(row.querySelector(".case-contexts").value.trim()!=="[]"||row.querySelector(".case-context-source").value!=="{}"?{contexts:JSON.parse(row.querySelector(".case-contexts").value||"[]")} : {}),...(row.querySelector(".case-context-source").value!=="{}"?{context_source:JSON.parse(row.querySelector(".case-context-source").value)}:{}),...(row.querySelector(".case-judge").value.trim()!=="{}"?{judge:JSON.parse(row.querySelector(".case-judge").value)}:{})}))};
  if(library.kind==="schedules"){
    const rawTask=form.get("task")?.trim()||"";
    const wfInputRaw=form.get("workflow_input_json")?.trim();
    let wfInput=undefined;
    if(wfInputRaw){try{wfInput=JSON.parse(wfInputRaw);}catch{}}
    return {target_kind,target_id,task:rawTask,run_at:new Date(form.get("run_at")).toISOString(),interval_seconds:Number(form.get("interval_minutes"))*60,enabled:form.has("enabled"),...(wfInput!==undefined?{workflow_input:wfInput}:{})};
  }
  if(library.kind==="entrypoints"){const auth_mode=form.get("auth_mode")||"bearer";return {target_kind,target_id,a2a_enabled:form.has("a2a_enabled"),type:form.get("entrypoint_type"),token_env:form.get("token_env"),enabled:form.has("enabled"),auth_mode,...(auth_mode==="hmac"?{slug:form.get("slug")||"",route_path:form.get("route_path")||"",hmac_header:form.get("hmac_header")||"X-Hub-Signature-256",hmac_algorithm:form.get("hmac_algorithm")||"sha256",hmac_prefix:form.get("hmac_prefix")!==null?form.get("hmac_prefix"):"sha256=",hmac_encoding:form.get("hmac_encoding")||"hex"}:{}),identity_map:JSON.parse(form.get("identity_map")||"{}"),allowed_channels:String(form.get("allowed_channels")||"").split("\n").map(v=>v.trim()).filter(Boolean),notification_connector_id:form.get("notification_connector_id")||"",auto_reply:form.has("auto_reply"),approval_notifications:form.has("approval_notifications"),...(form.get("entrypoint_type")==="slack"?{capture_slack_files:target_kind==="agent"&&form.has("threaded_conversations")&&form.has("capture_slack_files"),threaded_conversations:target_kind==="agent"&&form.has("threaded_conversations")}:{ }),...(form.get("entrypoint_type")==="teams"?{team_id:form.get("teams_team_id"),channel_id:form.get("teams_channel_id"),tenant_id:form.get("teams_tenant_id"),subscription_id:form.get("teams_subscription_id"),graph_token_env:form.get("teams_graph_token_env"),command_prefix:form.get("teams_command_prefix")}:{})};}
  if(["github","gitlab","telegram","discord","slack","openclaw","mcp"].includes(form.get("connector_type"))){const data={type:form.get("connector_type"),enabled:form.has("enabled"),repo:form.get("repo"),base_url:form.get("base_url")||undefined,token_env:form.get("token_env"),channel:form.get("channel"),credential_scope:form.get("credential_scope"),command:form.get("command"),persistent:form.has("mcp_persistent"),args:JSON.parse(form.get("mcp_args")||"[]"),env:JSON.parse(form.get("mcp_env")||"{}"),tools:String(form.get("mcp_tools")||"").split("\n").map(t=>t.trim()).filter(Boolean),read_only_tools:String(form.get("mcp_safe")||"").split("\n").map(t=>t.trim()).filter(Boolean)};const actions=String(form.get("actions")||"").split(",").map(a=>a.trim()).filter(Boolean);if(actions.length)data.actions=actions;return data;}
  return {type:form.get("connector_type"),url:form.get("url"),path:form.get("path"),token_env:form.get("token_env"),enabled:form.has("enabled"),allow_private:form.has("allow_private")};
}
function operationActions(item){
  if(item.archived)return "";
  if(library.kind==="externalagents")return `${badge(item.data.enabled?"Enabled":"Disabled",item.data.enabled?"ready":"")}<button class="secondary" data-external-discover="${escapeHtml(item.id)}" ${item.data.enabled?"":"disabled"}>Inspect capabilities</button><button data-external-run="${escapeHtml(item.id)}" ${item.data.enabled?"":"disabled"}>Run task</button>`;
  if(library.kind==="experiments")return `<button data-saved-experiment-run="${escapeHtml(item.id)}">Run experiment</button><button class="secondary" data-experiment-export="${escapeHtml(item.id)}">Export JSON</button>`;
  if(library.kind==="evaluators")return `<button class="secondary" data-evaluator-export="${escapeHtml(item.id)}">Export JSON</button>`;
  if(library.kind==="datasets")return `<button class="secondary" data-dataset-export="${escapeHtml(item.id)}">Export JSON</button>`;
  if(library.kind==="evaluations")return `<button data-evaluate="${escapeHtml(item.id)}">Run checks</button><button class="secondary" data-experiment="${escapeHtml(item.id)}">Experiment Lab</button>`;
  if(library.kind==="connectors")return `<button class="secondary" data-guide-resume="${escapeHtml(item.id)}">Setup guide</button>${badge(item.data.enabled?"Enabled":"Disabled",item.data.enabled?"ready":"")}<button class="secondary" data-connector-test="${escapeHtml(item.id)}" ${item.data.enabled?"":"disabled"}>Test connection</button>`;
  if(library.kind==="entrypoints")return `${badge(item.data.enabled?"Enabled":"Disabled",item.data.enabled?"ready":"")}<button class="secondary" data-entrypoint="${escapeHtml(item.id)}">Incoming URL</button>${item.data.a2a_enabled?`<button class="secondary" data-a2a-health="${escapeHtml(item.id)}">Delivery health</button>`:""}`;
  if(library.kind==="decisionmodels")return badge(item.data.backend)+`<button class="secondary" data-decisionmodel-test="${escapeHtml(item.id)}">Use in Workbench</button>`;
  if(library.kind==="schedules")return badge(item.data.target_kind==="workflow"?"Workflow":"Agent")+badge(item.data.enabled?"Enabled":"Paused",item.data.enabled?"ready":"")+`<button class="secondary" data-schedule-toggle="${escapeHtml(item.id)}">${item.data.enabled?"Pause":"Enable schedule"}</button><button class="secondary" data-schedule-details="${escapeHtml(item.id)}">Dates & history</button>`;
  return "";
}
async function refreshOperationResults(){
  const root=$("operation-results");root.replaceChildren();
  if(["evaluations","experiments"].includes(library.kind)){
    const reports=(await api("/api/evaluation-reports")).items.filter(report=>library.kind!=="experiments"||report.experiment_id);
    root.innerHTML=`<div class="operation-heading"><h2>Reports</h2><button class="secondary" id="refresh-operation-results">Refresh reports</button></div>`+(reports.length?reports.map(report=>`<article class="library-row"><div><h3>${escapeHtml(report.experiment_name||report.name)}</h3>${experimentReportSummary(report)}<p>${report.passed}/${report.total} cases passed · suite revision ${report.suite_revision} · ${escapeHtml(new Date(report.created_at).toLocaleString())}</p></div><button class="secondary" data-evaluation-report="${escapeHtml(report.id)}">View report</button></article>`).join(""):library.kind==="experiments"?'<p>Run a saved experiment to create your first report.</p>':'<p>Run a suite to create your first report.</p>');
  }else if(library.kind==="externalagents"){
    const remote=(await api("/api/workbench/externalagents")).runs;
    root.innerHTML='<h2>Remote tasks</h2>'+remote.map(item=>`<article class="library-row"><div><h3>${escapeHtml(item.agent_id)}</h3><p>${escapeHtml(item.status)} · ${escapeHtml(item.task_id||"Remote task ID not acknowledged")}</p></div><div class="card-actions">${["input-required","auth-required"].includes(item.status)?`<button data-remote-continue="${escapeHtml(item.id)}">Provide input</button>`:""}${item.task_id?`<button class="secondary" data-remote-refresh="${escapeHtml(item.id)}">Refresh remote status</button>`:""}${item.task_id&&!["completed","failed","canceled","cancelled","rejected"].includes(item.status)?`<button class="secondary" data-remote-cancel="${escapeHtml(item.id)}">Cancel remote task</button>`:""}${item.callback_id?`<button class="secondary" data-remote-callbacks="${escapeHtml(item.id)}">Callback updates</button>`:""}<button class="secondary" data-run="${escapeHtml(item.id)}">Details</button></div></article>`).join("");
  }else if(library.kind==="schedules"){
    const states=(await api("/api/schedule-state")).items;
    root.innerHTML=`<div class="operation-heading"><h2>Schedule activity</h2><button class="secondary" id="refresh-operation-results">Refresh activity</button></div>`+(states.length?states.map(state=>`<article class="library-row"><div><h3>${escapeHtml(state.id)}</h3><p>${state.last_error?escapeHtml(state.last_error):state.next_run_at?"Next run: "+escapeHtml(new Date(state.next_run_at).toLocaleString()):"One-time occurrence claimed"}</p></div>${state.run_id?`<button class="secondary" data-run="${escapeHtml(state.run_id)}">Run details</button>`:""}</article>`).join(""):'<p>No schedule occurrences yet.</p>');
  }
}
async function runEvaluation(id,variants,experimentId=null){
  const payload=experimentId?{kind:"evaluation",experiment_id:experimentId}:{kind:"evaluation",evaluation_id:id};if(variants&&!experimentId)payload.variants=variants;
  const run=await api("/api/runs",payload);showRun(run);
  pollRun(run.id,updateRunDetail).then(async()=>{await refreshOperationResults();await refreshRuns();}).catch(async error=>{updateRunDetail(error.run||{id:run.id,status:"failed",error:error.message});await refreshOperationResults();});
}
function variantFields(index,data={},removable=false){return `<div class="experiment-variant"><label>Variant name<input class="variant-name" value="${escapeHtml(data.name||`Variant ${index}`)}" required></label><label>Provider<select class="variant-provider">${providerOptions(data.provider||"",true)}</select></label><label>Model override<input class="variant-model" value="${escapeHtml(data.model||"")}" placeholder="Saved/default model"></label><label>Effort<select class="variant-effort">${effortOptions(data.effort||"")}</select></label>${removable?'<button type="button" class="secondary" data-remove-experiment-variant>Remove variant</button>':""}</div>`;}

async function showEvaluationReport(id){
  const report=await api(`/api/evaluation-reports/${encodeURIComponent(id)}`);
  showDetail(report.experiment_name||report.name,`${experimentReportSummary(report)}<p>${report.passed}/${report.total} cases passed.</p><button type="button" class="secondary" data-evaluation-report-export="${escapeHtml(id)}">Download report JSON</button><p class="workspace-scope">Includes task inputs, answers and grading details. Keep this file private. Check it locally with <code>nexus eval check-report report.json</code>; no tasks are rerun.</p>${report.results.map(result=>`<details class="report-case"><summary>${escapeHtml(result.variant)} · case ${result.case}${report.runs_per_example>1?` · repetition ${result.repetition}/${report.runs_per_example}`:""} ${badge(result.passed?"Passed":"Failed",result.passed?"ready":"failed")}</summary><p>${escapeHtml(result.input)}</p>${evaluationFileSources(result)}${result.error?`<p class="form-error">${escapeHtml(result.error)}</p>`:""}<ul>${result.checks.map(check=>`<li>${check.passed?"Pass":"Fail"}: ${escapeHtml(({choice_judge:"AI choice grade",rubric_judge:"AI rubric grade"})[check.type]||check.type)} “${escapeHtml(check.phrase)}”${check.score!==undefined?` · score ${check.score} (threshold ${check.threshold})`:""}${check.choice!==undefined?` · choice ${escapeHtml(check.choice)}`:""}${check.reason?` · ${escapeHtml(check.reason)}`:""}${check.error?` · ${escapeHtml(check.error)}`:""}</li>`).join("")}</ul><pre>${escapeHtml(result.output)}</pre><small>${result.duration_seconds}s</small></details>`).join("")}`);
}
function evaluationFileSources(result){
  const sources=result.file_sources||{};
  return Object.keys(sources).length?`<details class="evaluation-file-sources"><summary>Pinned file sources</summary>${["input_files","reference_files"].map(role=>(sources[role]||[]).map(file=>`<p><strong>${role==="input_files"?"Input":"Grading reference"}</strong> · ${escapeHtml(file.filename)} · revision ${escapeHtml(file.revision)}<br><small>${escapeHtml(file.id)} · SHA-256 ${escapeHtml(file.sha256)}</small></p>`).join("")).join("")}</details>`:"";
}
async function exportEvaluationReport(button){
  button.disabled=true;
  try{
    const report=await api(`/api/evaluation-reports/${encodeURIComponent(button.dataset.evaluationReportExport)}`);
    downloadJson("evaluation-report.json",report);
  }finally{if(button.isConnected)button.disabled=false;}
}
document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.dataset.caseFileRole)await chooseEvaluationFile(button);
    if(button.dataset.clearCaseFiles)clearEvaluationFiles(button);
    if(button.dataset.evaluationReportExport)await exportEvaluationReport(button);
    if(button.dataset.savedExperimentRun)await runEvaluation(null,null,button.dataset.savedExperimentRun);
    if(button.dataset.experimentExport){const item=await api(`/api/catalog/experiments/${encodeURIComponent(button.dataset.experimentExport)}`);downloadJson(`${item.id}.json`,item.data);}
    if(button.id==="saved-experiment-add-variant"){const root=$("saved-experiment-variants");if(root.children.length>=4)throw new Error("Maximum four variants.");root.insertAdjacentHTML("beforeend",variantFields(root.children.length+1,{},true));updateExperimentEstimate();}
    if(button.hasAttribute("data-remove-experiment-variant")){const root=$("saved-experiment-variants");if(root.children.length<=1)throw new Error("Keep at least one variant.");button.closest(".experiment-variant").remove();updateExperimentEstimate();}
    if(button.id==="evaluation-add-case"){if($("evaluation-cases").children.length>=100)throw new Error("Maximum 100 cases.");$("evaluation-cases").insertAdjacentHTML("beforeend",caseFields());}
    if(button.hasAttribute("data-remove-case"))button.closest(".evaluation-case").remove();
    if(button.id==="evaluation-apply-evaluator")await applySavedEvaluator(button);
    if(button.dataset.evaluatorExport){const item=await api(`/api/catalog/evaluators/${encodeURIComponent(button.dataset.evaluatorExport)}`);downloadJson(`${item.id}.json`,item.data);}
    if(button.id==="dataset-draft-generate")await generateDatasetDraft(button);
    if(button.id==="dataset-draft-append")await applyDatasetDraft(button,true);
    if(button.id==="dataset-draft-replace")await applyDatasetDraft(button,false);
    if(button.id==="evaluation-import")await importEvaluationDataset(button);
    if(button.id==="evaluation-use-dataset")await copySavedEvaluationDataset(button);
    if(button.dataset.datasetExport){const item=await api(`/api/catalog/datasets/${encodeURIComponent(button.dataset.datasetExport)}`);downloadJson(`${item.id}.json`,item.data);}
    if(button.dataset.evaluate)await runEvaluation(button.dataset.evaluate);
    if(button.dataset.experiment){experimentSuite=button.dataset.experiment;showDetail("Experiment Lab",'<p>Compare the same suite across provider/model variants. Each variant runs every case and consumes its provider’s usage.</p><div id="experiment-variants">'+variantFields(1)+variantFields(2)+'</div><p class="form-error" id="experiment-error" role="alert"></p><div class="card-actions"><button class="secondary" id="experiment-add">+ Add variant</button><button id="experiment-run">Run comparison</button></div>');}
    if(button.id==="experiment-add"){if($("experiment-variants").children.length>=4)throw new Error("Maximum four variants.");$("experiment-variants").insertAdjacentHTML("beforeend",variantFields($("experiment-variants").children.length+1));}
    if(button.id==="experiment-run"){const variants=[...document.querySelectorAll(".experiment-variant")].map(row=>({name:row.querySelector(".variant-name").value,provider:row.querySelector(".variant-provider").value,model:row.querySelector(".variant-model").value,...(row.querySelector(".variant-effort")?.value?{effort:row.querySelector(".variant-effort").value}:{})}));await runEvaluation(experimentSuite,variants);}
    if(button.dataset.evaluationReport)await showEvaluationReport(button.dataset.evaluationReport);
    if(button.dataset.scheduleToggle){const item=library.items.find(v=>v.id===button.dataset.scheduleToggle);button.disabled=true;try{await api(`/api/studio/schedules/${encodeURIComponent(item.id)}/enabled`,{revision:item.revision,enabled:!item.data.enabled});await refreshLibrary();}finally{button.disabled=false;}}
    if(button.dataset.scheduleDetails)await showScheduleHistory(button.dataset.scheduleDetails);
    if(button.id==="refresh-operation-results")await refreshOperationResults();
    if(button.dataset.remoteRefresh){button.disabled=true;try{await api(`/api/workbench/externalagents/${encodeURIComponent(button.dataset.remoteRefresh)}/refresh`,{});await refreshOperationResults();}finally{button.disabled=false;}}
    if(button.dataset.connectorTest){const item=library.items.find(item=>item.id===button.dataset.connectorTest);showDetail(item.name,`<p>${item.data.type==="http"?"Fetch the configured HTTP endpoint.":item.data.type==="sqlite"?"Run a read-only SQLite query.":"Choose an enabled service action and review its arguments."}</p><form id="connector-test-form" data-id="${escapeHtml(item.id)}">${!["http","sqlite"].includes(item.data.type)?`<label>Action<select name="action">${item.data.actions.map(action=>`<option>${escapeHtml(action)}</option>`).join("")}</select></label><label>Action arguments (JSON object)<textarea name="arguments">{}</textarea></label>${item.data.type==="mcp"?'<label>MCP tool name<input name="tool"></label>':""}<label class="check-label"><input name="confirm" type="checkbox"> I reviewed and confirm any external changes from this action</label>`:""}${item.data.type==="sqlite"?'<label>Read-only query<textarea name="query">SELECT name FROM sqlite_master WHERE type=\'table\'</textarea></label>':""}<button type="submit">Run connection test</button><pre id="connector-test-output"></pre></form>`);}
    if(button.dataset.entrypoint){const item=library.items.find(item=>item.id===button.dataset.entrypoint),channel=item.data.type&&item.data.type!=="webhook",isHmac=item.data.auth_mode==="hmac";const targetUrl=isHmac?(location.origin+(item.data.slug?"/gw/"+item.data.slug+(item.data.route_path||""):"/api/webhooks/"+item.id+(item.data.route_path||""))):(location.origin+(channel?"/api/channel-events/":"/api/webhooks/")+item.id);const authInfo=isHmac?`HMAC signature header: ${escapeHtml(item.data.hmac_header||"X-Hub-Signature-256")} (${escapeHtml(item.data.hmac_algorithm||"sha256")}) using secret in ${escapeHtml(item.data.token_env)}`:(channel?"Verification setting: ":"Authorization: Bearer &lt;value of ")+escapeHtml(item.data.token_env)+(channel?"":"&gt;");showDetail(item.name,`<p>${isHmac?"Send signed JSON webhook requests to this URL. The incoming payload triggers the target workflow or agent automatically.":channel?"Register this incoming URL in your service settings. Requests must pass the service’s verification; Teams uses its configured Graph subscription secret.":"POST JSON with a task field to this authenticated URL."}</p><pre>${escapeHtml(targetUrl)}</pre><p>${authInfo}</p><p>${item.data.a2a_enabled?`<h3>A2A gateway</h3><p>Use the same bearer token. Supports JSON-RPC 1.0 and 0.3, streaming and task reads. Approvals stay in Nexus. Configure this card path in the remote client:</p><pre>/api/a2a/${escapeHtml(item.id)}/agent-card.json</pre><p>RPC endpoint:</p><pre>${escapeHtml(location.origin+"/api/a2a/"+item.id)}</pre>`:""}${item.data.enabled?"Enabled. Incoming channel identities and project permissions are checked before execution.":"Disabled. Edit the entrypoint when account setup is complete."}</p>`);}
    if(button.dataset.decisionmodelTest){const id=button.dataset.decisionmodelTest;if(typeof openWorkbench==="function")openWorkbench("decisions");else location.hash="#workbench/decisions";setTimeout(async()=>{if(typeof refreshDecisionPresets==="function")await refreshDecisionPresets();const select=$("wb-decision-preset");if(select){select.value=id;select.dispatchEvent(new Event("change"));}},50);}
  }catch(error){if($("experiment-error"))$("experiment-error").textContent=error.message;else $("library-error").textContent=error.message;}
});
document.addEventListener("submit",async event=>{
  if(event.target.id!=="connector-test-form")return;event.preventDefault();const form=event.target,button=form.querySelector("button");button.disabled=true;
  try{const selected=library.items.find(item=>item.id===form.dataset.id);const native=selected&&!["http","sqlite"].includes(selected.data.type);const result=await api(native?`/api/workbench/connections/${encodeURIComponent(form.dataset.id)}/action`:`/api/connectors/${encodeURIComponent(form.dataset.id)}/test`,(()=>{const data=Object.fromEntries(new FormData(form));if(data.arguments)data.arguments=JSON.parse(data.arguments);data.confirm=new FormData(form).has("confirm");return data;})());$("connector-test-output").textContent=result.output;}
  catch(error){$("connector-test-output").textContent=error.message;}finally{button.disabled=false;}
});
if(state.ready&&view()==="library")refreshLibrary();

function updateConnectionFields(){const type=document.querySelector("#connector-type")?.value;if(!type)return;const fields={http:["url","token_env","allow_private"],sqlite:["path"],github:["repo","token_env","actions"],gitlab:["repo","base_url","token_env","actions"],telegram:["token_env","channel","credential_scope","actions"],discord:["token_env","channel","credential_scope","actions"],slack:["token_env","channel","credential_scope","actions"],openclaw:["base_url","token_env","channel","credential_scope","actions"],mcp:["command","mcp_persistent","mcp_args","mcp_env","mcp_tools","mcp_safe","actions"]};const all=new Set(Object.values(fields).flat());document.querySelectorAll("#library-fields [name]").forEach(input=>{if(all.has(input.name))input.closest("label").hidden=!(fields[type]||[]).includes(input.name);});}
document.addEventListener("change",event=>{if(event.target.id==="connector-type")updateConnectionFields();if(event.target.id==="decisionmodel-backend"){const modelInput=document.querySelector('#library-fields [name="model"]');if(modelInput&&event.target.value==="openai"&&!modelInput.value)modelInput.value="gpt-6-luna";}});

async function showScheduleHistory(id){
  const result=await api(`/api/studio/schedules/${encodeURIComponent(id)}`),item=result.schedule,zone=item.data.timezone||"UTC";
  const date=value=>new Date(value).toLocaleString(undefined,{timeZone:zone,timeZoneName:"short"});
  showDetail(item.name,`<p>${item.data.enabled?"Enabled":"Paused"} · ${escapeHtml(zone)} · revision ${item.revision}</p><p>Missed recurring dates coalesce into one run when enabled. A completed one-time schedule stays completed when paused or edited; change its first run date to schedule another occurrence.</p>${result.state.legacy_timing_unverified?"<p class=callout>Earlier timing history is missing. Saved progress was kept; review the next date before enabling.</p>":""}<h3>${item.data.enabled?"Upcoming dates":"Dates if enabled"}</h3>${result.upcoming.length?`<ol>${result.upcoming.map(v=>`<li>${escapeHtml(date(v))}</li>`).join("")}</ol>`:"<p>No remaining occurrences.</p>"}<h3>Execution history</h3>${result.occurrences.length?result.occurrences.map(v=>`<article class="schedule-occurrence"><p>${escapeHtml(date(v.due_at))} · ${escapeHtml(v.status)} · schedule revision ${v.schedule_revision}</p><small>Dispatched ${escapeHtml(date(v.claimed_at))}</small>${v.run_id?`<button class="secondary" data-run="${escapeHtml(v.run_id)}">Run details</button>`:"<p>No acknowledged run. Inspect activity before submitting again.</p>"}</article>`).join(""):"<p>No recorded executions. Earlier legacy activity may be available in Activity.</p>"}${result.older_occurrences?`<p>${result.older_occurrences} older occurrences retained in workspace state.</p>`:""}`);
}

// Teams has Studio-owned results/approvals; unsupported controls do not enter FormData.
function updateIncomingServiceFields(){
  const epType=document.querySelector('[name="entrypoint_type"]')?.value;
  const teams=epType==="teams";
  const isWebhook=epType==="webhook";
  const authModeLabel=document.querySelector("#entrypoint-auth-mode-label");
  if(authModeLabel)authModeLabel.hidden=!isWebhook;
  const isHmac=isWebhook&&document.querySelector("#entrypoint-auth-mode")?.value==="hmac";
  const hmacDetails=document.querySelector("[data-hmac-entrypoint]");
  if(hmacDetails){hmacDetails.hidden=!isHmac;if(isHmac)hmacDetails.open=true;}
  const details=document.querySelector("[data-teams-entrypoint]");
  if(details){details.hidden=!teams;if(teams)details.open=true;}
  for(const name of ["notification_connector_id","auto_reply","approval_notifications","a2a_enabled","mcp_enabled"]){
    const input=document.querySelector(`[name="${name}"]`);if(!input)continue;
    const label=input.closest("label");
    const hide=teams||(isHmac&&["a2a_enabled","mcp_enabled"].includes(name));
    label.hidden=hide;input.disabled=hide;
    if(name==="mcp_enabled"&&label.nextElementSibling?.tagName==="P")label.nextElementSibling.hidden=hide;
  }
  const threaded=document.querySelector('[name="threaded_conversations"]');
  if(threaded){threaded.closest("label").hidden=epType!=="slack";threaded.disabled=epType!=="slack"||!String(document.querySelector('[name="target"]')?.value||"").startsWith("agent:");}
  const threadHelp=document.querySelector("[data-slack-thread-help]");if(threadHelp)threadHelp.hidden=epType!=="slack";
  const files=document.querySelector('[name="capture_slack_files"]');if(files){files.closest("label").hidden=epType!=="slack";files.disabled=!threaded||threaded.disabled||!threaded.checked;}
  const fileHelp=document.querySelector("[data-slack-files-help]");if(fileHelp)fileHelp.hidden=epType!=="slack";
  const callbacks=document.querySelector('[name="a2a_push_destinations"]')?.closest("details");
  if(callbacks){callbacks.hidden=teams;callbacks.querySelectorAll("input,textarea").forEach(input=>input.disabled=teams);}
}
document.addEventListener("change",event=>{if(event.target.name==="entrypoint_type"||event.target.name==="threaded_conversations"||event.target.id==="entrypoint-auth-mode"||(event.target.id==="operation-target"&&document.querySelector('[name="entrypoint_type"]')))updateIncomingServiceFields();});
function updateScheduleTargetFields(){
  const targetSelect=document.querySelector('#operation-target, [name="target"]');
  if(!targetSelect||typeof library==="undefined"||library.kind!=="schedules")return;
  const isWf=targetSelect.value.startsWith("workflow:");
  const help=document.getElementById("schedule-target-help");
  const taskLabel=document.getElementById("schedule-task-label");
  const taskInput=document.getElementById("schedule-task");
  const wfInput=document.getElementById("schedule-workflow-input");
  if(help)help.textContent=isWf?"Ready-made workflow: will execute all configured steps and specialists in sequence.":"Agent task: the specialist will execute your instructions at each scheduled time.";
  if(taskLabel)taskLabel.firstChild.textContent=isWf?"Task / input (optional for workflows)":"Task instructions";
  if(taskInput){taskInput.required=!isWf;taskInput.placeholder=isWf?"Optional task text (leave blank to run the workflow directly)":"What should this agent do on schedule?";}
  if(wfInput)wfInput.style.display=isWf?"":"none";
}
document.addEventListener("change",event=>{if((event.target.id==="operation-target"||event.target.name==="target")&&typeof library!=="undefined"&&library.kind==="schedules")updateScheduleTargetFields();});

document.addEventListener("input",event=>{if(event.target.name==="runsPerExample")updateExperimentEstimate();});

document.addEventListener("change",event=>{if(event.target.id==="saved-experiment-suite")updateExperimentEstimate();});

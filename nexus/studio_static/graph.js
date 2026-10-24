"use strict";
const graphEditor={nodes:[],edges:[],selected:null,id:null,revision:null,drag:null,argumentText:{}};
function graphNodePosition(node,index){const p=node.position||{x:40+index*230,y:80};return {x:Number.isFinite(p.x)?Math.max(0,Math.min(10000,p.x)):40,y:Number.isFinite(p.y)?Math.max(0,Math.min(10000,p.y)):80};}
function arrangeGraph(){
  const levels={START:-1};let remaining=[...graphEditor.nodes];
  for(let round=0;round<41&&remaining.length;round++){
    const next=[];
    for(const node of remaining){const parents=graphEditor.edges.filter(edge=>edge.target===node.id).map(edge=>edge.source);if(parents.every(id=>levels[id]!==undefined))levels[node.id]=parents.length?Math.max(...parents.map(id=>levels[id]))+1:0;else next.push(node);}
    if(next.length===remaining.length)break;remaining=next;
  }
  const rows={};graphEditor.nodes.forEach(node=>{const level=levels[node.id]||0;const row=rows[level]||0;rows[level]=row+1;node.position={x:40+level*245,y:45+row*135};});renderGraphCanvas();
}
function renderGraphCanvas(){
  const nodes=graphEditor.nodes,svg=$("graph-canvas");let width=750,height=220;
  nodes.forEach((node,index)=>{const p=graphNodePosition(node,index);width=Math.max(width,p.x+240);height=Math.max(height,p.y+120);});
  svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.setAttribute("width",width);svg.setAttribute("height",height);
  const edges=graphEditor.edges.filter(edge=>edge.source!=="START").map(edge=>{const source=nodes.find(node=>node.id===edge.source),target=nodes.find(node=>node.id===edge.target);if(!source||!target)return "";const a=graphNodePosition(source,nodes.indexOf(source)),b=graphNodePosition(target,nodes.indexOf(target));return `<path d="M ${a.x+185} ${a.y+35} C ${a.x+215} ${a.y+35}, ${b.x-30} ${b.y+35}, ${b.x} ${b.y+35}" marker-end="url(#graph-arrow)" class="graph-edge"/>`;}).join("");
  svg.innerHTML='<defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>'+edges+nodes.map((node,index)=>{const p=graphNodePosition(node,index),entry=graphEditor.edges.some(edge=>edge.source==="START"&&edge.target===node.id);return `<g transform="translate(${p.x},${p.y})" data-graph-node="${escapeHtml(node.id)}" role="button" tabindex="0" aria-label="Select ${escapeHtml(node.name)}" class="graph-node ${node.id===graphEditor.selected?"selected":""}"><rect width="185" height="76" rx="8"/><text x="14" y="23" class="graph-node-kind">${node.kind==="approval"?"APPROVAL":node.kind==="tool"?"TOOL":node.kind!=="agent"?escapeHtml(node.kind.toUpperCase()):entry?"ENTRY AGENT":"AGENT"}</text><text x="14" y="48">${escapeHtml(String(node.name||node.id).slice(0,23))}</text></g>`;}).join("");
  const options=nodes.map(node=>`<option value="${escapeHtml(node.id)}">${escapeHtml(node.name)}</option>`).join("");
  $("graph-edge-source").innerHTML='<option value="START">START</option>'+options;$("graph-edge-target").innerHTML=options;
  $("graph-edges").innerHTML=graphEditor.edges.map((edge,index)=>`<div class="graph-edge-row"><span>${escapeHtml(edge.source)} → ${escapeHtml(edge.target)}${edge.choice?` · choice “${escapeHtml(edge.choice)}”`:""}${edge.contains?` · contains “${escapeHtml(edge.contains)}”`:""}</span><button type="button" class="secondary" data-remove-edge="${index}">Remove</button></div>`).join("");
}
function selectGraphNode(id){
  graphEditor.selected=id;const node=graphEditor.nodes.find(node=>node.id===id);renderGraphCanvas();
  $("graph-node-fields").innerHTML=node?`<div class="graph-node-settings"><label>Node name<input id="graph-node-name" value="${escapeHtml(node.name)}"></label>${node.kind==="agent"?`<label>Saved agent<select id="graph-node-agent">${state.agents.filter(item=>item.document).map(item=>`<option value="${escapeHtml(item.id)}" ${node.agent_id===item.id?"selected":""}>${escapeHtml(item.document.metadata?.name||item.id)}</option>`).join("")}</select></label><label class="graph-node-prompt">Instructions · use {task}<textarea id="graph-node-prompt">${escapeHtml(node.prompt||"{task}")}</textarea></label>`:node.kind==="tool"?`<label>Registered tool<select id="graph-node-tool">${state.tools.map(tool=>`<option value="${escapeHtml(tool.id)}" ${node.tool_id===tool.id?"selected":""}>${escapeHtml(tool.id)}</option>`).join("")}</select></label><label class="graph-node-prompt">${graphEditor.meta.version===2?"Keyword arguments (JSON; data references supported)":"Keyword arguments (JSON) · {task} and {previous} supported"}<textarea id="graph-node-arguments">${escapeHtml(graphEditor.argumentText[node.id]??JSON.stringify(node.kwargs||{},null,2))}</textarea></label>`:node.kind==="approval"?'<p>Workflow stops here until an operator approves. Rejecting cancels the run.</p>':""}${node.kind==="decision"?`<label>Decision model preset<select id="graph-node-decision-model"><option value="">Custom / inline configuration</option>${(state.decisionmodels||[]).map(dm=>`<option value="${escapeHtml(dm.id)}" ${(node.decision?.decision_model_id===dm.id||node.decision_model_id===dm.id)?"selected":""}>${escapeHtml(dm.name)} (${escapeHtml(dm.data.backend)} · ${escapeHtml(dm.data.model)})</option>`).join("")}</select></label>`:""}${["decision","integration","delay"].includes(node.kind)?`<label class="graph-node-prompt">Step configuration (JSON)<textarea id="graph-node-config">${escapeHtml(graphEditor.argumentText[node.id]??JSON.stringify(Object.fromEntries(Object.entries(node).filter(([key])=>!["id","name","kind","position","retries","input","input_schema","output_schema","timeout_seconds","depends_on"].includes(key))),null,2))}</textarea></label><p>Integration changes must directly follow approval gates. Decision edges match the named choice exactly.</p>`:""}<label>Automatic retries (0–3)<input id="graph-node-retries" type="number" min="0" max="3" value="${node.retries||0}"></label><button type="button" class="secondary" id="graph-remove-node">Remove node</button></div>`:"";
  renderAdvancedNodeSettings(node);
}
async function openGraph(id){
  if(typeof loadDecisionModels==="function")await loadDecisionModels().catch(()=>{});
  graphEditor.policy=(await api("/api/studio/workflow-policy")).effective;
  graphEditor.stack=[];graphEditor.fieldText={};graphEditor.graphText={};
  graphEditor.argumentText={};graphEditor.meta={version:2};graphEditor.id=id||null;graphEditor.revision=null;
  const item=id?state.workflows.find(item=>item.id===id):null;
  graphEditor.nodes=structuredClone(item?.document.graph.nodes||[]);graphEditor.edges=structuredClone(item?.document.graph.edges||[]);graphEditor.selected=null;
  if(item){graphEditor.revision=item.revision;graphEditor.meta=Object.fromEntries(Object.entries(item.document.graph).filter(([k])=>!["nodes","edges"].includes(k)));}
  $("graph-name").value=item?.document.metadata?.name||"";$("graph-description").value=item?.document.metadata?.description||"";
  $("graph-error").textContent="";$("graph-node-fields").replaceChildren();$("graph-editor").showModal();renderGraphCanvas();renderGraphScope();
}
function addGraphNode(kind){
  if(["map","loop","workflow","switch","input"].includes(kind)&&graphEditor.meta.version!==2)throw new Error("Advanced nodes need a new version-2 workflow; legacy graphs keep their format.");
  if(graphEditor.nodes.length>=40)throw new Error("Maximum 40 nodes.");
  const first=state.agents.find(item=>item.document);if(kind==="agent"&&!first)throw new Error("Create an agent first.");
  const id="node-"+crypto.randomUUID().slice(0,8),node={id,name:({approval:"Review and approve",tool:"Tool step",map:"Each item",loop:"Repeat",workflow:"Nested workflow",switch:"Choose a branch",input:"Ask for input"})[kind]||kind[0].toUpperCase()+kind.slice(1)+" step",kind,position:{x:40+graphEditor.nodes.length%3*245,y:45+Math.floor(graphEditor.nodes.length/3)*135}};
  if(kind==="agent"){node.agent_id=first.id;node.prompt="{task}";}
  if(kind==="tool"){node.tool_id="text:wordcount";node.args=[];node.kwargs={text:graphEditor.meta.version===2?{ref:"input"}:"{task}"};}
  if(kind==="decision")node.decision={instructions:"Choose the best route",options:{research:"Gather evidence",review:"Check evidence"},default:"review",threshold:0.6,backend:"openrouter"};
  if(kind==="integration"){node.connector_id="saved-connection-id";node.action="list_issues";node.arguments={};}
  if(kind==="delay")node.delay_seconds=1;
  if(kind==="map"){node.items={ref:"input"};node.max_items=Math.min(100,graphEditor.policy.map_items);node.concurrency=Math.min(4,graphEditor.policy.map_concurrency);node.body=identityGraph();}
  if(kind==="loop"){node.max_iterations=Math.min(100,graphEditor.policy.loop_iterations);node.condition={left:{ref:"iteration"},op:"lt",right:0};node.body=identityGraph();}
  if(kind==="switch"){node.cases=[{condition:{left:true,op:"eq",right:true},value:{choice:"first"}}];node.default=null;}
  if(kind==="workflow")node.workflow_id=state.workflows.find(w=>w.document?.graph?.version===2&&w.id!==graphEditor.id)?.id||"";
  if(kind==="input")node.output_schema={type:"string"};
  graphEditor.nodes.push(node);if(graphEditor.nodes.length===1)graphEditor.edges.push({source:"START",target:id,contains:""});selectGraphNode(id);
}
$("graph-form").addEventListener("submit",async event=>{
  event.preventDefault();$("graph-save").disabled=true;$("graph-error").textContent="";
  try{commitGraphFields();while(graphEditor.stack.length)leaveGraphScope();const payload={name:$("graph-name").value,description:$("graph-description").value,graph:{...(graphEditor.meta||{}),nodes:graphEditor.nodes,edges:graphEditor.edges}};if(graphEditor.id)payload.revision=graphEditor.revision;await api(graphEditor.id?`/api/workflows/${encodeURIComponent(graphEditor.id)}/update`:"/api/workflows",payload);$("graph-editor").close();await refreshDefinitions();location.hash="workflows";}
  catch(error){$("graph-error").textContent=error.message;}finally{$("graph-save").disabled=false;}
});
document.addEventListener("input",event=>{
  const node=graphEditor.nodes.find(node=>node.id===graphEditor.selected);if(!node)return;
  if(event.target.id==="graph-node-name"){node.name=event.target.value;renderGraphCanvas();}
  if(event.target.id==="graph-node-config")graphEditor.argumentText[node.id]=event.target.value;
  if(event.target.id==="graph-node-retries")node.retries=Number(event.target.value);
  if(event.target.id==="graph-node-arguments"){graphEditor.argumentText[node.id]=event.target.value;}
  if(event.target.id==="graph-node-tool"){node.tool_id=event.target.value;}
  if(event.target.id==="graph-node-agent")node.agent_id=event.target.value;
  if(event.target.id==="graph-node-prompt")node.prompt=event.target.value;
});
document.addEventListener("click",async event=>{
  const node=event.target.closest("[data-graph-node]");if(node){selectGraphNode(node.dataset.graphNode);return;}
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.id==="create-graph")await openGraph();if(button.dataset.editGraph)await openGraph(button.dataset.editGraph);
    if(button.id==="graph-add-decision")addGraphNode("decision");if(button.id==="graph-add-integration")addGraphNode("integration");if(button.id==="graph-add-delay")addGraphNode("delay");
    if(button.dataset.graphAdvanced)addGraphNode(button.dataset.graphAdvanced);
    if(button.dataset.graphBody)enterGraphScope("body",button.dataset.graphBody);
    if(button.dataset.graphHandler)enterGraphScope(button.dataset.graphHandler);
    if(button.id==="graph-scope-back")leaveGraphScope();
    if(button.dataset.removeHandler){delete graphEditor.meta[button.dataset.removeHandler];renderGraphScope();}
    if(button.id==="graph-add-tool")addGraphNode("tool");
    if(button.id==="graph-add-agent")addGraphNode("agent");if(button.id==="graph-add-gate")addGraphNode("approval");if(button.id==="graph-layout")arrangeGraph();
    if(button.id==="graph-remove-node"){graphEditor.nodes=graphEditor.nodes.filter(node=>node.id!==graphEditor.selected);graphEditor.edges=graphEditor.edges.filter(edge=>edge.source!==graphEditor.selected&&edge.target!==graphEditor.selected);selectGraphNode(null);}
    if(button.id==="graph-add-edge"){graphEditor.edges.push({source:$("graph-edge-source").value,target:$("graph-edge-target").value,contains:$("graph-edge-condition").value,...($("graph-edge-choice").value?{choice:$("graph-edge-choice").value}:{})});$("graph-edge-condition").value="";$("graph-edge-choice").value="";renderGraphCanvas();}
    if(button.dataset.removeEdge!==undefined){graphEditor.edges.splice(Number(button.dataset.removeEdge),1);renderGraphCanvas();}
    if(button.dataset.approveRun||button.dataset.rejectRun){const id=button.dataset.approveRun||button.dataset.rejectRun;const run=await api(`/api/runs/${encodeURIComponent(id)}/resume`,{decision:button.dataset.approveRun?"approve":"reject",evaluation_checkpoint:button.dataset.evaluationCheckpoint||undefined});showRun(run);if(run.status==="queued"||run.status==="running")pollRun(id,updateRunDetail).then(()=>refreshRuns()).catch(error=>{updateRunDetail(error.run||{id,status:"failed",error:error.message});});}
  }catch(error){if($("graph-editor").open)$("graph-error").textContent=error.message;else notify(error.message);}
});
function graphPointerPosition(event) {
  const canvas=$("graph-canvas"), point=canvas.createSVGPoint();
  point.x=event.clientX;point.y=event.clientY;
  return point.matrixTransform(canvas.getScreenCTM().inverse());
}
$("graph-canvas").addEventListener("keydown",event=>{
  const el=event.target.closest("[data-graph-node]");if(!el)return;
  if(["Enter"," "].includes(event.key)){event.preventDefault();selectGraphNode(el.dataset.graphNode);}
  else if(["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(event.key)){
    event.preventDefault();const node=graphEditor.nodes.find(item=>item.id===el.dataset.graphNode),p=graphNodePosition(node,graphEditor.nodes.indexOf(node)),step=event.shiftKey?40:10;
    node.position={x:Math.max(0,Math.min(10000,p.x+(event.key==="ArrowRight"?step:event.key==="ArrowLeft"?-step:0))),y:Math.max(0,Math.min(10000,p.y+(event.key==="ArrowDown"?step:event.key==="ArrowUp"?-step:0)))};
    renderGraphCanvas();$("graph-canvas").querySelector(`[data-graph-node="${node.id}"]`).focus();
  }
});
$("graph-canvas").addEventListener("pointerdown",event=>{
  if(event.button!==0)return;const el=event.target.closest("[data-graph-node]");if(!el)return;
  const node=graphEditor.nodes.find(node=>node.id===el.dataset.graphNode),p=graphNodePosition(node,graphEditor.nodes.indexOf(node)),point=graphPointerPosition(event);
  graphEditor.drag={id:node.id,pointer:event.pointerId,x:point.x,y:point.y,position:{...p},moved:false};
  $("graph-canvas").setPointerCapture(event.pointerId);
});
$("graph-canvas").addEventListener("pointermove",event=>{
  const drag=graphEditor.drag;if(!drag||drag.pointer!==event.pointerId)return;
  const node=graphEditor.nodes.find(node=>node.id===drag.id),point=graphPointerPosition(event),dx=point.x-drag.x,dy=point.y-drag.y;
  if(Math.abs(dx)+Math.abs(dy)<4&&!drag.moved)return;drag.moved=true;
  node.position={x:Math.min(10000,Math.max(0,drag.position.x+dx)),y:Math.min(10000,Math.max(0,drag.position.y+dy))};renderGraphCanvas();
});
$("graph-canvas").addEventListener("pointerup",event=>{
  const drag=graphEditor.drag;if(!drag||drag.pointer!==event.pointerId)return;graphEditor.drag=null;
  if($("graph-canvas").hasPointerCapture(event.pointerId))$("graph-canvas").releasePointerCapture(event.pointerId);
  selectGraphNode(drag.id);
});
for(const event of ["pointercancel","lostpointercapture"])$("graph-canvas").addEventListener(event,()=>graphEditor.drag=null);

// Structured authoring reuses the same canvas for map/loop bodies and handlers.
function identityGraph(){return {version:2,nodes:[{id:"result",name:"Return input",kind:"switch",cases:[{condition:{left:true,op:"eq",right:true},value:{ref:"input"}}]}],edges:[],output_mapping:{ref:"nodes.result"}};}
function currentGraph(){return {...graphEditor.meta,nodes:structuredClone(graphEditor.nodes),edges:structuredClone(graphEditor.edges)};}
function jsonSetting(property,label,value,node){
  const pending=node?graphEditor.fieldText[node.id]?.[property]:graphEditor.graphText[property];
  return `<label class="graph-node-prompt">${escapeHtml(label)}<textarea rows="3" maxlength="131072" ${node?`data-node-json="${property}" data-node-owner="${node.id}"`:`data-graph-json="${property}"`}>${escapeHtml(pending??(value===undefined?"":JSON.stringify(value,null,2)))}</textarea></label>`;
}
function commitGraphFields(){
  const nodes=structuredClone(graphEditor.nodes),meta=structuredClone(graphEditor.meta);
  for(const node of nodes){
    if(node.kind==="tool"&&graphEditor.argumentText[node.id]!==undefined)node.kwargs=JSON.parse(graphEditor.argumentText[node.id]);
    if(["decision","integration","delay"].includes(node.kind)&&graphEditor.argumentText[node.id]!==undefined){
      const config=JSON.parse(graphEditor.argumentText[node.id]);
      const allowed={decision:["decision"],integration:["connector_id","action","arguments","tool"],delay:["delay_seconds"]}[node.kind];
      if(!config||Array.isArray(config)||typeof config!=="object"||Object.keys(config).some(k=>!allowed.includes(k)))throw new Error("Step configuration must contain only settings for this step type.");
      for(const key of allowed)delete node[key];Object.assign(node,config);
    }
    for(const [key,text] of Object.entries(graphEditor.fieldText[node.id]||{})){
      if(text.trim())node[key]=JSON.parse(text);else delete node[key];
    }
  }
  for(const [key,text] of Object.entries(graphEditor.graphText||{})){
    if(text.trim())meta[key]=JSON.parse(text);else delete meta[key];
  }
  graphEditor.nodes=nodes;graphEditor.meta=meta;
}
function loadGraphScope(graph){
  graphEditor.nodes=structuredClone(graph.nodes||[]);graphEditor.edges=structuredClone(graph.edges||[]);
  graphEditor.meta=Object.fromEntries(Object.entries(graph).filter(([key])=>!["nodes","edges"].includes(key)));
  graphEditor.selected=null;graphEditor.fieldText={};graphEditor.argumentText={};graphEditor.graphText={};
  $("graph-error").textContent="";$("graph-node-fields").replaceChildren();renderGraphCanvas();renderGraphScope();
}
function enterGraphScope(relation,nodeId){
  commitGraphFields();if(graphEditor.stack.length+1>=graphEditor.policy.nesting_depth)throw new Error("Administrator nesting limit reached.");
  const parent=currentGraph(),node=parent.nodes.find(n=>n.id===nodeId);
  const child=relation==="body"?node.body:parent[relation]||identityGraph();
  graphEditor.stack.push({parent,relation,nodeId,label:node?node.name:relation==="on_success"?"Completion handler":"Error handler"});loadGraphScope(child);
}
function leaveGraphScope(){
  if(!graphEditor.stack.length)return;commitGraphFields();const child=currentGraph(),saved=graphEditor.stack.pop();
  if(saved.relation==="body")saved.parent.nodes.find(n=>n.id===saved.nodeId).body=child;else saved.parent[saved.relation]=child;
  loadGraphScope(saved.parent);
}
function renderGraphScope(){
  let root=$("graph-scope");if(!root){root=document.createElement("div");root.id="graph-scope";$("graph-node-fields").before(root);}
  const structured=graphEditor.meta.version===2;
  document.querySelectorAll('[data-graph-advanced]').forEach(b=>b.disabled=!structured);
  root.innerHTML=`<div class="graph-scope-path">${graphEditor.stack.length?'<button type="button" class="secondary" id="graph-scope-back">Back to parent</button>':""}<p>${escapeHtml(["Workflow",...graphEditor.stack.map(p=>p.label)].join(" / "))}</p></div>`+(structured?`<details class="graph-contract"><summary>Input, output and handlers</summary><p>Reference values with {"ref":"input"} or {"ref":"nodes.node-id"}. Node references need an upstream connection.</p>${jsonSetting("input_schema","Workflow input schema (optional)",graphEditor.meta.input_schema)}${jsonSetting("output_mapping","Workflow output mapping (optional)",graphEditor.meta.output_mapping)}${jsonSetting("output_schema","Workflow output schema (optional)",graphEditor.meta.output_schema)}<div class="graph-toolbar">${[["on_success","Completion handler"],["on_error","Error handler"]].map(([key,label])=>`<button type="button" class="secondary" data-graph-handler="${key}">${graphEditor.meta[key]?"Edit":"Add"} ${label.toLowerCase()}</button>${graphEditor.meta[key]?`<button type="button" class="secondary" data-remove-handler="${key}">Remove ${label.toLowerCase()}</button>`:""}`).join("")}</div></details>`:'<p class="callout">Legacy workflow: original format and task templates are preserved. Create a new workflow for structured nodes.</p>');
}
function renderAdvancedNodeSettings(node){
  if(!node||graphEditor.meta.version!==2)return;
  const box=document.createElement("div");box.className="graph-node-settings graph-structured-settings";
  let html="";
  if(node.kind==="map")html=jsonSetting("items","Collection to process",node.items,node)+`<label>Maximum items<input type="number" min="1" max="${graphEditor.policy.map_items}" value="${node.max_items}" data-node-number="max_items"></label><label>Parallel items<input type="number" min="1" max="${graphEditor.policy.map_concurrency}" value="${node.concurrency}" data-node-number="concurrency"></label><button type="button" class="secondary" data-graph-body="${node.id}">Edit item steps</button>`;
  if(node.kind==="loop")html=jsonSetting("condition","Continue while (deterministic condition)",node.condition,node)+`<label>Maximum iterations<input type="number" min="1" max="${graphEditor.policy.loop_iterations}" value="${node.max_iterations}" data-node-number="max_iterations"></label><button type="button" class="secondary" data-graph-body="${node.id}">Edit repeated steps</button><p>Body runs first. Condition reads result and iteration; true continues, false stops.</p>`;
  if(node.kind==="switch")html=jsonSetting("cases","Conditions and returned values (first matching case wins)",node.cases,node)+jsonSetting("default","Default value",node.default,node)+'<p>Conditions use left, op and right. Operators: eq, ne, gt, gte, lt, lte, contains, in. Return {"choice":"name"} to select a named edge.</p>';
  if(node.kind==="workflow")html=`<label>Saved structured workflow<select data-node-workflow><option value="">Choose a workflow</option>${state.workflows.filter(w=>w.id!==graphEditor.id&&w.document?.graph?.version===2).map(w=>`<option value="${escapeHtml(w.id)}" ${node.workflow_id===w.id?"selected":""}>${escapeHtml(w.document.metadata?.name||w.id)}</option>`).join("")}</select></label><p>Input is mapped below. Queued execution uses pinned definitions.</p>`;
  if(node.kind==="input")html='<p>Execution pauses for operator input. Set its expected response with the output schema below.</p>';
  html=`<p class="graph-node-prompt">Output reference: <code>nodes.${escapeHtml(node.id)}</code></p>`+html;
  html+=`<label>Timeout (seconds)<input type="number" min="1" max="3600" value="${node.timeout_seconds||300}" data-node-number="timeout_seconds"></label><details class="graph-node-prompt"><summary>Input and validation</summary>${jsonSetting("input","Input mapping (blank uses workflow input)",node.input,node)}${jsonSetting("input_schema","Input schema (optional)",node.input_schema,node)}${jsonSetting("output_schema","Output schema (optional)",node.output_schema,node)}${node.kind==="tool"?jsonSetting("args","Positional arguments",node.args||[],node):""}</details>`;
  box.innerHTML=html;$("graph-node-fields").append(box);
}
document.addEventListener("input",event=>{
  const element=event.target;
  if(element.dataset.graphJson)graphEditor.graphText[element.dataset.graphJson]=element.value;
  if(element.dataset.nodeJson){const id=element.dataset.nodeOwner;graphEditor.fieldText[id]??={};graphEditor.fieldText[id][element.dataset.nodeJson]=element.value;}
  if(element.dataset.nodeNumber){const node=graphEditor.nodes.find(n=>n.id===graphEditor.selected);if(node)node[element.dataset.nodeNumber]=Number(element.value);}
});
document.addEventListener("change",event=>{
  if(event.target.hasAttribute("data-node-workflow")){const node=graphEditor.nodes.find(n=>n.id===graphEditor.selected);if(node)node.workflow_id=event.target.value;}
  if(event.target.id==="graph-node-decision-model"){
    const node=graphEditor.nodes.find(n=>n.id===graphEditor.selected);
    if(!node||node.kind!=="decision")return;
    const modelId=event.target.value;
    const chosen=(state.decisionmodels||[]).find(m=>m.id===modelId);
    node.decision=node.decision||{};
    if(chosen){
      node.decision.decision_model_id=chosen.id;
      node.decision.backend=chosen.data.backend;
      node.decision.model=chosen.data.model;
      if(chosen.data.timeout_seconds)node.decision.timeout_seconds=chosen.data.timeout_seconds;
    }else{
      delete node.decision.decision_model_id;
    }
    const configArea=$("graph-node-config");
    if(configArea){
      const data=Object.fromEntries(Object.entries(node).filter(([k])=>!["id","name","kind","position","retries","input","input_schema","output_schema","timeout_seconds","depends_on"].includes(k)));
      configArea.value=JSON.stringify(data,null,2);
      graphEditor.argumentText[node.id]=configArea.value;
    }
  }
});

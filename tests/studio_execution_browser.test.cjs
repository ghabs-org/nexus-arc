// Replay behavior without a browser dependency. Real layout is verified separately.
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("nexus/studio_static/features.js","utf8");
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.style={};this.dataset={};this.isConnected=true;this.classList={add(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key];}
  querySelectorAll(){return [];}
}
function fixture(){
  const calls=[],timers=new Map(),root=new Element("section");let id=0;
  const context=vm.createContext({document:{createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},state:{detailRunId:"one"},updateRunDetail(){},api:async path=>{calls.push(path);return {kind:"text",text:"original"};},showDetail(){},$:()=>new Element("div"),previewContent:()=>new Element("preview"),featureApi:async path=>{calls.push(path);throw Error("Unexpected request");},setInterval:fn=>{timers.set(++id,fn);return id;},clearInterval:key=>timers.delete(key),console});
  vm.runInContext(source.slice(source.indexOf("function executionGraph("),source.indexOf("const embeddedAgent=")),context);
  return {context,calls,timers,root,render:data=>{context.root=root;context.data=data;vm.runInContext('renderExecutionReplay(root,data,{id:"one",status:"completed"})',context);}};
}
function data(events){return {graph:{nodes:[{id:"a",kind:"tool",name:"Count"}],edges:[]},states:{a:"completed"},timeline:events,replay:{previous_cursor:null},coverage:{provider_tool_events:"Only recorded calls.",missing_timestamps:0}};}
const events=[{id:"first",sequence:1,node_id:"a",kind:"node",status:"running",at:"2026-10-18T09:00:00Z"},{id:"last",sequence:2,node_id:"a",kind:"node",status:"completed",at:"2026-10-18T09:00:01Z"}];
function control(root,label){return root.children[1].children.find(node=>node.getAttribute("aria-label")===label);}

test("keyboard slider, stepping and playback only review saved events",()=>{
  const {render,root,calls,timers}=fixture();render(data(events));
  const slider=control(root,"Execution replay position");slider.value="0";slider.oninput();assert.match(root.children[2].textContent,/Event 0 of 2/);
  control(root,"Next execution event").onclick();assert.match(root.children[2].textContent,/Event 1 of 2/);
  control(root,"Play recorded execution events").onclick();assert.equal(timers.size,1);[...timers.values()][0]();assert.match(root.children[2].textContent,/Event 2 of 2/);assert.equal(timers.size,0);assert.deepEqual(calls,[]);
});

test("live refresh pauses old playback and preserves a selected event when earlier pages arrive",()=>{
  const {render,root,timers,calls}=fixture();const later=events.map((event,index)=>({...event,sequence:index+3}));render(data(later));
  control(root,"Previous execution event").onclick();assert.match(root.children[2].textContent,/Event 1 of 2/);
  render(data([{id:"older-a",sequence:1,kind:"run",status:"queued"},{id:"older-b",sequence:2,kind:"run",status:"running"},...later]));assert.match(root.children[2].textContent,/Event 3 of 4/);
  control(root,"Play recorded execution events").onclick();assert.equal(timers.size,1);render(data(later));assert.equal(timers.size,0);assert.deepEqual(calls,[]);
});

test("graph keeps readable node sizes inside a keyboard-scrollable region",()=>{
  const {context}=fixture();context.data=data(events);const box=vm.runInContext("executionGraph(data,1)",context);assert.equal(box.tabIndex,0);assert.equal(box.getAttribute("role"),"region");assert.equal(box.style.overflow,"auto");assert.equal(box.children[0].style.width,"600px");assert.equal(box.children[0].getAttribute("role"),"img");
});

test("artifact replay previews the captured immutable revision",async()=>{
  const {render,root,calls}=fixture();render(data([{id:"artifact",sequence:1,kind:"artifact",status:"created",artifact_id:"saved-file",artifact_revision:1}]));
  const button=root.children[4].children[0].children[0].children.find(node=>node.textContent==="View artifact");await button.onclick();
  assert.deepEqual(calls,["/api/catalog/artifacts/saved-file/preview?revision=1"]);
});

test("recorded participant graph follows replay state without inventing ordering",()=>{
  const {context}=fixture();context.data={graph:{nodes:[{id:"run",kind:"run",name:"Run"},{id:"invoke",kind:"invocation",name:"Assistant"}],edges:[{source:"run",target:"invoke",relationship:"run-membership"}],relationship_note:"Membership, not order."},states:{invoke:"completed"},timeline:[{id:"start",sequence:1,kind:"invocation",graph_node_id:"invoke",status:"running"},{id:"end",sequence:2,kind:"invocation",graph_node_id:"invoke",status:"completed"}]};
  const box=vm.runInContext("executionGraph(data,1)",context),svg=box.children[0],nodes=svg.children.filter(node=>node.tagName==="g");assert.equal(nodes[1].children.at(-1).textContent,"running");assert.equal(svg.children[0].getAttribute("stroke-dasharray"),"4 4");assert.equal(box.children[1].textContent,"Membership, not order.");
});

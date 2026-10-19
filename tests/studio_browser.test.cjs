// Run with `node --test tests/studio_browser.test.cjs` (no extra dependencies).
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync("nexus/studio_static/studio.js","utf8");

function monitorContext(api){
  const elements=new Map(),window=new EventTarget(),document={hidden:false,addEventListener(){}};
  const context=vm.createContext({api,window,document,Event,navigator:{onLine:true},state:{ready:true},sessionStorage:{setItem(){},getItem(){return "[]";}},setTimeout:(fn)=>setTimeout(fn,0),clearTimeout,console,$:id=>{if(!elements.has(id))elements.set(id,{hidden:true,textContent:""});return elements.get(id);}});
  vm.runInContext(source.slice(source.indexOf("const watchedRuns="),source.indexOf("async function sendMessage")),context);
  return {context,elements};
}

test("polling reconnects after network loss and observes the same run without a POST",async()=>{
  const paths=[],seen=[];let calls=0;
  const {context,elements}=monitorContext(async(path,body)=>{paths.push([path,body]);if(++calls<=2){const error=new Error("offline");error.retryable=true;throw error;}return {id:"run-1",status:calls===3?"running":"completed",output:"Saved answer"};});
  context.update=run=>seen.push(run.status);
  const result=await vm.runInContext('pollRun("run-1",update)',context);
  assert.equal(result.output,"Saved answer");assert.deepEqual(seen,["running","completed"]);
  assert.equal(paths.length,4);assert.ok(paths.every(([path,body])=>path==="/api/runs/run-1"&&body===undefined));
  assert.equal(elements.get("connection-status").hidden,true);
});

test("multiple views share one monitor and a lost view does not stop execution tracking",async()=>{
  let calls=0,release;const changed=[];
  const {context}=monitorContext(async()=>{calls++;await new Promise(resolve=>release=resolve);return {id:"same",status:"completed"};});
  context.first=()=>changed.push("first");context.second=()=>changed.push("second");
  const one=vm.runInContext('pollRun("same",first)',context),two=vm.runInContext('pollRun("same",second)',context);
  release();await Promise.all([one,two]);assert.equal(calls,1);assert.deepEqual(changed,["first","second"]);
});

test("permission and actual run failures are not retried as connection problems",async()=>{
  let calls=0;const {context}=monitorContext(async()=>{calls++;const error=new Error("Resource is private");error.status=403;throw error;});
  await assert.rejects(vm.runInContext('pollRun("private")',context),/private/);assert.equal(calls,1);
  context.api=async()=>({id:"failed",status:"failed",error:"Account usage limit reached"});
  await assert.rejects(vm.runInContext('pollRun("failed")',context),/usage limit/);
});

test("cancelled polling retains cleanup warnings and interruption errors retain actual saved status",async()=>{
  const cancelled={id:"stop",status:"cancelled",cleanup_status:"completed",workflow_warnings:[{error_type:"CleanupRejected"}]};
  const {context}=monitorContext(async()=>cancelled);
  assert.equal(await vm.runInContext('pollRun("stop")',context),cancelled);
  const interrupted={id:"lost",status:"interrupted",cancellation_cleanup:true,cleanup_status:"interrupted",error:"Studio stopped"};
  context.api=async()=>interrupted;
  await assert.rejects(vm.runInContext('pollRun("lost")',context),error=>error.run===interrupted&&error.run.status==="interrupted");
});

test("an unconfirmed submission is never automatically replayed",async()=>{
  let calls=0;const context=vm.createContext({state:{token:"csrf"},AbortController,setTimeout,clearTimeout,fetch:async()=>{calls++;throw new TypeError("Network lost after send");}});
  vm.runInContext(source.slice(source.indexOf("async function api("),source.indexOf("function providerName")),context);
  await assert.rejects(vm.runInContext('api("/api/runs",{task:"One task"})',context),/may have reached the server/);
  assert.equal(calls,1);
});

test("service worker never intercepts or caches account data, actions or other sites",async()=>{
  const handlers={},fetched=[],cached=[];
  const context=vm.createContext({URL,self:{location:{origin:"https://studio.test"},addEventListener:(name,fn)=>handlers[name]=fn},fetch:async request=>{fetched.push(request.url);throw new Error("offline");},caches:{match:async(path)=>{cached.push(path);return "offline shell";}}});
  vm.runInContext(fs.readFileSync("nexus/studio_static/service-worker.js","utf8"),context);
  for(const [path,method] of [["/api/bootstrap","GET"],["/api/runs","POST"],["/api/catalog/artifacts/private/download","GET"],["/auth/callback","GET"],["/login","GET"],["/?secret=example","GET"]]){
    handlers.fetch({request:{url:"https://studio.test"+path,method},respondWith:()=>assert.fail("Private request intercepted")});
  }
  handlers.fetch({request:{url:"https://outside.test/studio.js",method:"GET"},respondWith:()=>assert.fail("Other origin intercepted")});
  let response;
  handlers.fetch({request:{url:"https://studio.test/",method:"GET"},respondWith:promise=>response=promise});
  assert.equal(await response,"offline shell");assert.deepEqual(cached,["/"]);assert.equal(fetched.length,1);
});


test("waiting controls distinguish input, approval, mixed and remote requests",()=>{
  const context=vm.createContext({escapeHtml:value=>String(value).replace(/</g,"&lt;")});
  vm.runInContext(source.slice(source.indexOf("function waitingRunActions("),source.indexOf("function updateRunDetail(")),context);
  context.run={id:"one",status:"waiting",kind:"workflow",awaiting_input:[{name:"<Answer>"}]};
  let html=vm.runInContext('waitingRunActions(run)',context);assert.match(html,/Input required: &lt;Answer>/);assert.doesNotMatch(html,/data-approve|Approval required/);
  context.run.awaiting_approval=[{name:"Review"}];html=vm.runInContext('waitingRunActions(run)',context);assert.match(html,/Input required/);assert.match(html,/data-approve-run="one"/);
  context.run.awaiting_input=[];html=vm.runInContext('waitingRunActions(run)',context);assert.doesNotMatch(html,/Input required/);assert.match(html,/Review/);
  context.run.kind="external";assert.match(vm.runInContext('waitingRunActions(run)',context),/remote task controls/);
  context.run.status="completed";assert.equal(vm.runInContext('waitingRunActions(run)',context),"");
});

test("cleanup status explains uncertainty and separate approval without exposing internal paths",()=>{
  const context=vm.createContext({escapeHtml:value=>String(value).replaceAll("<","&lt;").replaceAll(">","&gt;")});
  vm.runInContext(source.slice(source.indexOf("function waitingRunActions("),source.indexOf("function updateRunDetail(")),context);
  context.run={id:"one",kind:"workflow",status:"waiting",cancellation_cleanup:true,cleanup_status:"waiting",awaiting_approval:[{name:"Cleanup review"}],workflow_warnings:[{node_id:"root/interop_exit/<cleanup>",error_type:"CleanupEffectUncertainError"},{node_id:"legacy/nested-scopes",error_type:"LegacyScopeCapturesUnavailable"}]};
  const actions=vm.runInContext('waitingRunActions(run)',context),status=vm.runInContext('cleanupRunStatus(run)',context);
  assert.match(actions,/Approve and continue/);assert.match(actions,/Skip cleanup step/);assert.match(status,/Step was not repeated/);assert.match(status,/Older save lacks nested cleanup context/);assert.match(status,/&lt;cleanup&gt;/);assert.doesNotMatch(status,/root\/interop_exit|<cleanup>|CleanupEffectUncertainError/);
});

test("decision history keeps typed questions separate from legacy confidence and never replays workflow routing",()=>{
  const workbench=fs.readFileSync("nexus/studio_static/workbench.js","utf8"),element={innerHTML:""};
  const escapeHtml=value=>String(value??"").replaceAll("<","&lt;").replaceAll(">","&gt;");
  const context=vm.createContext({wb:{data:{}},$:()=>element,escapeHtml,badge:value=>escapeHtml(value),state:{agents:[{id:"billing"}]},wbRows:(items,render)=>items.map(render).join("")});
  vm.runInContext(workbench.slice(workbench.indexOf("function renderDecisions("),workbench.indexOf("function renderControls(")),context);
  context.items=[{id:"typed",format:"nexus-typed-decision/v1",backend:"openrouter",model:"selected-model",route_on:"team",result:{answers:{team:{choice:"billing",confidence:.9},urgent:{value:true,probability:.8}},routed_branch:"billing",escalated:false}}, {id:"legacy",choice:"other",accepted:true,confidence:.7,threshold:.6,reason:"Existing record"}];
  vm.runInContext("renderDecisions(items)",context);
  assert.match(element.innerHTML,/2 questions/);assert.match(element.innerHTML,/Routing confidence 0.90/);assert.match(element.innerHTML,/Reported confidence 0.70/);
  assert.doesNotMatch(element.innerHTML,/data-wb-route="typed"/);
  context.items=[{id:"failure",format:"nexus-typed-decision/v1",backend:"openrouter",model:"selected-model",result:{answers:{},selected_branch:"review",escalated:true,provider_error:"timeout"}}];
  vm.runInContext("renderDecisions(items)",context);
  assert.match(element.innerHTML,/Sent for review/);assert.match(element.innerHTML,/Provider error: timeout/);assert.doesNotMatch(element.innerHTML,/confidence|NaN|undefined/);
});

test("typed decision details show separate answer probabilities without internal checkpoint fields",()=>{
  const workbench=fs.readFileSync("nexus/studio_static/workbench.js","utf8");let rendered="";
  const context=vm.createContext({escapeHtml:value=>String(value??"").replaceAll("<","&lt;").replaceAll(">","&gt;"),showDetail:(title,html)=>rendered=html});
  vm.runInContext(workbench.slice(workbench.indexOf("function showTypedDecision("),workbench.indexOf("function renderDecisions(")),context);
  context.item={id:"internal-id",request_digest:"internal-digest",model:"selected-model",result:{escalated:true,selected_branch:"review",answers:{team:{choice:"billing",confidence:.2,probabilities:{billing:1}},urgent:{value:true,probability:.8},detail:{score:1,confidence:.9,legend:{0:"low",1:"high"}}}}};
  vm.runInContext("showTypedDecision(item)",context);
  assert.match(rendered,/Sent for review/);assert.match(rendered,/Confidence: 20%/);assert.match(rendered,/Probability of yes: 80%/);assert.match(rendered,/low → high/);
  assert.doesNotMatch(rendered,/internal-id|internal-digest|request_digest/);
});

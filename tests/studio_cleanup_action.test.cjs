const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync("nexus/studio_static/workbench.js","utf8");
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function setup(api=async()=>({items:[],pending:0})){
  const elements=new Map();
  const $=id=>{
    if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,textContent:"",innerHTML:"",attributes:{},setAttribute(name,value){this.attributes[name]=value;}});
    return elements.get(id);
  };
  const context=vm.createContext({$,api,escapeHtml,badge:escapeHtml,
    state:{ready:true,mode:"local"},wb:{cleanupBusy:false,cleanupAvailable:true},
    wbError:error=>$("wb-error").textContent=error.message});
  vm.runInContext(source.slice(source.indexOf("function canRetryContainerCleanup("),source.indexOf("async function refreshWorkbench(")),context);
  return {context,$};
}

test("cleanup controls require an owner and respect shutdown readiness",()=>{
  const {context,$}=setup();
  context.data={items:[],analysis_cleanup:{available:true}};
  for(const role of ["viewer","operator","builder"]){
    context.state={ready:true,mode:"team",user:{role}};
    vm.runInContext("renderDeploymentReadiness(data)",context);
    assert.equal($("container-cleanup").hidden,true);
  }
  context.state={ready:true,mode:"team",user:{role:"owner"}};
  vm.runInContext("renderDeploymentReadiness(data)",context);
  assert.equal($("container-cleanup").hidden,false);
  assert.equal($("retry-container-cleanup").disabled,false);
  context.data.analysis_cleanup.available=false;
  vm.runInContext("renderDeploymentReadiness(data)",context);
  assert.equal($("retry-container-cleanup").disabled,true);
});

test("cleanup explains all pending outcomes and escapes record names",()=>{
  const {context,$}=setup();
  context.report={pending:8,items:["removed","owner_active","other_host","route_changed","ownership_mismatch","awaiting_creation","invalid_record","unconfirmed","future_status"].map(status=>({status,name:"<script>"}))};
  vm.runInContext("renderContainerCleanupResult(report)",context);
  const html=$("container-cleanup-result").innerHTML;
  for(const text of ["1 removed","8 active or unresolved","Worker active","Another server","Docker connection changed","Ownership needs review","Creation unconfirmed","Saved record needs review","Cleanup unconfirmed","Needs review"])assert.ok(html.includes(text),text);
  assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes("undefined"));
});

test("one cleanup POST disables duplicate clicks then refreshes read-only readiness",async()=>{
  let release;const calls=[];
  const {context,$}=setup(async(path,body)=>{
    calls.push({path,body});
    if(body!==undefined){await new Promise(resolve=>release=resolve);return {items:[],pending:0};}
    return {items:[],analysis_cleanup:{available:true}};
  });
  const first=vm.runInContext("retryContainerCleanup()",context);
  assert.equal($("retry-container-cleanup").disabled,true);
  assert.equal($("container-cleanup-result").attributes["aria-busy"],"true");
  await vm.runInContext("retryContainerCleanup()",context);
  assert.equal(calls.length,1);
  release();await first;
  assert.deepEqual(calls.map(item=>item.path),["/api/workbench/readiness/cleanup","/api/workbench/readiness"]);
  assert.equal(Object.keys(calls[0].body).length,0);assert.equal(calls[1].body,undefined);
  assert.equal($("retry-container-cleanup").disabled,false);
  assert.equal($("container-cleanup-result").attributes["aria-busy"],"false");
  assert.match($("container-cleanup-result").innerHTML,/No saved workers need cleanup/);
});

test("lost cleanup reply is never automatically replayed",async()=>{
  let calls=0;
  const {context,$}=setup(async()=>{calls++;const error=new Error("lost response");error.retryable=true;throw error;});
  await vm.runInContext("retryContainerCleanup()",context);
  assert.equal(calls,1);
  assert.match($("container-cleanup-result").textContent,/No cleanup confirmation received/);
  assert.equal($("retry-container-cleanup").disabled,false);
});

test("permission loss hides cleanup and displays the actual refusal",async()=>{
  const {context,$}=setup(async()=>{const error=new Error("Owner role required");error.status=403;throw error;});
  await vm.runInContext("retryContainerCleanup()",context);
  assert.equal($("container-cleanup").hidden,true);
  assert.equal($("retry-container-cleanup").disabled,true);
  assert.equal($("wb-error").textContent,"Owner role required");
});

test("successful cleanup results survive a failed readiness refresh",async()=>{
  const {context,$}=setup(async(path,body)=>{
    if(body!==undefined)return {items:[{name:"owned",status:"removed"}],pending:0};
    throw new Error("readiness unavailable");
  });
  await vm.runInContext("retryContainerCleanup()",context);
  assert.match($("container-cleanup-result").innerHTML,/1 removed/);
  assert.match($("wb-error").textContent,/Cleanup results are shown below/);
});

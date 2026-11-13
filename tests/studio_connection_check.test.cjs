const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync("nexus/studio_static/workbench.js","utf8");
function setup(fetch,extra={}){
  const elements=new Map();
  const $=id=>{if(!elements.has(id))elements.set(id,{hidden:true,disabled:false,textContent:"",innerHTML:"",attributes:{},addEventListener(){},setAttribute(key,value){this.attributes[key]=value;}});return elements.get(id);};
  const context=vm.createContext({fetch,AbortController,setTimeout,clearTimeout,$,
    document:{addEventListener(){}},window:{addEventListener(){}},escapeHtml:String,
    state:{ready:true,mode:"team",user:{role:"owner"}},...extra});
  const ready=context.state.ready;context.state.ready=false;vm.runInContext(source,context);context.state.ready=ready;return {context,$};
}
function response(status=200,type="application/json",cache="private, no-store"){
  return {status,redirected:false,headers:{get(name){return {"Content-Type":type,"Cache-Control":cache}[name]??null;}},body:{async cancel(){}},json(){throw Error("Private bodies must never be read");},text(){throw Error("Private bodies must never be read");}};
}
test("private check uses two bounded same-origin GETs and retains no private content",async()=>{
  const calls=[],{context}=setup(async(path,options)=>{calls.push({path,options});return response();});
  const result=await vm.runInContext("checkPrivateResponses()",context);
  assert.equal(result.passed,true);assert.equal(calls.length,2);
  assert.deepEqual(calls.map(x=>x.path),["/api/bootstrap","/api/workbench/inbox"]);
  for(const {options} of calls){
    assert.equal(options.method,"GET");assert.equal(options.credentials,"same-origin");
    assert.equal(options.mode,"same-origin");assert.equal(options.redirect,"error");
    assert.equal(options.cache,"no-store");assert.ok(options.signal);assert.equal(options.headers,undefined);
  }
  assert.deepEqual(Object.keys(result.items[0]).sort(),["checks","name","passed","path","status"]);
});
test("401, HTML, redirects and misleading cache directives cannot pass",async()=>{
  for(const bad of [response(401),response(200,"text/html"),response(200,"application/json","no-cache"),response(200,"application/json","x-no-store"),response(200,"application/json",'private="no-store"'),{...response(),redirected:true}]){
    const {context}=setup(async()=>bad),result=await vm.runInContext("checkPrivateResponses()",context);
    assert.equal(result.passed,false);assert.equal(result.items.length,2);
  }
});
test("JSON parameters and case-insensitive no-store directives pass",async()=>{
  const {context}=setup(async()=>response(200,"Application/JSON; charset=utf-8","private, NO-STORE"));
  assert.equal((await vm.runInContext("checkPrivateResponses()",context)).passed,true);
});
test("request errors are redacted, timers clear and checks never retry",async()=>{
  let requests=0,cleared=0;
  const {context}=setup(async(_path,{signal})=>{requests++;return new Promise((resolve,reject)=>signal.addEventListener("abort",()=>reject(Error("private credential must not escape")),{once:true}));},{setTimeout(fn,ms){assert.equal(ms,8000);queueMicrotask(fn);return requests+1;},clearTimeout(){cleared++;}});
  const result=await vm.runInContext("checkPrivateResponses()",context);
  assert.equal(result.passed,false);assert.equal(requests,2);assert.equal(cleared,2);
  assert.ok(!JSON.stringify(result).includes("credential"));
});
test("UI prevents duplicate checks and renders header outcomes without raw bodies",async()=>{
  let release;let requests=0;
  const {context,$}=setup(async()=>{requests++;if(requests===1)await new Promise(resolve=>release=resolve);return response();});
  const pending=vm.runInContext("runBrowserConnectionCheck()",context);
  assert.equal($("check-browser-connection").disabled,true);
  assert.equal($("browser-connection-result").attributes["aria-busy"],"true");
  await vm.runInContext("runBrowserConnectionCheck()",context);assert.equal(requests,1);
  release();await pending;assert.equal(requests,2);
  assert.equal($("check-browser-connection").disabled,false);
  assert.equal($("browser-connection-result").attributes["aria-busy"],"false");
  assert.match($("browser-connection-result").innerHTML,/Private connection checks passed/);
  assert.match($("browser-connection-result").innerHTML,/Private caching: passed/);
});
test("viewers cannot trigger owner deployment diagnostics",async()=>{
  let requests=0;const {context}=setup(async()=>{requests++;return response();},{state:{ready:true,mode:"team",user:{role:"viewer"}}});
  await vm.runInContext("runBrowserConnectionCheck()",context);assert.equal(requests,0);
});

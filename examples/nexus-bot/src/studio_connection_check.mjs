/** Exercise Studio's browser connection check with a disposable HTTP fixture.
 * Run from repository root: node examples/nexus-bot/src/studio_connection_check.mjs
 * No real login, provider, workspace or credential is used.
 */
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import http from "node:http";
import vm from "node:vm";

const paths=["/api/bootstrap","/api/workbench/inbox"],calls=[];
const server=http.createServer((request,response)=>{
  calls.push({method:request.method,path:request.url});
  if(request.method!=="GET"||!paths.includes(request.url)||request.headers.cookie!=="fixture=authorized"){
    response.writeHead(401,{"Content-Type":"application/json","Cache-Control":"no-store"});
    response.end('{}');return;
  }
  response.writeHead(200,{"Content-Type":"application/json; charset=utf-8","Cache-Control":"private, no-store"});
  response.end(JSON.stringify({fixture_private_content:"must-not-enter-the-report"}));
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
try{
  const origin=`http://127.0.0.1:${server.address().port}`;
  const context=vm.createContext({state:{ready:false},AbortController,setTimeout,clearTimeout,$:()=>({addEventListener(){}}),
    document:{addEventListener(){}},window:{addEventListener(){}},
    fetch:(path,options)=>{
      assert.ok(paths.includes(path));assert.equal(options.credentials,"same-origin");
      return fetch(origin+path,{...options,headers:{Cookie:"fixture=authorized"}});
    }});
  vm.runInContext(await readFile(new URL("../../../nexus/studio_static/workbench.js",import.meta.url),"utf8"),context);
  const report=await vm.runInContext("checkPrivateResponses()",context);
  assert.equal(report.passed,true);assert.equal(calls.length,2);
  assert.ok(calls.every(call=>call.method==="GET"));
  assert.ok(!JSON.stringify(report).includes("must-not-enter-the-report"));
  console.log(JSON.stringify({scope:"disposable HTTP fixture, not live Nexus login",passed:true,read_only_requests:calls.length,private_content_in_report:false}));
}finally{
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
}

const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function message(){
  const body={dataset:{},textContent:''},status={};
  const node={dataset:{},querySelector:selector=>selector==='.message-body'?body:selector==='.stream-status'?status:null,append(){}};
  const context=vm.createContext({renderContent:(element,text)=>{element.textContent=text;},showCitations(){}});
  vm.runInContext(fs.readFileSync('nexus/studio_static/conversation-content.js','utf8'),context);
  context.renderRetrievalNotice=()=>{};context.renderStructuredContent=()=>{};
  return {body,status,node,render:run=>context.renderRunMessage(node,run)};
}

test('pending reply has one status label and no placeholder response',()=>{
  const {body,status,node,render}=message();
  render({status:'running'});
  assert.equal(body.textContent,'');assert.equal(body.hidden,true);
  assert.equal(status.textContent,'Working…');assert.match(status.className,/is-loading/);
  assert.equal(node.dataset.pending,'true');
  render({status:'queued'});
  assert.equal(body.textContent,'');assert.equal(status.textContent,'Queued');
});

test('streamed text stays visible until completion removes the loading state',()=>{
  const {body,status,node,render}=message();
  render({status:'running',output:'First words',stream:{status:'streaming'}});
  assert.equal(body.textContent,'First words');assert.equal(body.hidden,false);
  assert.equal(status.textContent,'Receiving response…');assert.match(status.className,/is-loading/);
  render({status:'completed',output:'The complete response.'});
  assert.equal(body.textContent,'The complete response.');assert.equal(status.hidden,true);
  assert.equal(node.dataset.pending,'false');assert.doesNotMatch(status.className,/is-loading/);
});

test('failure stops the loader and preserves a partial response and its separate error',()=>{
  const {body,status,node,render}=message();
  render({status:'running'});
  render({status:'failed',output:'Partial response',error:'Connection unavailable'});
  assert.equal(body.textContent,'Partial response');assert.equal(status.textContent,'Connection unavailable');
  assert.equal(node.dataset.pending,'false');assert.doesNotMatch(status.className,/is-loading/);
});

test('cancellation clears the pending row without erasing streamed content',()=>{
  const {body,status,node,render}=message();
  render({status:'running',output:'Partial response'});
  render({status:'cancelled',output:'Partial response'});
  assert.equal(body.textContent,'Partial response');assert.equal(status.hidden,true);
  assert.equal(node.dataset.pending,'false');assert.doesNotMatch(status.className,/is-loading/);
});

test('truncated streams retain their explanation while waiting for the final output',()=>{
  const {body,status,render}=message();
  render({status:'running',output:'Preview',stream:{status:'streaming',truncated:true}});
  assert.equal(body.textContent,'Preview');assert.match(status.textContent,/Live preview limit reached/);
  render({status:'completed',output:'Full output'});
  assert.equal(body.textContent,'Full output');assert.equal(status.hidden,true);
});

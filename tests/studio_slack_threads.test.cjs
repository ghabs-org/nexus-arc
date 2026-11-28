const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const fields=source.slice(source.indexOf('function operationFields('),source.indexOf('function operationFormData('));
const parser=source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions('));
const visibility=source.slice(source.indexOf('function updateIncomingServiceFields('),source.indexOf('document.addEventListener("change",event=>{if(event.target.name==="entrypoint_type"'));

test('thread option reopens with saved preference and explains private history',()=>{
 const context=vm.createContext({library:{kind:'entrypoints'},targetField:()=>'',inputField:()=>'',escapeHtml:value=>String(value).replaceAll('<','&lt;'),queueMicrotask:()=>{},updateIncomingServiceFields:()=>{}});
 vm.runInContext(fields,context);
 const html=context.operationFields({data:{type:'slack',target_kind:'agent',threaded_conversations:true}});
 assert.match(html,/name="threaded_conversations" checked/);
 assert.match(html,/each person's conversation/);assert.match(html,/Replies remain visible/);
});

test('only Slack agent forms can opt in; other service payloads stay compatible',()=>{
 const context=vm.createContext({library:{kind:'entrypoints'},JSON});vm.runInContext(parser,context);
 for(const [type,target,checked,expected] of [['slack','agent:reviewer',true,true],['slack','agent:reviewer',false,false],['slack','workflow:reviewer',true,false],['telegram','agent:reviewer',true,undefined]]){
  const form=new Map([['entrypoint_type',type],['target',target],['identity_map','{}'],['allowed_channels','room']]);
  if(checked)form.set('threaded_conversations','on');
  assert.equal(context.operationFormData(form).threaded_conversations,expected);
 }
});

test('thread checkbox visibility and availability follow service and target',()=>{
 for(const [type,target,hidden,disabled] of [['slack','agent:reviewer',false,false],['slack','workflow:reviewer',false,true],['telegram','agent:reviewer',true,true]]){
  const label={hidden:false},help={hidden:false},input={disabled:false,closest:()=>label};
  const document={querySelector:selector=>selector==='[name="entrypoint_type"]'?{value:type}:selector==='[name="target"]'?{value:target}:selector==='[name="threaded_conversations"]'?input:selector==='[data-slack-thread-help]'?help:null};
  const context=vm.createContext({document});vm.runInContext(visibility,context);context.updateIncomingServiceFields();
  assert.equal(label.hidden,hidden);assert.equal(help.hidden,hidden);assert.equal(input.disabled,disabled);
 }
});

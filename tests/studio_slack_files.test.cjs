const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const fields=source.slice(source.indexOf('function operationFields('),source.indexOf('function operationFormData('));
const parser=source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions('));
const visibility=source.slice(source.indexOf('function updateIncomingServiceFields('),source.indexOf('document.addEventListener("change",event=>{if(event.target.name==="entrypoint_type"'));
test('Slack file capture explains provider transfer and preserves saved choice',()=>{
 const context=vm.createContext({library:{kind:'entrypoints'},targetField:()=>'',inputField:()=>'',escapeHtml:String,queueMicrotask:()=>{},updateIncomingServiceFields:()=>{}});vm.runInContext(fields,context);
 const html=context.operationFields({data:{type:'slack',capture_slack_files:true}});
 assert.match(html,/name="capture_slack_files" checked/);assert.match(html,/private Library/);assert.match(html,/selected agent\/provider/);assert.match(html,/files:read/);
});
test('file capture needs explicit selection and Slack agent threads',()=>{
 const context=vm.createContext({library:{kind:'entrypoints'},JSON});vm.runInContext(parser,context);
 for(const [service,target,threaded,files,expected] of [['slack','agent:reviewer',true,true,true],['slack','agent:reviewer',false,true,false],['slack','agent:reviewer',true,false,false],['slack','workflow:reviewer',true,true,false],['telegram','agent:reviewer',true,true,undefined]]){
  const form=new Map([['entrypoint_type',service],['target',target]]);if(threaded)form.set('threaded_conversations','on');if(files)form.set('capture_slack_files','on');
  assert.equal(context.operationFormData(form).capture_slack_files,expected);
 }
});
test('file checkbox hides for other services and disables without agent threads',()=>{
 for(const [service,target,checked,hidden,disabled] of [['slack','agent:reviewer',true,false,false],['slack','agent:reviewer',false,false,true],['slack','workflow:reviewer',true,false,true],['telegram','agent:reviewer',true,true,true]]){
  const label={hidden:false},help={hidden:false},threadLabel={},threadHelp={};const thread={checked,disabled:false,closest:()=>threadLabel},files={disabled:false,closest:()=>label};
  const document={querySelector:s=>({'[name="entrypoint_type"]':{value:service},'[name="target"]':{value:target},'[name="threaded_conversations"]':thread,'[name="capture_slack_files"]':files,'[data-slack-thread-help]':threadHelp,'[data-slack-files-help]':help})[s]||null};
  const context=vm.createContext({document});vm.runInContext(visibility,context);context.updateIncomingServiceFields();
  assert.equal(label.hidden,hidden);assert.equal(help.hidden,hidden);assert.equal(files.disabled,disabled);
 }
});

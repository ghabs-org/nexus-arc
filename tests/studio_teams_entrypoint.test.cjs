const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const context=vm.createContext({library:{kind:'entrypoints'},JSON});
const start=source.indexOf('function operationFormData(');
vm.runInContext(source.slice(start,source.indexOf('\nfunction operationActions(',start)),context);
function form(type){return new Map([['target','workflow:review'],['entrypoint_type',type],['token_env','EVENT_SECRET'],['identity_map','{"alice":"desktop"}'],['allowed_channels','selected-channel'],['teams_team_id','team-id'],['teams_channel_id','selected-channel'],['teams_tenant_id','tenant-id'],['teams_subscription_id','subscription-id'],['teams_graph_token_env','READER_TOKEN'],['teams_command_prefix','/nexus ']]);}
test('Teams form preserves selected subscription and credential references with disabled reply fields',()=>{
 const value=JSON.parse(JSON.stringify(context.operationFormData(form('teams'))));
 assert.equal(value.notification_connector_id,'');
 assert.equal(value.command_prefix,'/nexus ');
 assert.equal(value.graph_token_env,'READER_TOKEN');
 assert.equal(value.subscription_id,'subscription-id');
 assert.equal(value.auto_reply,false);
 assert.deepEqual(value.identity_map,{alice:'desktop'});
});
test('Legacy entrypoints omit inactive Teams settings',()=>{
 const value=context.operationFormData(form('slack'));
 assert.equal(Object.hasOwn(value,'subscription_id'),false);
 assert.equal(Object.hasOwn(value,'graph_token_env'),false);
});

const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/features.js','utf8');
const code=source.slice(source.indexOf('const enterpriseFormData=operationFormData;'),source.indexOf('let promptSuggestionCache='));
function context(editing){
  const scope=vm.createContext({library:{kind:'connectors',editing},enterpriseTypes:{nuclia:'Nuclia',powerbi:'Power BI'},operationFormData:()=>({legacy:true})});
  vm.runInContext(code,scope);
  return scope;
}
function form(type){return new Map([['connector_type',type],['enterprise_url','https://example.org'],['enterprise_knowledge_box','selected'],['enterprise_token_env','NUCLIA_TOKEN']]);}

test('editing ordinary Nuclia fields preserves a stored template without sharing mutable state',()=>{
  const template={version:1,parameters:[{name:'region',type:'string'}],query_prompt:'{{query}} {{region}}'};
  const scope=context({data:{type:'nuclia',template}});
  const output=scope.operationFormData(form('nuclia'));
  assert.equal(JSON.stringify(output.template),JSON.stringify(template));
  output.template.parameters[0].name='changed';
  assert.equal(template.parameters[0].name,'region');
});

test('new or different connector types never inherit a Nuclia template',()=>{
  assert.equal(context(null).operationFormData(form('nuclia')).template,undefined);
  const scope=context({data:{type:'nuclia',template:{version:1}}});
  assert.equal(scope.operationFormData(form('powerbi')).template,undefined);
  assert.equal(context({data:{type:'powerbi',template:{unrelated:true}}}).operationFormData(form('nuclia')).template,undefined);
});

test('Nuclia evidence capture follows the explicit checkbox and never transfers to another connector',()=>{
  const scope=context({data:{type:'nuclia',capture_evaluation:true}}),selected=form('nuclia');
  selected.set('enterprise_capture_evaluation','on');
  assert.equal(scope.operationFormData(selected).capture_evaluation,true);
  assert.equal(scope.operationFormData(form('nuclia')).capture_evaluation,false);
  assert.equal(scope.operationFormData(form('powerbi')).capture_evaluation,undefined);
});

test('editing a bound Nuclia connection preserves fixed settings without sharing state',()=>{
  const ask_settings={top_k:7,rephrase:false,prompt:{user:'Use {context}'}};
  const scope=context({data:{type:'nuclia',ask_settings}});
  const output=scope.operationFormData(form('nuclia'));
  assert.equal(JSON.stringify(output.ask_settings),JSON.stringify(ask_settings));
  output.ask_settings.prompt.user='changed';
  assert.equal(ask_settings.prompt.user,'Use {context}');
  assert.equal(scope.operationFormData(form('powerbi')).ask_settings,undefined);
  assert.equal(context(null).operationFormData(form('nuclia')).ask_settings,undefined);
});

test('import review escapes adaptation notes and supports reports without adaptations',()=>{
  const scope=vm.createContext({escapeHtml:value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')});
  vm.runInContext(source.slice(source.indexOf('function interopAdaptations('),source.indexOf('async function importPortableConfiguration(')),scope);
  assert.equal(scope.interopAdaptations({}), '');
  const html=scope.interopAdaptations({adaptations:[{policy:'<policy>',changes:['<script>bad</script>']}]});
  assert.ok(html.includes('&lt;policy&gt;')&&html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
});

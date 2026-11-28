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

test('retrieval evidence scope is explicit, retained when capture is off, and never inherited by other types',()=>{
  const scope=context({data:{type:'nuclia',evaluation_context:'retrieved'}}),selected=form('nuclia');
  assert.equal(scope.operationFormData(selected).evaluation_context,'retrieved');
  assert.equal(scope.operationFormData(selected).capture_evaluation,false);
  selected.set('enterprise_evaluation_context','cited');
  assert.equal(scope.operationFormData(selected).evaluation_context,'cited');
  assert.equal(scope.operationFormData(form('powerbi')).evaluation_context,undefined);
  const fresh=form('nuclia');fresh.set('enterprise_evaluation_context','cited');
  assert.equal(context(null).operationFormData(fresh).evaluation_context,undefined);
  fresh.set('enterprise_evaluation_context','retrieved');
  assert.equal(context(null).operationFormData(fresh).evaluation_context,'retrieved');
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

test('ordinary connector edits retain answer presentation without inheriting another type',()=>{
  const answer_options={output_response_as_artifact:false,output_filename_base:'policy',inline_citation_links:false};
  const scope=context({data:{type:'nuclia',answer_options}});
  const output=scope.operationFormData(form('nuclia'));
  assert.equal(JSON.stringify(output.answer_options),JSON.stringify(answer_options));
  output.answer_options.output_filename_base='changed';
  assert.equal(answer_options.output_filename_base,'policy');
  assert.equal(scope.operationFormData(form('powerbi')).answer_options,undefined);
  assert.equal(context(null).operationFormData(form('nuclia')).answer_options,undefined);
});

test('explicit presentation controls save selected options and preserve other fixed settings',()=>{
  const scope=context({data:{type:'nuclia',ask_settings:{top_k:7,rephrase:false}}}),selected=form('nuclia');
  for(const [key,value] of Object.entries({answer_controls:'1',answer_customize:'on',answer_filename:'policy.md',answer_prefix:'20',answer_citations:'llm_footnotes',answer_include:'on'}))selected.set('enterprise_'+key,value);
  const output=scope.operationFormData(selected);
  assert.equal(output.answer_options.output_response_as_artifact,false);
  assert.equal(output.answer_options.inline_citation_links,false);
  assert.equal(output.answer_options.include_citations_in_tool_response,true);
  assert.equal(output.answer_options.output_filename_base,'policy.md');
  assert.equal(output.answer_options.artifact_description_query_max_length,20);
  assert.equal(output.ask_settings.top_k,7);
  assert.equal(output.ask_settings.citations,'llm_footnotes');
});

test('turning off presentation customization removes only answer options',()=>{
  const scope=context({data:{type:'nuclia',answer_options:{output_filename_base:'policy'},ask_settings:{citations:'none',top_k:7}}}),selected=form('nuclia');
  selected.set('enterprise_answer_controls','1');
  const output=scope.operationFormData(selected);
  assert.equal(output.answer_options,undefined);
  assert.equal(output.ask_settings.citations,'none');
  assert.equal(output.ask_settings.top_k,7);
  assert.equal(context(null).operationFormData(selected).answer_options,undefined);
});

test('import review escapes adaptation notes and supports reports without adaptations',()=>{
  const scope=vm.createContext({escapeHtml:value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')});
  vm.runInContext(source.slice(source.indexOf('function interopAdaptations('),source.indexOf('async function importPortableConfiguration(')),scope);
  assert.equal(scope.interopAdaptations({}), '');
  const html=scope.interopAdaptations({adaptations:[{policy:'<policy>',changes:['<script>bad</script>']}]});
  assert.ok(html.includes('&lt;policy&gt;')&&html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
});

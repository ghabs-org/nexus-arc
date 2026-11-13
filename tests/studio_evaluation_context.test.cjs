const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

test('editing an evaluation keeps copied reference passages and their pinned provenance',()=>{
  const fields={'.case-input':'Policy?','.case-contains':'Thirty days','.case-excludes':'', '.case-checks':'[]','.case-judge':'{}','.case-contexts':'["Thirty days."]','.case-context-source':'{"artifact_id":"evidence","revision":1}'};
  const scope=vm.createContext({library:{kind:'evaluations'},document:{querySelectorAll:()=>[{querySelector:key=>({value:fields[key]})}]}});
  vm.runInContext(source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions(')),scope);
  scope.form=new Map([['target','agent:policy']]);
  const output=JSON.parse(JSON.stringify(vm.runInContext('operationFormData(form)',scope)));
  assert.deepEqual(output.cases[0].contexts,['Thirty days.']);
  assert.deepEqual(output.cases[0].context_source,{artifact_id:'evidence',revision:1});
  fields['.case-contexts']='[]';fields['.case-context-source']='{}';
  const plain=JSON.parse(JSON.stringify(vm.runInContext('operationFormData(form)',scope)));
  assert.equal(plain.cases[0].contexts,undefined);
  assert.equal(plain.cases[0].context_source,undefined);
});

test('reference context renders as escaped editable data',()=>{
  const scope=vm.createContext({escapeHtml});
  vm.runInContext(source.slice(source.indexOf('function caseFields('),source.indexOf('function operationFields(')),scope);
  scope.item={contexts:['</textarea><script>unsafe()</script>'],context_source:{artifact_id:'evidence',revision:1}};
  const rendered=vm.runInContext('caseFields(item)',scope);
  assert.ok(rendered.includes('&lt;/textarea&gt;&lt;script&gt;'));
  assert.ok(!rendered.includes('<script>'));
  assert.ok(rendered.includes('case-context-source'));
});

test('case editor preserves distinct file roles through save and dataset copies',()=>{
 const fields={'.case-input':'Task','.case-contains':'Approved','.case-excludes':'','.case-checks':'[]','.case-judge':'{"provider":"codex","rubric":"Compare"}', '.case-contexts':'[]','.case-context-source':'{}','.case-input_files':'[{"id":"input","revision":2}]','.case-reference_files':'[{"id":"expected","revision":3}]'};
 const scope=vm.createContext({library:{kind:'evaluations'},document:{querySelectorAll:()=>[{querySelector:key=>({value:fields[key]})}]}});
 vm.runInContext(source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions(')),scope);
 scope.form=new Map([['target','agent:reviewer']]);
 const result=JSON.parse(JSON.stringify(vm.runInContext('operationFormData(form)',scope)));
 assert.deepEqual(result.cases[0].input_files,[{id:'input',revision:2}]);assert.deepEqual(result.cases[0].reference_files,[{id:'expected',revision:3}]);
 const rendering=vm.createContext({escapeHtml});vm.runInContext(source.slice(source.indexOf('function caseFields('),source.indexOf('function operationFields(')),rendering);
 rendering.item={...result.cases[0],reference_files:[{id:'<script>unsafe</script>',revision:3}]};
 const html=vm.runInContext('caseFields(item)',rendering);
 assert.match(html,/Input files · 1 selected/);assert.match(html,/Grading-reference files · 1 selected/);assert.match(html,/goes only to the AI judge/);assert.doesNotMatch(html,/<script>/);
});

test('file chooser pins selected revision without saving a resource or reading bytes',async()=>{
 const calls=[],fields={value:'[]'},summary={textContent:''},list={textContent:''};
 const dialog={open:true},row={isConnected:true,querySelector:selector=>selector.endsWith('-list')?list:fields};
 fields.closest=()=>({querySelector:()=>summary});
 const button={dataset:{caseFileRole:'input_files'},closest:()=>row,isConnected:true,disabled:false};
 const scope=vm.createContext({escapeHtml,$:id=>id==='library-editor'?dialog:{textContent:''},api:async(path)=>{calls.push(path);return {items:[{id:'source',revision:4,data:{filename:'source.txt'}}]};},fieldsDialog:async()=>new Map([['file','{"id":"source","revision":4}']])});
 vm.runInContext(source.slice(source.indexOf('function caseFields('),source.indexOf('function operationFields(')),scope);
 await scope.chooseEvaluationFile(button);
 assert.deepEqual(calls,['/api/catalog/artifacts']);assert.deepEqual(JSON.parse(fields.value),[{id:'source',revision:4}]);assert.match(summary.textContent,/1 selected/);assert.equal(button.disabled,false);
});

test('expected judge response remains separate from target input when editing',()=>{
 const fields={'.case-input':'Public question','.case-expected-response':'SECRET_REFERENCE','.case-contains':'','.case-excludes':'','.case-checks':'[]','.case-judge':'{"provider":"codex","rubric":"Compare"}', '.case-contexts':'[]','.case-context-source':'{}'};
 const scope=vm.createContext({library:{kind:'datasets'},document:{querySelectorAll:()=>[{querySelector:key=>({value:fields[key]})}]}});
 vm.runInContext(source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions(')),scope);
 const result=scope.operationFormData(new Map());assert.equal(result.cases[0].expected_response,'SECRET_REFERENCE');assert.equal(result.cases[0].input,'Public question');
});

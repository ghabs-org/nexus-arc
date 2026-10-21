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

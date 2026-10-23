const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const helpers=source.slice(source.indexOf('function caseFields('),source.indexOf('function operationFields('));
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setup(api){
 const elements=new Map(['evaluation-cases','evaluation-dataset-status','evaluation-dataset','library-form-error','evaluation-saved-dataset','evaluation-use-dataset'].map(id=>[id,{innerHTML:'ORIGINAL_CASES',textContent:'',isConnected:true,disabled:false,value:'policy'}]));
 const button={isConnected:true,disabled:false};
 const scope=vm.createContext({escapeHtml,api,$:id=>elements.get(id),library:{kind:'datasets'},targetField:()=>'<select name="target"></select>',queueMicrotask:()=>{}});
 vm.runInContext(helpers,scope);return {scope,elements,button};
}
test('dataset editor reuses case controls and explains shared, non-executing imports',()=>{
 const {scope}=setup();const html=scope.evaluationDatasetFields({cases:[{input:'</textarea><script>bad()</script>',contains:['Approved']}]});
 assert.match(html,/Shared with this workspace/);assert.match(html,/CSV, YAML or JSON/);
 assert.doesNotMatch(html,/<script>|name="target"|evaluation-saved-dataset/);
 scope.library.kind='evaluations';const suite=scope.evaluationDatasetFields({});
 assert.match(suite,/evaluation-saved-dataset/);assert.match(suite,/Later dataset edits never change your suite/);
});
test('invalid import keeps all draft cases and shows errors inside the editor',async()=>{
 const {scope,elements,button}=setup(async()=>{throw new Error('CSV record 2 has missing cells');});
 elements.get('evaluation-dataset').files=[{name:'cases.csv',size:30,text:async()=>'input,expected\nx,ok\ny'}];
 await scope.importEvaluationDataset(button);
 assert.equal(elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
 assert.match(elements.get('library-form-error').textContent,/missing cells/);assert.equal(button.disabled,false);
});
test('file import uses bounded explicit format and validated cases without saving',async()=>{
 const calls=[];const {scope,elements,button}=setup(async(path,data)=>{calls.push({path,data});return {cases:[{input:'Question',contains:['Approved']}]};});
 elements.get('evaluation-dataset').files=[{name:'cases.YML',size:30,text:async()=>'cases: []'}];
 await scope.importEvaluationDataset(button);
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/studio/datasets/parse');assert.equal(calls[0].data.format,'yaml');
 assert.match(elements.get('evaluation-cases').innerHTML,/Question/);assert.match(elements.get('evaluation-dataset-status').textContent,/Save to keep/);
 elements.get('evaluation-dataset').files=[{name:'large.json',size:900*1024+1}];
 await scope.importEvaluationDataset(button);assert.equal(calls.length,1);
});
test('saved dataset copy uses returned revision, escapes contents and never executes',async()=>{
 const calls=[];const {scope,elements,button}=setup(async path=>{calls.push(path);return {name:'Policy',revision:4,data:{cases:[{input:'<script>bad()</script>',contains:['Approved']}]}};});
 await scope.copySavedEvaluationDataset(button);
 assert.deepEqual(calls,['/api/catalog/datasets/policy']);
 assert.match(elements.get('evaluation-dataset-status').textContent,/revision 4/);
 assert.match(elements.get('evaluation-cases').innerHTML,/&lt;script&gt;/);
});
test('archival or a closed editor prevents replacing draft cases',async()=>{
 const first=setup(async()=>({archived:true}));await first.scope.copySavedEvaluationDataset(first.button);
 assert.equal(first.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');assert.match(first.elements.get('library-form-error').textContent,/archived/);
 let finish;const late=setup(()=>new Promise(resolve=>{finish=resolve;}));
 const pending=late.scope.copySavedEvaluationDataset(late.button);late.elements.get('evaluation-cases').isConnected=false;
 finish({name:'Old',revision:1,data:{cases:[{input:'Old'}]}});await pending;
 assert.equal(late.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
});
test('datasets save canonical cases without an evaluation target',()=>{
 const {scope}=setup();
 const fields={'.case-input':'Question','.case-contains':'Approved','.case-excludes':'','.case-checks':'[]','.case-contexts':'["Source"]','.case-context-source':'{}','.case-judge':'{}'};
 scope.document={querySelectorAll:()=>[{querySelector:key=>({value:fields[key]})}]};
 vm.runInContext(source.slice(source.indexOf('function operationFormData('),source.indexOf('function operationActions(')),scope);
 const output=scope.operationFormData(new Map());
 assert.equal(output.kind,'dataset');assert.equal(output.version,1);assert.equal(output.target_id,undefined);
 assert.equal(output.cases[0].contexts[0],'Source');
});

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
 const scope=vm.createContext({escapeHtml,api,$:id=>elements.get(id),library:{kind:'datasets'},providerOptions:()=>'<option value="codex">Codex</option>',targetField:()=>'<select name="target"></select>',queueMicrotask:()=>{}});
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

function draftSetup(api){
 const fixture=setup(api),{scope,elements}=fixture;
 elements.set("library-editor",{open:true});
 for(const [id,value] of Object.entries({'dataset-draft-task':'Refund rules','dataset-draft-count':'2','dataset-draft-provider':'codex','dataset-draft-model':'chosen','dataset-draft-preview':'{"cases":[]}','dataset-draft-status':'','dataset-draft-review':''}))elements.set(id,{value,textContent:'',hidden:true,isConnected:true});
 return fixture;
}
test('AI draft generation preserves edits, uses chosen provider and requires explicit apply',async()=>{
 const calls=[];const {scope,elements,button}=draftSetup(async(path,data)=>{calls.push({path,data});return {id:'draft-run'};});
 scope.pollRun=async()=>({status:'completed',design_proposal:{data:{cases:[{input:'<script>',contains:['No']}]}}});
 await scope.generateDatasetDraft(button);
 assert.equal(calls[0].path,'/api/studio/design');assert.equal(calls[0].data.design_kind,'dataset');assert.equal(calls[0].data.case_count,2);
 assert.equal(calls[0].data.model,'chosen');assert.equal(elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
 assert.equal(elements.get('dataset-draft-review').hidden,false);assert.match(elements.get('dataset-draft-preview').value,/<script>/);assert.equal(button.disabled,false);
});
test('failed or late generation preserves cases and prior reviewed JSON',async()=>{
 const failed=draftSetup(async()=>({id:'run'}));failed.scope.pollRun=async()=>{throw new Error('Provider failed');};
 await failed.scope.generateDatasetDraft(failed.button);
 assert.match(failed.elements.get('library-form-error').textContent,/Provider failed/);assert.equal(failed.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
 const late=draftSetup(async()=>({id:'run'}));late.scope.pollRun=async()=>{late.elements.get('evaluation-cases').isConnected=false;return {status:'completed',design_proposal:{data:{cases:[]}}};};
 await late.scope.generateDatasetDraft(late.button);assert.equal(late.elements.get('dataset-draft-review').hidden,true);
});
test('append retains existing DOM edits and rejects duplicates before changing cases',async()=>{
 const {scope,elements,button}=draftSetup(async()=>({cases:[{input:'New case',contains:['Yes']}]}));
 const root=elements.get('evaluation-cases'),insertions=[];
 root.querySelectorAll=()=>[{value:'Existing unsaved case'}];root.insertAdjacentHTML=(position,html)=>insertions.push({position,html});
 await scope.applyDatasetDraft(button,true);
 assert.equal(root.innerHTML,'ORIGINAL_CASES');assert.equal(insertions.length,1);assert.match(insertions[0].html,/New case/);
 assert.match(elements.get('evaluation-dataset-status').textContent,/Save to keep/);
 root.querySelectorAll=()=>[{value:' NEW CASE '}];await scope.applyDatasetDraft(button,true);
 assert.equal(insertions.length,1);assert.match(elements.get('library-form-error').textContent,/Duplicate/);
});
test('edited invalid review and stale validation responses cannot replace draft cases',async()=>{
 const invalid=draftSetup(async()=>{throw new Error('Unsupported check');});
 await invalid.scope.applyDatasetDraft(invalid.button,false);
 assert.equal(invalid.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
 let complete;const late=draftSetup(()=>new Promise(resolve=>{complete=resolve;}));
 const pending=late.scope.applyDatasetDraft(late.button,false);late.elements.get('dataset-draft-preview').value='changed during validation';
 complete({cases:[{input:'Old response',contains:['No']}]});await pending;
 assert.equal(late.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
});
test('explicit replacement escapes generated input and does not save or run',async()=>{
 const calls=[];const {scope,elements,button}=draftSetup(async(path,data)=>{calls.push({path,data});return {cases:[{input:'<script>bad()</script>',contains:['Safe']}]};});
 await scope.applyDatasetDraft(button,false);
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/studio/datasets/parse');
 assert.match(elements.get('evaluation-cases').innerHTML,/&lt;script&gt;/);assert.doesNotMatch(elements.get('evaluation-cases').innerHTML,/<script>/);
});

test('closing a still-connected dialog ignores late generation',async()=>{
 const fixture=draftSetup(async()=>({id:'run'}));
 fixture.scope.pollRun=async()=>{fixture.elements.get('library-editor').open=false;return {status:'completed',design_proposal:{data:{cases:[]}}};};
 await fixture.scope.generateDatasetDraft(fixture.button);
 assert.equal(fixture.elements.get('dataset-draft-review').hidden,true);
 assert.equal(fixture.elements.get('evaluation-cases').innerHTML,'ORIGINAL_CASES');
});

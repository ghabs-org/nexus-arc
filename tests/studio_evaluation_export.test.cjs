const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const functions=source.slice(source.indexOf('async function exportEvaluationPackage('));
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function setup(){
 const calls=[],details=[],downloads=[],elements=new Map();
 const form=new Map([['dataset','{"id":"cases","revision":2}'],['evaluator','{"id":"judge","revision":3}'],['model_alias','portable']]);
 const report={valid:true,source:{},binding:{provider:'codex',model:'chosen'},threshold:0.84,notes:['Contains <private> prompts'],files:['dataset.yaml','evaluator.yaml','cases.csv','evaluation-handoff.json'].map(filename=>({filename,media_type:'text/plain',content:'<script>reference</script>'}))};
 const scope=vm.createContext({escapeHtml,api:async path=>{calls.push({path});return {items:[{id:path.endsWith('datasets')?'cases':'judge',revision:2,name:'<Resource>'}]};},fieldsDialog:async()=>form,featureApi:async(path,data)=>{calls.push({path,data});return report;},showDetail:(title,html)=>details.push({title,html}),$:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},downloadConfigurationFile:file=>downloads.push(file),reviewEvaluationImport:report=>details.push({review:report})});
 vm.runInContext(functions,scope);return {scope,calls,details,downloads,elements,form,report};
}
test('exact revisions and model alias prepare private previews without automatic downloads',async()=>{
 const {scope,calls,details,downloads,elements}=setup();await scope.exportEvaluationPackage();
 assert.equal(calls.length,3);assert.equal(calls[2].path,'configuration/evaluations/export');assert.equal(calls[2].data.dataset.revision,2);assert.equal(calls[2].data.evaluator.revision,3);assert.equal(calls[2].data.model_alias,'portable');
 assert.equal(downloads.length,0);assert.match(details[0].html,/Nothing has been downloaded/);assert.match(details[0].html,/&lt;script&gt;/);assert.doesNotMatch(details[0].html,/<script>|<private>/);
 elements.get('evaluation-file-download-2').onclick();assert.equal(downloads.length,1);assert.equal(downloads[0].filename,'cases.csv');assert.equal(calls.length,3);
});
test('unsupported native data returns diagnostics without any usable file or save action',async()=>{
 const {scope,report,details,downloads,calls}=setup();Object.assign(report,{valid:false,files:[],diagnostics:[{path:'/dataset/cases/0/contexts',code:'unsupported_case_field',message:'<cannot drop>'}]});
 await scope.exportEvaluationPackage();assert.equal(downloads.length,0);assert.equal(calls.length,3);assert.match(details[0].html,/No export files/);assert.match(details[0].html,/&lt;cannot drop&gt;/);
});
test('cancel or authorization failure cannot download, save or run anything',async()=>{
 const cancel=setup();cancel.scope.fieldsDialog=async()=>null;await cancel.scope.exportEvaluationPackage();assert.equal(cancel.calls.length,2);
 const denied=setup();denied.scope.featureApi=async()=>{throw Error('Forbidden');};await assert.rejects(denied.scope.exportEvaluationPackage(),/Forbidden/);assert.equal(denied.details.length,0);assert.equal(denied.downloads.length,0);
});
test('handoff preserves original text for strict parsing and returns only a review draft',async()=>{
 const {scope,calls,details}=setup();const text='{"duplicate":1,"duplicate":2}';scope.fieldsDialog=async()=>new Map([['handoff',{size:text.length,text:async()=>text}]]);
 await scope.importEvaluationHandoff();assert.equal(calls.length,1);assert.equal(calls[0].path,'configuration/evaluations/handoff/convert');assert.equal(calls[0].data.handoff,text);assert.ok(details[0].review);
});
test('empty or oversized handoffs never reach the server',async()=>{
 for(const size of [0,2*1024*1024+1]){const {scope,calls}=setup();scope.fieldsDialog=async()=>new Map([['handoff',{size,text:async()=>{throw Error('must not read');}}]]);await assert.rejects(scope.importEvaluationHandoff(),/nonempty/);assert.equal(calls.length,0);}
});

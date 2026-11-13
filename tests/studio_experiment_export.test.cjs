const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const functions=source.slice(source.indexOf('async function exportExperimentDeclaration('));
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function setup(){
 const calls=[],details=[],downloads=[],elements=new Map();
 const report={valid:true,diagnostics:[],source:{experiment_id:'<experiment>',revision:3},notes:['Keep companion <private>'],files:[{filename:'experiment.yaml',media_type:'application/yaml',content:'<script>unsafe</script>'},{filename:'experiment-handoff.json',media_type:'application/json',content:'{"native":"settings"}'}]};
 const scope=vm.createContext({escapeHtml,api:async path=>{calls.push({path});return path.endsWith('datasets')?{items:[{id:'cases',revision:4,name:'<Dataset>'}]}:{id:'experiment',revision:3};},fieldsDialog:async()=>new Map([['dataset','{"id":"cases","revision":4}']]),featureApi:async(path,data)=>{calls.push({path,data});return report;},showDetail:(title,html)=>details.push({title,html}),$:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},reviewExperimentImport:report=>details.push({review:report})});
 vm.runInContext(functions,scope);scope.downloadConfigurationFile=file=>downloads.push(file);return {scope,calls,details,downloads,elements,report};
}
test('export prepares exact revisions and downloads only after separate review clicks',async()=>{
 const {scope,calls,details,downloads,elements}=setup();await scope.exportExperimentDeclaration('experiment');
 assert.equal(calls.length,3);assert.equal(calls[2].path,'configuration/experiments/export');assert.equal(calls[2].data.revision,3);assert.equal(calls[2].data.dataset.revision,4);assert.equal(downloads.length,0);
 assert.match(details[0].html,/Nothing has been downloaded/);assert.match(details[0].html,/&lt;script&gt;/);assert.doesNotMatch(details[0].html,/<script>|<private>|<experiment>/);
 elements.get('experiment-file-download-0').onclick();assert.equal(downloads[0].filename,'experiment.yaml');assert.equal(downloads.length,1);
 elements.get('experiment-file-download-1').onclick();assert.equal(downloads[1].filename,'experiment-handoff.json');assert.equal(calls.length,3);
});
test('unsupported export shows diagnostic paths without downloads or save requests',async()=>{
 const {scope,report,details,calls,downloads}=setup();Object.assign(report,{valid:false,files:[],diagnostics:[{path:'/dataset/cases',code:'case_mismatch',message:'<changed>'}]});
 await scope.exportExperimentDeclaration('experiment');assert.equal(calls.length,3);assert.equal(downloads.length,0);assert.match(details[0].html,/No export files/);assert.match(details[0].html,/\/dataset\/cases/);assert.match(details[0].html,/&lt;changed&gt;/);
});
test('cancel and authorization failure cannot download or apply anything',async()=>{
 const cancel=setup();cancel.scope.fieldsDialog=async()=>null;await cancel.scope.exportExperimentDeclaration('experiment');assert.equal(cancel.calls.length,2);assert.equal(cancel.downloads.length,0);
 const denied=setup();denied.scope.featureApi=async()=>{throw Error('Forbidden');};await assert.rejects(denied.scope.exportExperimentDeclaration('experiment'),/Forbidden/);assert.equal(denied.downloads.length,0);assert.equal(denied.details.length,0);
});
test('handoff sends original bounded file text for strict server parsing and only opens review',async()=>{
 const {scope,calls,details}=setup();const text='{"format":"handoff","duplicate":1,"duplicate":2}';
 scope.fieldsDialog=async()=>new Map([['handoff',{size:text.length,text:async()=>text}]]);await scope.importExperimentHandoff();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'configuration/experiments/handoff/convert');assert.equal(calls[0].data.handoff,text);assert.equal(details.length,1);assert.ok(details[0].review);
});
test('empty or oversized handoff is rejected before reading or submitting it',async()=>{
 for(const size of [0,2*1024*1024+1]){const {scope,calls}=setup();scope.fieldsDialog=async()=>new Map([['handoff',{size,text:async()=>{throw Error('must not read');}}]]);await assert.rejects(scope.importExperimentHandoff(),/nonempty/);assert.equal(calls.length,0);}
});
test('declaration action uses configuration owner visibility and retains existing JSON export',()=>{
 const context=vm.createContext({escapeHtml,library:{kind:'experiments'},state:{mode:'team',user:{role:'viewer'}}});
 vm.runInContext(source.slice(source.indexOf('function operationActions('),source.indexOf('async function refreshOperationResults(')),context);
 assert.doesNotMatch(context.operationActions({id:'experiment',data:{}}),/Export declaration/);
 context.state.user.role='owner';const html=context.operationActions({id:'experiment',data:{}});assert.match(html,/Export declaration/);assert.match(html,/Export JSON/);
});

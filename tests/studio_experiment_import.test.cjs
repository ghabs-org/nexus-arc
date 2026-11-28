const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const functions=source.slice(source.indexOf('async function importExperimentDefinition('),source.indexOf('window.addEventListener("studio-ready"',source.indexOf('async function importExperimentDefinition(')));
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function setup(){
 const calls=[],details=[],elements=new Map();
 const form=new Map([['source','kind: experiment'],['dataset','{"id":"cases","revision":2}'],['evaluator','{"id":"judge","revision":3}'],['target','{"id":"agent","revision":"hash"}'],['threshold','0.8']]);
 const report={planned_executions:3,source:{},adaptations:['Sequential execution'],document:{resources:[],definitions:[],format:'nexus-studio-config/v1'}};
 const scope=vm.createContext({escapeHtml,state:{agents:[{id:'agent',revision:'hash',document:{metadata:{name:'Agent'}}}]},api:async(path)=>{calls.push({path});return {items:[{id:path.endsWith('datasets')?'cases':'judge',name:'Fixture',revision:path.endsWith('datasets')?2:3}]};},fieldsDialog:async()=>form,featureApi:async(path,data)=>{calls.push({path,data});return path.endsWith('convert')?report:{id:'plan',digest:'reviewed',changes:[{action:'create',resource:'suite'}],document:report.document};},showDetail:(title,html)=>details.push({title,html}),$:id=>{if(!elements.has(id))elements.set(id,{isConnected:true,close(){}});return elements.get(id);},refreshLibrary:async()=>{},notify:()=>{}});
 vm.runInContext(functions,scope);return {scope,calls,details,elements,form};
}
test('explicit revisions and threshold reach converter; saving requires separate reviewed actions',async()=>{
 const {scope,calls,elements}=setup();await scope.importExperimentDefinition();
 assert.equal(calls.length,3);const converted=calls[2];assert.equal(converted.path,'configuration/experiments/convert');
 assert.equal(converted.data.bindings.dataset.revision,2);assert.equal(converted.data.bindings.evaluator.revision,3);assert.equal(converted.data.bindings.target.revision,'hash');assert.equal(converted.data.pass_threshold,0.8);
 await elements.get('experiment-import-plan').onclick();assert.equal(calls[3].path,'configuration/plan');assert.equal(calls.length,4);
 await elements.get('experiment-import-apply').onclick();assert.equal(calls[4].path,'configuration/apply');assert.equal(calls[4].data.digest,'reviewed');
});
test('unsupported worker settings retain source for explicit edit without saving',async()=>{
 const {scope,calls,details,elements,form}=setup();form.set('source','maxWorkers: 5');
 scope.featureApi=async(path,data)=>{calls.push({path,data});throw new Error('maxWorkers must be an integer from 1 to 4');};
 await scope.importExperimentDefinition();assert.equal(calls.length,3);assert.match(details[0].html,/maxWorkers/);
 let draft;scope.importExperimentDefinition=async value=>{draft=value;};await elements.get('experiment-import-edit').onclick();
 assert.equal(draft.source,'maxWorkers: 5');assert.equal(draft.dataset,form.get('dataset'));assert.equal(draft.threshold,'0.8');
});
test('cancel and missing required resources cannot submit conversion or plans',async()=>{
 const cancelled=setup();cancelled.scope.fieldsDialog=async()=>null;await cancelled.scope.importExperimentDefinition();assert.equal(cancelled.calls.length,2);
 const empty=setup();empty.scope.api=async()=>({items:[]});await assert.rejects(empty.scope.importExperimentDefinition(),/Save a dataset/);assert.equal(empty.calls.length,0);
});
test('multiple evaluator aliases bind exact selected revisions without starting save or run',async()=>{
 const {scope,calls,form}=setup();form.set('evaluatorAlias','source-accuracy');form.set('evaluatorAlias2','source-clarity');form.set('evaluator2','{"id":"clarity","revision":4}');
 await scope.importExperimentDefinition();const data=calls[2].data;
 assert.equal(data.bindings.evaluators['source-accuracy'].revision,3);assert.equal(data.bindings.evaluators['source-clarity'].id,'clarity');assert.equal(data.bindings.evaluator,undefined);assert.equal(calls.length,3);
});
test('partial or duplicated alias bindings cannot reach conversion and remain editable',async()=>{
 const {scope,calls,form,details,elements}=setup();form.set('evaluatorAlias','same');form.set('evaluatorAlias2','same');form.set('evaluator2','{"id":"other","revision":2}');
 await scope.importExperimentDefinition();assert.equal(calls.length,2);assert.match(details[0].html,/unique source alias/);
 let draft;scope.importExperimentDefinition=async value=>{draft=value;};await elements.get('experiment-import-edit').onclick();assert.equal(draft.evaluatorAlias2,'same');assert.equal(draft.evaluator2,form.get('evaluator2'));
});

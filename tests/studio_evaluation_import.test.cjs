const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const wizard=source.slice(source.indexOf('async function importEvaluationPackage('),source.indexOf('window.addEventListener("studio-ready"',source.indexOf('async function importEvaluationPackage(')));
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setup(){
 const calls=[],details=[],elements=new Map();
 const file=(name,text)=>({name,size:20,text:async()=>text});
 const form=new Map([['dataset',file('dataset.yaml','kind: dataset')],['evaluator',file('evaluator.yaml','kind: evaluator')],['examples',file('cases.csv','sequence_number,prompt,expected_response')],['alias','general'],['provider','codex'],['model','chosen-model'],['threshold','0.75']]);
 const report={dataset:{cases:[{}]},source:{filename:'cases.csv',sha256:'digest'},adaptations:['JSON choice response'],document:{format:'nexus-studio-config/v1',definitions:[],resources:[]}};
 const scope=vm.createContext({escapeHtml,providerOptions:()=>'<option>Codex</option>',fieldsDialog:async()=>form,$:id=>{if(!elements.has(id))elements.set(id,{isConnected:true,disabled:false,close(){}});return elements.get(id);},featureApi:async(path,data)=>{calls.push({path,data});return path.endsWith('convert')?report:{id:'plan',digest:'reviewed',changes:[{action:'create',resource:'datasets:cases'}],document:report.document};},showDetail:(title,html)=>details.push({title,html}),refreshLibrary:async()=>{},notify:()=>{}});
 vm.runInContext(wizard,scope);return {scope,calls,form,elements,details};
}
test('wizard sends only three explicitly chosen file texts and requires review before saving',async()=>{
 const {scope,calls,elements,details}=setup();await scope.importEvaluationPackage();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'configuration/evaluations/convert');
 assert.deepEqual(Object.keys(calls[0].data.files),['cases.csv']);assert.equal(calls[0].data.binding.model,'chosen-model');assert.equal(calls[0].data.threshold,0.75);
 assert.match(details[0].html,/JSON choice response/);
 await elements.get('evaluation-package-plan').onclick();assert.equal(calls[1].path,'configuration/plan');assert.equal(calls.length,2);
 await elements.get('evaluation-package-apply').onclick();assert.equal(calls[2].path,'configuration/apply');assert.equal(calls[2].data.digest,'reviewed');
});
test('cancel or oversized files produce no server request',async()=>{
 const cancelled=setup();cancelled.scope.fieldsDialog=async()=>null;await cancelled.scope.importEvaluationPackage();assert.equal(cancelled.calls.length,0);
 const large=setup();large.form.get('examples').size=900*1024+1;await assert.rejects(large.scope.importEvaluationPackage(),/900 KiB/);assert.equal(large.calls.length,0);
});
test('conversion failure creates neither review plan nor saves',async()=>{
 const {scope,calls}=setup();scope.featureApi=async(path)=>{calls.push({path});throw new Error('CSV filename mismatch');};
 await assert.rejects(scope.importEvaluationPackage(),/filename mismatch/);assert.equal(calls.length,1);
});

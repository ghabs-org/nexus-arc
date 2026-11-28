const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const helpers=source.slice(source.indexOf('function evaluatorCopy('),source.indexOf('function operationFields('));
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const copy=(id='accuracy')=>({id,revision:2,name:'<Judge>',judge:{provider:'codex',rubric:'Grade'}});
function setup(){
 const selects=[{value:JSON.stringify(copy())},{value:JSON.stringify(copy('clarity'))},{value:''},{value:''}],primary={value:'clarity'},status={textContent:''};
 const root={isConnected:true,querySelectorAll:()=>selects};
 const elements={'experiment-evaluators':root,'experiment-primary-evaluator':primary,'experiment-evaluator-status':status};
 const context=vm.createContext({escapeHtml,$:id=>elements[id],api:async()=>({items:[]})});vm.runInContext(helpers,context);
 return {context,selects,primary,status,root};
}
test('editor preserves exact copied settings and explicit all-judge policy',()=>{
 const {context}=setup();const result=context.experimentEvaluatorFormData(new Map([['primaryEvaluatorId','clarity']]));
 assert.equal(result.evaluatorPolicy,'all');assert.equal(result.evaluators.length,2);assert.equal(result.evaluators[0].revision,2);assert.equal(result.primaryEvaluatorId,'clarity');
 const html=context.experimentEvaluatorFields(result);assert.match(html,/Every selected evaluator must pass/);assert.match(html,/&lt;Judge&gt;/);assert.doesNotMatch(html,/<Judge>/);assert.equal((html.match(/class="experiment-evaluator-choice"/g)||[]).length,4);
});
test('duplicate selection and missing primary cannot save; no copies preserves old case judges',()=>{
 const {context,selects}=setup();assert.throws(()=>context.experimentEvaluatorFormData(new Map()),/primary evaluator/);
 selects[1].value=selects[0].value;assert.throws(()=>context.experimentEvaluatorFormData(new Map([['primaryEvaluatorId','accuracy']])),/distinct/);
 selects.forEach(select=>select.value='');assert.equal(Object.keys(context.experimentEvaluatorFormData(new Map())).length,0);
});
test('failed catalog refresh retains saved copies and removing primary requires another explicit choice',async()=>{
 const {context,selects,primary,status}=setup();context.api=async()=>{throw Error('Unavailable');};
 const original=selects.map(select=>select.value);await context.loadExperimentEvaluators({});assert.deepEqual(selects.map(select=>select.value),original);assert.match(status.textContent,/Saved copies retained/);
 selects[1].value='';context.syncExperimentPrimary();assert.equal(primary.required,true);assert.doesNotMatch(primary.innerHTML,/value="clarity"/);
});
test('report labels primary and secondary outcomes and exposes escaped interrupted grading',()=>{
 const {context}=setup();const report={evaluators:[copy(),copy('clarity')],primaryEvaluatorId:'clarity',evaluator_summaries:[{...copy(),passed:0,total:1,expected_total:2,mean_score:0.1},{...copy('clarity'),passed:1,total:1,expected_total:2,mean_score:1}]};
 const html=context.experimentEvaluatorReport(report);assert.match(html,/0\/2 passed/);assert.match(html,/primary/);assert.doesNotMatch(html,/<Judge>/);
 report.pending_grading={slot:{variant:'<A>',case:1,repetition:1,output:'<script>unsafe</script>',evaluators:[{...copy(),passed:true}]}};
 const pending=context.experimentPendingGrades(report);assert.match(pending,/Unfinished grading/);assert.match(pending,/not replay automatically/);assert.match(pending,/&lt;script&gt;/);assert.doesNotMatch(pending,/<script>/);
});

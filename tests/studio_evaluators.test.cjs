const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const helpers=source.slice(source.indexOf('function evaluatorFields('),source.indexOf('function evaluationDatasetFields('));
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setup(api){
 const judges=[{value:'OLD_JUDGE'},{value:'OLD_JUDGE_2'}],elements=new Map();
 for(const id of ['evaluation-saved-evaluator','evaluation-apply-evaluator','evaluation-cases','evaluation-evaluator-status','library-form-error'])elements.set(id,{isConnected:true,value:'evidence',textContent:'',querySelectorAll:()=>judges});
 const context=vm.createContext({escapeHtml,api,$:id=>elements.get(id),providerOptions:()=>'<option>Codex</option>',inputField:()=>''});vm.runInContext(helpers,context);
 return {context,judges,elements,button:{isConnected:true,disabled:false}};
}
test('evaluator form creates fixed label scores and keeps special property names as data',()=>{
 const {context}=setup();
 const form=new Map([['provider','codex'],['rubric','Evidence'],['threshold','0.5'],['choices','Supported = 1\nPartial = 0.5\n__proto__ = 0']]);
 const result=context.evaluatorFormData(form);
 assert.equal(result.kind,'evaluator');assert.equal(result.type,'llm_judge');assert.equal(result.choices.Partial,0.5);assert.equal(result.choices.__proto__,0);
 form.set('choices','');assert.equal(context.evaluatorFormData(form).choices,undefined);
});
test('ambiguous or invalid choice mappings are rejected before saving',()=>{
 const {context}=setup();
 for(const value of ['Pass = 1\nPass = 0','Pass = NaN','Pass =','Pass = 2','Pass 1'])assert.throws(()=>context.evaluatorFormData(new Map([['choices',value]])),/unique labels/);
});
test('editor escapes instructions and explains provider usage',()=>{
 const {context}=setup();const html=context.evaluatorFields({rubric:'</textarea><script>bad()</script>',choices:{'"bad"':1,Fail:0}});
 assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);assert.match(html,/account.s usage/);
 assert.match(context.savedEvaluatorFields(),/all draft cases/);
});
test('applying copies only judge settings to each current case without executing',async()=>{
 const calls=[];const {context,judges,elements,button}=setup(async path=>{calls.push(path);return {name:'Evidence',revision:3,data:{kind:'evaluator',version:1,type:'llm_judge',provider:'codex',rubric:'Evidence',threshold:0.5,choices:{Pass:1,Fail:0}}};});
 await context.applySavedEvaluator(button);
 assert.deepEqual(calls,['/api/catalog/evaluators/evidence']);
 assert.equal(JSON.parse(judges[0].value).kind,undefined);assert.equal(JSON.parse(judges[1].value).choices.Pass,1);
 assert.match(elements.get('evaluation-evaluator-status').textContent,/revision 3/);assert.match(elements.get('evaluation-evaluator-status').textContent,/no AI was called/);
});
test('archival, failures and stale editor responses never replace judges',async()=>{
 for(const api of [async()=>({archived:true}),async()=>{throw new Error('Access expired');}]){
  const {context,judges,button}=setup(api);await context.applySavedEvaluator(button);assert.equal(judges[0].value,'OLD_JUDGE');
 }
 let finish;const view=setup(()=>new Promise(resolve=>{finish=resolve;}));const pending=view.context.applySavedEvaluator(view.button);
 view.elements.get('evaluation-cases').isConnected=false;finish({data:{rubric:'Late'}});await pending;assert.equal(view.judges[0].value,'OLD_JUDGE');
});
test('report renders choice labels and invalid response errors as escaped text',async()=>{
 let rendered;const context=vm.createContext({experimentReportSummary:()=>'',escapeHtml,badge:()=>'',api:async()=>({name:'Report',passed:0,total:1,results:[{variant:'A',case:1,passed:false,input:'Review',output:'Answer',checks:[{type:'choice_judge',phrase:'Evidence',passed:false,choice:'<unsafe>',error:'<invalid>'}]}]}),showDetail:(name,html)=>{rendered=html;}});
 vm.runInContext(source.slice(source.indexOf('async function showEvaluationReport('),source.indexOf('document.addEventListener("click",async event=>{',source.indexOf('async function showEvaluationReport('))),context);
 await context.showEvaluationReport('report');assert.match(rendered,/choice &lt;unsafe&gt;/);assert.match(rendered,/&lt;invalid&gt;/);assert.doesNotMatch(rendered,/<unsafe>|<invalid>/);
});

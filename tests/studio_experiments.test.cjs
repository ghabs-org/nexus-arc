const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const helpers=source.slice(source.indexOf('function savedExperimentFields('),source.indexOf('function operationFields('));
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setup(api){
 const root={children:[{},{}],querySelectorAll:()=>[]},select={isConnected:true,value:'',innerHTML:'',selectedOptions:[{dataset:{cases:'2'}}],insertAdjacentHTML:(_,html)=>{select.innerHTML+=html;}},status={textContent:''},error={textContent:''};
 const elements={'saved-experiment-suite':select,'saved-experiment-variants':root,'saved-experiment-estimate':status,'library-form-error':error};
 const context=vm.createContext({escapeHtml,api,$:id=>elements[id],queueMicrotask:()=>{},variantFields:()=>'<div>Variant</div>',document:{querySelector:()=>({value:'3'})}});
 vm.runInContext(helpers,context);return {context,select,root,status,error};
}
test('saved editor explains pinned revisions, independent thresholds and execution limits',()=>{
 const {context}=setup();const html=context.savedExperimentFields({runsPerExample:3,passThreshold:0.5});
 assert.match(html,/Suite edits require reviewing/);assert.match(html,/Every variant must meet/);assert.match(html,/Saving never executes/);assert.match(html,/name="runsPerExample"/);
 assert.match(html,/name="maxWorkers" min="1" max="4"/);assert.match(html,/workflow experiments require one worker/);
});
test('editing an old pin never silently selects a newer suite revision',async()=>{
 const view=setup(async()=>({items:[{id:'suite',name:'<unsafe>',revision:2,data:{cases:[{}]}}]}));
 await view.context.loadExperimentSuites({evaluation_id:'suite',evaluation_revision:1});
 assert.equal(view.select.value,'["suite",1]');assert.match(view.select.innerHTML,/review latest suite/);assert.match(view.select.innerHTML,/&lt;unsafe&gt;/);
 view.context.updateExperimentEstimate();assert.match(view.status.textContent,/12 planned case executions/);
});
test('saved form preserves reviewed suite, repeat count, threshold and variant settings',()=>{
 const view=setup();const fields={'.variant-name':'Baseline','.variant-provider':'codex','.variant-model':'model','.variant-effort':'high'};
 view.root.querySelectorAll=()=>[{querySelector:key=>({value:fields[key]})}];
 const data=view.context.savedExperimentFormData(new Map([['suite','["suite",2]'],['runsPerExample','3'],['passThreshold','0.5'],['maxWorkers','4']]));
 assert.equal(data.kind,'experiment');assert.equal(data.evaluation_revision,2);assert.equal(data.runsPerExample,3);assert.equal(data.passThreshold,0.5);assert.equal(data.maxWorkers,4);assert.equal(data.variants[0].effort,'high');
});
test('report distinguishes complete threshold rejection from partial execution',()=>{
 const {context}=setup();const report={experiment_id:'experiment',experiment_revision:1,expected_total:4,total:4,pass_threshold:0.5,complete:true,acceptance:false,variant_results:[{name:'<Weak>',passed:0,expected_total:2,accepted:false}]};
 const rejected=context.experimentReportSummary({...report,max_workers:4});assert.match(rejected,/not met by every variant/);assert.match(rejected,/&lt;Weak&gt;/);assert.match(rejected,/4 workers/);assert.doesNotMatch(rejected,/Incomplete/);
 const partial=context.experimentReportSummary({...report,complete:false,acceptance:null,total:1});assert.match(partial,/No acceptance result/);assert.doesNotMatch(partial,/Below threshold/);
 assert.equal(context.experimentReportSummary({}), '');
});
test('named launch sends only experiment identity and uses existing job polling',async()=>{
 const calls=[];const context=vm.createContext({api:async(path,data)=>{calls.push({path,data});return{id:'run'};},showRun:()=>{},pollRun:()=>Promise.resolve(),updateRunDetail:()=>{},refreshOperationResults:async()=>{},refreshRuns:async()=>{}});
 vm.runInContext(source.slice(source.indexOf('async function runEvaluation('),source.indexOf('function variantFields(')),context);
 await context.runEvaluation(null,null,'experiment');assert.equal(calls[0].path,'/api/runs');assert.equal(calls[0].data.experiment_id,'experiment');assert.equal(calls[0].data.evaluation_id,undefined);assert.equal(calls[0].data.variants,undefined);
});
test('waiting experiment controls identify the case and bind decisions to its pause',()=>{
 const studio=fs.readFileSync('nexus/studio_static/studio.js','utf8');
 const context=vm.createContext({escapeHtml});
 vm.runInContext(studio.slice(studio.indexOf('function waitingRunActions('),studio.indexOf('function cleanupRunStatus(')),context);
 const html=context.waitingRunActions({id:'run',kind:'evaluation',status:'waiting',evaluation_checkpoint:'pause-one',evaluation_case:{variant:'<Baseline>',case:2,repetition:3,runs_per_example:4},awaiting_approval:[{name:'Review'}]});
 assert.match(html,/Experiment case: &lt;Baseline&gt; · case 2 · repetition 3\/4/);
 assert.equal((html.match(/data-evaluation-checkpoint="pause-one"/g)||[]).length,2);
 assert.doesNotMatch(source,/Experiment Lab accepts non-interactive workflows/);
});
test('approval actions submit the checkpoint rendered with that button',async()=>{
 let listener,submitted;
 const context=vm.createContext({$:()=>({addEventListener(){}}),document:{addEventListener:(name,callback)=>{if(name==='click')listener=callback;}},api:async(path,data)=>{submitted={path,data};return {status:'waiting'};},showRun:()=>{},notify:message=>{throw Error(message);}});
 vm.runInContext(fs.readFileSync('nexus/studio_static/graph.js','utf8'),context);
 await listener({target:{closest:selector=>selector==="button"?({dataset:{approveRun:'run',evaluationCheckpoint:'reviewed-pause'}}):null}});
 assert.equal(submitted.path,'/api/runs/run/resume');assert.equal(submitted.data.decision,'approve');assert.equal(submitted.data.evaluation_checkpoint,'reviewed-pause');
});
test('typed input forms submit the checkpoint and preserve structured values',async()=>{
 const features=fs.readFileSync('nexus/studio_static/features.js','utf8');
 let form,submitted;
 const context=vm.createContext({run:{id:'run',evaluation_checkpoint:'input-pause',awaiting_input:[{id:'root/answer',name:'Count',schema:{type:'integer'}}]},root:{append:element=>{form=element;}},document:{createElement:()=>({dataset:{}})},escapeHtml,FormData:class{get(){return '7';}},featureApi:async(path,data)=>{submitted={path,data};},pollRun:()=>Promise.resolve(),updateRunDetail:()=>{},notify:message=>{throw Error(message);}});
 vm.runInContext(features.slice(features.indexOf('for(const requested of run.awaiting_input'),features.indexOf('\n  }).catch(error=>',features.indexOf('for(const requested of run.awaiting_input'))),context);
 await form.onsubmit({preventDefault(){}});
 assert.equal(submitted.path,'runs/run/input');assert.equal(submitted.data.evaluation_checkpoint,'input-pause');assert.equal(submitted.data.values['root/answer'],7);
});

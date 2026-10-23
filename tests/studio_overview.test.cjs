const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const assert=require('node:assert/strict');
const source=fs.readFileSync('nexus/studio_static/workspace.js','utf8');
const functions=source.slice(source.indexOf('function operationalOverviewHtml'),source.indexOf('document.addEventListener("click",async event=>{',source.indexOf('function operationalOverviewHtml')));
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const data=()=>({scope:'account',recent_limit:20,feedback:{helpful:2,needs_improvement:1},evaluations:{total:1,completed:0,items:[{id:'report',name:'<unsafe>',status:'failed',passed:1,total:1}]},builds:{items:[{id:'build',name:'Build',status:'saved',revision:2,health:{passed:1,failed:1,stale:1,supported:5,checked:3},comparison:{from_release_id:'before',to_release_id:'after',added:['agents:<unsafe>'],removed:[],changed:['prompts:review']}}]}});
function setup(api){const root={innerHTML:'old',textContent:''};const context=vm.createContext({escapeHtml,$:()=>root,api});vm.runInContext(functions,context);return {context,root};}
test('overview explains partial results, saved health, comparison and existing detail links',()=>{
 const {context}=setup();const html=context.operationalOverviewHtml(data());
 assert.match(html,/Partial or unknown execution/);assert.match(html,/1 passed · 1 failed · 1 stale · 2 unchecked/);
 assert.match(html,/data-evaluation-report="report"/);assert.match(html,/data-overview-build="build"/);
 assert.match(html,/agents:&lt;unsafe&gt;/);assert.doesNotMatch(html,/<unsafe>/);
 assert.match(html,/private to your account/);assert.match(html,/never runs checks/);
});
test('empty and unavailable states remain explicit',()=>{
 const {context}=setup();const empty=data();empty.evaluations.items=[];empty.builds.items=[];
 assert.match(context.operationalOverviewHtml(empty),/No visible evaluation reports/);
 const missing=data();Object.assign(missing.builds.items[0],{health:null,health_status:'unavailable',comparison:null,comparison_status:'unavailable'});
 assert.match(context.operationalOverviewHtml(missing),/Health unavailable/);
 assert.match(context.operationalOverviewHtml(missing),/Release comparison unavailable/);
});
test('fully graded threshold rejection is not mislabeled as partial execution',()=>{
 const {context}=setup();const report=data();Object.assign(report.evaluations.items[0],{complete:true,acceptance:false,status:'failed'});
 const html=context.operationalOverviewHtml(report);assert.match(html,/Threshold not met/);assert.doesNotMatch(html,/Partial or unknown execution/);
});
test('late requests cannot overwrite a newer refresh and failures clear old summaries',async()=>{
 const pending=[];const {context,root}=setup(()=>new Promise((resolve,reject)=>pending.push({resolve,reject})));
 const first=context.refreshOperationalOverview(),second=context.refreshOperationalOverview();
 const fresh=data();fresh.feedback.helpful=9;pending[1].resolve(fresh);await second;
 const current=root.innerHTML;pending[0].resolve(data());await first;assert.equal(root.innerHTML,current);
 const failed=context.refreshOperationalOverview();pending[2].reject(new Error('Access expired'));await failed;
 assert.match(root.textContent,/unavailable: Access expired/);
});

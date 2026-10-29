const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/operations.js','utf8');
const functions=source.slice(source.indexOf('async function showEvaluationReport('),source.indexOf('document.addEventListener("click",async event=>{',source.indexOf('async function showEvaluationReport(')));
const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

test('report viewer exposes an explicit private export without downloading on open',async()=>{
  let html,downloads=0;
  const scope=vm.createContext({escapeHtml,api:async()=>({results:[],passed:0,total:0}),experimentReportSummary:()=>'',showDetail:(_,body)=>{html=body;},downloadJson:()=>downloads++});
  vm.runInContext(functions,scope);
  await scope.showEvaluationReport('report"<unsafe>');
  assert.ok(html.includes('Download report JSON'));
  assert.ok(html.includes('nexus eval check-report report.json'));
  assert.ok(html.includes('Keep this file private'));
  assert.ok(!html.includes('<unsafe>'));
  assert.equal(downloads,0);
});

test('export fetches the authenticated report once and retains its full evidence',async()=>{
  const report={complete:true,results:[{input:'private',checks:[{passed:true}]}]};
  const requests=[],downloads=[];
  const scope=vm.createContext({api:async(...args)=>{requests.push(args);return report;},downloadJson:(...args)=>downloads.push(args)});
  vm.runInContext(functions,scope);
  const button={dataset:{evaluationReportExport:'report/one'},isConnected:true};
  await scope.exportEvaluationReport(button);
  assert.deepEqual(requests,[['/api/evaluation-reports/report%2Fone']]);
  assert.deepEqual(downloads,[['evaluation-report.json',report]]);
  assert.equal(button.disabled,false);
});

test('authorization or transport failure produces no export and reenables its button',async()=>{
  let downloads=0;
  const scope=vm.createContext({api:async()=>{throw new Error('Forbidden');},downloadJson:()=>downloads++});
  vm.runInContext(functions,scope);
  const button={dataset:{evaluationReportExport:'report'},isConnected:true};
  await assert.rejects(scope.exportEvaluationReport(button),/Forbidden/);
  assert.equal(downloads,0);
  assert.equal(button.disabled,false);
});

test('report file provenance distinguishes roles and escapes metadata without file text',async()=>{
 const scope=vm.createContext({escapeHtml});vm.runInContext(functions,scope);
 const result=scope.evaluationFileSources({file_sources:{input_files:[{id:'source',revision:2,filename:'<script>input.txt',sha256:'digest'}],reference_files:[{id:'expected',revision:3,filename:'reference.txt',sha256:'other',text:'DO_NOT_RENDER'}]}});
 assert.match(result,/Input/);assert.match(result,/Grading reference/);assert.match(result,/revision 3/);assert.match(result,/&lt;script&gt;/);assert.doesNotMatch(result,/<script>|DO_NOT_RENDER/);
});

const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const studio=fs.readFileSync('nexus/studio_static/studio.js','utf8');
const content=fs.readFileSync('nexus/studio_static/conversation-content.js','utf8');
const workbench=fs.readFileSync('nexus/studio_static/workbench.js','utf8');
const between=(source,start,end)=>{const from=source.indexOf(start);assert.ok(from>=0,start);const to=end?source.indexOf(end,from+start.length):source.length;return source.slice(from,to);};
const escapeHtml=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');

function context(extra={}){
  const dom={'wb-usage':{innerHTML:''},'wb-router':{innerHTML:''},'wb-openclaw':{innerHTML:''}};
  const ctx=vm.createContext({escapeHtml,state:{providers:[{id:'codex',name:'Codex',configured:true},{id:'claude-code',name:'Claude Code',configured:false}],capabilities:{}},
    $:id=>dom[id],badge:(text,kind='')=>`<span class="badge ${kind}">${text}</span>`,...extra});
  const line=(source,start)=>between(source,start).split('\n')[0];
  vm.runInContext(line(studio,'function providerName(')+'\n'+line(studio,'function providerOptions('),ctx);
  vm.runInContext(line(workbench,'function wbRows('),ctx);
  vm.runInContext(between(workbench,'function wbCount('),ctx);
  vm.runInContext(between(content,'const ROUTE_TASKS=','async function sendRouteFeedback('),ctx);
  return {ctx,dom};
}

test('Auto appears only where allowed and only with a configured router',()=>{
  const {ctx}=context();
  assert.doesNotMatch(ctx.providerOptions('codex',false,true),/value="auto"/);
  ctx.state.capabilities={model_router:true};
  assert.doesNotMatch(ctx.providerOptions('codex'),/value="auto"/);
  const html=ctx.providerOptions('auto',false,true);
  assert.match(html,/<option value="auto" selected>Auto \(model router\)<\/option>/);
  assert.match(html,/Claude Code · setup needed/);
  assert.match(ctx.providerOptions('',true,true),/Use each agent’s provider<\/option><option value="auto"/);
});

test('routed answers show where they ran and ask for feedback once',()=>{
  const {ctx}=context();
  assert.equal(ctx.routeInfoHtml({status:'completed',provider:'codex'}),'');
  const run={id:'r1',status:'completed',provider:'claude-code',model:'claude-opus-5-5',router_decision_id:'d1',fallback_from:'codex'};
  const html=ctx.routeInfoHtml(run);
  assert.match(html,/<span class="route-badge">Auto<\/span><span class="route-model">claude-code · claude-opus-5-5<\/span>/);
  assert.match(html,/switched from codex/);
  for(const verdict of ['correct','too_cheap','too_powerful'])assert.match(html,new RegExp(`data-route-feedback="${verdict}" data-run-id="r1"`));
  assert.match(html,/select data-route-task="r1"/);
  assert.match(html,/<option value="code_review">Code review<\/option>/);
  assert.match(html,/<option value="">Pick…<\/option>/);
  const told=ctx.routeInfoHtml({...run,router_explain:{task:'coding',complexity:'high',confidence:0.906,candidates:[{model:'m',score:1}]}});
  assert.match(told,/<span class="route-task"[^>]*>Coding · high complexity · 91% sure<\/span>/);
  assert.match(told,/data-current="coding"/);
  assert.match(told,/<option value="coding" selected>Coding \(router\)<\/option>/);
  assert.doesNotMatch(told,/Pick…/);
  assert.doesNotMatch(ctx.routeInfoHtml({...run,status:'running'}),/data-route-feedback/);
  const done=ctx.routeInfoHtml({...run,router_feedback:{verdict:'wrong'}});
  assert.match(done,/feedback went to the router \(wrong pick\)/);
  assert.doesNotMatch(done,/data-route-feedback/);
  assert.match(ctx.routeInfoHtml({status:'completed',provider:'codex',router_error:'down'}),/Automatic model selection unavailable · Answered by codex/);
});

test('router fallback never claims an answer before the run completes',()=>{
  const {ctx}=context();
  const fallback={provider:'codex',router_error:'down'};
  for(const status of ['queued','running','failed','interrupted','cancelled']){
    const html=ctx.routeInfoHtml({...fallback,status});
    assert.doesNotMatch(html,/Answered by/);
    assert.match(html,/Automatic model selection unavailable · Fallback provider: codex/);
  }
  assert.match(ctx.routeInfoHtml({...fallback,status:'completed'}),/Answered by codex/);
});

test('usage table separates reported tokens from providers that report none',()=>{
  const {ctx,dom}=context();
  ctx.renderUsage({providers:[{provider:'codex',invocations:3,reported:0,total_tokens:0},{provider:'claude-code',invocations:2,reported:1,total_tokens:1200}]});
  const html=dom['wb-usage'].innerHTML;
  assert.match(html,/<th scope="row">Codex<\/th><td>3<\/td><td><span class="muted">not reported/);
  assert.match(html,/Claude Code<\/th><td>2<\/td><td>1\D?200 <span class="muted">\(1 not reported\)/);
  ctx.renderUsage({providers:[]});
  assert.match(dom['wb-usage'].innerHTML,/No runs in the last 30 days/);
});

test('router panel explains setup, shows warnings, cooldowns and CLI use, and isolates failures',()=>{
  const {ctx,dom}=context();
  ctx.renderRouter({configured:false});
  assert.match(dom['wb-router'].innerHTML,/allow_private/);
  ctx.renderRouter({configured:true,url:'http://127.0.0.1:7771',
    diagnostics:{warnings:['registry is old'],registry:{models:10,authed_models:4,updated_at:'2026-11-01'},classifier:{local_available:false},
      providers:{nvidia:{enabled:true,health_score:0.9,auth:'ok',quota:'healthy'}},model_cooldowns:[{model:'nvidia/x',scope:'coding',cooldown_until:'soon'}]},
    cli_status:{error:'Configured runtime service is unavailable'}});
  const html=dom['wb-router'].innerHTML;
  assert.match(html,/1 warning<\/strong><ul><li>registry is old/);
  assert.match(html,/4 of 10 models signed in/);
  assert.match(html,/heuristic fallback/);
  assert.match(html,/health 0.90 · auth ok/);
  assert.match(html,/nvidia\/x on coding until soon/);
  assert.match(html,/role="alert">Configured runtime service is unavailable/);
  ctx.renderRouter({configured:true,url:'u',diagnostics:{warnings:[]},cli_status:{active_session_pins:[{}],ordering:'learned',usage_24h:[{target:'a:b',turns:2,failed:0,input_tokens:5,output_tokens:1}],exports:['export ANTHROPIC_BASE_URL=x']}});
  assert.match(dom['wb-router'].innerHTML,/No warnings/);
  assert.match(dom['wb-router'].innerHTML,/1 pinned sessions · ordering learned/);
  assert.match(dom['wb-router'].innerHTML,/a:b: 2 turns, 0 failed, 5 in \/ 1 out tokens/);
  assert.match(dom['wb-router'].innerHTML,/export ANTHROPIC_BASE_URL=x/);
});

test('OpenClaw panel lists entrypoints, their runs and waiting replies',()=>{
  const {ctx,dom}=context();
  ctx.renderOpenClaw({uncertain_receipts:1,items:[{name:'Ops',enabled:true,token_configured:false,channels:['telegram'],mapped_senders:2,targets:['agents:helper'],open_gates:1,
    runs:[{run_id:'r1',task:'status <b>',channel:'telegram',status:'waiting',waiting:true,updated_at:'now'}]}]});
  const html=dom['wb-openclaw'].innerHTML;
  assert.match(html,/1 OpenClaw request did not confirm dispatch/);
  assert.match(html,/telegram · 2 mapped senders · targets agents:helper · signing secret missing · 1 waiting for a reply/);
  assert.match(html,/status &lt;b>/);
  assert.match(html,/badge ">Waiting/);
  ctx.renderOpenClaw({items:[]});
  assert.match(dom['wb-openclaw'].innerHTML,/No OpenClaw entrypoints/);
});

test('why-this-model explains task, candidates and rules', () => {
  const {ctx}=context();
  assert.equal(ctx.routeWhyHtml(null),'');
  const html=ctx.routeWhyHtml({task:'code_review',confidence:0.91,classifier:'local',rules:['caller_catalog'],complexity:'high',candidates:[{model:'anthropic/claude-opus-5-5',score:0.8212},{model:'x/<y>',score:0.5}],considered:7,policy:'exp:b'});
  assert.match(html,/<summary>Why this model\?<\/summary>/);
  assert.match(html,/<dt>Task<\/dt><dd>Code review \(91% confidence\)<\/dd>/);
  assert.match(html,/<dt>Complexity<\/dt><dd>High<\/dd><dt>Classifier<\/dt><dd>Local<\/dd><dt>Policy<\/dt><dd>exp:b<\/dd>/);
  assert.match(html,/<li class="is-picked"><span class="route-cand-model">anthropic\/claude-opus-5-5<\/span><span class="route-cand-score">0.82<\/span><span class="route-cand-tag">picked<\/span><\/li>/);
  assert.match(html,/<li><span class="route-cand-model">x\/&lt;y><\/span><span class="route-cand-score">0.50<\/span><\/li>/);
  assert.match(html,/\+5 more models scored lower/);
  assert.match(html,/<span class="route-rule" title="caller_catalog">Studio's model list<\/span>/);
  assert.doesNotMatch(ctx.routeWhyHtml({candidates:[{model:'m',score:1}],considered:1}),/more model/);
  const run={id:'r1',status:'completed',provider:'claude-code',router_decision_id:'d',router_explain:{candidates:[{model:'m',score:1}]}};
  assert.match(ctx.routeInfoHtml(run),/route-why/);
});

test('run detail lists each routed step and providers show the router view', () => {
  const {ctx}=context();
  vm.runInContext(between(studio,'function runRouterStepsHtml(','function renderRunRouterSteps(')+between(studio,'function routerViewText(','async function refreshRouterProviders('),ctx);
  assert.equal(ctx.runRouterStepsHtml({}),'');
  const html=ctx.runRouterStepsHtml({router_decisions:[{step:'draft',routed_model:'anthropic/claude-opus-5-5',provider:'claude-code',outcome:'success',explain:{task:'coding'}},{routed_model:'openai-codex/gpt-6.1-sol'}]});
  assert.match(html,/<strong>draft<\/strong>: anthropic\/claude-opus-5-5 via Claude Code · success · read as coding/);
  assert.match(html,/<li>openai-codex\/gpt-6.1-sol<\/li>/);
  ctx.state.routerProviders={codex:{health_score:0.42,auth:'ok',quota:'low',cooldown_until:'2026-11-09T10:00:00Z'}};
  assert.match(ctx.routerViewText({id:'codex'}),/Router view · health 42% · signed in · quota low · cooling until 2026-11-09T10:00:00Z/);
  assert.equal(ctx.routerViewText({id:'agy'}),'');
});

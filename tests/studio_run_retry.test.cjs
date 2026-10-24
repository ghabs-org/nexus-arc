const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/features.js','utf8');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attributes={};this.isConnected=true;}
  append(...children){this.children.push(...children);}
  before(node){this.previous=node;}
  remove(){this.isConnected=false;}
  setAttribute(k,v){this.attributes[k]=v;}
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
function fixture({review=false,blocked='',saved=false}={}){
  const requests=[],watched=[],notices=[],output=new Element('div');let shown;
  const plan={digest:'revision-1',can_retry:!blocked,blocked_reason:blocked,continues_workflow:!saved,
    steps:[{id:saved?'__build_finalize__':'root/map/1/review',name:saved?'Validate and save build':'Review recommendation',kind:review?'tool':'agent',requires_effect_review:review,uses_saved_output:saved}]};
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},state:{detailRunId:'one'},
    $:id=>id==='run-output'?output:null,
    featureApi:async(path,body)=>{requests.push([path,body]);return body?{id:'one',status:'queued'}:plan;},
    showRun:run=>shown=run,refreshRuns:async()=>{},pollRun:async(id)=>{watched.push(id);},
    updateRunDetail(){},notify:message=>notices.push(message),
  });
  vm.runInContext(source.slice(source.indexOf('async function renderRunRetryActions'),source.indexOf('const basicRunDetail=')),context);
  return {context,requests,watched,notices,output,shown:()=>shown};
}

test('Activity inspection adds named retry controls before output without submitting work',async()=>{
  const f=fixture();await f.context.renderRunRetryActions({}, {id:'one',status:'failed'});
  const section=f.output.previous;
  assert.equal(section.id,'run-retry-controls');
  const row=section.children.find(c=>c.className==='run-retry-step');
  assert.equal(row.children[0].children[0].textContent,'Review recommendation');
  assert.equal(row.children[0].children[1].textContent,'root/map/1/review');
  assert.equal(f.requests.length,1);assert.equal(f.requests[0][1],undefined);
  const button=section.querySelectorAll('button')[0];await button.onclick();
  assert.equal(f.requests[1][0],'runs/one/retry');
  assert.deepEqual(JSON.parse(JSON.stringify(f.requests[1][1])),{digest:'revision-1',step_id:'root/map/1/review'});
  assert.deepEqual(f.watched,['one']);assert.equal(f.shown().status,'queued');
});

test('tool retry requires explicit effect review before any submission',async()=>{
  const f=fixture({review:true});await f.context.renderRunRetryActions({}, {id:'one',status:'failed'});
  const section=f.output.previous,button=section.querySelectorAll('button')[0],checkbox=section.querySelectorAll('input')[0];
  await button.onclick();assert.equal(f.requests.length,1);
  assert.match(section.children.at(-1).textContent,/external effects/);
  checkbox.checked=true;await button.onclick();assert.equal(f.requests[1][1].confirm_effects,true);
});

test('uncertain integrations expose existing review controls instead of a retry button',async()=>{
  const f=fixture({blocked:'Review the uncertain external action under Workspace tools before retrying.'});
  await f.context.renderRunRetryActions({}, {id:'one',status:'failed'});
  assert.equal(f.output.previous.querySelectorAll('button').length,0);
  assert.equal(f.output.previous.querySelectorAll('a')[0].href,'#workbench/effects');
  assert.equal(f.requests.length,1);
});

test('completed execution views never load retry plans or offer resubmission',async()=>{
  const f=fixture();await f.context.renderRunRetryActions({}, {id:'one',status:'completed'});
  assert.equal(f.requests.length,0);assert.equal(f.output.previous,undefined);
});

test('build recovery offers a single local step and makes its lack of LLM cost clear',async()=>{
  const f=fixture({saved:true});await f.context.renderRunRetryActions({}, {id:'one',status:'failed'});
  const section=f.output.previous,buttons=section.querySelectorAll('button');
  assert.equal(buttons.length,1);
  assert.equal(buttons[0].textContent,'Retry step');
  assert.equal(buttons[0].attributes['aria-label'],'Retry Validate and save build');
  assert.match(section.children[1].textContent,/completed LLM response/);
  assert.match(section.children[1].textContent,/No new LLM call or token cost/);
  assert.equal(f.requests.length,1);
  await buttons[0].onclick();
  assert.equal(f.requests[1][1].step_id,'__build_finalize__');
});

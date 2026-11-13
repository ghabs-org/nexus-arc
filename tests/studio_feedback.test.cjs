const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync('nexus/studio_static/features.js', 'utf8');
const functions = source.slice(source.indexOf('async function refreshFeedbackView('), source.indexOf('async function shareConversation('));
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function setup({items = [], cancel = false, conflict = false} = {}) {
  const calls = [], dialogs = [], downloads = [], notices = [], elements = new Map();
  const context = vm.createContext({
    state: {mode:'desktop'},
    escapeHtml,
    $: id => {if(!elements.has(id))elements.set(id, {innerHTML:'', disabled:false});return elements.get(id);},
    notify: value => notices.push(value),
    downloadJson: (name,data) => downloads.push({name,data}),
    featureApi: async (path, data) => {
      calls.push({path,data});
      if(!data)return {items};
      if(conflict)throw new Error('Feedback changed; reopen its latest revision');
      return {...data, revision:data.revision+1};
    },
    fieldsDialog: async (title, html) => {
      dialogs.push({title,html});
      return cancel ? null : new Map([['rating','-1'],['comment','Revised note']]);
    }
  });
  vm.runInContext(functions, context);
  return {context,calls,dialogs,downloads,notices,elements,items};
}

test('editing uses the latest owner record and preserves optimistic concurrency', async () => {
  const view=setup({items:[{run_id:'run',rating:1,revision:4,comment:'</textarea><script>unsafe()</script>'}]});
  const saved=await view.context.rateResponse('run');
  assert.equal(saved.rating,-1);
  assert.equal(view.calls[1].data.revision,4);
  assert.match(view.dialogs[0].html,/&lt;\/textarea&gt;&lt;script&gt;/);
  assert.doesNotMatch(view.dialogs[0].html,/<script>/);
  assert.match(view.dialogs[0].html, /value="1" selected/);
  assert.equal(view.dialogs[0].title,'Edit response feedback');
});

test('new thumb rating starts at revision zero and existing comments survive rating changes', async () => {
  const fresh=setup();await fresh.context.rateResponse('new',-1);
  assert.equal(fresh.calls[1].data.revision,0);
  assert.match(fresh.dialogs[0].html,/value="-1" selected/);
  const prior=setup({items:[{run_id:'run',rating:1,revision:1,comment:'Retain this note'}]});
  await prior.context.rateResponse('run',-1);
  assert.match(prior.dialogs[0].html,/Retain this note/);
  assert.equal(prior.calls[1].data.revision,1);
});

test('cancel and conflict never overwrite feedback or report a false success', async () => {
  const cancelled=setup({cancel:true});
  assert.equal(await cancelled.context.rateResponse('run'),null);
  assert.equal(cancelled.calls.length,1);
  const conflict=setup({items:[{run_id:'run',revision:1}],conflict:true});
  await assert.rejects(conflict.context.rateResponse('run'),/reopen its latest revision/);
  assert.equal(conflict.notices.length,0);
  assert.equal(conflict.calls.length,2);
});

test('history escapes comments and connects editing, response inspection and promotion', async () => {
  const view=setup({items:[{id:'feedback',run_id:'run',rating:-1,comment:'<script>unsafe()</script>',task:'Question',updated_at:'2026-10-23T10:00:00Z'}]});
  await view.context.refreshFeedbackView();
  const html=view.elements.get('feedback-content').innerHTML;
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
  assert.match(html,/data-feedback-edit="run"/);
  assert.match(html,/data-run="run"/);
  assert.match(html,/data-feedback-promote="feedback"/);
});

test('export refetches current history instead of exporting stale page contents', async () => {
  const view=setup({items:[{id:'old'}]});await view.context.refreshFeedbackView();
  view.items.splice(0,1,{id:'updated',revision:2});
  await view.elements.get('feedback-export').onclick();
  assert.equal(view.calls.length,2);
  assert.equal(view.downloads[0].data.format,'nexus-response-feedback/v1');
  assert.equal(view.downloads[0].data.items[0].id,'updated');
  assert.equal(view.elements.get('feedback-export').disabled,false);
});

test('history controls reflect existing team write permissions', async () => {
  for (const [role,edit,promote] of [['viewer',false,false],['operator',true,false],['builder',true,true]]) {
    const view=setup({items:[{id:'feedback',run_id:'run',rating:1,updated_at:'2026-10-23T10:00:00Z'}]});
    view.context.state={mode:'team',user:{role}};
    await view.context.refreshFeedbackView();
    const html=view.elements.get('feedback-content').innerHTML;
    assert.equal(/data-feedback-edit="run" disabled/.test(html),!edit);
    assert.equal(/data-feedback-run="run" disabled/.test(html),!promote);
  }
});

test('promotion saves reviewed input and opens the existing evaluation editor without executing', async () => {
  const begin=source.indexOf('  if(b.dataset.feedbackPromote){');
  const branch=source.slice(begin,source.indexOf('  if(b.id==="design-resource")',begin));
  const calls=[],editors=[],elements=new Map();
  const context=vm.createContext({
    b:{dataset:{feedbackPromote:'feedback',feedbackRun:'run'}}, escapeHtml,
    api:async()=>({structured_content:[]}), targetField:()=>'',inputField:()=>'',
    fieldsDialog:async()=>new Map([['target','agent:reviewer'],['name','Review'],['expected','Approved']]),
    featureApi:async(path,data)=>{calls.push({path,data});return {id:'review',name:'Review'};},
    showDetail:()=>{}, library:{kind:'prompts'},location:{hash:'feedback'},
    $:id=>{if(!elements.has(id))elements.set(id,{close(){}});return elements.get(id);},
    openLibraryEditor:async id=>editors.push(id),notify:()=>{}
  });
  await vm.runInContext('(async()=>{'+branch+'})()',context);
  assert.equal(calls[0].path,'feedback/promote');
  assert.equal(calls[0].data.expected,'Approved');
  await elements.get('feedback-review-evaluation').onclick();
  assert.equal(context.library.kind,'evaluations');
  assert.equal(context.location.hash,'evaluations');
  assert.deepEqual(editors,['review']);
  assert.equal(calls.length,1);
});

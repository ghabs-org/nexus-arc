const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('nexus/studio_static/drafts.js', 'utf8');
const factorySource = source.slice(0, source.indexOf('\n(function () {'));
function storage() {
  const data = new Map();
  return {data, getItem:key=>data.get(key) ?? null, setItem:(key,value)=>data.set(key,value), removeItem:key=>data.delete(key)};
}
function store(shared = storage(), scope = ['team','alice','/workspace',''], now = ()=>1000000) {
  let n = 0;
  const context = vm.createContext({window:{crypto:{randomUUID:()=>`record-${++n}`}}});
  vm.runInContext(factorySource,context);
  return {shared, value:new context.window.NexusDraftStore(shared,scope,now,()=>`record-${++n}`)};
}
const payload = (text='Unsent task')=>({text,artifacts:['file-one'],settings:{provider:'codex',model:'chosen',effort:'high'},context:'conversation-one'});

test('drafts survive a new controller but remain isolated by user, workspace, mode and embedded agent',()=>{
  const {shared,value}=store(); value.write('chat',payload(),null);
  assert.equal(store(shared).value.read('chat').record.text,'Unsent task');
  for(const scope of [['team','bob','/workspace',''],['team','alice','/other',''],['desktop','alice','/workspace',''],['team','alice','/workspace','agent']]) assert.equal(store(shared,scope).value.read('chat').record,null);
  assert.equal(value.read('builder').record,null);
});

test('stored drafts contain only allowlisted settings and attachment references, never arbitrary file data or credentials',()=>{
  const {shared,value}=store();
  value.write('builder',{...payload(),file_bytes:'FILE_BODY',token:'TOKEN_SECRET',settings:{...payload().settings,password:'PASSWORD'}},null);
  const raw=[...shared.data.values()][0];
  assert.doesNotMatch(raw,/FILE_BODY|TOKEN_SECRET|PASSWORD|file_bytes|password/);
  assert.equal(JSON.parse(raw).artifacts[0],'file-one');
});

test('expiry and malformed/oversized records are removed only for their exact scope',()=>{
  let clock=1000000;const {shared,value}=store(undefined,undefined,()=>clock);
  value.write('chat',payload(),null); value.write('builder',payload(),null);
  clock+=7*24*60*60*1000;
  assert.equal(value.read('chat').record,null);assert.ok(shared.data.has(value.key('builder')));
  for(const raw of ['{invalid','null','"text"',JSON.stringify({version:1,id:'valid',savedAt:clock,text:3,artifacts:[]}), 'x'.repeat(1000001)]){
    shared.setItem(value.key('chat'),raw);assert.equal(value.read('chat').record,null);assert.equal(shared.getItem(value.key('chat')),null);
  }
  assert.throws(()=>value.key('toString'),/Unknown draft/);
});

test('version checks prevent overwriting or deleting a draft changed by another tab',()=>{
  const {shared,value}=store();const old=value.write('chat',payload(),null).record;
  const other=store(shared).value;other.uuid=()=> 'other-version';
  const newer=other.write('chat',payload('Other text'),old.id).record;
  assert.equal(value.write('chat',payload('Old tab'),old.id).conflict,true);
  assert.equal(value.remove('chat',old.id).conflict,true);
  assert.equal(value.read('chat').record.id,newer.id);
});

test('storage denial, quota failure and length limits preserve a usable caller without truncating current text',()=>{
  const denied=store({getItem(){throw Error('Denied');}}).value;
  assert.equal(denied.read('chat').ok,false);
  assert.equal(denied.write('chat',payload(),null).ok,false);
  const shared=storage(),current=store(shared).value;shared.setItem=()=>{throw Error('Quota');};
  assert.equal(current.write('chat',payload(),null).ok,false);
  assert.equal(current.write('builder',payload('x'.repeat(16001)),null).ok,false);
  assert.equal(current.write('chat',{...payload(),artifacts:Array(6).fill('id')},null).ok,false);
});

function ui(shared=storage(), options={}) {
  const elements=new Map(), events={}, docEvents={}, timers=new Map(), calls=[], notes=[];
  let sequence=0;
  function element(id) {
    if(!elements.has(id)) {
      const isSelect=/-artifacts$|-provider$|-agent$|-project$|-effort$|-tools$/.test(id);
      elements.set(id,{id,value:'',hidden:false,disabled:false,dataset:{},tagName:isSelect?'SELECT':'TEXTAREA',options:[],listeners:{},
        addEventListener(name,fn){this.listeners[name]=fn;},focus(){this.focused=true;},
        get selectedOptions(){return this.options.filter(option=>option.selected);}});
    }
    return elements.get(id);
  }
  for(const name of ['chat','builder']){
    for(const suffix of ['task','artifacts','draft','draft-message','draft-restore','draft-discard','provider','model','effort','tools','agent','project'])element(`${name}-${suffix}`);
    element(`${name}-provider`).value='codex';element(`${name}-provider`).options=[{value:'codex'},{value:'claude-code'}];
    element(`${name}-effort`).options=[{value:''},{value:'high'}];
    element(`${name}-tools`).value='web';element(`${name}-tools`).options=[{value:'web'},{value:'none'}];
    element(`${name}-agent`).options=[{value:''}];element(`${name}-project`).options=[{value:''}];
    element(`${name}-artifacts`).options=[{value:'file-one',selected:false}];
  }
  const state={ready:true,mode:'team',user:{nexus_id:'alice'},workspacePath:'/workspace',sessionId:null,busy:false};
  const builder={id:null,busy:false};
  const window={localStorage:shared,crypto:{randomUUID:()=>`ui-${++sequence}`},addEventListener:(name,fn)=>events[name]=fn};
  const document={hidden:false,addEventListener:(name,fn)=>docEvents[name]=fn};
  const context=vm.createContext({window,document,state,builder,$:element,URLSearchParams,location:{pathname:'/',search:''},
    setTimeout:fn=>{const n=++sequence;timers.set(n,fn);return n;},clearTimeout:id=>timers.delete(id),
    openSession:async id=>{calls.push(['session',id]);if(options.sessionError)throw Error('Private or missing session');state.sessionId=id;},
    newChat:()=>{calls.push(['new-chat']);state.sessionId=null;},
    loadBuild:async id=>{calls.push(['build',id]);builder.id=id;},resetBuilder:()=>{calls.push(['new-build']);builder.id=null;},
    refreshModelChoices:async()=>{},loadKnowledgeProjects:async()=>{},refreshContextChoices:async()=>{},renderChatArtifacts(){},linkModelInputs(){},
    notify:message=>notes.push(message),
  });
  vm.runInContext(source,context);
  return {context,shared,state,builder,e:element,events,docEvents,calls,notes,controller:window.NexusDrafts,
    change(name,text){element(`${name}-task`).value=text;element(`${name}-task`).listeners.input();},
    drain(){const pending=[...timers.values()];timers.clear();for(const fn of pending)fn();},
    async restore(name){await element(`${name}-draft-restore`).listeners.click();},
    discard(name){element(`${name}-draft-discard`).listeners.click();},
    saved(name){return JSON.parse([...shared.data.entries()].find(([key])=>key.endsWith(':'+name))?.[1]||'null');},
  };
}

test('reload offers explicit recovery; restoring text, settings, context and available attachments never submits anything',async()=>{
  const first=ui();first.state.sessionId='original-session';first.e('chat-artifacts').options[0].selected=true;
  first.e('chat-effort').value='high';first.change('chat','<script>draft</script>');first.drain();
  const second=ui(first.shared);
  assert.equal(second.e('chat-task').value,'');assert.equal(second.e('chat-draft').hidden,false);assert.equal(second.calls.length,0);
  await second.restore('chat');
  assert.equal(second.e('chat-task').value,'<script>draft</script>');assert.equal(second.state.sessionId,'original-session');
  assert.equal(second.e('chat-effort').value,'high');assert.equal(second.e('chat-artifacts').selectedOptions.length,1);
  assert.deepEqual(second.calls,[['session','original-session']]);
});

test('draft cannot overwrite newly typed text; discard preserves new text and active-draft discard clears it',async()=>{
  const first=ui();first.change('chat','Old saved');first.drain();
  const second=ui(first.shared);second.change('chat','New current');second.drain();
  assert.equal(second.saved('chat').text,'Old saved');await second.restore('chat');assert.equal(second.e('chat-task').value,'New current');
  second.discard('chat');assert.equal(second.saved('chat').text,'New current');
  second.discard('chat');assert.equal(second.saved('chat'),null);assert.equal(second.e('chat-task').value,'');
});

test('pagehide flush captures the latest keystroke before debounce and avoids rewriting unchanged data',()=>{
  const f=ui();f.change('chat','Last keystroke');f.events.pagehide();const id=f.saved('chat').id;
  f.events.pagehide();assert.equal(f.saved('chat').id,id);
  const second=ui(f.shared);assert.equal(second.e('chat-draft').hidden,false);
});

test('attachment-only selection is saved by reference and stale attachment IDs are skipped on restore',async()=>{
  const f=ui();f.e('builder-artifacts').options[0].selected=true;f.e('builder-artifacts').listeners.change();f.drain();
  assert.equal(f.saved('builder').artifacts[0],'file-one');
  const second=ui(f.shared);second.e('builder-artifacts').options=[];await second.restore('builder');
  assert.equal(second.e('builder-artifacts').selectedOptions.length,0);assert.match(second.notes[0],/no longer available/);
});

test('unconfirmed submissions retain a warning and require explicit restoration; confirmed queueing clears only its own draft',async()=>{
  const f=ui();f.e('chat-artifacts').options[0].selected=true;f.change('chat','May have reached server');const ticket=f.controller.begin('chat');
  f.e('chat-task').value='';f.state.busy=true;f.controller.failed(ticket);
  assert.equal(f.e('chat-draft-restore').disabled,true);f.state.busy=false;f.controller.settled('chat');assert.equal(f.e('chat-draft-restore').disabled,false);
  assert.match(f.e('chat-draft-message').textContent,/Check Activity/);
  const reload=ui(f.shared);reload.e('chat-artifacts').options=[];assert.equal(reload.e('chat-task').value,'');await reload.restore('chat');assert.match(reload.notes[0],/Check Activity/);assert.match(reload.e('chat-draft-message').textContent,/Check Activity/);
  const confirmed=reload.controller.begin('chat');reload.controller.confirmed(confirmed);reload.e('chat-task').value='';
  reload.controller.failed(confirmed);reload.events.pagehide();assert.equal(reload.saved('chat'),null);
});

test('confirmed Quick Build text is not recreated as an unsent draft while polling or after a run failure',()=>{
  const f=ui();f.change('builder','Generate this');const ticket=f.controller.begin('builder');f.controller.confirmed(ticket);f.builder.id='newly-queued-build';
  f.events.pagehide();f.controller.failed(ticket);f.events.pagehide();assert.equal(f.saved('builder'),null);
  f.change('builder','Explicit new refinement');f.drain();assert.equal(f.saved('builder').text,'Explicit new refinement');
});

test('an acknowledgement cannot delete a newer draft from another tab',()=>{
  const f=ui();f.change('chat','Sending');const ticket=f.controller.begin('chat');
  const key=[...f.shared.data.keys()][0],newer={...f.saved('chat'),id:'other-tab',text:'Keep me'};f.shared.setItem(key,JSON.stringify(newer));
  f.controller.confirmed(ticket);assert.equal(f.saved('chat').text,'Keep me');
});

test('lost authorization hides recovery and clears visible draft input; another account sees none',()=>{
  const f=ui();f.change('chat','Private alice draft');f.drain();f.state.ready=false;f.controller.lock();
  assert.equal(f.e('chat-task').value,'');assert.equal(f.e('chat-draft').hidden,true);
  f.state.ready=true;f.state.user={nexus_id:'bob'};f.events['studio-ready']();
  assert.equal(f.e('chat-draft').hidden,true);assert.equal(f.e('chat-task').value,'');
});

test('external removal does not resurrect a stale draft on visibility flush; current text remains usable',()=>{
  const f=ui();f.change('chat','Current');f.drain();const key=[...f.shared.data.keys()][0];
  f.shared.removeItem(key);f.events.storage({key});f.events.pagehide();
  assert.equal(f.saved('chat'),null);assert.equal(f.e('chat-task').value,'Current');
  f.change('chat','New edit');f.drain();assert.equal(f.saved('chat').text,'New edit');
});

test('missing original conversation leaves the saved draft intact and reports failure without automatic work',async()=>{
  const f=ui();f.state.sessionId='gone';f.change('chat','Keep original');f.drain();
  const reload=ui(f.shared,{sessionError:true});await reload.restore('chat');
  assert.equal(reload.e('chat-task').value,'');assert.equal(reload.saved('chat').text,'Keep original');
  assert.match(reload.e('chat-draft-message').textContent,/saved draft has been kept/);
});

test('blocked browser storage reports unavailable recovery without disabling either composer',()=>{
  const f=ui({getItem(){throw Error('Denied');}});f.change('chat','Still type');f.drain();
  assert.match(f.e('chat-draft-message').textContent,/storage is blocked/);assert.equal(f.e('chat-task').value,'Still type');
  assert.equal(f.e('chat-task').disabled,false);assert.doesNotThrow(()=>f.controller.begin('chat'));
});


test('unavailable provider or agent cannot silently fall back when a draft is restored',async()=>{
  const first=ui();first.e('chat-provider').value='claude-code';first.change('chat','Use selected provider');first.drain();
  const second=ui(first.shared);second.e('chat-provider').options=[{value:'codex'}];await second.restore('chat');
  assert.equal(second.e('chat-task').value,'');assert.equal(second.saved('chat').text,'Use selected provider');
  assert.match(second.e('chat-draft-message').textContent,/saved provider is unavailable/);
});

test('Quick Build handler confirms at queue acknowledgement, then settles controls after monitoring failure without restoring submitted work',async()=>{
  const text=fs.readFileSync('nexus/studio_static/quick-build.js','utf8'),elements=new Map(),events=[];
  const get=id=>{if(!elements.has(id))elements.set(id,{value:'',selectedOptions:[],textContent:'',hidden:false});return elements.get(id);};
  get('builder-task').value='Build a team';get('builder-provider').value='codex';get('builder-tools').value='web';
  const ticket={id:'ticket'};
  const context=vm.createContext({$:get,builder:{busy:false,id:null,draft:null},state:{},
    resolvePromptValues:async()=>({}),proposalFromFields(){},builderBusy(){},buildError(){},
    NexusDrafts:{begin:name=>{events.push(['begin',name]);return ticket;},confirmed:t=>events.push(['confirmed',t]),failed:t=>events.push(['failed',t]),settled:name=>events.push(['settled',name])},
    api:async()=>{events.push(['request']);return {id:'run',build_id:'queued-build'};},
    pollRun:async()=>{throw Error('Monitor unavailable');},refreshRuns:async()=>{},
  });
  vm.runInContext(text.slice(text.indexOf('async function generateBuild'),text.indexOf('async function saveBuild')),context);
  await context.generateBuild({preventDefault(){}});
  assert.deepEqual(events,[['begin','builder'],['request'],['confirmed',ticket],['failed',ticket],['settled','builder']]);
});


test('editing a model saves its latest value before the field loses focus',()=>{
  const f=ui();f.change('chat','Keep model choice');f.drain();
  f.e('chat-model').value='chosen-latest-model';f.e('chat-model').listeners.input();f.drain();
  assert.equal(f.saved('chat').settings.model,'chosen-latest-model');
});


test('quota failure before submission cannot delete an earlier saved draft after an uncertain result',()=>{
  const f=ui();f.change('chat','Keep last saved');f.drain();
  f.shared.setItem=()=>{throw Error('Quota');};
  const ticket=f.controller.begin('chat');f.e('chat-task').value='';f.controller.failed(ticket);f.events.pagehide();
  assert.equal(f.saved('chat').text,'Keep last saved');
});

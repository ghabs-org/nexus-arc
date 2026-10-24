const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const buildSource=fs.readFileSync('nexus/studio_static/quick-build.js','utf8');
const featureSource=fs.readFileSync('nexus/studio_static/features.js','utf8');
const workspaceSource=fs.readFileSync('nexus/studio_static/workspace.js','utf8');

function element(value=''){
  const attributes=new Map(),listeners={};
  return {value,hidden:false,selectedOptions:[],children:[],listeners,textContent:'',
    setAttribute:(k,v)=>attributes.set(k,v),getAttribute:k=>attributes.get(k),
    addEventListener:(name,fn)=>listeners[name]=fn,
    append(...nodes){this.children.push(...nodes);},prepend(...nodes){this.children.unshift(...nodes);},appendChild(node){this.children.push(node);},
    contains:()=>false,focus(){this.focused=true;},scrollIntoView(){}};
}

test('Quick Build forwards effort for both new drafts and refinements; Default omits override',async()=>{
  for(const effort of ['high','']){
    const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
    get('builder-task').value='Design a team';get('builder-provider').value='claude-code';
    get('builder-model').value='selected-model';get('builder-effort').value=effort;get('builder-tools').value='web';
    const requests=[],builder={busy:false,id:null,draft:null};
    const context=vm.createContext({$:get,builder,state:{},resolvePromptValues:async()=>({}),
      builderBusy(){},buildError:error=>{throw error;},proposalFromFields:()=>({name:'Edited'}),
      api:async(path,payload)=>{if(payload){requests.push(payload);return {id:'run',build_id:'draft'};}return {id:'draft',revision:1};},
      pollRun:async()=>({status:'completed'}),renderBuild(){},refreshBuilds:async()=>{},refreshRuns:async()=>{},
    });
    vm.runInContext(buildSource.slice(buildSource.indexOf('async function generateBuild'),buildSource.indexOf('async function saveBuild')),context);
    await context.generateBuild({preventDefault(){}});
    assert.equal(requests[0].effort,effort||undefined);
    assert.equal(requests[0].provider,'claude-code');
    assert.equal(requests[0].model,'selected-model');
    get('builder-task').value='Refine this';
    await context.generateBuild({preventDefault(){}});
    assert.equal(requests[1].build_id,'draft');assert.equal(requests[1].revision,1);
    assert.equal(requests[1].effort,effort||undefined);
  }
});

test('model profiles apply effort with provider/model; manual effort clears profile selection',async()=>{
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
  const parents=new Map(['.chat-config-fields','.builder-provider-fields'].map(selector=>[selector,element()]));
  for(const parent of parents.values())parent.querySelector=()=>parent.children[0]?.children[0];
  const models=[{id:'deep',name:'Deep',data:{provider:'codex',model:'chosen',effort:'high'}},
                {id:'default',name:'Default',data:{provider:'claude-code',model:'opus'}}];
  const context=vm.createContext({$:get,document:{querySelector:selector=>parents.get(selector),createElement:()=>element()},
    api:async path=>({items:path.endsWith('/models')?models:[]}),escapeHtml:String,
    renderChatArtifacts(){},linkModelInputs(){},notify:error=>{throw new Error(error);},
  });
  vm.runInContext(workspaceSource.slice(workspaceSource.indexOf('async function refreshModelChoices'),workspaceSource.indexOf('$("refresh-workspace")')),context);
  await context.refreshModelChoices();
  for(const [selector,prefix] of [['.chat-config-fields','chat'],['.builder-provider-fields','builder']]){
    const select=parents.get(selector).children[0].children[0];select.value='deep';select.onchange();
    assert.equal(get(prefix+'-effort').value,'high');assert.equal(get(prefix+'-model').value,'chosen');
    get(prefix+'-effort').oninput();assert.equal(select.value,'');
    select.value='default';select.onchange();assert.equal(get(prefix+'-effort').value,'');
  }
});

test('mobile chat settings collapse without losing values and resize restores desktop controls',()=>{
  const elements=new Map(),get=id=>elements.get(id)||null;
  for(const id of ['chat-project','chat-agent','chat-provider','chat-model','chat-effort','chat-task','builder-task','chat-configuration','chat-form','share-conversation','record-voice'])elements.set(id,element());
  const controls=element();elements.get('chat-project').closest=()=>controls;
  const create=()=>{const node=element();Object.defineProperty(node,'id',{set(id){elements.set(id,node);}});return node;};
  const mobile={matches:true,addEventListener(name,fn){this.change=fn;}};
  const context=vm.createContext({$:get,document:{createElement:create,body:{classList:{contains:()=>false}}},
    matchMedia:()=>mobile,refreshPromptSuggestions:async()=>{},attachPromptSuggestions(){},notify(){},
  });
  vm.runInContext(featureSource.slice(featureSource.indexOf('function setChatOptionsExpanded'),featureSource.indexOf('if(state.ready)initializeFeatureControls')),context);
  context.initializeFeatureControls();
  const panel=get('chat-configuration'),button=get('mobile-chat-options');
  assert.equal(panel.hidden,true);assert.equal(button.getAttribute('aria-expanded'),'false');
  assert.equal(button.getAttribute('aria-controls'),'chat-configuration');
  get('chat-effort').value='high';button.onclick();
  assert.equal(panel.hidden,false);assert.equal(button.getAttribute('aria-expanded'),'true');
  controls.listeners.keydown({key:'Escape',preventDefault(){}});
  assert.equal(panel.hidden,true);assert.equal(button.focused,true);
  button.onclick();
  get('chat-form').listeners.submit();assert.equal(panel.hidden,true);assert.equal(get('chat-effort').value,'high');
  mobile.matches=false;mobile.change();assert.equal(panel.hidden,false);
  mobile.matches=true;mobile.change();assert.equal(panel.hidden,true);
});

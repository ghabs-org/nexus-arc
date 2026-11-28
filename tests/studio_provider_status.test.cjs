const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/studio.js','utf8');
function workspace(api=async()=>({items:[]})){
  const dom={'providers-content':{innerHTML:''},'refresh-providers':{disabled:false,textContent:'Refresh status'}};
  const listeners={};
  const context=vm.createContext({
    state:{mode:'desktop',ready:true,workspacePath:'/fixture',providers:[]},
    window:{__NEXUS_DESKTOP_PROVIDER_LOGIN__:true,addEventListener:(name,handler)=>{listeners[name]=handler;}},
    document:{hidden:false,addEventListener:(name,handler)=>{listeners[name]=handler;}},
    $:id=>dom[id],api,notify(){},renderModelLists(){},view:()=> 'providers',
    escapeHtml:value=>String(value??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    badge:(value,kind)=>`<span class="badge ${kind}">${value}</span>`
  });
  vm.runInContext('let providerStatusRefresh=null,providerStatusCheckedAt=0;\n'+source.slice(source.indexOf('function desktopProviderSignin('),source.indexOf('function renderTools()')),context);
  const eventStart=source.indexOf('function refreshVisibleProviderStatuses()');
  vm.runInContext(source.slice(eventStart,source.indexOf('$("retry-login")',eventStart)),context);
  return {context,dom,listeners};
}
const cli=(authentication,status='Signed in')=>({id:'claude-code',name:'Claude Code',kind:'cli',configured:true,status,authentication});

test('verified login is shown as signed in with a reconnect action',()=>{
  const {context,dom}=workspace();
  context.state.providers=[cli({state:'signed-in',method:'subscription'})];
  context.renderProviders();
  const html=dom['providers-content'].innerHTML;
  assert.match(html,/badge ready">Signed in/);
  assert.match(html,/Subscription login/);
  assert.match(html,/>Reconnect<\/a>/);
  assert.doesNotMatch(html,/>Sign in<\/a>/);
});

test('installed but signed-out or unverifiable providers are not shown as ready',()=>{
  const {context,dom}=workspace();
  for(const [state,status] of [['signed-out','Sign-in needed'],['unknown','Couldn’t verify sign-in']]){
    context.state.providers=[cli({state,message:status},status)];context.renderProviders();
    assert.doesNotMatch(dom['providers-content'].innerHTML,/badge ready/);
    assert.match(dom['providers-content'].innerHTML,/>Sign in<\/a>/);
  }
  context.state.providers=[cli({state:'signed-in',method:'api'},'API credentials configured')];context.renderProviders();
  assert.match(dom['providers-content'].innerHTML,/API credentials · API billing/);
  assert.doesNotMatch(dom['providers-content'].innerHTML,/Subscription login/);
});

test('connected Antigravity offers reconnect without claiming subscription or API billing',()=>{
  const {context,dom}=workspace();
  context.state.providers=[{...cli({state:'signed-in',method:'other'}),id:'agy',name:'Google Antigravity'}];
  context.renderProviders();
  const html=dom['providers-content'].innerHTML;
  assert.match(html,/badge ready">Signed in/);
  assert.match(html,/provider-login\/agy/);
  assert.match(html,/>Reconnect<\/a>/);
  assert.match(html,/Provider connection · billing managed by CLI/);
  assert.doesNotMatch(html,/Subscription login|API credentials · API billing/);
});

test('returning to visible Providers checks auth once, with shared and hidden views excluded',async()=>{
  let calls=0,finish;
  const {context,dom,listeners}=workspace((route,body)=>{
    assert.equal(route,'/api/providers/status');assert.deepEqual(Object.keys(body),[]);
    calls++;return new Promise(resolve=>{finish=resolve;});
  });
  listeners.focus();listeners.visibilitychange();listeners.hashchange();
  assert.equal(calls,1);assert.equal(dom['refresh-providers'].disabled,true);
  const pending=context.refreshProviderStatuses(true);
  finish({items:[cli({state:'signed-in',method:'subscription'})]});await pending;
  assert.equal(dom['refresh-providers'].disabled,false);
  assert.match(dom['providers-content'].innerHTML,/Signed in/);
  context.state.mode='team';await context.refreshProviderStatuses(true);assert.equal(calls,1);
  context.state.mode='desktop';context.document.hidden=true;listeners.focus();assert.equal(calls,1);
});

test('checker failure clears a previous signed-in badge and re-enables refresh',async()=>{
  const {context,dom}=workspace(async()=>{throw new Error('offline');});
  context.state.providers=[cli({state:'signed-in',method:'subscription'}),{...cli({state:'signed-in',method:'other'}),id:'agy',name:'Google Antigravity'}];
  await context.refreshProviderStatuses(true);
  assert.match(dom['providers-content'].innerHTML,/Couldn’t verify sign-in/);
  assert.doesNotMatch(dom['providers-content'].innerHTML,/badge ready/);
  assert.equal(dom['refresh-providers'].disabled,false);
  assert.equal(context.state.providers.find(provider=>provider.id==='agy').authentication.state,'unknown');
});

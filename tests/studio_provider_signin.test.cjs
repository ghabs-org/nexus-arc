const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function renderer(){
  const context=vm.createContext({
    state:{mode:'local',providers:[{id:'claude-code',name:'Claude Code',kind:'cli'}]},
    escapeHtml:value=>String(value).replaceAll('<','&lt;'),
    desktopProviderSignin:()=>'<a href="/__nexus_desktop__/provider-login/claude">Sign in</a>',
    document:{createElement:()=>({dataset:{},setAttribute(){}})},
    renderContent:(body,text)=>{body.textContent=text;},
    showCitations(){}
  });
  vm.runInContext(fs.readFileSync('nexus/studio_static/conversation-content.js','utf8'),context);
  context.renderRetrievalNotice=()=>{};
  context.renderStructuredContent=()=>{};
  return context;
}

test('failed CLI authentication has a sign-in action instead of suggesting chat /login',()=>{
  const context=renderer();
  const html=context.providerSigninHelp({status:'failed',provider:'claude-code',error:'Not logged in · Please run /login'});
  assert.match(html,/provider-login\/claude/);
  assert.match(html,/Do not send \/login as a Studio chat message/);
  assert.equal(context.providerSigninHelp({status:'completed',provider:'claude-code',error:'Not logged in'}),'');
  assert.equal(context.providerSigninHelp({status:'failed',provider:'claude-code',error:'Timeout'}),'');
  assert.equal(context.providerSigninHelp({status:'failed',provider:'openai',error:'Not logged in'}),'');
  context.state.mode='team';
  assert.equal(context.providerSigninHelp({status:'failed',provider:'claude-code',error:'Not logged in'}),'');
});

test('browser-only users receive provider setup without native launch links',()=>{
  const context=renderer();
  context.desktopProviderSignin=()=>'';
  const html=context.providerSigninHelp({status:'failed',provider:'claude-code',error:'Not logged in'});
  assert.match(html,/href="#providers"/);
  assert.doesNotMatch(html,/__nexus_desktop__/);
});

test('chat does not repeat identical output and error text',()=>{
  const context=renderer();
  const body={dataset:{}},status={};
  const children=[];
  const node={dataset:{},querySelector:selector=>selector==='.message-body'?body:selector==='.stream-status'?status:null,append:child=>children.push(child)};
  context.renderRunMessage(node,{status:'failed',provider:'claude-code',output:'Not logged in',error:'Not logged in'});
  assert.equal(body.textContent,'Not logged in');
  assert.equal(status.hidden,true);
  assert.equal(children.length,1);
  assert.match(children[0].innerHTML,/>Sign in<\/a>/);
  context.renderRunMessage(node,{status:'failed',output:'Partial reply',error:'Separate failure'});
  assert.equal(status.hidden,false);
  assert.equal(status.textContent,'Separate failure');
});

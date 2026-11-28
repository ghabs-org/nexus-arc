const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function fixture(fetch){
  function element(tag='div'){
    return {tag,children:[],textContent:'',append(...nodes){this.children.push(...nodes);},replaceChildren(){this.children=[];},scrollIntoView(){this.scrolled=true;}};
  }
  const nodes=Object.fromEntries(['release-notes-notice','release-notes-content','release-notes-versions'].map(id=>[id,element()]));
  const ctx=vm.createContext({fetch,location:{hash:'#1.0.5'},document:{createElement:element,getElementById:id=>nodes[id]||nodes['release-notes-content'].children.find(child=>child.id===id)}});
  vm.runInContext(fs.readFileSync('nexus/studio_static/release-notes.js','utf8').replace(/loadReleaseNotes\(\);\s*$/,''),ctx);
  return {ctx,nodes};
}

test('release page preserves explicit evidence limits and renders content as text',()=>{
  const {ctx,nodes}=fixture();
  const data={notice:'Source history, not installed-version proof',entries:[{version:'1.0.5',title:'<img src=x onerror=alert(1)>',date:null,status:'local-install',changes:['Status badges'],limitations:['No DMG'],evidence:['docs/check.json']}]};
  ctx.renderReleaseNotes(data);
  const section=nodes['release-notes-content'].children[0];
  assert.equal(section.children[0].textContent,'1.0.5 — <img src=x onerror=alert(1)>');
  assert.equal(section.children[1].textContent,'Date not established · local-install');
  assert.equal(section.scrolled,true);
  assert.match(nodes['release-notes-notice'].textContent,/not installed-version proof/);
  assert.equal(nodes['release-notes-versions'].children[0].children[0].children[0].href,'#1.0.5');
});

test('failed catalog load has visible recovery text',async()=>{
  const {ctx,nodes}=fixture(async()=>({ok:false}));
  await ctx.loadReleaseNotes();
  assert.match(nodes['release-notes-notice'].textContent,/could not be loaded.*Retry/);
});

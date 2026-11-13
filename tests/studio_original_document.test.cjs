const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/content.js','utf8');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.events={};}
  append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
  querySelector(selector){return this.children.find(item=>selector==='.source-list'&&item.className==='source-list')||null;}
  remove(){this.parent.children=this.parent.children.filter(item=>item!==this);}
  replaceWith(item){const index=this.parent.children.indexOf(this);item.parent=this.parent;this.parent.children[index]=item;}
  addEventListener(name,handler){this.events[name]=handler;}
  setAttribute(name,value){this[name]=value;}
  replaceChildren(...items){this.children=[];this.append(...items);}
}
function fixture(fail=false,outputs=null){
  const calls=[],notices=[];
  const scope=vm.createContext({document:{createElement:tag=>new Element(tag)},api:async(path,body)=>{calls.push({path,body});if(fail)throw new Error('Submission may have reached the server');return {id:'download'};},pollRun:async id=>({output:JSON.stringify(outputs?outputs.shift():{artifacts:[{id:'private-file',revision:1}]})}),notify:message=>notices.push(message)});
  vm.runInContext(source.slice(source.indexOf('function showCitations('),source.indexOf('function previewDocument(')),scope);
  const node=new Element('div');
  const citation={kind:'document',artifact_id:'paragraph',artifact_revision:1,page:1,filename:'Cited paragraph',source_run_id:'owned-run',source_index:0,original_document:{connector_id:'selected',url:'https://untrusted.invalid/?token=never-forward'}};
  return {scope,calls,notices,node,citation,render:()=>scope.showCitations(node,[citation])};
}

test('original action sends only a saved source selector and opens a private revision',async()=>{
  const f=fixture();f.render();const details=f.node.children[0],button=details.children.find(item=>item.tag==='button');
  await button.events.click();
  assert.equal(f.calls.length,1);
  assert.equal(JSON.stringify(f.calls[0]),JSON.stringify({path:'/api/studio/original-document',body:{source_run_id:'owned-run',source_index:0}}));
  const saved=details.children.find(item=>item.textContent==='Open saved original');
  assert.equal(saved.href,'/artifact.html?id=private-file&revision=1&page=1');
  assert.equal(saved.target,'_blank');
});

test('a lost original-file submission is never automatically replayed',async()=>{
  const f=fixture(true);f.render();const button=f.node.children[0].children.find(item=>item.tag==='button');
  await button.events.click();
  assert.equal(f.calls.length,1);assert.equal(button.disabled,false);
  assert.equal(button.textContent,'Save current original');assert.equal(f.notices.length,1);
});

test('older citations retain their pinned source without advertising unsupported originals',()=>{
  const f=fixture();delete f.citation.original_document;f.render();
  assert.equal(f.node.children[0].children.some(item=>item.tag==='button'),false);
  assert.equal(f.calls.length,0);
});

test('conversation controls list opaque choices, escape filenames and save only the selected file',async()=>{
  const id='a'.repeat(64),name='<img src=x onerror=bad>';
  const f=fixture(false,[{attachments:[{attachment_id:id,filename:name,message_id:'message'}],page:1,pages:1,attachment_count:1,unsupported_references:0},{artifacts:[{id:'selected-private',revision:2}]}]);
  f.citation.field_id='c/discussion';f.render();
  const details=f.node.children[0],button=details.children.find(item=>item.tag==='button');
  assert.equal(button.textContent,'Browse attachments');assert.equal(f.calls.length,0);
  await button.events.click();
  assert.equal(JSON.stringify(f.calls[0].body),JSON.stringify({source_run_id:'owned-run',source_index:0,operation:'list',page:1,offset:0}));
  const panel=details.children.find(item=>item.className==='source-attachments'),row=panel.children.find(item=>item.className==='source-attachment-row');
  assert.equal(row.children[0].textContent,`${name} · message message`);assert.equal(row.children[0].innerHTML,undefined);
  await row.children[1].events.click();
  assert.equal(JSON.stringify(f.calls[1].body),JSON.stringify({source_run_id:'owned-run',source_index:0,attachment_id:id}));
  assert.equal(row.children[1].href,'/artifact.html?id=selected-private&revision=2&page=1');
});

test('attachment browsing exposes page navigation and unsupported references without binary downloads',async()=>{
  const f=fixture(false,[{attachments:[],page:1,pages:2,attachment_count:0,unsupported_references:1,next_page:2},{attachments:[],page:2,pages:2,attachment_count:0,unsupported_references:0}]);
  f.citation.field_id='c/discussion';f.render();
  const details=f.node.children[0],button=details.children.find(item=>item.tag==='button');await button.events.click();
  const panel=details.children.find(item=>item.className==='source-attachments');
  assert.ok(panel.children.some(item=>item.textContent==='1 non-file or split references cannot be downloaded here.'));
  await panel.children.at(-1).children.find(item=>item.textContent==='Next page').events.click();
  assert.equal(f.calls.length,2);assert.equal(f.calls[1].body.page,2);
  assert.ok(f.calls.every(item=>item.body.operation==='list'&&item.body.attachment_id===undefined));
});

test('failed conversation listing never resubmits a possibly accepted job',async()=>{
  const f=fixture(true);f.citation.field_id='c/discussion';f.render();
  const button=f.node.children[0].children.find(item=>item.tag==='button');await button.events.click();
  assert.equal(f.calls.length,1);assert.equal(button.disabled,false);assert.equal(f.notices.length,1);
});

// Verify resolved embed parts use the existing safe conversation renderer.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';}
  append(...items){this.children.push(...items);}
  setAttribute(key,value){this.attributes[key]=value;}
  querySelector(){return null;}
}
function renderer(){
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},encodeURIComponent});
  vm.runInContext(fs.readFileSync('nexus/studio_static/conversation-content.js','utf8'),context);
  return context;
}

test('resolved artifact embeds link to their pinned revision using text-only labels',()=>{
  const context=renderer(),root=new Element('article');
  context.renderStructuredContent(root,[{type:'artifact',artifact_id:'report & private',revision:3,filename:'<img src=x onerror=steal()>'}]);
  const link=root.children[0].children[0].children[0];
  assert.equal(link.href,'/artifact.html?id=report%20%26%20private&revision=3&page=1');
  assert.equal(link.textContent,'<img src=x onerror=steal()>');
  assert.equal(link.rel,'noopener noreferrer');
  assert.equal(link.innerHTML,undefined);
});

test('invalid embed attachment revisions cannot create unpinned links',()=>{
  const context=renderer(),root=new Element('article');
  context.renderStructuredContent(root,[{type:'artifact',artifact_id:'bad',revision:0},{type:'artifact',artifact_id:'bad',revision:'1'}]);
  assert.equal(root.children[0].children.length,0);
});

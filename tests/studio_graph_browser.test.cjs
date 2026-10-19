// Authoring state contracts without a browser dependency; real layout checked separately.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.value='';this.dataset={};this.disabled=false;this.innerHTML='';}
 setAttribute(){} replaceChildren(){} before(){} append(){} addEventListener(){}
}
function fixture(){
 const elements=new Map(),advanced=[new Element()];
 const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const context=vm.createContext({structuredClone,crypto:require('node:crypto').webcrypto,document:{createElement:()=>new Element(),querySelectorAll:()=>advanced,addEventListener(){}},$:element,state:{workflows:[],agents:[],tools:[]},escapeHtml:value=>String(value).replace(/[<&"]/g,c=>({'<':'&lt;','&':'&amp;','"':'&quot;'}[c])),api:async()=>({effective:{map_items:100,map_concurrency:4,loop_iterations:100,nesting_depth:10}})});
 vm.runInContext(fs.readFileSync('nexus/studio_static/graph.js','utf8'),context);
 vm.runInContext('graphEditor.stack=[];graphEditor.fieldText={};graphEditor.graphText={};graphEditor.policy={map_items:100,map_concurrency:4,loop_iterations:100,nesting_depth:10};',context);
 return {context,advanced,element,run:code=>vm.runInContext(code,context),plain:code=>JSON.parse(JSON.stringify(vm.runInContext(code,context)))};
}
test('editing a map body preserves parent metadata, edges and handlers',()=>{
 const f=fixture();f.run('loadGraphScope({version:2,input_schema:{type:"array"},on_error:identityGraph(),nodes:[{id:"each",name:"Each",kind:"map",items:{ref:"input"},body:identityGraph()}],edges:[{source:"START",target:"each"}]});enterGraphScope("body","each");graphEditor.fieldText.result={cases:JSON.stringify([{condition:{left:true,op:"eq",right:true},value:42}])};leaveGraphScope();');
 const saved=f.plain('currentGraph()');assert.equal(saved.nodes[0].body.nodes[0].cases[0].value,42);assert.equal(saved.input_schema.type,'array');assert.equal(saved.edges[0].source,'START');assert.equal(saved.on_error.output_mapping.ref,'nodes.result');
});
test('invalid nested JSON keeps the draft, current scope and parent intact',()=>{
 const f=fixture();f.run('loadGraphScope({version:2,nodes:[{id:"each",name:"Each",kind:"map",body:identityGraph()}],edges:[]});enterGraphScope("body","each");graphEditor.fieldText.result={cases:"["};');
 assert.throws(()=>f.run('leaveGraphScope()'),/JSON/);assert.equal(f.run('graphEditor.stack.length'),1);assert.equal(f.run('graphEditor.fieldText.result.cases'),'[');assert.equal(f.run('graphEditor.stack[0].parent.nodes[0].body.nodes[0].cases.length'),1);
});
test('legacy graphs remain legacy and cannot silently acquire advanced nodes',()=>{
 const f=fixture();f.run('loadGraphScope({nodes:[{id:"old",name:"Old",kind:"delay",delay_seconds:0}],edges:[]});');assert.equal(f.advanced[0].disabled,true);assert.throws(()=>f.run('addGraphNode("loop")'),/version-2/);assert.equal(f.run('graphEditor.nodes.length'),1);assert.equal(f.run('graphEditor.meta.version'),undefined);
});
test('canvas clamps untrusted coordinates before rendering markup',()=>{
 const f=fixture();f.run('loadGraphScope({version:2,nodes:[{id:"one",name:"<img src=x>",kind:"switch",position:{x:"0) onload=bad",y:-10}}],edges:[]});');assert.deepEqual(f.plain('graphNodePosition(graphEditor.nodes[0],0)'),{x:40,y:0});const markup=f.element('graph-canvas').innerHTML;assert.ok(!markup.includes('onload=bad'));assert.ok(!markup.includes('<img'));
});

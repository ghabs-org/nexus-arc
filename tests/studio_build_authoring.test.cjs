// Reusable authoring checks without browser or frontend dependencies.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/quick-build.js','utf8');

function fixture(fields=[],options=[],skills=[]){
 const errors=[],requests=[];
 const builder={busy:false,id:'build',draft:{revision:2,status:'draft',proposal:{
   name:'Build',summary:'Summary',agents:[{id:'review',name:'Review',description:'Review evidence',
   instructions:'Old instructions',provider:'codex',model:'',max_model_calls:4,tool_ids:['text:wordcount']}],
   workflow:null,
 }}};
 const root={querySelectorAll(selector){
   if(selector==='[data-build-field]')return fields;
   if(selector==='[data-build-agent-skills]')return skills;
   return options;
 }};
 const context=vm.createContext({builder,structuredClone,Set,JSON,Number,Object,
   $:id=>id==='builder-proposal'?root:{hidden:false},
   buildError:error=>errors.push(error.message),builderBusy:value=>{builder.busy=value;},
   featureApi:async(...args)=>{requests.push(args);return {};},renderBuildReview(){},
 });
 vm.runInContext(source.slice(source.indexOf('function proposalFromFields'),source.indexOf('function builderField')),context);
 vm.runInContext(source.slice(source.indexOf('async function saveBuild'),source.indexOf('async function testBuild')),context);
 return {context,builder,errors,requests};
}
const field=(path,value,json=false)=>({dataset:{buildField:path},value,hasAttribute:()=>json});
const option=value=>({dataset:{buildAgentOptions:'0'},value});
const read=context=>JSON.parse(JSON.stringify(vm.runInContext('proposalFromFields()',context)));

test('basic edits survive advanced resource assignments; removed settings stay removed',()=>{
 const f=fixture([field('agents.0.instructions','New instructions')],[option('{"max_model_calls":2,"skill_ids":["evidence"],"output_schema":false}')]);
 const result=read(f.context);
 assert.equal(result.agents[0].instructions,'New instructions');
 assert.equal(result.agents[0].max_model_calls,2);
 assert.equal(result.agents[0].output_schema,false);
 assert.deepEqual(result.agents[0].skill_ids,['evidence']);
 assert.equal('tool_ids' in result.agents[0],false);
 assert.equal(f.builder.draft.proposal.agents[0].instructions,'Old instructions');
});

test('new named children persist as typed definitions',()=>{
 const children=[{id:'child',name:'Child',graph:{version:2,nodes:[]}}];
 const f=fixture([field('workflows',JSON.stringify(children),true)]);
 assert.deepEqual(read(f.context).workflows,children);
});

test('advanced edits cannot overwrite basic identity or object prototypes',()=>{
 for(const value of ['{"name":"Other"}','{"__proto__":{"polluted":true}}','null','[]']){
   const f=fixture([],[option(value)]);
   assert.throws(()=>read(f.context));
   assert.equal(f.builder.draft.proposal.agents[0].name,'Review');
   assert.equal({}.polluted,undefined);
 }
});

test('malformed configuration shows error and sends no review request',async()=>{
 const f=fixture([field('workflows','{broken',true)]);
 await vm.runInContext('saveBuild()',f.context);
 assert.equal(f.errors.length,1);
 assert.equal(f.requests.length,0);
 assert.equal(f.builder.busy,false);
});

test('agent skill selection assigns single skill resource ID and handles unassigned',()=>{
 const f=fixture([field('agents.0.skill_ids','content-strategy')],[option('{"max_model_calls":2}')]);
 const result=read(f.context);
 assert.deepEqual(result.agents[0].skill_ids,['content-strategy']);

 const fEmpty=fixture([field('agents.0.skill_ids','')],[option('{"max_model_calls":2}')]);
 const resultEmpty=read(fEmpty.context);
 assert.deepEqual(resultEmpty.agents[0].skill_ids,[]);
});

test('quick build collects multiple checked skill_ids into agent.skill_ids',()=>{
 const skillsContainer={dataset:{buildAgentSkills:'0'},querySelectorAll(sel){
   return sel==='input[name="skill_ids"]:checked'?[{value:'brag'},{value:'code-review'},{value:'design-taste-frontend'}]:[];
 }};
 const f=fixture([],[option('{"max_model_calls":2}')],[skillsContainer]);
 const result=read(f.context);
 assert.deepEqual(result.agents[0].skill_ids,['brag','code-review','design-taste-frontend']);
 assert.equal(result.agents[0].max_model_calls,2);
});

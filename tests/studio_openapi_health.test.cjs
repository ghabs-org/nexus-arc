const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/features.js','utf8');
const context=vm.createContext({Set,Object,Array,JSON});
vm.runInContext(source.slice(source.indexOf('function openAPIHealthOptions(')),context);
const options=(spec,selected)=>JSON.parse(JSON.stringify(context.openAPIHealthOptions(spec,selected)));

test('health options require selected safe methods without required inputs',()=>{
 const spec={paths:{'/health':{get:{operationId:'getHealth'},head:{operationId:'headHealth'},post:{operationId:'mutate'}},
 '/hidden':{get:{operationId:'hidden'}},
 '/required':{get:{operationId:'required',parameters:[{name:'id',in:'query',required:true}]}},
 '/path/{id}':{parameters:[{name:'id',in:'path'}],get:{operationId:'path'}},
 '/body':{get:{operationId:'body',requestBody:{required:false}}}}};
 assert.deepEqual(options(spec,['getHealth','headHealth','mutate','required','path','body']),[
  {id:'getHealth',label:'GET /health'},{id:'headHealth',label:'HEAD /health'}]);
});

test('operation fallback IDs and optional parameters stay usable',()=>{
 assert.deepEqual(options({paths:{'/health':{options:{parameters:[{name:'verbose',in:'query',required:false}]}}}},['options:/health']),[{id:'options:/health',label:'OPTIONS /health'}]);
 assert.deepEqual(options({paths:{}},[]),[]);
});

function formResult(health){
 const form=new Map([['connector_type','openapi'],['remote_url','https://example.test'],['remote_auth','none'],['openapi_spec','{}'],['openapi_health_operation',health]]);
 form.getAll=()=>['getHealth'];
 const c=vm.createContext({operationFormData:()=>({}),library:{kind:'connectors'},JSON});
 const start=source.indexOf('const connectorFormData=');
 vm.runInContext(source.slice(start,source.indexOf('\n};',start)+4),c);
 return JSON.parse(JSON.stringify(c.operationFormData(form)));
}

test('connector form preserves explicit health selection',()=>{
 const result=formResult('getHealth');
 assert.equal(result.health_operation,'getHealth');
 assert.deepEqual(result.operations,['getHealth']);
 assert.equal(result.enabled,false);
});

test('old connectors gain no health selection until explicitly configured',()=>{
 assert.equal(Object.hasOwn(formResult(''),'health_operation'),false);
});

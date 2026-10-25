const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/features.js','utf8');
const choose=source.slice(source.indexOf('async function chooseSpeech('),source.indexOf('async function beginRecording('));
const profiles=[{id:'local-whisper',action:'transcribe',local:true,model_ready:false,billing:'Local model',missing_prerequisites:['Whisper Python runtime']},{id:'api-transcribe',action:'transcribe',local:false,model_ready:true,billing:'Separate API account and billing'}];
test('missing local runtime stops before recording; no API fallback',async()=>{
 let html='';const context=vm.createContext({featureApi:async()=>({items:profiles}),escapeHtml:String,fieldsDialog:async(title,body)=>{html=body;return new Map([['profile','local-whisper']]);}});vm.runInContext(choose,context);
 await assert.rejects(context.chooseSpeech('transcribe'),/Local speech setup required: Whisper Python runtime/);
 assert.match(html,/local-whisper.*setup required/);assert.match(html,/Local setup required/);assert.ok(html.indexOf('local-whisper')<html.indexOf('api-transcribe'));
});
test('API profile is used only when explicitly selected',async()=>{
 const context=vm.createContext({featureApi:async()=>({items:profiles}),escapeHtml:String,fieldsDialog:async()=>new Map([['profile','api-transcribe'],['language','it']])});vm.runInContext(choose,context);
 const chosen=await context.chooseSpeech('transcribe');assert.equal(chosen.profile,'api-transcribe');assert.equal(chosen.language,'it');
});
test('no configured profile reports setup rather than requesting a microphone',async()=>{
 const context=vm.createContext({featureApi:async()=>({items:[]}),fieldsDialog:()=>assert.fail('No profile must not open recording dialog')});vm.runInContext(choose,context);
 await assert.rejects(context.chooseSpeech('transcribe'),/No configured speech provider/);
});

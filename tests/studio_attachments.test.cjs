const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("nexus/studio_static/library.js","utf8");
function setup(files, failAt=0){
  const options=[{value:"existing",selected:true,dataset:{size:"3"}}],calls=[];
  const select={options,get selectedOptions(){return options.filter(item=>item.selected);}};
  const fields={files:select,status:{textContent:""},"send-message":{disabled:false},"builder-generate":{disabled:false}};
  const context=vm.createContext({state:{busy:false},builder:{busy:false},$:id=>fields[id],uploadArtifact:async file=>{
    calls.push(file.name);if(calls.length===failAt)throw new Error("Upload failed");return {id:file.name};
  }});
  vm.runInContext(source.slice(source.indexOf("async function attachUploadedFiles"),source.indexOf('$("builder-upload").addEventListener')),context);
  context.input={files,disabled:false,value:"chosen"};
  context.refresh=async()=>{for(const name of calls)if(!options.some(item=>item.value===name))options.push({value:name,selected:false,dataset:{size:"8"}});};
  return {context,calls,select,fields};
}
test("uploads originals without reading their text and selects them only for explicit submission",async()=>{
  const h=setup([{name:"photo.png",size:8},{name:"archive.zip",size:8}]);
  await vm.runInContext('attachUploadedFiles(input,"files","status",refresh)',h.context);
  assert.deepEqual(h.calls,["photo.png","archive.zip"]);
  assert.deepEqual(h.select.selectedOptions.map(item=>item.value),["existing","photo.png","archive.zip"]);
  assert.match(h.fields.status.textContent,/Sent only when you submit/);
  assert.equal(h.fields["send-message"].disabled,false);
  assert.equal(h.context.input.disabled,false);
});
test("over-limit uploads do not upload anything; partial failure retains successful selection",async()=>{
  const oversized=setup([{name:"huge.bin",size:17*1024*1024}]);
  await vm.runInContext('attachUploadedFiles(input,"files","status",refresh)',oversized.context);
  assert.equal(oversized.calls.length,0);assert.match(oversized.fields.status.textContent,/16 MiB/);
  const partial=setup([{name:"first.png",size:8},{name:"second.png",size:8}],2);
  await vm.runInContext('attachUploadedFiles(input,"files","status",refresh)',partial.context);
  assert.deepEqual(partial.select.selectedOptions.map(item=>item.value),["existing","first.png"]);
  assert.equal(partial.fields.status.textContent,"Upload failed");
  assert.equal(partial.fields["builder-generate"].disabled,false);
});

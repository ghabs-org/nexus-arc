const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/quick-build.js','utf8');
const helper=source.slice(source.indexOf('function buildPrerequisiteHtml('),source.indexOf('async function refreshBuildLifecycle('));
const escapeHtml=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

test('build setup shows escaped local prerequisites and separates remote health',()=>{
 const context=vm.createContext({escapeHtml});vm.runInContext(helper,context);
 const html=context.buildPrerequisiteHtml({prerequisite_checks:[{name:'<worker>',status:'<missing>'}]});
 assert.match(html,/Software and worker setup/);assert.match(html,/&lt;worker&gt;: &lt;missing&gt;/);
 assert.doesNotMatch(html,/<worker>|<missing>/);assert.match(html,/without running your workflow actions/);
});

test('older lifecycle responses remain readable without prerequisite fields',()=>{
 const context=vm.createContext({escapeHtml});vm.runInContext(helper,context);
 assert.equal(context.buildPrerequisiteHtml({}), '');
});

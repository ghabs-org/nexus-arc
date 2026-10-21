const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('nexus/studio_static/features.js','utf8');
const helper=source.slice(source.indexOf('function integrationSetupDetails('),source.indexOf('async function showIntegrationCatalog('));
const escapeHtml=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

test('setup disclosure escapes templates, labels and installed package versions',()=>{
  const context=vm.createContext({escapeHtml});vm.runInContext(helper,context);
  const row={setup_metadata:{billing:'API usage <script>',requirements:['Use <token> reference'],installation:['pip install pinned==1'],configuration_template:{token_env:'<img src=x onerror=steal()>'},alias_of:'<alias>'},readiness:{notice:'Live check pending',checks:[{name:'<package>',status:'installed',installed_version:'<version>'}]}};
  const html=context.integrationSetupDetails(row);
  assert.match(html,/Billing:/);assert.match(html,/Requirements and setup/);
  assert.match(html,/never installs or enables anything/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<script>|<img|<package>/);
  assert.match(html,/&lt;version&gt;/);assert.match(html,/Shared implementation/);
});

test('older servers remain readable when setup metadata is absent',()=>{
  const context=vm.createContext({escapeHtml});vm.runInContext(helper,context);
  assert.equal(context.integrationSetupDetails({}), '');
});

test('catalog setup blocks missing dependencies and preserves configured/legacy controls',async()=>{
  const configure=source.slice(source.indexOf('async function configureIntegration('),source.indexOf('window.addEventListener("studio-ready",()=>{',source.indexOf('async function configureIntegration(')));
  for(const [readiness,blocked] of [[{state:'dependencies-missing'},true],[{state:'configuration-present'},false],[undefined,false]]){
    let rendered='';
    const row={id:'finance',name:'Finance',mode:'tool',tool_id:'finance-tool',configured:true,readiness};
    const context=vm.createContext({escapeHtml,featureApi:async()=>({items:[row]}),showDetail:(title,html)=>{rendered=html;}});
    vm.runInContext(helper+configure,context);
    await context.configureIntegration('finance');
    assert.equal(/<button data-tool="finance-tool" disabled>/.test(rendered),blocked);
    assert.match(rendered,/>Run tool<\/button>/);
  }
});

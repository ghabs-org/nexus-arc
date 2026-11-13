pm.test('Native Nuclia configuration retains typed templates and credential references', function () {
  pm.response.to.have.status(200);
  const record = pm.response.json().items.find(item => item.name === 'Templated knowledge');
  pm.expect(record.data.enabled).to.eql(false);
  pm.expect(record.data.token_env).to.eql('NUCLIA_TOKEN');
  pm.expect(record.data.template.version).to.eql(1);
  pm.expect(record.data.template.parameters).to.eql([{name:'region',type:'string',required:true}]);
  pm.expect(record.data.template.query_prompt).to.include('{{query}}');
  pm.expect(record.data.actions).to.eql(['query','ask']);
});

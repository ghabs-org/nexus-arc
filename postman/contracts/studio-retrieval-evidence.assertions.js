pm.test('Private evaluation cases retain copied context and pinned provenance', function () {
  pm.response.to.have.status(200);
  const suite = pm.response.json().items.find(item => item.name === 'Mobile grounded policy');
  pm.expect(suite.data.cases[0].contexts).to.eql(['Thirty days.']);
  pm.expect(suite.data.cases[0].context_source.revision).to.eql(1);
  pm.expect(suite.data.cases[0].context_source.artifact_id).to.be.a('string');
  pm.expect(suite.data.cases[0].contains).to.eql(['Thirty days']);
  pm.expect(suite.data.cases[0].judge).to.eql(undefined);
});

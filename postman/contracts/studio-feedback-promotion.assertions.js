pm.test('Promoted evaluation retains selected source context without private feedback', () => {
  pm.response.to.have.status(200);
  const suite = pm.response.json().items.find(item => item.name === 'Returns feedback regression');
  pm.expect(suite.owner_id).to.eql('alice');
  pm.expect(suite.data.target_id).to.eql('feedback-reviewer');
  const example = suite.data.cases[0];
  pm.expect(example.input).to.eql('Return policy?');
  pm.expect(example.contains).to.eql(['thirty days']);
  pm.expect(example.contexts).to.eql(['Thirty days.']);
  pm.expect(example.context_source.revision).to.eql(1);
  pm.expect(JSON.stringify(suite)).not.to.include('Initial note');
});

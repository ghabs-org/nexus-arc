pm.test('Original citation download queues an existing private tool job', function () {
  pm.response.to.have.status(202);
  const run = pm.response.json();
  pm.expect(run.id).to.be.a('string');
  pm.expect(run.kind).to.eql('tool');
  pm.expect(['queued', 'running', 'completed']).to.include(run.status);
  pm.expect(JSON.stringify(run)).not.to.include('synthetic-nuclia-key');
  pm.expect(JSON.stringify(run)).not.to.include('eph-token');
});

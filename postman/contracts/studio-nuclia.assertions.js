pm.test('Nuclia generation remains opt-in with explicit billing and citation setup', function () {
  pm.response.to.have.status(200);
  const row = pm.response.json().official_items.find(item => item.id === 'community-nuclia-tool');
  pm.expect(row.setup_metadata.requirements.join(' ')).to.include('provider-billed');
  pm.expect(row.setup_metadata.requirements.join(' ')).to.include('private cited paragraph snapshots');
  pm.expect(row.setup_metadata.configuration_template.data.actions).to.eql(['query']);
  pm.expect(row.setup_metadata.configuration_template.data.enabled).to.eql(false);
  pm.expect(row.readiness.live_verified).to.eql(false);
});

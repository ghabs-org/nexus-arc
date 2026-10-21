pm.test('Power BI setup exposes permissions and partial-result limits without activating', function () {
  pm.response.to.have.status(200);
  const row = pm.response.json().official_items.find(item => item.id === 'community-powerbi');
  const setup = row.setup_metadata;
  pm.expect(setup.requirements.join(' ')).to.include('read/build');
  pm.expect(setup.requirements.join(' ')).to.include('256 KiB');
  pm.expect(setup.requirements.join(' ')).to.include('partial tables');
  pm.expect(setup.configuration_template.data.enabled).to.eql(false);
  pm.expect(setup.configuration_template.data.actions).to.eql(['read','query']);
  pm.expect(row.readiness.live_verified).to.eql(false);
});

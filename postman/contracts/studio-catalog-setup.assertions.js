pm.test('Catalog includes passive setup for every supported identity', function () {
  pm.response.to.have.status(200);
  const catalog=pm.response.json();
  const rows=[...catalog.items,...catalog.official_items];
  pm.expect(rows.length).to.eql(27);
  pm.expect(new Set(rows.map(row=>row.id)).size).to.eql(27);
  pm.expect(catalog.excluded.length).to.eql(4);
  for(const row of rows){
    pm.expect(row.setup_metadata.billing).to.be.a('string').and.not.empty;
    pm.expect(row.setup_metadata.requirements.length).to.be.above(0);
    pm.expect(row.setup_metadata.installation.length).to.be.above(0);
    pm.expect(row.readiness.live_verified).to.eql(false);
    const data=row.setup_metadata.configuration_template.data;
    if(data)pm.expect(data.enabled).to.eql(false);
  }
});

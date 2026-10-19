// Run against the isolated framework example's GET lifecycle operation.
const body=pm.response.json();
pm.test('Lifecycle contract returns 200',()=>pm.response.to.have.status(200));
pm.test('Saved build exposes managed components',()=>{
 pm.expect(body.status).to.equal('saved');
 pm.expect(body.components.filter(item=>item.managed).length).to.be.above(0);
 pm.expect(body.components.every(item=>typeof item.key==='string'&&Array.isArray(item.issues))).to.equal(true);
});
pm.test('Connectivity evidence stays separate from setup readiness',()=>{
 pm.expect(['not_checked','partial']).to.include(body.live_health);
 pm.expect(body.components.every(item=>item.health&&item.live_health===item.health.status)).to.equal(true);
 pm.expect(body.components.filter(item=>item.health.result).every(item=>item.health.result.format==='nexus-resource-health/v1')).to.equal(true);
});
pm.test('Releases preserve identity and reviewed revision',()=>{
 pm.expect(body.releases.length).to.be.above(0);
 pm.expect(body.releases.every(item=>typeof item.id==='string'&&Number.isInteger(item.revision))).to.equal(true);
});

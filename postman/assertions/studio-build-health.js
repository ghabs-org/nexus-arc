const body=pm.response.json();
pm.test('Explicit local probe succeeds',()=>{
 pm.response.to.have.status(200);
 pm.expect(body.status).to.equal('passed');
});
pm.test('Health belongs to exact build release and component revision',()=>{
 pm.expect(body.format).to.equal('nexus-resource-health/v1');
 pm.expect(body.release_id).to.be.a('string').and.not.empty;
 pm.expect(body.component_revision).to.match(/^[a-f0-9]{64}$/);
 pm.expect(body.revision).to.be.a('number');
});
pm.test('Probe stores metadata without response bodies',()=>{
 pm.expect(body.action).to.equal('select_one');
 pm.expect(body).not.to.have.property('output');
 pm.expect(body).not.to.have.property('rows');
 pm.expect(body).not.to.have.property('error');
 pm.expect(body.reason).to.equal(null);
});

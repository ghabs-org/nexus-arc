pm.test('Scoped operational overview keeps feedback private and reports partial runs',()=>{
 pm.response.to.have.status(200);
 const body=pm.response.json();
 pm.expect(body.format).to.eql('nexus-studio-overview/v1');
 pm.expect(body.scope).to.eql('workspace');
 pm.expect(body.feedback.total).to.eql(0);
 pm.expect(body.evaluations.total).to.eql(2);
 pm.expect(body.evaluations.completed).to.eql(1);
 pm.expect(body.evaluations.items.map(item=>item.status).sort()).to.eql(['completed','failed']);
 pm.expect(body.builds.items[0].health_status).to.eql('available');
 pm.expect(body.builds.items[0].health.checked).to.eql(0);
 pm.expect(body.builds.items[0].comparison).to.eql(null);
 pm.expect(JSON.stringify(body)).not.to.include('PRIVATE_COMMENT');
});

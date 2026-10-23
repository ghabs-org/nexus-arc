pm.test('Saved experiment exposes pinned suite, repeated cases and explicit threshold',()=>{
 pm.response.to.have.status(200);
 const item=pm.response.json().items.find(item=>item.id==='comparison-experiment');
 pm.expect(item.revision).to.eql(1);
 pm.expect(item.data.kind).to.eql('experiment');
 pm.expect(item.data.evaluation_id).to.eql('evidence-suite');
 pm.expect(item.data.evaluation_revision).to.eql(1);
 pm.expect(item.data.runsPerExample).to.eql(2);
 pm.expect(item.data.passThreshold).to.eql(0.5);
 pm.expect(item.data.variants).to.have.lengthOf(1);
});

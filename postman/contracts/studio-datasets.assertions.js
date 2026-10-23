pm.test('Dataset library exposes canonical saved workspace cases',()=>{
 pm.response.to.have.status(200);
 const body=pm.response.json();
 const item=body.items.find(item=>item.id==='policy-references');
 pm.expect(item.revision).to.eql(1);
 pm.expect(item.data.kind).to.eql('dataset');
 pm.expect(item.data.version).to.eql(1);
 pm.expect(item.data.cases[0].input).to.eql('Review policy');
 pm.expect(item.data.cases[0].checks).to.eql([{type:'exact',expected:'Approved'}]);
});

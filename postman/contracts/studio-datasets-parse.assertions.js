pm.test('CSV import validates exact reference output without executing',()=>{
 pm.response.to.have.status(200);
 const body=pm.response.json();
 pm.expect(body.kind).to.eql('dataset');
 pm.expect(body.version).to.eql(1);
 pm.expect(body.cases).to.have.lengthOf(1);
 pm.expect(body.cases[0].checks).to.eql([{type:'exact',expected:'Approved'}]);
});
